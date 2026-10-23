"""Verdrahtet alle Komponenten anhand der Einstellungen."""

from __future__ import annotations

from dataclasses import dataclass

from newsbot.analysis.summarizer import build_summarizer
from newsbot.config import Settings
from newsbot.db.database import Database
from newsbot.monitoring.alerts import AlertManager
from newsbot.pipeline import Pipeline
from newsbot.publishing.publisher import Publisher
from newsbot.publishing.telegram import TelegramClient
from newsbot.research.collector import Collector
from newsbot.research.http import HttpFetcher
from newsbot.verification.links import LinkChecker


@dataclass
class App:
    settings: Settings
    db: Database
    fetcher: HttpFetcher
    telegram: TelegramClient | None
    pipeline: Pipeline

    async def aclose(self) -> None:
        await self.fetcher.aclose()
        if self.telegram:
            await self.telegram.aclose()
        self.db.close()


def build_app(settings: Settings, *, fetcher: HttpFetcher | None = None, telegram: TelegramClient | None = None,
              summarizer=None) -> App:
    db = Database(settings.db_path)
    db.migrate()
    db.sync_sources(settings.config.sources)
    rc = settings.config.research
    fetcher = fetcher or HttpFetcher.create(
        rc.user_agent, rc.request_timeout_seconds,
        max_retries=rc.max_retries, base_delay=rc.retry_base_delay_seconds, max_bytes=rc.max_response_bytes,
    )
    if telegram is None and settings.secrets.telegram_bot_token and settings.allowed_chat_ids():
        telegram = TelegramClient(settings.secrets.telegram_bot_token, settings.allowed_chat_ids())
    link_checker = LinkChecker(db, fetcher, settings.config.verification.max_link_checks_per_cycle)
    pipeline = Pipeline(
        settings, db,
        Collector(settings, db, fetcher),
        summarizer or build_summarizer(settings, db),
        Publisher(settings, db, telegram),
        link_checker,
        AlertManager(settings, db, telegram),
    )
    return App(settings, db, fetcher, telegram, pipeline)
