"""Answers for each form question.

Standard and legal questions (name, work authorization, sponsorship, EEO) are
answered by fixed rules from the profile. Everything else goes to Haiku in one
call per application, which flags any answer it isn't sure of.
"""

import json
import re
from dataclasses import dataclass

import anthropic
import yaml
from pydantic import BaseModel

from .job import Job, Question
from .profile import Profile

MODEL = "claude-haiku-5-5"


@dataclass
class Answer:
    value: str | list[str] | None  # text, chosen option label(s), or None to leave blank
    source: str                      # "profile", "rule" or "ai"
    confident: bool = True


def pick(options: list[str], *patterns: str) -> str | None:
    """First option matching the first pattern that matches anything."""
    for p in patterns:
        for o in options:
            if re.search(p, o, re.IGNORECASE):
                return o
    return None


YES, NO = r"^\s*yes\b", r"^\s*no\b"


def _rule(q: Question, p: Profile) -> Answer | None:
    label = q.label.lower()
    text = {
        "first_name": p["name.first"],
        "last_name": p["name.last"],
        "preferred_name": p["name.preferred"],
        "email": p["email"],
        "phone": p["phone"],
        "_systemfield_name": f'{p["name.first"]} {p["name.last"]}',
        "_systemfield_email": p["email"],
    }
    if q.field in text:
        return Answer(text[q.field], "profile")
    if q.field == "comments":
        return Answer(None, "rule")  # Lever's free-form "Additional information": leave blank
    if q.kind == "location":
        return Answer(None, "profile")  # each form's location search box is filled by its filler
    if re.search(r"current (company|employer)", label):
        return Answer(None, "rule")  # left blank on purpose
    if q.kind == "text":
        if re.fullmatch(r"(full |legal )?name", label):
            return Answer(f'{p["name.first"]} {p["name.last"]}', "profile")
        if re.fullmatch(r"(e-?mail|email address)", label):
            return Answer(p["email"], "profile")
        if re.search(r"\bphone\b", label) and "?" not in label:
            return Answer(p["phone"], "profile")
        if re.search(r"\b(zip|postal)\b", label):
            return Answer(str(p["address.zip"]), "profile")
    if q.kind in ("text", "textarea"):
        if "linkedin" in label:
            return Answer(p["links.linkedin"], "profile")
        if "github" in label:
            return Answer(p["links.github"], "profile")
        if re.search(r"website|portfolio|personal (site|page)", label):
            # No portfolio: leave it blank (GitHub only goes where GitHub is asked for).
            # A required one is left blank too and flagged for you to decide.
            return Answer(p["links.portfolio"] or None, "profile", confident=bool(p["links.portfolio"]) or not q.required)
        if re.search(r"preferred (first )?name", label):
            return Answer(p["name.preferred"], "profile")
        # "If yes, please explain…" follow-ups: every gating answer below is "no".
        if re.match(r"if (yes|you|so)\b", label) and not q.required:
            return Answer(None, "rule")
    if q.kind != "select":
        return None

    o = q.options
    rules = [
        (r"authori[sz]ed to work|legally (able|eligible|permitted) to work|right to work", (YES,)),
        (r"sponsor", (NO,)),
        (r"18 years|age of 18", (YES,)),
        # "Yes, I'm willing to relocate" — never "Yes, I already live there". Anything else goes to Haiku.
        (r"relocat", (r"(willing|open|able|plan(ning)?|happy) to (re)?locate|will relocate|relocate (myself|on my own)",
                      r"^\s*yes\s*\.?$", r"^\s*yes\b(?!.*\b(already|currently)\b)")),
        (r"veteran", (r"not a protected veteran", r"^i am not", NO)),
        (r"disabilit", (r"^no,? i (do not|don'?t) have", NO)),
        (r"lgbt", (NO,)),
        (r"hispanic|latin[oa]", (NO,)),
        (r"race|ethnic", (r"^asian\b", r"south asian", r"asian")),
        (r"gender|\bsex\b", (r"^male$", r"^man$", r"^male\b", r"^man\b")),
        (r"transgender", (NO,)),
        (r"citizen", (YES, r"u\.?s\.? citizen")),
    ]
    for pattern, wanted in rules:
        if re.search(pattern, label):
            choice = pick(o, *wanted)
            if choice:
                return Answer(choice, "rule")
    return None


class AIAnswer(BaseModel):
    field: str
    text: str = ""
    choices: list[str] = []
    confident: bool


class AIAnswers(BaseModel):
    answers: list[AIAnswer]


SYSTEM = """You fill out internship applications for the candidate below. Answer each question \
truthfully from the candidate's profile; never invent experience, skills, or facts.

Rules:
- For "select" questions, put exactly one option label in `choices`, copied exactly. For \
"multiselect", one or more labels. Leave `text` empty.
- For text questions, put the answer in `text`. Keep short answers short (a name, a number, a \
city). For essay questions ("why this company", "tell us about a project") write 3-5 plain, \
specific sentences connecting the candidate's real experience to this job. No clichés.
- Salary or pay expectations: use the pay range in the job description if there is one; \
otherwise give the typical hourly rate for this kind of internship at this company in the US.
- Yes/no questions about the candidate's history (worked here before, relatives here, \
non-competes, conflicts of interest, clearances) come from the profile's `facts`.
- Class year / academic standing / "undergrad level": use `facts.class_standing`, not the \
dates; it already accounts for the candidate graduating early.
- Location and relocation: the candidate is open to, available for and willing to relocate \
anywhere in the US, with no relocation assistance needed. Always say so. When asked to choose \
preferred locations or offices, pick options matching `facts.preferred_locations`, in that order \
(respect any limit like "select 1-3"); if none match, pick the largest cities offered. In a \
free-text location question, just say they're open to relocating anywhere in the US; don't \
list other cities or add extra sentences.
- Website / portfolio questions: the candidate has no portfolio site. Leave them empty; never \
put the GitHub link there unless the question asks for GitHub specifically.
- Acknowledgements and certifications that the application is accurate: agree.
- How the candidate heard about the job: they found it on an online internship list. Pick a \
generic option ("Job Board", "Online job posting", "Internet", "Other"); never name a specific \
site like Indeed or LinkedIn.
- An optional question you can't answer from the profile: leave it empty, confident true.
- Set confident=false whenever the profile doesn't say and you had to guess.
- Answer every question in the list, using its exact `field`."""


def _ai(questions: list[Question], job: Job, p: Profile) -> dict[str, Answer]:
    if not questions:
        return {}
    profile = {k: v for k, v in p.data.items() if k != "eeo"}
    prompt = (
        f"<profile>\n{yaml.safe_dump(profile, sort_keys=False, allow_unicode=True)}</profile>\n\n"
        f"<job company={json.dumps(job.company)} title={json.dumps(job.title)} location={json.dumps(job.location)}>\n"
        f"{job.description[:8000]}\n</job>\n\n"
        "<questions>\n"
        + json.dumps([{"field": q.field, "label": q.label, "kind": q.kind, "required": q.required,
                       **({"options": q.options} if q.options else {})} for q in questions], indent=1)
        + "\n</questions>"
    )
    client = anthropic.Anthropic()
    msg = client.messages.parse(
        model=MODEL,
        max_tokens=12000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=AIAnswers,
    )
    by_field = {q.field: q for q in questions}
    out = {}
    for a in msg.parsed_output.answers:
        q = by_field.get(a.field)
        if not q:
            continue
        if q.kind in ("select", "multiselect"):
            valid = [c for c in a.choices if c in q.options]
            ok = a.confident and bool(valid) and len(valid) == len(a.choices)
            value = (valid[:1] if q.kind == "select" else valid) or None
            out[q.field] = Answer(value[0] if value and q.kind == "select" else value, "ai", ok)
        else:
            out[q.field] = Answer(a.text.strip() or None, "ai", a.confident)
    return out


def answer_all(job: Job, p: Profile, resume: str) -> dict[str, Answer]:
    answers: dict[str, Answer] = {}
    pending = []
    for q in job.questions:
        if q.kind == "file":
            if q.field in ("resume", "_systemfield_resume") or re.search(r"\b(resume|cv)\b", q.label, re.I):
                answers[q.field] = Answer(str(p.resumes[resume]), "profile")
            else:
                # Cover letters: skip unless required (flagged for review).
                answers[q.field] = Answer(None, "rule", confident=not q.required)
            continue
        a = _rule(q, p)
        if a:
            answers[q.field] = a
        else:
            pending.append(q)
    answers.update(_ai(pending, job, p))
    for q in pending:
        answers.setdefault(q.field, Answer(None, "ai", confident=not q.required))
    return answers
