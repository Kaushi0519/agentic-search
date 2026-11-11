"""The answer-writer bot: send it an application question, get an answer back in Telegram.

    python -m autoapply.writer

Send the company, role, job description and question in any format. The answer comes back in a
copy box. Reply to an answer to revise it ("shorter", "use the fraud detector instead"), and tap
💾 Save to add it to your answer bank, which this bot and the auto-applier both reuse.
It writes from your profile, both resumes, private/knowledge/ (stories, prepared answers, project
notes) and the answer bank, and can search the web for salary questions.
"""

import html
import logging
import os
import re
import time

import anthropic
import yaml
from dotenv import load_dotenv

from .profile import PRIVATE, Profile, load_profile
from .telegram import Telegram

log = logging.getLogger("writer")

MODEL = "claude-haiku-5-5"
KNOWLEDGE = PRIVATE / "knowledge"

RULES = """You write answers to internship application questions for the candidate described in \
<candidate>. The user sends a question plus whatever context they have (company, role, job \
description). Write the answer they will paste into the application, in the candidate's voice.

How to write it:
- Answer only from <candidate>. Never invent experience, numbers, tools, team sizes, roles or \
outcomes. Use the exact numbers and names given there.
- If the answer bank or prepared answers already cover the question, start from that and adapt it \
to this company and role.
- Pick the ONE story, project or experience that best fits the question and the job, and go deep: \
what the candidate did, the specific tools, the hard part, how they handled it, the result. \
Connect it to specifics from the job description.
- Be accurate about team projects: say "my team" and name the candidate's own part (the project \
notes say who did what).
- First person, plain and direct. No filler ("I am passionate about", "I believe", "In today's \
world"). No markdown, no bullet points, no headings: plain paragraphs that paste cleanly.
- Length: follow any limit in the question (words or characters). Otherwise about 120-200 words \
for "why" questions and 150-250 for behavioral or project questions; a short-answer field gets a \
short answer.
- Salary or pay questions: search the web for what this company pays this kind of intern (official \
postings, Levels.fyi, Glassdoor, Simplify), answer with an hourly range in USD that matches the \
evidence, plus "open to your standard intern rate", and list the sources in the notes.
- If the question needs a fact the candidate's materials don't have, write the best answer you can \
and say exactly what to check in the notes.

Reply in exactly this format. If the message has several questions, write a separate \
<question>/<answer> pair for EACH one, in the order asked; never merge them into one answer:
<question>the question, restated in a few words</question>
<answer>the answer to paste</answer>
(…one more pair per additional question…)
<notes>one or two short lines: which story or project you used, anything to check, and sources \
for salary answers. Empty if nothing to add.</notes>"""


def candidate_context(p: Profile) -> str:
    profile = {k: v for k, v in p.data.items() if k != "eeo"}
    parts = [f"<profile>\n{yaml.safe_dump(profile, sort_keys=False, allow_unicode=True)}</profile>"]
    for version in p.resumes:
        parts.append(f"<resume version={version!r}>\n{p.resume_text(version)}\n</resume>")
    for path in sorted(KNOWLEDGE.glob("*.md")):
        parts.append(f"<{path.stem}>\n{path.read_text()}\n</{path.stem}>")
    parts.append(f"<answer_bank>\n{p.answer_bank()}\n</answer_bank>")
    return "<candidate>\n" + "\n\n".join(parts) + "\n</candidate>"


def _tag(text: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    return m[1].strip() if m else ""


class Writer:
    def __init__(self, tg: Telegram):
        self.tg = tg
        self.client = anthropic.Anthropic()
        self.threads: dict[int, list[dict]] = {}       # answer message id -> conversation so far
        self.saved: dict[int, tuple[str, str, int]] = {}  # answer number -> (question, answer, message id)
        self.count = 0

    def write(self, history: list[dict]) -> str:
        # Reloaded every time, so edits to private/ apply to the next question.
        system = [{"type": "text", "text": RULES},
                  {"type": "text", "text": candidate_context(load_profile()),
                   "cache_control": {"type": "ephemeral"}}]
        messages = list(history)
        for _ in range(4):  # web search can pause a long turn; continue it
            msg = self.client.messages.create(
                model=MODEL, max_tokens=4000, system=system, messages=messages,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}])
            if msg.stop_reason != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": msg.content}]
        return "".join(b.text for b in msg.content if b.type == "text")

    def handle_message(self, m: dict) -> None:
        history = self.threads.get(m["reply_to"], [])
        history = [*history, {"role": "user", "content": m["text"]}]
        self.tg.send("✍️ Writing…")
        try:
            reply = self.write(history)
        except Exception as e:  # noqa: BLE001 — tell you instead of going quiet
            log.exception("write failed")
            self.tg.send(f"⚠️ Couldn't write that one: {html.escape(str(e)[:300])}")
            return
        pairs = re.findall(r"<question>(.*?)</question>\s*<answer>(.*?)</answer>", reply, re.S)
        if not pairs:
            pairs = [("Saved answer", _tag(reply, "answer") or reply.strip())]
        if notes := _tag(reply, "notes"):
            self.tg.send(f"<i>{html.escape(notes)}</i>")
        for question, answer in pairs:
            question, answer = question.strip(), answer.strip()
            self.count += 1
            message_id = self.tg.send(f"<b>{html.escape(question)}</b>\n<pre>{html.escape(answer)}</pre>",
                                      [("💾 Save to answer bank", f"save:{self.count}")])
            # Replying to any of the answers continues the same conversation.
            self.threads[message_id] = [*history, {"role": "assistant", "content": reply}]
            self.saved[self.count] = (question or "Saved answer", answer, message_id)

    def save(self, number: int) -> None:
        if number not in self.saved:
            self.tg.send("I don't have that answer anymore (the bot restarted). Send it again to save it.")
            return
        question, answer, message_id = self.saved.pop(number)
        with (PRIVATE / "answer_bank.md").open("a") as f:
            f.write(f"\n## {question}\n\n{answer}\n")
        self.tg.edit_buttons(message_id, "💾 Saved to answer bank")

    def run(self) -> None:
        self.tg.send("🟢 Answer writer is running. Send a question with the company, role and job "
                     "description. Reply to an answer to change it.")
        while True:
            try:
                self.tg.poll(20)
                messages, self.tg.messages = self.tg.messages, []
                self.tg.texts.clear()
                for m in messages:
                    if m["text"].strip().lower() not in ("/start", "hi", "hello"):
                        self.handle_message(m)
                while self.tg.taps:
                    action, number = self.tg.taps.pop(0)
                    if action == "save":
                        self.save(int(number))
            except Exception:  # noqa: BLE001 — network blips; try again
                log.exception("loop error")
                time.sleep(15)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2"):  # their request logs include the bot token in the URL
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_dotenv(PRIVATE / "tracker.env")
    Writer(Telegram(os.environ["ANSWER_BOT_TOKEN"], os.environ["ANSWER_CHAT_ID"])).run()


if __name__ == "__main__":
    main()
