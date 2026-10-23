"""Warnungen bei längerem Ausfall – an den Admin-Chat (falls konfiguriert) und immer ins Log."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta

from newsbot.config import Settings
from newsbot.db.database import Database, iso, parse_iso
from newsbot.publishing.telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)


class AlertManager:
    def __init__(self, settings: Settings, db: Database, telegram: TelegramClient | None):
        self.settings = settings
        self.cfg = settings.config.monitoring
        self.db = db
        self.telegram = telegram
        self.admin_chat = settings.secrets.telegram_admin_chat_id

    async def evaluate(self, now: datetime, *, telegram_auth_error: bool = False) -> list[str]:
        conditions: dict[str, str | None] = {}
        last = self.db.kv_get("last_successful_research")
        started = self.db.kv_get("started_at")
        reference = parse_iso(last) if last else (parse_iso(started) if started else None)
        limit = timedelta(minutes=self.cfg.alert_no_successful_research_minutes)
        conditions["no_research"] = (
            f"Seit über {self.cfg.alert_no_successful_research_minutes} min keine erfolgreiche Recherche."
            if reference and now - reference > limit else None
        )
        failed = self.db.kv_get("consecutive_failed_cycles") or 0
        conditions["failed_cycles"] = (
            f"{failed} Durchläufe in Folge fehlgeschlagen." if failed >= self.cfg.alert_consecutive_failed_cycles else None
        )
        pub_fail = self.db.kv_get("consecutive_publish_failures") or 0
        conditions["publish_failures"] = (
            f"{pub_fail} Veröffentlichungen in Folge fehlgeschlagen." if pub_fail >= self.cfg.alert_consecutive_publish_failures else None
        )
        conditions["telegram_auth"] = (
            "Telegram lehnt das Bot-Token ab (ungültig oder widerrufen). Token in .env erneuern." if telegram_auth_error else None
        )
        sent = []
        for key, message in conditions.items():
            row = self.db.one("SELECT last_sent_at FROM alerts WHERE key = ?", (key,))
            if message:
                if row and now - parse_iso(row["last_sent_at"]) < timedelta(minutes=self.cfg.alert_cooldown_minutes):
                    continue
                await self._notify(f"⚠️ NewsBot-Warnung: {message}")
                self.db.execute(
                    "INSERT INTO alerts(key, last_sent_at, message) VALUES (?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET last_sent_at = excluded.last_sent_at, message = excluded.message",
                    (key, iso(now), message),
                )
                sent.append(key)
            elif row:
                self.db.execute("DELETE FROM alerts WHERE key = ?", (key,))
                await self._notify(f"✅ NewsBot: Problem behoben ({key}).")
        return sent

    async def _notify(self, text: str) -> None:
        log.critical(text)
        if self.telegram is None or self.admin_chat is None:
            return
        try:
            await self.telegram.send_message(self.admin_chat, html.escape(text))
        except TelegramError as exc:
            log.error("Warnung konnte nicht per Telegram gesendet werden: %s", exc)
