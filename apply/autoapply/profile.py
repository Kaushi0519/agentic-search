"""Your profile and resumes, from the git-ignored private/ folder."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pypdf import PdfReader

PRIVATE = Path(__file__).resolve().parents[2] / "private"

AI_TITLE_RE = re.compile(r"\b(ai|ml|machine learning|data|deep learning|llm|nlp|computer vision)\b", re.IGNORECASE)


@dataclass
class Profile:
    data: dict
    resumes: dict[str, Path]  # "AI/ML" / "SWE" -> PDF

    def __getitem__(self, path: str):
        """profile["name.first"] -> "Kaushal"."""
        value = self.data
        for part in path.split("."):
            value = value[part]
        return value

    def resume_text(self, version: str) -> str:
        return "\n".join(page.extract_text() or "" for page in PdfReader(self.resumes[version]).pages)

    def answer_bank(self) -> str:
        """Answers you've written or approved, reused for matching questions."""
        path = PRIVATE / "answer_bank.md"
        return path.read_text() if path.exists() else ""

    def resume_for(self, title: str, category: str | None) -> str:
        """AI/ML resume for AI, ML and data roles; SWE resume for everything else."""
        if AI_TITLE_RE.search(title) or (category and AI_TITLE_RE.search(category)):
            return "AI/ML"
        return "SWE"


def load_profile() -> Profile:
    data = yaml.safe_load((PRIVATE / "profile.yaml").read_text())
    resumes = {}
    for pdf in PRIVATE.glob("*.pdf"):
        # Finder shows "AI/ML" in a file name as "AI:ML" on disk.
        resumes["AI/ML" if re.search(r"AI[:/_ -]?ML", pdf.name, re.IGNORECASE) else "SWE"] = pdf
    missing = {"AI/ML", "SWE"} - resumes.keys()
    if missing:
        raise SystemExit(f"Missing resume PDF(s) in {PRIVATE}: {', '.join(sorted(missing))}")
    return Profile(data, resumes)
