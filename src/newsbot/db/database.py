"""SQLite-Zugriff mit einfachen, versionierten Migrationen."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # --- Migrationen -------------------------------------------------------
    def migrate(self) -> list[str]:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {r["version"] for r in self.conn.execute("SELECT version FROM schema_migrations")}
        applied = []
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.stem in done:
                continue
            with self.tx() as c:
                for stmt in f.read_text(encoding="utf-8").split(";"):
                    if stmt.strip():
                        c.execute(stmt)
                c.execute("INSERT INTO schema_migrations VALUES (?, ?)", (f.stem, iso(utcnow())))
            applied.append(f.stem)
        return applied

    # --- Hilfsfunktionen ---------------------------------------------------
    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def kv_get(self, key: str) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else None

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), iso(utcnow())),
        )

    def sync_sources(self, sources) -> None:
        with self.tx() as c:
            for s in sources:
                c.execute(
                    "INSERT INTO sources(id, name, url, tier, trust, owner_group, enabled) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, tier=excluded.tier, "
                    "trust=excluded.trust, owner_group=excluded.owner_group, enabled=excluded.enabled",
                    (s.id, s.name, s.url, s.tier.value, s.trust, s.owner_group, int(s.enabled)),
                )
