"""GitHub-hosted internship lists and their parsers."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .models import Listing

log = logging.getLogger(__name__)


def _from_unix(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _from_iso(s: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s) if s else None
    except ValueError:
        return None
    # zshah writes some dates as plain "2026-10-28"; treat those as UTC.
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def parse_simplify_format(data: list[dict], source: str) -> list[Listing]:
    """SimplifyJobs' listings.json; vanshb03 uses the same schema."""
    out = []
    for x in data:
        if not x.get("active") or not x.get("is_visible", True):
            continue
        terms = x.get("terms") or ([x["season"]] if x.get("season") else [])
        out.append(Listing(
            source=source,
            source_id=x["id"],
            company=x["company_name"].strip(),
            title=x["title"].strip(),
            url=x["url"],
            locations=x.get("locations") or [],
            category=x.get("category"),
            terms=terms,
            posted_at=_from_unix(x.get("date_posted")),
            degrees=x.get("degrees"),
        ))
    return out


def parse_zshah(data: dict[str, dict], source: str) -> list[Listing]:
    out = []
    for key, x in data.items():
        if not x.get("is_open"):
            continue
        season = x.get("season")
        out.append(Listing(
            source=source,
            source_id=key,
            company=x["company"].strip(),
            title=x["title"].strip(),
            url=x["url"],
            locations=[p.strip() for p in re.split(r";", x.get("location") or "") if p.strip()],
            category=x.get("category"),
            terms=[season] if season and season != "Not stated" else [],
            posted_at=_from_iso(x.get("posted_at")),
        ))
    return out


@dataclass(frozen=True)
class GitHubSource:
    name: str
    repo: str
    branch: str
    path: str
    parse: Callable[[Any, str], list[Listing]]


SOURCES = [
    GitHubSource("simplify", "SimplifyJobs/Summer2027-Internships", "dev",
                 ".github/scripts/listings.json", parse_simplify_format),
    GitHubSource("vansh", "vanshb03/Summer2027-Internships", "dev",
                 ".github/scripts/listings.json", parse_simplify_format),
    GitHubSource("zshah", "zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships",
                 "main", "data/jobs.json", parse_zshah),
]


@dataclass
class Update:
    source: GitHubSource
    sha: str
    etag: str | None
    listings: list[Listing]


class GitHubPoller:
    """Detects new commits to each source's data file.

    Uses ETags, so unchanged polls return 304 and don't count against the
    GitHub rate limit.
    """

    def __init__(self, http: httpx.Client, token: str):
        self.http = http
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        self.etags: dict[str, str] = {}
        self.shas: dict[str, str] = {}

    def check(self, src: GitHubSource) -> Update | None:
        headers = dict(self.headers)
        if etag := self.etags.get(src.name):
            headers["If-None-Match"] = etag
        r = self.http.get(
            f"https://api.github.com/repos/{src.repo}/commits",
            params={"sha": src.branch, "path": src.path, "per_page": 1},
            headers=headers,
        )
        if r.status_code == 304:
            return None
        r.raise_for_status()
        sha = r.json()[0]["sha"]
        if sha == self.shas.get(src.name):
            self.etags[src.name] = r.headers.get("ETag")
            return None
        # Pin the raw URL to the commit; the branch URL is CDN-cached for minutes.
        raw = self.http.get(f"https://raw.githubusercontent.com/{src.repo}/{sha}/{src.path}")
        raw.raise_for_status()
        return Update(src, sha, r.headers.get("ETag"), src.parse(raw.json(), src.name))

    def commit(self, update: Update) -> None:
        """Mark an update as processed. Until then it's retried on the next poll."""
        self.shas[update.source.name] = update.sha
        if update.etag:
            self.etags[update.source.name] = update.etag
