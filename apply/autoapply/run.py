"""Watch for new roles and apply, with a Submit / Skip tap in Telegram for each one.

    python -m autoapply.run

For every role the scout adds from now on, on a system the bot supports, it fills
the form in a real browser and sends you the answers and a screenshot. Tap Submit
and it re-fills the form, submits (asking you for any emailed security code) and
adds the row to the tracker. Roles on other systems are recorded and skipped.
"""

import html
import logging
import os
import time
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, sync_playwright

from .answers import Answer, answer_all
from .job import Job
from .profile import PRIVATE, Profile, load_profile
from .store import Store
from .systems import System, system_for
from .telegram import Telegram
from .tracker import log_application

log = logging.getLogger("autoapply")

POLL_SECONDS = 60
SHOTS = PRIVATE / "screenshots"


def new_roles(since: str) -> list[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.execute(
            """select id, company, title, url, locations, category from roles
               where first_seen_at > %s order by first_seen_at, id""", (since,))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _summary(job: Job, answers: dict[str, Answer], problems: list[str], resume: str) -> str:
    e = html.escape
    labels = {q.field: q.label for q in job.questions}
    lines, unsure = [], []
    for field, a in answers.items():
        if a.source == "profile" or a.value in (None, "", []):
            continue
        value = ", ".join(a.value) if isinstance(a.value, list) else str(a.value)
        line = f"• {e(labels[field][:80])}\n   → <i>{e(value[:300])}</i>"
        (lines if a.confident else unsure).append(line)
    for field, a in answers.items():
        if not a.confident and a.value in (None, "", []):
            unsure.append(f"• {e(labels[field][:80])}\n   → <i>(left blank)</i>")
    text = (f"<b>{e(job.company)}</b> — {e(job.title)}\n"
            f"{e(job.location)} · {job.system} · {resume} resume\n"
            f'<a href="{e(job.url)}">Open the form</a>\n')
    if problems:
        text += "\n⚠️ <b>Couldn't fill</b>\n" + "\n".join(f"• {e(p)}" for p in problems) + "\n"
    if unsure:
        text += "\n❓ <b>Check these</b>\n" + "\n".join(unsure) + "\n"
    return text + "\n<b>Answers</b>\n" + "\n".join(lines)


def _stamp(job: Job) -> str:
    return f"{job.board}-{job.id}-{datetime.now():%Y%m%d-%H%M%S}"


class Runner:
    def __init__(self, ctx: BrowserContext, profile: Profile, store: Store, tg: Telegram):
        self.ctx, self.profile, self.store, self.tg = ctx, profile, store, tg

    def prepare(self, role: dict) -> None:
        """Fill the form, screenshot it, and ask for a Submit / Skip tap."""
        role = {**role, "location": ", ".join(role["locations"][:3])}
        system = system_for(role["url"])
        if not system:
            self.store.add(role, "unsupported")
            return
        app_id = self.store.add(role, "preparing", system=system.name)
        try:
            job = system.fetch_job(role["url"])
            resume = self.profile.resume_for(job.title, role["category"])
            answers = answer_all(job, self.profile, resume)
            page = self.ctx.new_page()
            try:
                problems = system.fill(page, job, answers, self.profile)
                shot = SHOTS / f"{_stamp(job)}-filled.png"
                page.screenshot(path=str(shot), full_page=True)
            finally:
                page.close()
        except Exception as e:  # noqa: BLE001 — one bad posting shouldn't stop the bot
            log.exception("prepare failed for %s", role["url"])
            self.store.update(app_id, status="failed", detail=f"prepare: {e}"[:500])
            self.tg.send(f"⚠️ Couldn't prepare <b>{html.escape(role['company'])}</b> — "
                         f"{html.escape(role['title'])}: {html.escape(str(e)[:200])}")
            return
        message_id = self.tg.send(_summary(job, answers, problems, resume),
                                  [("✅ Submit", f"submit:{app_id}"), ("⏭ Skip", f"skip:{app_id}")])
        self.tg.send_file(shot, "Filled form (not submitted yet)")
        self.store.update(app_id, status="awaiting", resume=resume, answers=answers, message_id=message_id)
        log.info("awaiting your OK: %s — %s", job.company, job.title)

    def handle(self, action: str, app_id: str) -> None:
        row = self.store.get(int(app_id))
        if not row or row["status"] != "awaiting":
            return
        if action == "skip":
            self.store.update(row["id"], status="skipped")
            self.tg.edit_buttons(row["message_id"], "⏭ Skipped")
            return
        if action == "submit":
            self.tg.edit_buttons(row["message_id"], "⏳ Submitting…")
            self.submit(row)

    def submit(self, row) -> None:
        system: System = system_for(row["url"])
        page, shot = self.ctx.new_page(), None
        try:
            job = system.fetch_job(row["url"])
            problems = system.fill(page, job, Store.answers_of(row), self.profile)
            if problems:
                log.warning("refill problems: %s", problems)
            shot = SHOTS / f"{_stamp(job)}-submitted.png"
            result = system.submit(
                page, shot,
                ask_code=lambda: self.tg.send(
                    f"🔐 <b>{html.escape(row['company'])}</b> emailed a security code to "
                    f"{html.escape(self.profile['email'])}. Reply here with the code (15 min)."),
                get_code=self.tg.next_text,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("submit failed for %s", row["url"])
            result = f"error: {e}"
        finally:
            page.close()

        if result == "submitted":
            sheet_row = log_application(row["company"], row["title"], row["location"], row["resume"],
                                        row["system"], row["url"])
            self.store.update(row["id"], status="submitted")
            self.tg.edit_buttons(row["message_id"], f"✅ Submitted · tracker row {sheet_row}")
        else:
            self.store.update(row["id"], status="failed", detail=result[:500])
            self.tg.edit_buttons(row["message_id"], "❌ Not submitted")
            self.tg.send(f"❌ <b>{html.escape(row['company'])}</b> — {html.escape(row['title'])} "
                         f"wasn't submitted: {html.escape(result[:300])}")
            if shot and shot.exists():
                self.tg.send_file(shot, "What the form showed")

    def handle_taps(self, wait: int) -> None:
        self.tg.poll(wait)
        while self.tg.taps:
            self.handle(*self.tg.taps.pop(0))
        self.tg.texts.clear()  # stray messages (codes are read while submitting)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2"):  # their request logs include the bot token in the URL
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_dotenv(PRIVATE / "tracker.env")
    load_dotenv(PRIVATE.parent / "worker" / ".env")  # DATABASE_URL
    SHOTS.mkdir(exist_ok=True)
    profile, store, tg = load_profile(), Store(), Telegram()

    # Only roles that appear after the bot first starts; the backlog isn't applied to.
    since = store.get_meta("since")
    if since is None:
        since = datetime.now(timezone.utc).isoformat()
        store.set_meta("since", since)

    with sync_playwright() as pw:
        # A real, visible browser with a saved profile passes reCAPTCHA far more often.
        ctx = pw.chromium.launch_persistent_context(
            PRIVATE / "browser-profile", headless=False, viewport={"width": 1280, "height": 1000})
        runner = Runner(ctx, profile, store, tg)
        tg.send("🟢 Auto-apply is running.")
        log.info("watching for roles added after %s", since)
        while True:
            try:
                for role in new_roles(since):
                    if not store.known(role["id"]):
                        runner.prepare(role)
                        runner.handle_taps(0)
                deadline = time.monotonic() + POLL_SECONDS
                while time.monotonic() < deadline:
                    runner.handle_taps(20)
            except Exception:  # noqa: BLE001 — network blips; try again next round
                log.exception("loop error")
                time.sleep(30)


if __name__ == "__main__":
    main()
