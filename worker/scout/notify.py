"""Telegram notifications."""

import html
import logging
import time
from datetime import datetime, timezone

import httpx

from .models import Role

log = logging.getLogger(__name__)


def format_age(since: datetime, now: datetime | None = None) -> str:
    seconds = int(((now or datetime.now(timezone.utc)) - since).total_seconds())
    if seconds < 60:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return "just now"


def format_message(role: Role) -> str:
    e = html.escape
    lines = [f"🏢 <b>{e(role.company)}</b>", f"💼 {e(role.title)}"]
    if len(role.locations) == 1:
        lines.append(f"📍 {e(role.locations[0])}")
    elif role.locations:
        lines.append(f"📍 {len(role.locations)} locations")
        lines += [f"   • {e(loc)}" for loc in role.locations]
    else:
        lines.append("📍 Not listed")
    lines.append(f"⏱ {format_age(role.first_seen_at)}")
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, http: httpx.Client, token: str, chat_id: str):
        self.http = http
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, role: Role) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": format_message(role),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": [[{"text": "Apply", "url": role.url}]]},
        }
        for _ in range(3):
            r = self.http.post(self.url, json=payload)
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
                continue
            if r.status_code == 400 and "reply_markup" in payload:
                # Telegram rejects some URLs as button targets; fall back to a plain link.
                payload.pop("reply_markup")
                payload["text"] += f'\n\n<a href="{html.escape(role.url)}">Apply</a>'
                continue
            if not r.is_success:
                log.error("Telegram send failed for role %s: %s", role.id, r.text)
                return False
            return True
        return False


class ConsoleNotifier:
    def send(self, role: Role) -> bool:
        print("─" * 40)
        print(format_message(role).replace("<b>", "").replace("</b>", ""))
        print(f"🔗 {role.url}")
        return True
