"""Fill a Greenhouse application form in a real browser."""

import re
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .answers import Answer
from .job import Job
from .profile import PRIVATE, Profile


def _by_id(field: str) -> str:
    return f'[id="{field}"]'


def _choose(page: Page, field: str, label: str) -> None:
    """Pick an option in one of Greenhouse's searchable dropdowns."""
    box = page.locator(_by_id(field))
    box.click()
    box.fill(label[:40])
    option = page.get_by_role("option", name=label, exact=True)
    try:
        option.first.click(timeout=3000)
    except PlaywrightTimeout:
        # Some long labels don't survive the search filter; list everything instead.
        box.fill("")
        page.get_by_role("option", name=label, exact=True).first.click(timeout=3000)


def _choose_first(page: Page, field: str, query: str, prefer: str | None = None) -> None:
    """Type into a search-as-you-type dropdown and pick the first (or preferred) suggestion."""
    box = page.locator(_by_id(field))
    box.click()
    box.press_sequentially(query, delay=50)  # a bulk fill doesn't trigger the search
    options = page.get_by_role("option")
    options.first.wait_for(timeout=8000)
    if prefer and (match := options.filter(has_text=prefer)).count():
        match.first.click()
    else:
        options.first.click()


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def fill(page: Page, job: Job, answers: dict[str, Answer], p: Profile) -> list[str]:
    """Fill every answered field. Returns problems (fields that couldn't be filled)."""
    problems = []
    page.goto(job.url, wait_until="networkidle")
    kinds = {q.field: q for q in job.questions}

    # Fields the API doesn't list: country (before phone) and the education block.
    extras = [
        ("country", lambda: _choose_first(page, "country", "United States")),
        # Not in the API, and the race question only appears once it's answered.
        ("hispanic_ethnicity", lambda: _choose(page, "hispanic_ethnicity", "Yes" if p["eeo.hispanic_latino"] else "No")),
        ("candidate-location", lambda: _choose_first(page, "candidate-location", p["address.city"], prefer="Virginia")),
        ("school--0", lambda: _choose_first(page, "school--0", p["education.school"])),
        ("degree--0", lambda: _choose_first(page, "degree--0", "Bachelor")),
        ("discipline--0", lambda: _choose_first(page, "discipline--0", p["education.major"])),
        ("end-month--0", lambda: _choose(page, "end-month--0", MONTHS[int(str(p["education.graduation"])[5:7]) - 1])),
        ("start-year--0", lambda: page.fill(_by_id("start-year--0"), str(p["education.start"])[:4])),
        ("end-year--0", lambda: page.fill(_by_id("end-year--0"), str(p["education.graduation"])[:4])),
    ]
    for field, action in extras:
        if page.locator(_by_id(field)).count() and not page.locator(_by_id(field)).input_value():
            try:
                action()
            except Exception as e:  # noqa: BLE001 — optional fields; note and move on
                problems.append(f"{field}: {type(e).__name__}")

    for field, a in answers.items():
        if a.value in (None, "", []):
            continue
        q = kinds[field]
        try:
            if q.kind == "file":
                page.locator(f'input[type=file]{_by_id(field)}').set_input_files(a.value)
            elif q.kind in ("text", "textarea"):
                page.locator(_by_id(field)).fill(str(a.value))
            elif q.kind == "select":
                _choose(page, field, a.value)
            elif q.kind == "multiselect":
                for v in a.value:
                    _choose(page, field, v)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{q.label[:60]}: {type(e).__name__}")
    # Required consent boxes (e.g. "I consent to … processing my demographic responses"). They only
    # cover answers the form already holds, so tick them; anything else is left for review.
    for box in page.locator("input[type=checkbox]:not(:checked)").all():
        label = page.locator(f'label[for="{box.get_attribute("id")}"]')
        text = label.inner_text() if label.count() else ""
        if box.get_attribute("required") is not None and re.search(r"consent|acknowledge|agree|certify", text, re.I):
            box.check()
        elif box.get_attribute("required") is not None:
            problems.append(f"checkbox: {text[:60]}")
    return problems


CONFIRM_RE = re.compile(r"thank you for (applying|your application)|application (has been )?(received|submitted)", re.I)


CODE_FILE = PRIVATE / "code.txt"


def _outcome(page: Page) -> str | None:
    body = page.inner_text("body")
    if CONFIRM_RE.search(body) or "/confirmation" in page.url:
        return "submitted"
    if re.search(r"security code|verification code", body, re.I):
        return "needs_code"
    return None


def confirmed(page: Page) -> bool:
    return _outcome(page) == "submitted"


def _code_from_file() -> str | None:
    if not CODE_FILE.exists():
        return None
    code = CODE_FILE.read_text()
    CODE_FILE.unlink()
    return code


def _ask_in_terminal() -> None:
    CODE_FILE.unlink(missing_ok=True)
    print("Waiting for the emailed security code "
          f"(type it in the browser and click Submit, or write it to {CODE_FILE})", flush=True)


def wait_for_code(page: Page, screenshot: Path, ask: Callable[[], None], get: Callable[[], str | None],
                  minutes: int = 15) -> str:
    """Greenhouse emailed a security code. `ask` tells you; `get` is polled for the code (it can
    also be typed straight into the open browser). Then submit."""
    ask()
    for _ in range(minutes * 30):
        if _outcome(page) == "submitted":
            break
        if code := get():
            code = re.sub(r"\s", "", code)
            boxes = page.locator('input[id^="security-input"], input[name*="security"], input[autocomplete="one-time-code"], input[maxlength="1"]')
            if boxes.count() > 1:
                for box, ch in zip(boxes.all(), code):
                    box.fill(ch)
            else:
                (boxes.first if boxes.count() else page.get_by_label(re.compile("security code", re.I))).fill(code)
            page.get_by_role("button", name=re.compile(r"submit", re.I)).first.click()
        page.wait_for_timeout(2000)
    page.wait_for_timeout(2000)
    page.screenshot(path=str(screenshot), full_page=True)
    return _outcome(page) or "error: no confirmation after code"


def submit(page: Page, screenshot: Path, ask_code: Callable[[], None] = _ask_in_terminal,
           get_code: Callable[[], str | None] = _code_from_file) -> str:
    """Click Submit. Returns "submitted", "needs_code" (email security code) or "error: …"."""
    page.get_by_role("button", name=re.compile(r"submit", re.I)).first.click()
    try:
        page.wait_for_function(
            """() => /thank you for (applying|your application)|application (has been )?(received|submitted)|security code|verification code/i
                     .test(document.body.innerText)""",
            timeout=30000,
        )
    except PlaywrightTimeout:
        pass
    page.screenshot(path=str(screenshot), full_page=True)
    outcome = _outcome(page)
    if outcome == "needs_code":
        return wait_for_code(page, screenshot, ask_code, get_code)
    if outcome:
        return outcome
    errors = page.locator('[class*="error"], [role="alert"]').all_inner_texts()
    return "error: " + ("; ".join(e.strip() for e in errors if e.strip())[:300] or "no confirmation")
