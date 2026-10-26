"""Filter → dedup → store → notify."""

import logging
import time
from typing import Callable

from .dedup import identity_key
from .filters import is_undergrad, us_locations
from .models import Listing

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store, notifier, resolve_url: Callable[[str], str]):
        self.store = store
        self.notifier = notifier
        self.resolve_url = resolve_url
        self.keys: dict[str, int] = dict(store.load_keys())
        # A source we've never stored anything from gets seeded silently,
        # so adding a source doesn't send hundreds of old roles.
        self.seeded = {k.split(":")[1] for k in self.keys if k.startswith("src:")}

    def process(self, source: str, listings: list[Listing]) -> dict[str, int]:
        """Same job ID = same role; different job IDs = different roles."""
        silent = source not in self.seeded
        stats = {"new": 0, "duplicate": 0, "filtered": 0}
        for listing in listings:
            src_key = f"src:{source}:{listing.source_id}"
            if src_key in self.keys:
                continue
            locations = us_locations(listing.locations)
            if locations is None or not is_undergrad(listing):
                stats["filtered"] += 1
                continue

            url = self.resolve_url(listing.url)
            job_key = identity_key(url)
            keys = [src_key, job_key]
            role_id = self.keys.get(job_key)
            if role_id is not None:
                self.store.attach(role_id, keys, source, locations)
                stats["duplicate"] += 1
            else:
                role_id = self.store.insert_role(listing, locations, url, keys, silent)
                stats["new"] += 1
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
