"""Identify the same job posting across sources by its job ID."""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

TRACKING_PARAMS = {"ref", "src", "source", "gh_src", "lever-source", "lever-origin", "trk"}
SHORTENER_HOSTS = {"go.vansh.nyc"}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
# Workday puts the requisition ID at the end of the slug: ..._JR0286629, ..._REQ_0000083297-1
WORKDAY_REQ_RE = re.compile(r"_([A-Za-z]{0,6}[-_]?\d[\w-]*)$")
# A bare number is an ID; a number leading a slug only if it is long, so the
# year in "2027-data-science-intern" never counts.
NUMERIC_SEGMENT_RE = re.compile(r"^(\d{4,})$|^(\d{6,})-")
ID_QUERY_PARAMS = ("gh_jid", "job", "jobid", "job_id", "jid", "pid", "reqid", "requisitionid", "id")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    )
    path = parts.path.rstrip("/").removesuffix("/apply")
    return urlunsplit(("https", host, path, urlencode(query), ""))


def job_id(url: str) -> str | None:
    """The posting's ID in its job system, scoped so IDs can't collide.

    Returns None when the URL has no recognizable ID.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").removesuffix("/apply").removesuffix("/application")
    segments = [s for s in path.split("/") if s]
    query = {k.lower(): v for k, v in parse_qsl(parts.query)}

    # Greenhouse IDs are global, and company sites embed them as ?gh_jid=.
    if "gh_jid" in query:
        return f"greenhouse:{query['gh_jid']}"
    if host.endswith("greenhouse.io"):
        if m := re.search(r"/jobs/(\d+)", path):
            return f"greenhouse:{m.group(1)}"
        if query.get("token", "").isdigit():  # /embed/job_app?token=<job id>
            return f"greenhouse:{query['token']}"

    # The same Workday req shows up under different location/site paths.
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        if "myworkdayjobs.com" in host:
            tenant = host.split(".")[0]
        else:
            tenant = segments[segments.index("recruiting") + 1] if "recruiting" in segments[:-1] else host
        if segments and (m := WORKDAY_REQ_RE.search(segments[-1])):
            return f"workday:{tenant}:{m.group(1).upper()}"

    # Lever, Ashby, Rippling and others: UUIDs are unique on their own.
    if m := UUID_RE.search(path):
        return f"uuid:{m.group(0).lower()}"
    if host == "apply.workable.com" and (m := re.search(r"/j/([A-Z0-9]{6,})", path)):
        return f"workable:{m.group(1)}"
    if host.endswith("jobvite.com") and (m := re.search(r"/job/(\w+)", path)):
        return f"jobvite:{m.group(1)}"

    # Company career sites: the last all-numeric path segment (amazon.jobs/en/jobs/10556927/slug).
    for segment in reversed(segments):
        if m := NUMERIC_SEGMENT_RE.match(segment):
            return f"{host}:{m.group(1) or m.group(2)}"
    for param in ID_QUERY_PARAMS:
        if any(c.isdigit() for c in query.get(param, "")):
            return f"{host}:{query[param]}"
    return None


def identity_key(resolved_url: str) -> str:
    """One key per posting: its job ID, or the cleaned-up URL if it has none."""
    if jid := job_id(resolved_url):
        return f"job:{jid}"
    return f"url:{canonical_url(resolved_url)}"


class UrlResolver:
    """Follows link shorteners so the same posting gets the same key."""

    def __init__(self, http: httpx.Client):
        self.http = http
        self.cache: dict[str, str] = {}

    def resolve(self, url: str) -> str:
        if urlsplit(url).netloc.lower() not in SHORTENER_HOSTS:
            return url
        if url not in self.cache:
            try:
                # One hop is enough: the shortener redirects straight to the posting.
                r = self.http.get(url, follow_redirects=False)
                if not r.is_redirect:
                    return url
                self.cache[url] = r.headers["Location"]
            except httpx.HTTPError:
                return url
        return self.cache[url]

    def prefetch(self, urls: list[str]) -> None:
        """Resolve many short links in parallel (first run has ~1,000)."""
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(self.resolve, set(urls)))
