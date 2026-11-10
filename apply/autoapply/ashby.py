"""Ashby: questions from its public job-posting API, filled in the hosted application form."""

import re

import httpx
from playwright.sync_api import Page

from .answers import Answer
from .job import Job, Question, strip_html
from .profile import Profile

URL_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")

QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    title locationName descriptionHtml
    applicationForm { sections { fieldEntries { ... on FormFieldEntry { field isRequired } } } }
    surveyForms { sections { fieldEntries { ... on FormFieldEntry { field isRequired } } } }
  }
}"""

# Ashby allows one top-level field per query, so the company name is a second request.
ORG_QUERY = """query ApiOrganization($organizationHostedJobsPageName: String!) {
  organization: organizationFromHostedJobsPageName(organizationHostedJobsPageName: $organizationHostedJobsPageName) { name }
}"""

KINDS = {
    "String": "text",
    "Email": "text",
    "Phone": "text",
    "Number": "text",
    "Date": "text",
    "LongText": "textarea",
    "File": "file",
    "Boolean": "select",
    "ValueSelect": "select",
    "MultiValueSelect": "multiselect",
    "Location": "location",
}


def matches(url: str) -> bool:
    return bool(URL_RE.search(url))


def _graphql(query: str, **variables) -> dict:
    r = httpx.post("https://jobs.ashbyhq.com/api/non-user-graphql",
                   json={"query": query, "variables": variables}, timeout=20)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"Ashby API: {body['errors'][0]['message']}")
    return body["data"]


def fetch_job(url: str) -> Job:
    m = URL_RE.search(url)
    if not m:
        raise ValueError(f"Not an Ashby posting: {url}")
    org, job_id = m[1], m[2]
    posting = _graphql(QUERY, organizationHostedJobsPageName=org, jobPostingId=job_id)["jobPosting"]
    organization = _graphql(ORG_QUERY, organizationHostedJobsPageName=org)["organization"]
    if not posting:
        raise ValueError(f"Ashby posting not found (closed?): {url}")

    questions = []
    for form in [posting["applicationForm"], *(posting.get("surveyForms") or [])]:
        for section in form["sections"]:
            for entry in section["fieldEntries"]:
                f = entry.get("field")
                kind = f and KINDS.get(f.get("type"))
                if not kind:
                    continue
                options = ["Yes", "No"] if f["type"] == "Boolean" else [
                    v["label"] for v in f.get("selectableValues") or []]
                questions.append(Question(f["path"], strip_html(f.get("title", "")), kind, entry["isRequired"], options))

    return Job(
        system="Ashby",
        board=org,
        id=job_id,
        company=(organization or {}).get("name") or org,
        title=posting["title"],
        location=posting.get("locationName") or "",
        url=f"https://jobs.ashbyhq.com/{org}/{job_id}/application",
        description=strip_html(posting.get("descriptionHtml", "")),
        questions=questions,
    )


def _entry(page: Page, field: str):
    return page.locator(f'[data-field-path="{field}"]').first


def _pick(page: Page, field: str, label: str) -> None:
    entry = _entry(page, field)
    yes_no = entry.locator(f'button[data-option="{label.lower()}"]')
    if yes_no.count():
        yes_no.first.click()
        return
    option = entry.locator("label").filter(has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))
    if option.count():
        option.first.click()
        return
    # Long lists render as a searchable dropdown.
    box = entry.locator("input").first
    box.click()
    box.press_sequentially(label[:40], delay=30)
    page.get_by_role("option", name=label, exact=True).first.click(timeout=5000)


def fill(page: Page, job: Job, answers: dict[str, Answer], p: Profile) -> list[str]:
    problems = []
    page.goto(job.url, wait_until="networkidle")
    by_field = {q.field: q for q in job.questions}
    for field, a in answers.items():
        q = by_field[field]
        try:
            if q.kind == "location":
                box = _entry(page, field).locator("input").first
                box.click()
                box.press_sequentially(p["address.city"], delay=50)
                options = page.get_by_role("option")
                options.first.wait_for(timeout=8000)
                match = options.filter(has_text="Virginia")
                (match if match.count() else options).first.click()
                continue
            if a.value in (None, "", []):
                continue
            if q.kind == "file":
                _entry(page, field).locator("input[type=file]").set_input_files(a.value)
            elif q.kind in ("text", "textarea"):
                _entry(page, field).locator("input, textarea").first.fill(str(a.value))
            elif q.kind == "select":
                _pick(page, field, a.value)
            elif q.kind == "multiselect":
                for v in a.value:
                    _pick(page, field, v)
        except Exception as e:  # noqa: BLE001 — report and keep going
            problems.append(f"{q.label[:60]}: {type(e).__name__}")

    # The form re-renders while it loads and can drop typed text; refill anything that's empty.
    page.wait_for_timeout(1000)
    for field, a in answers.items():
        q = by_field[field]
        if q.kind in ("text", "textarea") and a.value:
            box = _entry(page, field).locator("input, textarea").first
            if box.count() and not box.input_value():
                box.fill(str(a.value))
                if not box.input_value():
                    problems.append(f"{q.label[:60]}: stays empty")
    return problems


CONFIRM_RE = re.compile(r"thank you for applying|application (was )?(successfully )?submitted|we.ve received your application", re.I)


def confirmed(page: Page) -> bool:
    return bool(CONFIRM_RE.search(page.inner_text("body")))


def submit(page: Page, screenshot, ask_code=None, get_code=None) -> str:
    """Ashby doesn't email codes; the code arguments match Greenhouse's submit."""
    page.get_by_role("button", name=re.compile(r"submit application", re.I)).first.click()
    try:
        page.wait_for_function(
            "() => /thank you for applying|successfully submitted|received your application|missing|required/i.test(document.body.innerText)",
            timeout=30000,
        )
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(1500)
    page.screenshot(path=str(screenshot), full_page=True)
    if CONFIRM_RE.search(page.inner_text("body")):
        return "submitted"
    errors = page.locator('[class*="error"], [role="alert"]').all_inner_texts()
    return "error: " + ("; ".join(e.strip() for e in errors if e.strip())[:300] or "no confirmation")
