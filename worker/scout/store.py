"""Role storage: Postgres in production, in-memory for tests and dry runs."""

from datetime import datetime, timezone

import psycopg

from .models import Listing, Role


class PostgresStore:
    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=True)

    def load_keys(self) -> list[tuple[str, int]]:
        return self.conn.execute("select key, role_id from role_keys").fetchall()

    def insert_role(self, listing: Listing, locations: list[str], url: str,
                    keys: list[str], silent: bool) -> int:
        now = datetime.now(timezone.utc)
        # Seeded roles take the source's post date so the dashboard's ages make sense.
        first_seen = (listing.posted_at or now) if silent else now
        with self.conn.transaction():
            (role_id,) = self.conn.execute(
                """insert into roles (company, title, url, locations, category, terms,
                                      sources, posted_at, first_seen_at, notified_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                (listing.company, listing.title, url, locations, listing.category,
                 listing.terms, [listing.source], listing.posted_at, first_seen,
                 now if silent else None),
            ).fetchone()
            self._insert_keys(role_id, keys)
        return role_id

    def attach(self, role_id: int, keys: list[str], source: str, locations: list[str]) -> None:
        """Record that another source listed an existing role."""
        with self.conn.transaction():
            self.conn.execute(
                """update roles set
                     sources = case when %(src)s = any(sources) then sources
                                    else sources || array[%(src)s] end,
                     locations = locations || array(
                       select l from unnest(%(locs)s::text[]) l where not l = any(locations))
                   where id = %(id)s""",
                {"src": source, "locs": locations, "id": role_id},
            )
            self._insert_keys(role_id, keys)

    def unnotified(self) -> list[Role]:
        rows = self.conn.execute(
            """select id, company, title, url, locations, first_seen_at
               from roles where notified_at is null order by id"""
        ).fetchall()
        return [Role(*row) for row in rows]

    def mark_notified(self, role_id: int) -> None:
        self.conn.execute("update roles set notified_at = now() where id = %s", (role_id,))

    def _insert_keys(self, role_id: int, keys: list[str]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                "insert into role_keys (key, role_id) values (%s, %s) on conflict do nothing",
                [(k, role_id) for k in keys],
            )


class MemoryStore:
    def __init__(self):
        self.roles: dict[int, dict] = {}
        self.keys: set[tuple[str, int]] = set()

    def load_keys(self) -> list[tuple[str, int]]:
        return list(self.keys)

    def insert_role(self, listing, locations, url, keys, silent) -> int:
        role_id = len(self.roles) + 1
        now = datetime.now(timezone.utc)
        self.roles[role_id] = {
            "company": listing.company, "title": listing.title, "url": url,
            "locations": list(locations), "sources": [listing.source],
            "first_seen_at": (listing.posted_at or now) if silent else now,
            "notified": silent,
        }
        self.keys |= {(k, role_id) for k in keys}
        return role_id

    def attach(self, role_id, keys, source, locations) -> None:
        role = self.roles[role_id]
        if source not in role["sources"]:
            role["sources"].append(source)
        role["locations"] += [l for l in locations if l not in role["locations"]]
        self.keys |= {(k, role_id) for k in keys}

    def unnotified(self) -> list[Role]:
        return [
            Role(i, r["company"], r["title"], r["url"], r["locations"], r["first_seen_at"])
            for i, r in self.roles.items() if not r["notified"]
        ]

    def mark_notified(self, role_id) -> None:
        self.roles[role_id]["notified"] = True
