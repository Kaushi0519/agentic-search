"""Worker entry point: poll sources forever.

    python -m scout.main            # production (Postgres + Telegram)
    python -m scout.main --dry-run  # in-memory store, prints notifications
    python -m scout.main --once     # single poll cycle, then exit
"""

import argparse
import logging
import os
import time

import httpx
from dotenv import load_dotenv

from .dedup import UrlResolver
from .notify import ConsoleNotifier, TelegramNotifier
from .pipeline import Pipeline
from .sources import SOURCES, GitHubPoller
from .store import MemoryStore, PostgresStore
from .verify import LocationChecker

log = logging.getLogger("scout")

# Consecutive failed polls of one source before Telegram gets a warning.
ALERT_AFTER_FAILURES = 10


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    http = httpx.Client(timeout=30, headers={"User-Agent": "agentic-search/0.1"})
    if args.dry_run:
        store, notifier = MemoryStore(), ConsoleNotifier()
    else:
        store = PostgresStore(os.environ["DATABASE_URL"])
        notifier = TelegramNotifier(http, os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])

    poller = GitHubPoller(http, os.environ["GITHUB_TOKEN"])
    resolver = UrlResolver(http)
    pipeline = Pipeline(store, notifier, resolver.resolve, LocationChecker(http).locations)
    interval = int(os.environ.get("POLL_SECONDS", "60"))

    failures = {src.name: 0 for src in SOURCES}

    while True:
        for src in SOURCES:
            try:
                if update := poller.check(src):
                    resolver.prefetch([l.url for l in update.listings])
                    pipeline.process(src.name, update.listings)
                    poller.commit(update)
                if failures[src.name] >= ALERT_AFTER_FAILURES:
                    notifier.send_text(f"✅ {src.name} is back to normal.")
                failures[src.name] = 0
            except Exception:
                log.exception("Polling %s failed; will retry", src.name)
                failures[src.name] += 1
                if failures[src.name] == ALERT_AFTER_FAILURES:
                    notifier.send_text(
                        f"⚠️ {src.name} has failed {ALERT_AFTER_FAILURES} polls in a row. "
                        "New roles from it may be delayed; check the Railway logs."
                    )
        try:
            if sent := pipeline.flush_notifications():
                log.info("Sent %d notification(s)", sent)
        except Exception:
            log.exception("Sending notifications failed; will retry")
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
