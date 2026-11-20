"""Ask the employer's own site where a job is when a list can't be trusted."""

import logging
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

APPLE_JOB_RE = re.compile(r"jobs\.apple\.com/.*/details/([\w-]+)")


class LocationChecker:
    """Vansh lists every Apple job as "United States" (the country of Apple's
    US site), even ones in Munich or Shanghai. Apple's API has the real one."""

    def __init__(self, http: httpx.Client):
        self.http = http

    def locations(self, url: str) -> list[str] | None:
        """The posting's real locations, or None to keep the list's."""
        if not (m := APPLE_JOB_RE.search(url)):
            return None
        try:
            r = self.http.get(f"https://jobs.apple.com/api/v1/jobDetails/{m[1]}")
            r.raise_for_status()
            return [_apple_location(l) for l in r.json()["res"]["locations"]]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            log.warning("Couldn't check Apple location for %s; using the list's", url)
            return None


def _apple_location(loc: dict) -> str:
    # "United States" postings are nationwide; others name a city.
    if loc["name"] == loc["countryName"]:
        return loc["name"]
    return ", ".join(p for p in (loc.get("city"), loc.get("stateProvince"), loc["countryName"]) if p)


LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def workday_api_url(url: str) -> str | None:
    """The JSON endpoint behind a Workday posting page.
    tenant.wd5.myworkdayjobs.com[/en-US]/Site/job/…  →  tenant.wd5.myworkdayjobs.com/wday/cxs/tenant/Site/job/…
    wd5.myworkdaysite.com[/en-US]/recruiting/tenant/Site/job/…  →  wd5.myworkdaysite.com/wday/cxs/tenant/Site/job/…"""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    segments = [s for s in parts.path.split("/") if s]
    if segments and LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    if "job" not in segments:
        return None
    before, job = segments[: segments.index("job")], segments[segments.index("job"):]
    if host.endswith(".myworkdayjobs.com") and len(before) == 1:
        tenant, site = host.split(".")[0], before[0]
    elif host.endswith("myworkdaysite.com") and len(before) == 3 and before[0] == "recruiting":
        tenant, site = before[1], before[2]
    else:
        return None
    return f"https://{host}/wday/cxs/{tenant}/{site}/" + "/".join(job)


class PostedDateChecker:
    """Lists date a role by when they added it, not when the company posted it, so a
    month-old Workday job can arrive looking brand new. Workday's API has the real date."""

    BACKOFF = 15 * 60  # Workday answers bursts with 429 for several minutes

    def __init__(self, http: httpx.Client):
        self.http = http
        self.paused_until = 0.0

    def posted_at(self, url: str) -> datetime | None:
        """The day the employer posted the job (midnight UTC), or None if unknown."""
        if not (api := workday_api_url(url)) or time.monotonic() < self.paused_until:
            return None
        try:
            r = self.http.get(api, headers={"Accept": "application/json"})
            if r.status_code == 429:
                self.paused_until = time.monotonic() + self.BACKOFF
                log.warning("Workday is rate-limiting; using the lists' dates for %d minutes", self.BACKOFF // 60)
                return None
            r.raise_for_status()
            day = date.fromisoformat(r.json()["jobPostingInfo"]["startDate"])
            return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            log.warning("Couldn't check Workday post date for %s; using the list's", url)
            return None
