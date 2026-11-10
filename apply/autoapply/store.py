"""Every role the bot has picked up, kept in private/applications.db on your Mac."""

import json
import sqlite3
from dataclasses import asdict

from .answers import Answer
from .profile import PRIVATE

SCHEMA = """
create table if not exists applications (
    id          integer primary key,
    role_id     integer unique not null,   -- roles.id in Supabase
    url         text not null,
    company     text not null,
    title       text not null,
    location    text not null,
    system      text,
    resume      text,
    answers     text,                      -- JSON, reused when you tap Submit
    status      text not null,             -- unsupported | awaiting | skipped | submitted | failed
    detail      text,
    message_id  integer,                   -- the Telegram message with the buttons
    created_at  text not null default (datetime('now')),
    updated_at  text not null default (datetime('now'))
);
create table if not exists meta (key text primary key, value text not null);
"""


class Store:
    def __init__(self, path=PRIVATE / "applications.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("select value from meta where key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("insert or replace into meta values (?, ?)", (key, value))
        self.db.commit()

    def known(self, role_id: int) -> bool:
        return self.db.execute("select 1 from applications where role_id = ?", (role_id,)).fetchone() is not None

    def by_role(self, role_id: int) -> sqlite3.Row | None:
        return self.db.execute("select * from applications where role_id = ?", (role_id,)).fetchone()

    def delete(self, app_id: int) -> None:
        self.db.execute("delete from applications where id = ?", (app_id,))
        self.db.commit()

    def add(self, role: dict, status: str, **fields) -> int:
        cur = self.db.execute(
            "insert into applications (role_id, url, company, title, location, status) values (?, ?, ?, ?, ?, ?)",
            (role["id"], role["url"], role["company"], role["title"], role["location"], status))
        self.db.commit()
        if fields:
            self.update(cur.lastrowid, **fields)
        return cur.lastrowid

    def update(self, app_id: int, **fields) -> None:
        if "answers" in fields:
            fields["answers"] = json.dumps({k: asdict(a) for k, a in fields["answers"].items()})
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"update applications set {sets}, updated_at = datetime('now') where id = ?",
                        (*fields.values(), app_id))
        self.db.commit()

    def get(self, app_id: int) -> sqlite3.Row | None:
        return self.db.execute("select * from applications where id = ?", (app_id,)).fetchone()

    @staticmethod
    def answers_of(row: sqlite3.Row) -> dict[str, Answer]:
        return {k: Answer(**v) for k, v in json.loads(row["answers"]).items()}
