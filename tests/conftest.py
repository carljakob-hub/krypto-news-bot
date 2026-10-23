from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest

from newsbot.config import load_settings
from newsbot.db.database import Database
from newsbot.models import RawItem
from newsbot.research.http import FetchError, FetchResult

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


async def no_sleep(_s: float) -> None:
    return None


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "config", tmp_path / "config")
    (tmp_path / "data").mkdir()
    return tmp_path


def make_settings(base_dir: Path, **env):
    full = {"NEWSBOT_MODE": "test", "NEWSBOT_DB_PATH": "data/test.sqlite3"}
    full.update(env)
    return load_settings(env=full, base_dir=base_dir)


@pytest.fixture
def settings(base_dir):
    return make_settings(base_dir)


@pytest.fixture
def db(settings):
    d = Database(settings.db_path)
    d.migrate()
    d.sync_sources(settings.config.sources)
    yield d
    d.close()


def item(source_id: str, title: str, summary: str = "", *, url: str | None = None, age_hours: float = 1,
         links: list[str] | None = None, flags: list[str] | None = None, published: bool = True) -> RawItem:
    return RawItem(
        source_id=source_id,
        url=url or f"https://www.{source_id}.example/{abs(hash(title)) % 10**8}",
        title=title,
        summary=summary,
        published_at=(NOW - timedelta(hours=age_hours)) if published else None,
        fetched_at=NOW,
        links=links or [],
        flags=flags or [],
    )


def rss(entries: list[dict]) -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title><link>https://x.test</link><description>d</description>']
    for e in entries:
        pub = format_datetime(e.get("published", NOW - timedelta(hours=1)))
        parts.append(
            f"<item><title>{escape(e['title'])}</title><link>{escape(e['link'])}</link>"
            f"<description>{escape(e.get('summary', ''))}</description><pubDate>{pub}</pubDate></item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts).encode()


class FakeFetcher:
    """Ersetzt HTTP: Antworten pro URL (bytes, FetchError oder Liste für aufeinanderfolgende Aufrufe)."""

    def __init__(self, responses: dict[str, object] | None = None):
        self.responses = responses or {}
        self.calls: list[str] = []

    async def get(self, url, etag=None, last_modified=None):
        self.calls.append(url)
        resp = self.responses.get(url)
        if isinstance(resp, list):
            resp = resp.pop(0) if resp else FetchError("keine weitere Antwort", 404)
        if resp is None:
            raise FetchError("HTTP 404", 404)
        if isinstance(resp, Exception):
            raise resp
        return FetchResult(url, 200, resp)

    async def aclose(self):
        pass


def telegram_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
