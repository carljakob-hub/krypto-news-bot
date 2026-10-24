"""Ruft verlinkte Primärdokumente lesend ab (mit Cache), um ihre Existenz und ihren Inhalt zu prüfen."""

from __future__ import annotations

from datetime import datetime, timedelta

from newsbot.db.database import Database, iso, parse_iso
from newsbot.models import LinkCheck
from newsbot.research.http import FetchError, HttpFetcher
from newsbot.textutils import clean_text


class LinkChecker:
    def __init__(self, db: Database, fetcher: HttpFetcher, budget: int, cache_hours: float = 6):
        self.db = db
        self.fetcher = fetcher
        self.budget = budget
        self.cache = timedelta(hours=cache_hours)

    def reset_budget(self, budget: int) -> None:
        self.budget = budget

    async def check(self, url: str, now: datetime) -> LinkCheck:
        row = self.db.one("SELECT * FROM link_checks WHERE url = ?", (url,))
        if row and now - parse_iso(row["checked_at"]) < self.cache:
            return LinkCheck(url, bool(row["ok"]), row["status_code"], row["text_excerpt"], row["error"])
        if self.budget <= 0:
            return LinkCheck(url, False, None, "", "Prüfbudget für diesen Durchlauf erschöpft")
        self.budget -= 1
        try:
            res = await self.fetcher.get(url)
            text = clean_text(res.content.decode("utf-8", errors="replace"), limit=20000)
            result = LinkCheck(url, res.status == 200 and bool(text), res.status, text)
        except FetchError as exc:
            result = LinkCheck(url, False, exc.status, "", str(exc))
        self.db.execute(
            "INSERT INTO link_checks(url, checked_at, ok, status_code, text_excerpt, error) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET checked_at=excluded.checked_at, ok=excluded.ok, "
            "status_code=excluded.status_code, text_excerpt=excluded.text_excerpt, error=excluded.error",
            (url, iso(now), int(result.ok), result.status_code, result.text, result.error),
        )
        return result
