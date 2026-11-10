"""Lever: questions from the apply page's embedded card templates, filled on that page."""

import html
import json
import re

import httpx
from playwright.sync_api import Page

from .answers import Answer
from .job import Job, Question, strip_html
from .profile import Profile

URL_RE = re.compile(r"jobs\.(eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})")

CARD_KINDS = {
    "text": "text",
    "textarea": "textarea",
    "multiple-choice": "select",
    "multiple-select": "multiselect",
    "dropdown": "select",
}

# The fixed part of every Lever form: field name -> (label, kind, required)
STANDARD = {
    "resume": ("Resume/CV", "file", True),
    "name": ("Full name", "text", True),
    "email": ("Email", "text", True),
    "phone": ("Phone", "text", False),
    "location": ("Current location", "location", False),
    "org": ("Current company", "text", False),
    "comments": ("Additional information", "textarea", False),
}


def matches(url: str) -> bool:
    return bool(URL_RE.search(url))


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return html.unescape(m[1]) if m else ""


def fetch_job(url: str) -> Job:
    m = URL_RE.search(url)
    if not m:
        raise ValueError(f"Not a Lever posting: {url}")
    eu, site, job_id = m[1] or "", m[2], m[3]
    form_url = f"https://jobs.{eu}lever.co/{site}/{job_id}/apply"
    page = httpx.get(form_url, timeout=30, follow_redirects=True)
    page.raise_for_status()
    t = page.text

    questions = [Question(name, label, kind, req) for name, (label, kind, req) in STANDARD.items()
                 if f'name="{name}"' in t]
    for name in re.findall(r'name="(urls\[[^\]]+\])"', t):
        questions.append(Question(name, name[5:-1], "text", False))

    for tag in re.findall(r'<input[^>]*name="cards\[[0-9a-f-]+\]\[baseTemplate\]"[^>]*>', t):
        card_id = re.search(r'cards\[([0-9a-f-]+)\]', tag)[1]
        for i, f in enumerate(json.loads(_attr(tag, "value")).get("fields", [])):
            kind = CARD_KINDS.get(f.get("type"))
            if kind:
                questions.append(Question(f"cards[{card_id}][field{i}]", strip_html(f.get("text", "")), kind,
                                          bool(f.get("required")), [o["text"] for o in f.get("options") or []]))

    for name, body in re.findall(r'<select[^>]*name="(eeo\[\w+\])"[^>]*>(.*?)</select>', t, re.S):
        options = [html.unescape(o).strip() for o in re.findall(r"<option[^>]*>([^<]*)<", body)]
        questions.append(Question(name, name[4:-1], "select", False, [o for o in options if o and o != "Select ..."]))

    api = httpx.get(f"https://api.{eu}lever.co/v0/postings/{site}/{job_id}", timeout=20)
    posting = api.json() if api.status_code == 200 else {}
    # <title>Company - Role</title>
    title_tag = html.unescape(re.search(r"<title>(.*?)</title>", t, re.S)[1]).strip()
    company = title_tag.split(" - ")[0] if " - " in title_tag else site

    return Job(
        system="Lever",
        board=site,
        id=job_id,
        company=company,
        title=posting.get("text") or title_tag.split(" - ", 1)[-1],
        location=(posting.get("categories") or {}).get("location", ""),
        url=form_url,
        description=strip_html(posting.get("description", "") + " " + " ".join(
            l.get("content", "") for l in posting.get("lists", []))),
        questions=questions,
    )


def fill(page: Page, job: Job, answers: dict[str, Answer], p: Profile) -> list[str]:
    problems = []
    page.goto(job.url, wait_until="networkidle")
    by_field = {q.field: q for q in job.questions}

    # Resume first: Lever parses it and may prefill name / email, which we then overwrite.
    if (a := answers.get("resume")) and a.value:
        page.locator('input[type=file][name="resume"]').set_input_files(a.value)
        try:
            page.locator(".resume-upload-success, .filename").first.wait_for(timeout=20000)
        except Exception:  # noqa: BLE001
            problems.append("Resume: upload not confirmed")

    for field, a in answers.items():
        q = by_field[field]
        try:
            if q.kind == "location":
                box = page.locator(f'input[name="{field}"]')
                box.fill("")
                box.press_sequentially(f'{p["address.city"]}, {p["address.state"]}', delay=60)
                result = page.locator(".dropdown-results .dropdown-location")
                result.first.wait_for(timeout=8000)
                match = result.filter(has_text=re.compile(r"\bVA\b|Virginia"))
                (match if match.count() else result).first.click()
                continue
            if field == "resume":
                continue
            if a.value in (None, "", []):
                # Lever prefills some fields from the resume (e.g. current company); blank means blank.
                if q.kind in ("text", "textarea") and (box := page.locator(f'[name="{field}"]').first).input_value():
                    box.fill("")
                continue
            if q.kind in ("text", "textarea"):
                page.locator(f'[name="{field}"]').first.fill(str(a.value))
            elif q.kind == "select":
                choice = page.locator(f'input[type=radio][name="{field}"][value="{a.value}"]')
                if choice.count():
                    choice.check()
                else:
                    page.locator(f'select[name="{field}"]').select_option(label=a.value)
            elif q.kind == "multiselect":
                for v in a.value:
                    page.locator(f'input[type=checkbox][name="{field}"][value="{v}"]').check()
        except Exception as e:  # noqa: BLE001
            problems.append(f"{q.label[:60]}: {type(e).__name__}")
    return problems


CONFIRM_RE = re.compile(r"application (submitted|received)|thanks? (you )?for applying", re.I)


def confirmed(page: Page) -> bool:
    return "/thanks" in page.url or bool(CONFIRM_RE.search(page.inner_text("body")))


def submit(page: Page, screenshot, ask_code=None, get_code=None) -> str:
    """Lever doesn't email codes; the code arguments match Greenhouse's submit."""
    page.locator('#btn-submit, button[type=submit]').first.click()
    try:
        page.wait_for_url(re.compile(r"/thanks"), timeout=30000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(1500)
    page.screenshot(path=str(screenshot), full_page=True)
    if "/thanks" in page.url or CONFIRM_RE.search(page.inner_text("body")):
        return "submitted"
    if page.locator('iframe[src*="hcaptcha"][title*="challenge" i]').count():
        return "error: hCaptcha challenge (solve it in the bot's browser, or apply by hand)"
    errors = page.locator(".error-message, .application-error, [role=alert]").all_inner_texts()
    return "error: " + ("; ".join(e.strip() for e in errors if e.strip())[:300] or "no confirmation")
