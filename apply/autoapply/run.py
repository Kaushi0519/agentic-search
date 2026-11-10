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
import re
import time
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page, sync_playwright

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


def role_for_url(url: str) -> dict | None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.execute("select id, company, title, url, locations, category from roles where url = %s", (url,))
        row = cur.fetchone()
        return dict(zip([c.name for c in cur.description], row)) if row else None


def _summary(job: Job, answers: dict[str, Answer], problems: list[str], resume: str) -> str:
    """The role, plus only what needs a look: unfilled fields and answers Haiku wasn't sure of."""
    e = html.escape
    labels = {q.field: q.label for q in job.questions}
    unsure = []
    for field, a in answers.items():
        if a.confident:
            continue
        value = ", ".join(a.value) if isinstance(a.value, list) else (a.value or "(left blank)")
        unsure.append(f"• {e(labels[field][:80])}\n   → <i>{e(str(value)[:300])}</i>")
    filled = sum(1 for a in answers.values() if a.value not in (None, "", []))
    text = (f"<b>{e(job.company)}</b> — {e(job.title)}\n"
            f"{e(job.location)} · {job.system} · {resume} resume · {filled} answers filled\n")
    if problems:
        text += "\n⚠️ <b>Couldn't fill</b>\n" + "\n".join(f"• {e(p)}" for p in problems) + "\n"
    if unsure:
        text += "\n❓ <b>Check these</b>\n" + "\n".join(unsure) + "\n"
    if not problems and not unsure:
        text += "\nEverything filled with confident answers.\n"
    return text + "\nTap ✏️ to see or change the filled form on your Mac."


BUTTONS = lambda app_id: [("✅ Submit", f"submit:{app_id}"), ("✏️ Open & edit", f"edit:{app_id}"),
                          ("⏭ Skip", f"skip:{app_id}")]


BUTTONS = lambda app_id: [("✅ Submit", f"submit:{app_id}"), ("✏️ Open & edit", f"edit:{app_id}"),
                          ("⏭ Skip", f"skip:{app_id}")]


def _stamp(job: Job) -> str:
    return f"{job.board}-{job.id}-{datetime.now():%Y%m%d-%H%M%S}"


class Runner:
    """Fills forms in an invisible browser and keeps each filled page open until you decide.

    ✅ Submit submits that same page. ✏️ Open & edit shows the form, filled, in a visible
    window (only when you ask), where you can change answers and submit on the page or from
    Telegram. ⏭ Skip closes it.
    """

    def __init__(self, pw, profile: Profile, store: Store, tg: Telegram):
        self.pw, self.profile, self.store, self.tg = pw, profile, store, tg
        self.background = _background_browser(pw)
        self.visible: BrowserContext | None = None
        self.pages: dict[int, Page] = {}    # filled in the background, waiting for a tap
        self.editing: dict[int, Page] = {}  # open on screen for you

    def prepare(self, role: dict) -> None:
        """Fill the form in the background and ask for a tap."""
        role = {**role, "location": ", ".join(role["locations"][:3])}
        system = system_for(role["url"])
        if not system:
            self.store.add(role, "unsupported")
            return
        app_id = self.store.add(role, "preparing", system=system.name)
        page = None
        try:
            job = system.fetch_job(role["url"])
            self.store.update(app_id, company=job.company, title=job.title,
                              location=role["location"] or job.location)
            resume = self.profile.resume_for(job.title, role["category"])
            answers = answer_all(job, self.profile, resume)
            page = self.background.new_page()
            problems = system.fill(page, job, answers, self.profile)
        except Exception as e:  # noqa: BLE001 — one bad posting shouldn't stop the bot
            log.exception("prepare failed for %s", role["url"])
            if page:
                page.close()
            self.store.update(app_id, status="failed", detail=f"prepare: {e}"[:500])
            self.tg.send(f"⚠️ Couldn't prepare <b>{html.escape(role['company'])}</b> — "
                         f"{html.escape(role['title'])}: {html.escape(str(e)[:200])}")
            return
        self.pages[app_id] = page
        message_id = self.tg.send(_summary(job, answers, problems, resume), BUTTONS(app_id))
        self.store.update(app_id, status="awaiting", resume=resume, answers=answers, message_id=message_id)
        log.info("awaiting your OK: %s — %s", job.company, job.title)

    def _fill(self, ctx: BrowserContext, row) -> Page:
        """Fill a fresh page from the saved answers (after a restart, or for editing)."""
        system = system_for(row["url"])
        page = ctx.new_page()
        problems = system.fill(page, system.fetch_job(row["url"]), Store.answers_of(row), self.profile)
        if problems:
            log.warning("refill problems for %s: %s", row["company"], problems)
        return page

    def handle(self, action: str, app_id: str) -> None:
        row = self.store.get(int(app_id))
        if not row or row["status"] != "awaiting":
            return
        app_id = row["id"]
        if action == "skip":
            for pages in (self.pages, self.editing):
                if page := pages.pop(app_id, None):
                    page.close()
            self.store.update(app_id, status="skipped")
            self.tg.edit_buttons(row["message_id"], "⏭ Skipped")
        elif action == "edit":
            self.open_for_editing(row)
        elif action == "submit":
            self.tg.edit_buttons(row["message_id"], "⏳ Submitting…")
            self.submit(row)

    def open_for_editing(self, row) -> None:
        app_id = row["id"]
        if page := self.editing.get(app_id):
            page.bring_to_front()
            return
        if self.visible is None:
            self.visible = self.pw.chromium.launch_persistent_context(
                PRIVATE / "browser-profile-visible", headless=False, viewport={"width": 1280, "height": 1000})
            for blank in self.visible.pages:
                blank.close()
        # The background page can't be moved on screen, so the visible one is filled from the
        # same saved answers (no new Haiku call).
        if page := self.pages.pop(app_id, None):
            page.close()
        page = self._fill(self.visible, row)
        page.bring_to_front()
        self.editing[app_id] = page
        self.tg.send(f"✏️ <b>{html.escape(row['company'])}</b> is open on your Mac. Change anything, then "
                     "click Submit on the page or tap ✅ Submit above.")

    def submit(self, row) -> None:
        app_id = row["id"]
        system: System = system_for(row["url"])
        page = self.editing.pop(app_id, None) or self.pages.pop(app_id, None)
        shot = SHOTS / f"{row['company'].replace('/', '-')}-{app_id}-{datetime.now():%Y%m%d-%H%M%S}-submitted.png"
        try:
            page = page or self._fill(self.background, row)
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
            if page:
                page.close()
        self._finish(row, result, shot)

    def _finish(self, row, result: str, shot=None) -> None:
        if result == "submitted":
            sheet_row = log_application(row["company"], row["title"], row["location"], row["resume"],
                                        row["system"], row["url"])
            self.store.update(row["id"], status="submitted")
            self.tg.edit_buttons(row["message_id"], f"✅ Submitted · tracker row {sheet_row}")
        else:
            # Leave it awaiting so ✏️ Open & edit can still finish it by hand.
            message_id = self.tg.send(f"❌ <b>{html.escape(row['company'])}</b> — {html.escape(row['title'])} "
                         f"wasn't submitted: {html.escape(result[:300])}\n"
                         "Tap ✏️ Open & edit to finish it yourself, or ⏭ Skip.",
                         BUTTONS(row["id"]))
            self.store.update(row["id"], detail=result[:500], message_id=message_id)
            if shot and shot.exists():
                self.tg.send_file(shot, "What the form showed")
        self._close_visible_if_idle()

    def check_editing(self) -> None:
        """Notice when you submit (or close) a form you opened."""
        for app_id, page in list(self.editing.items()):
            row = self.store.get(app_id)
            if page.is_closed():
                self.editing.pop(app_id)
                message_id = self.tg.send(f"✏️ You closed <b>{html.escape(row['company'])}</b> without submitting. "
                             "It's still waiting: ✅ Submit, ✏️ Open & edit or ⏭ Skip.", BUTTONS(app_id))
                self.store.update(app_id, message_id=message_id)
            elif system_for(row["url"]).confirmed(page):
                self.editing.pop(app_id)
                page.close()
                self._finish(row, "submitted")
        self._close_visible_if_idle()

    def _close_visible_if_idle(self) -> None:
        if self.visible and not self.editing:
            self.visible.close()
            self.visible = None

    def prepare_link(self, url: str) -> None:
        """A job link you sent the bot: prepare it even if the scout never listed it."""
        role = role_for_url(url) or {"id": -abs(hash(url)) % 10**12, "company": "?", "title": "?",
                                     "url": url, "locations": [], "category": None}
        if self.store.known(role["id"]):
            self.tg.send("You've already sent that one; it's in the list above.")
        elif not system_for(url):
            self.tg.send("I can't apply on that site yet (Greenhouse, Ashby and Lever only).")
        else:
            self.tg.send("⏳ Preparing it…")
            self.prepare(role)

    def handle_taps(self, wait: int) -> None:
        self.tg.poll(wait)
        while self.tg.taps:
            self.handle(*self.tg.taps.pop(0))
        for text in self.tg.texts:
            if m := re.search(r"https?://\S+", text):
                self.prepare_link(m[0])
        self.tg.texts.clear()  # other stray messages (codes are read while submitting)
        self.check_editing()


def _background_browser(pw) -> BrowserContext:
    """Full Chrome in headless mode, so filling never takes over the screen. Headless Chrome
    announces itself in its user agent; use the normal one so sites see a regular browser."""
    path = PRIVATE / "browser-profile"
    ctx = pw.chromium.launch_persistent_context(path, channel="chromium", headless=True)
    agent = ctx.pages[0].evaluate("navigator.userAgent") if ctx.pages else ctx.new_page().evaluate("navigator.userAgent")
    ctx.close()
    ctx = pw.chromium.launch_persistent_context(
        path, channel="chromium", headless=True, viewport={"width": 1280, "height": 1000},
        user_agent=agent.replace("HeadlessChrome", "Chrome"))
    for blank in ctx.pages:
        blank.close()
    return ctx


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
        runner = Runner(pw, profile, store, tg)
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
                    runner.handle_taps(5 if runner.editing else 20)
            except Exception:  # noqa: BLE001 — network blips; try again next round
                log.exception("loop error")
                time.sleep(30)


if __name__ == "__main__":
    main()
