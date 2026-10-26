"""Print the chat ID of whoever last messaged the bot.

Send your bot any message in Telegram first, then run:
    python scripts/telegram_chat_id.py
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()
updates = httpx.get(
    f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/getUpdates"
).json()["result"]
chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") for u in updates if "message" in u}
if not chats:
    print("No messages yet. Send your bot a message in Telegram, then rerun.")
for chat_id, name in chats.items():
    print(f"TELEGRAM_CHAT_ID={chat_id}  ({name})")
