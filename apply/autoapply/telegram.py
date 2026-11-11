"""The apply bot's own Telegram chat: approvals, security codes and results."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class Telegram:
    token: str = field(default_factory=lambda: os.environ["APPLY_BOT_TOKEN"])
    chat_id: str = field(default_factory=lambda: os.environ["APPLY_CHAT_ID"])
    offset: int = 0
    taps: list[tuple[str, str]] = field(default_factory=list)  # (action, application id) from buttons
    texts: list[str] = field(default_factory=list)              # plain messages, e.g. a code
    messages: list[dict] = field(default_factory=list)          # same, with ids, for reply threads

    def _call(self, method: str, timeout: float = 30, **kwargs) -> dict:
        r = httpx.post(f"https://api.telegram.org/bot{self.token}/{method}", timeout=timeout, **kwargs)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method}: {body.get('description')}")
        return body["result"]

    def send(self, text: str, buttons: list[list[tuple[str, str]]] | list[tuple[str, str]] | None = None) -> int:
        """Send text, split into several messages if it's over Telegram's 4,096-character limit.
        Splits fall between lines, so keep each line's HTML tags closed. The buttons go on the
        last part; its message id is returned."""
        parts, current = [], ""
        for line in text.split("\n"):
            if current and len(current) + len(line) + 1 > 4000:
                parts.append(current)
                current = ""
            current += ("\n" if current else "") + line[:4000]
        parts.append(current)
        for i, part in enumerate(parts):
            data = {"chat_id": self.chat_id, "text": part, "parse_mode": "HTML", "disable_web_page_preview": True}
            if buttons and i == len(parts) - 1:
                rows = buttons if isinstance(buttons[0], list) else [buttons]  # one row, or several
                data["reply_markup"] = json.dumps(
                    {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]})
            message_id = self._call("sendMessage", data=data)["message_id"]
        return message_id

    def send_file(self, path: Path, caption: str = "") -> None:
        with path.open("rb") as f:
            self._call("sendDocument", timeout=120, data={"chat_id": self.chat_id, "caption": caption[:1024]},
                       files={"document": (path.name, f)})

    def edit_buttons(self, message_id: int, note: str) -> None:
        """Replace a message's buttons with a note like "Submitting…"."""
        self._call("editMessageReplyMarkup", data={
            "chat_id": self.chat_id, "message_id": message_id,
            "reply_markup": json.dumps({"inline_keyboard": [[{"text": note, "callback_data": "noop"}]]})})

    def poll(self, wait: int = 0) -> None:
        """Collect new button taps and messages from your chat."""
        updates = self._call("getUpdates", timeout=wait + 30,
                             data={"offset": self.offset, "timeout": wait,
                                   "allowed_updates": json.dumps(["message", "callback_query"])})
        for u in updates:
            self.offset = u["update_id"] + 1
            if cb := u.get("callback_query"):
                if str(cb["message"]["chat"]["id"]) != self.chat_id:
                    continue
                self._call("answerCallbackQuery", data={"callback_query_id": cb["id"]})
                action, _, app_id = cb.get("data", "").partition(":")
                if app_id:
                    self.taps.append((action, app_id))
            elif (msg := u.get("message")) and str(msg["chat"]["id"]) == self.chat_id and msg.get("text"):
                self.texts.append(msg["text"])
                self.messages.append({"id": msg["message_id"], "text": msg["text"],
                                      "reply_to": (msg.get("reply_to_message") or {}).get("message_id")})

    def next_text(self) -> str | None:
        self.poll()
        return self.texts.pop(0) if self.texts else None
