"""A Greenhouse posting's questions, from the public job board API."""

import html
import re
from dataclasses import dataclass, field

import httpx
from urllib.parse import urlsplit

# job-boards.greenhouse.io/<board>/jobs/<id>, boards.greenhouse.io/<board>/jobs/<id>,
# or an embed link ...?for=<board>&token=<id>
JOB_RE = re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)")
EMBED_RE = re.compile(r"[?&]for=([\w-]+).*?[?&]token=(\d+)")


@dataclass
class Question:
    field: str           # form field name, e.g. "first_name", "question_123", "4005121007"
    label: str
    kind: str            # "text", "textarea", "file", "select", "multiselect"
    required: bool
    options: list[str] = field(default_factory=list)


@dataclass
class Job:
    board: str
    id: str
    company: str
    title: str
    location: str
    url: str
    description: str
    questions: list[Question]


KINDS = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
    "input_hidden": None,
}


GH_JID_RE = re.compile(r"[?&]gh_jid=(\d+)")
BOARD_IN_PAGE_RE = re.compile(r"greenhouse\.io/(?:embed/job_board(?:/js)?\?for=|embed/job_app\?for=|)([\w-]+)")


def _board_exists(board: str, job_id: str) -> bool:
    r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", timeout=20)
    return r.status_code == 200


def parse_url(url: str) -> tuple[str, str] | None:
    """(board, job id) for a Greenhouse posting, including ones on a company's own site (?gh_jid=)."""
    m = JOB_RE.search(url) or EMBED_RE.search(url)
    if m:
        return m[1], m[2]
    m = GH_JID_RE.search(url)
    if not m:
        return None
    job_id = m[1]
    # The board is usually the company's domain name (epicgames.com -> epicgames)…
    host = urlsplit(url).hostname or ""
    name = host.removeprefix("www.").split(".")[0]
    for board in dict.fromkeys([name, name.replace("-", "")]):
        if _board_exists(board, job_id):
            return board, job_id
    # …otherwise it's named in the careers page's Greenhouse embed.
    page = httpx.get(url, timeout=20, follow_redirects=True).text
    for board in dict.fromkeys(BOARD_IN_PAGE_RE.findall(page)):
        if _board_exists(board, job_id):
            return board, job_id
    return None


def strip_html(s: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(s or "")))
    return re.sub(r"\s+", " ", text).strip()


def _questions(raw: list[dict]) -> list[Question]:
    out = []
    for q in raw:
        for f in q.get("fields", []):
            kind = KINDS.get(f["type"])
            # resume_text / cover_letter_text are the "enter manually" twins of the uploads.
            if kind is None or f["name"] in ("resume_text", "cover_letter_text"):
                continue
            options = [v["label"] for v in f.get("values", [])]
            # Yes/no checkboxes come back as a select with values 0/1.
            out.append(Question(f["name"].removesuffix("[]"), strip_html(q["label"]), kind, q.get("required", False), options))
    return out


def fetch_job(url: str) -> Job:
    parsed = parse_url(url)
    if not parsed:
        raise ValueError(f"Not a Greenhouse posting: {url}")
    board, job_id = parsed
    r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", params={"questions": "true"}, timeout=20)
    r.raise_for_status()
    d = r.json()

    questions = _questions(d.get("questions") or [])
    questions += _questions(d.get("location_questions") or [])
    for section in d.get("compliance") or []:
        questions += _questions(section.get("questions") or [])
    demo = d.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        kind = KINDS.get(q["type"])
        if kind:
            questions.append(Question(str(q["id"]), strip_html(q["label"]), kind, q.get("required", False),
                                      [o["label"] for o in q.get("answer_options", [])]))

    return Job(
        board=board,
        id=job_id,
        company=d.get("company_name") or board,
        title=d["title"],
        location=(d.get("location") or {}).get("name", ""),
        # The embedded form works even when a company sends its hosted page to its own site.
        url=f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={job_id}",
        description=strip_html(d.get("content", "")),
        questions=questions,
    )
