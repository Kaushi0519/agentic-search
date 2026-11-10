"""Fill (and optionally submit) one application (Greenhouse or Ashby).

    python -m autoapply <url>            fill, screenshot, don't submit
    python -m autoapply <url> --headed   same, in a visible browser
    python -m autoapply <url> --submit   fill and submit
"""

import argparse
import sys
from datetime import datetime

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .answers import answer_all
from .profile import PRIVATE, load_profile
from .systems import system_for
from .tracker import log_application


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--category", default=None)
    args = ap.parse_args()

    load_dotenv(PRIVATE / "tracker.env")
    profile = load_profile()
    system = system_for(args.url)
    if not system:
        raise SystemExit(f"Unsupported application system: {args.url}")
    job = system.fetch_job(args.url)
    resume = profile.resume_for(job.title, args.category)
    print(f"[{job.system}] {job.company} — {job.title} ({job.location})\nResume: {resume}\n")

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
        problems = system.fill(page, job, answers, profile)
        page.screenshot(path=str(shots / f"{stamp}-filled.png"), full_page=True)
        print("\nCouldn't fill:" if problems else "\nAll fields filled.", *problems, sep="\n  ")
        print(f"Screenshot: {shots / f'{stamp}-filled.png'}")
        if args.submit:
            result = system.submit(page, shots / f"{stamp}-submitted.png")
            print("Result:", result)
            if result == "submitted":
                row = log_application(job.company, job.title, job.location, resume, job.system, args.url)
                print(f"Added to tracker (row {row})")
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
