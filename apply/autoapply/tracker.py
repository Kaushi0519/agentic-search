"""Adds a row to the application tracker sheet through its Apps Script web app (tracker.gs)."""

import os
from datetime import date

import httpx


def add_row(row: dict[str, str]) -> int:
    """Append one application. Keys are the sheet's header names. Returns the row number."""
    # Apps Script answers a POST with a redirect to the result; httpx follows it with a GET.
    r = httpx.post(
        os.environ["TRACKER_URL"],
        json={"secret": os.environ["TRACKER_SECRET"], "row": row},
        follow_redirects=True,
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Tracker rejected the row: {body}")
    return body["row"]


def log_application(company: str, title: str, location: str, resume: str, system: str, url: str) -> int:
    return add_row({
        "Company": company,
        "Role": title,
        "Location": location,
        "Application Date": date.today().isoformat(),
        "Status": "Applied",
        "Resume Version": resume,
        "Cover Letter": "No",
        "Notes": f"Auto-applied ({system}) {url}",
    })
