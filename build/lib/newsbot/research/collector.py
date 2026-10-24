"""Fragt fällige Quellen ab und speichert neue Meldungen dedupliziert."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from newsbot.config import Settings, SourceConfig
from newsbot.db.database import Database, iso, parse_iso
from newsbot.models import RawItem
from newsbot.research.http import HttpFetcher
from newsbot.research.rss import parse_feed
from newsbot.textutils import canonical_url, content_hash, injection_signals, sha

log = logging.getLogger(__name__)


@dataclass
class CollectStats:
    sources_polled: int = 0
    fetch_failures: int = 0
    items_found: int = 0
    new_items: list[RawItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Collector:
    def __init__(self, settings: Settings, db: Database, fetcher: HttpFetcher):
        self.settings = settings
        self.db = db
        self.fetcher = fetcher

    def due_sources(self, now: datetime) -> list[SourceConfig]:
        due = []
        default = self.settings.config.schedule.default_poll_interval_seconds
        for src in self.settings.config.enabled_sources():
            row = self.db.one("SELECT last_polled_at, consecutive_failures FROM sources WHERE id = ?", (src.id,))
            last = parse_iso(row["last_polled_at"]) if row else None
            interval = src.poll_interval_seconds or default
            # Fehlerhafte Quellen seltener abfragen (exponentiell, max. 1 Stunde)
            failures = row["consecutive_failures"] if row else 0
            interval = min(interval * (2 ** min(failures, 4)), max(interval, 3600)) if failures else interval
            if last is None or now - last >= timedelta(seconds=interval):
                due.append(src)
        return due

    async def collect(self, now: datetime, sources: list[SourceConfig] | None = None) -> CollectStats:
        stats = CollectStats()
        sources = self.due_sources(now) if sources is None else sources
        results = await asyncio.gather(*(self._poll(src, now) for src in sources), return_exceptions=True)
        for src, res in zip(sources, results):
            stats.sources_polled += 1
            if isinstance(res, BaseException):
                stats.fetch_failures += 1
                stats.errors.append(f"{src.id}: {res}")
                continue
            stats.items_found += len(res)
            for item in res:
                if self._store(item):
                    stats.new_items.append(item)
        return stats

    async def _poll(self, src: SourceConfig, now: datetime) -> list[RawItem]:
        row = self.db.one("SELECT etag, last_modified FROM sources WHERE id = ?", (src.id,))
        status = None
        try:
            result = await self.fetcher.get(src.url, row["etag"] if row else None, row["last_modified"] if row else None)
            status = result.status
            items = [] if result.not_modified else parse_feed(
                result.content, src.id, now, self.settings.config.research.max_items_per_source
            )
        except Exception as exc:  # FetchError, FeedParseError oder Unerwartetes
            status = getattr(exc, "status", status)
            self._record_fetch(src.id, now, ok=False, status=status, items=0, error=str(exc))
            self.db.execute(
                "UPDATE sources SET last_polled_at = ?, consecutive_failures = consecutive_failures + 1 WHERE id = ?",
                (iso(now), src.id),
            )
            log.warning("Abruf fehlgeschlagen: %s (%s)", src.id, exc)
            raise
        self._record_fetch(src.id, now, ok=True, status=status, items=len(items), error=None)
        self.db.execute(
            "UPDATE sources SET last_polled_at = ?, last_success_at = ?, consecutive_failures = 0, "
            "etag = COALESCE(?, etag), last_modified = COALESCE(?, last_modified) WHERE id = ?",
            (iso(now), iso(now), result.etag, result.last_modified, src.id),
        )
        return items

    def _record_fetch(self, source_id, now, *, ok, status, items, error) -> None:
        self.db.execute(
            "INSERT INTO fetch_log(source_id, at, ok, http_status, items, error) VALUES (?,?,?,?,?,?)",
            (source_id, iso(now), int(ok), status, items, (error or "")[:500] or None),
        )

    def _store(self, item: RawItem) -> bool:
        canon = canonical_url(item.url)
        url_hash = sha(canon)
        c_hash = content_hash(item.title, item.summary)
        if self.db.one("SELECT 1 FROM items WHERE url_hash = ?", (url_hash,)):
            return False
        # Gleiche Meldung derselben Quelle unter neuer URL (z. B. Tracking-Varianten) ignorieren
        if self.db.one("SELECT 1 FROM items WHERE content_hash = ? AND source_id = ?", (c_hash, item.source_id)):
            return False
        if injection_signals(item.title + " " + item.summary):
            item.flags.append("prompt_injection_suspected")
        cur = self.db.execute(
            "INSERT INTO items(source_id, url, canonical_url, url_hash, content_hash, title, summary, published_at, "
            "fetched_at, links_json, flags_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.source_id, item.url, canon, url_hash, c_hash, item.title, item.summary,
                iso(item.published_at), iso(item.fetched_at), json.dumps(item.links), json.dumps(item.flags),
            ),
        )
        item.id = cur.lastrowid
        return True
