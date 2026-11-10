"""A posting and its form questions, the same shape for every application system."""

import html
import re
from dataclasses import dataclass, field


@dataclass
class Question:
    field: str           # the system's field ID, e.g. "first_name", "question_123", an Ashby UUID
    label: str
    kind: str            # "text", "textarea", "file", "select", "multiselect", "location"
    required: bool
    options: list[str] = field(default_factory=list)


@dataclass
class Job:
    system: str          # "Greenhouse", "Ashby", …
    board: str           # the company's board / org name on that system
    id: str
    company: str
    title: str
    location: str
    url: str             # the form the bot fills
    description: str
    questions: list[Question]


def strip_html(s: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(s or "")))
    return re.sub(r"\s+", " ", text).strip()
