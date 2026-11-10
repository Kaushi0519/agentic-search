"""Fill (and optionally submit) one Greenhouse application.

    python -m autoapply <greenhouse url>            fill, screenshot, don't submit
    python -m autoapply <greenhouse url> --headed   same, in a visible browser
    python -m autoapply <greenhouse url> --submit   fill and submit
"""

import argparse
import sys
from datetime import datetime

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .answers import answer_all
from .greenhouse_api import fetch_job
from .greenhouse_form import fill, submit
from .profile import PRIVATE, load_profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--category", default=None)
    args = ap.parse_args()

    load_dotenv(PRIVATE / "tracker.env")
    profile = load_profile()
    job = fetch_job(args.url)
    resume = profile.resume_for(job.title, args.category)
    print(f"{job.company} — {job.title} ({job.location})\nResume: {resume}\n")

    answers = answer_all(job, profile, resume)
    labels = {q.field: q for q in job.questions}
    for field, a in answers.items():
        q = labels[field]
        flag = "  " if a.confident else "??"
        value = a.value if not isinstance(a.value, str) or len(a.value) < 100 else a.value[:97] + "..."
        print(f"{flag} [{a.source:7}] {q.label[:70]}{'*' if q.required else ''}\n            -> {value}")

    shots = PRIVATE / "screenshots"
    shots.mkdir(exist_ok=True)
    stamp = f"{job.board}-{job.id}-{datetime.now():%Y%m%d-%H%M%S}"
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PRIVATE / "browser-profile", headless=not args.headed, viewport={"width": 1280, "height": 1000})
        page = ctx.new_page()
        problems = fill(page, job, answers, profile)
        page.screenshot(path=str(shots / f"{stamp}-filled.png"), full_page=True)
        print("\nCouldn't fill:" if problems else "\nAll fields filled.", *problems, sep="\n  ")
        print(f"Screenshot: {shots / f'{stamp}-filled.png'}")
        if args.submit:
            result = submit(page, shots / f"{stamp}-submitted.png")
            print("Result:", result)
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
