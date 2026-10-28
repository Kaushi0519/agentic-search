"""Filter → dedup → store → notify."""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from .dedup import identity_key
from .filters import is_past_term, is_undergrad, us_locations
from .models import Listing

log = logging.getLogger(__name__)

# Roles a list adds (or reopens) long after the company posted them go on the
# dashboard under their real age but don't send an alert.
MAX_ALERT_AGE = timedelta(days=2)


def is_stale(listing: Listing) -> bool:
    return (listing.posted_at is not None
            and datetime.now(timezone.utc) - listing.posted_at > MAX_ALERT_AGE)


class Pipeline:
    def __init__(self, store, notifier, resolve_url: Callable[[str], str],
                 check_locations: Callable[[str], list[str] | None] = lambda url: None):
        self.store = store
        self.notifier = notifier
        self.resolve_url = resolve_url
        self.check_locations = check_locations
        self.keys: dict[str, int] = dict(store.load_keys())
        # Job IDs some source marked grad-only. Sources without degree data
        # (vansh, zshah) can list the same job, so their copies are dropped too.
        self.excluded: set[str] = store.load_excluded()
        # A source we've never stored anything from gets seeded silently,
        # so adding a source doesn't send hundreds of old roles.
        self.seeded = {k.split(":")[1] for k in self.keys if k.startswith("src:")}

    def process(self, source: str, listings: list[Listing]) -> dict[str, int]:
        """Same job ID = same role; different job IDs = different roles."""
        silent = source not in self.seeded
        stats = {"new": 0, "old": 0, "duplicate": 0, "filtered": 0}
        for listing in listings:
            src_key = f"src:{source}:{listing.source_id}"
            if src_key in self.keys:
                continue
            locations = us_locations(listing.locations)
            if locations is None or is_past_term(listing.terms):
                stats["filtered"] += 1
                continue
            url = self.resolve_url(listing.url)
            if (real := self.check_locations(url)) is not None:
                locations = us_locations(real)
                if locations is None:
                    stats["filtered"] += 1
                    continue
            job_key = identity_key(url)
            if not is_undergrad(listing, url):
                if job_key not in self.excluded:
                    self.store.add_excluded(job_key)
                    self.excluded.add(job_key)
                stats["filtered"] += 1
                continue
            if job_key in self.excluded:
                stats["filtered"] += 1
                continue

            keys = [src_key, job_key]
            role_id = self.keys.get(job_key)
            if role_id is not None:
                self.store.attach(role_id, keys, source, locations)
                stats["duplicate"] += 1
            else:
                quiet = silent or is_stale(listing)
                role_id = self.store.insert_role(listing, locations, url, keys, quiet)
                stats["old" if quiet and not silent else "new"] += 1
            for k in keys:
                self.keys[k] = role_id

        self.seeded.add(source)
        log.info("%s%s: %s", source, " (seed)" if silent else "", stats)
        return stats

    def flush_notifications(self) -> int:
        """Send every role not yet notified. Failed sends retry next cycle."""
        sent = 0
        for role in self.store.unnotified():
            if not self.notifier.send(role):
                break
            self.store.mark_notified(role.id)
            sent += 1
            time.sleep(1)  # Telegram allows ~1 message/sec per chat
        return sent
