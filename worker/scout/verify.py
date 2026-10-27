"""Ask the employer's own site where a job is when a list can't be trusted."""

import logging
import re

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
