"""RSS/Atom-Feeds in RawItems umwandeln. Feed-Inhalte gelten als nicht vertrauenswürdig."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

import feedparser

from newsbot.models import RawItem
from newsbot.textutils import clean_text, extract_links


class FeedParseError(Exception):
    pass


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


def parse_feed(content: bytes, source_id: str, fetched_at: datetime, max_items: int = 40) -> list[RawItem]:
    if not content or not content.strip():
        raise FeedParseError("Leere Antwort")
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise FeedParseError(f"Ungültiger Feed: {type(parsed.get('bozo_exception')).__name__}")
    if not parsed.get("version"):
        raise FeedParseError("Kein RSS/Atom-Inhalt erkannt")
    items: list[RawItem] = []
    for entry in parsed.entries[:max_items]:
        link = (entry.get("link") or "").strip()
        title = clean_text(entry.get("title"), limit=300)
        if not title or not link.startswith(("http://", "https://")):
            continue
        raw_summary = entry.get("summary") or entry.get("description") or ""
        if entry.get("content"):
            raw_summary = entry["content"][0].get("value") or raw_summary
        published = _entry_time(entry)
        flags = []
        if published and published > fetched_at + timedelta(hours=1):
            flags.append("future_date")
            published = None
        if published is None:
            flags.append("missing_date")
        items.append(
            RawItem(
                source_id=source_id,
                url=link,
                title=title,
                summary=clean_text(raw_summary, limit=2000),
                published_at=published,
                fetched_at=fetched_at,
                links=[l for l in extract_links(raw_summary) if l != link][:20],
                flags=flags,
            )
        )
    return items
