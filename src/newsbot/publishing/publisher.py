"""Warteschlange, Frequenzbegrenzung, Duplikatschutz und Versand."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from newsbot.config import Mode, Settings
from newsbot.db.database import Database, iso, parse_iso
from newsbot.publishing.telegram import ChatNotAllowedError, TelegramAuthError, TelegramClient, TelegramError
from newsbot.textutils import jaccard, tokens

log = logging.getLogger(__name__)


@dataclass
class FlushResult:
    sent: int = 0
    failed: int = 0
    deferred: int = 0
    auth_error: bool = False


class Publisher:
    def __init__(self, settings: Settings, db: Database, telegram: TelegramClient | None):
        self.settings = settings
        self.cfg = settings.config.publishing
        self.db = db
        self.telegram = telegram
        self.log_path: Path = settings.resolve(self.cfg.post_log_file)

    # --- Warteschlange --------------------------------------------------
    def enqueue(self, *, cluster_id: int, text: str, headline: str, priority: int, level: str,
                verification_status: str, generated_by: str, now: datetime) -> str:
        if self.db.one("SELECT 1 FROM publications WHERE cluster_id = ?", (cluster_id,)):
            return "exists"
        fp = sorted(tokens(headline))
        status = "queued"
        since = iso(now - timedelta(hours=self.cfg.duplicate_lookback_hours))
        for row in self.db.query(
            "SELECT fingerprint FROM publications WHERE status IN ('queued','sent') AND created_at >= ?", (since,)
        ):
            if jaccard(set(fp), set(json.loads(row["fingerprint"]))) >= self.cfg.duplicate_similarity:
                status = "skipped_duplicate"
                break
        self.db.execute(
            "INSERT INTO publications(cluster_id, created_at, status, priority, mode, relevance_level, "
            "verification_status, fingerprint, text, generated_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cluster_id, iso(now), status, priority, self.settings.mode.value, level, verification_status,
             json.dumps(fp), text, generated_by),
        )
        return status

    # --- Versand ----------------------------------------------------------
    def _sent_since(self, since: datetime) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM publications WHERE status = 'sent' AND sent_at >= ?", (iso(since),))["n"]

    def _last_sent(self) -> datetime | None:
        row = self.db.one("SELECT MAX(sent_at) AS t FROM publications WHERE status = 'sent'")
        return parse_iso(row["t"]) if row and row["t"] else None

    async def flush(self, now: datetime) -> FlushResult:
        res = FlushResult()
        self.db.execute(
            "UPDATE publications SET status = 'expired', last_error = 'Zu lange in der Warteschlange' "
            "WHERE status = 'queued' AND created_at < ?",
            (iso(now - timedelta(hours=self.cfg.max_queue_age_hours)),),
        )
        queue = self.db.query(
            "SELECT * FROM publications WHERE status = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY priority DESC, created_at ASC",
            (iso(now),),
        )
        for row in queue:
            if self._sent_since(now - timedelta(hours=1)) >= self.cfg.max_posts_per_hour or \
               self._sent_since(now - timedelta(days=1)) >= self.cfg.max_posts_per_day:
                res.deferred += len(queue) - res.sent - res.failed
                log.info("Veröffentlichungslimit erreicht – %d Beiträge warten", res.deferred)
                break
            last = self._last_sent()
            high = row["relevance_level"] == "HOCH" and self.cfg.high_priority_bypasses_spacing
            if last and (now - last).total_seconds() < self.cfg.min_seconds_between_posts and not high:
                res.deferred += 1
                continue
            ok = await self._send(row, now, res)
            if ok:
                res.sent += 1
            else:
                res.failed += 1
            if res.auth_error:
                break
        return res

    async def _send(self, row, now: datetime, res: FlushResult) -> bool:
        if row["attempts"] == 0:
            self._write_log(row, now)
        chat_id = self.settings.target_chat_id()
        if self.telegram is None or chat_id is None:
            if self.settings.mode is Mode.PRODUCTION:
                return self._mark_failed(row, now, "Produktionsmodus ohne Telegram-Ziel", final=True)
            self._mark_sent(row, now, None, None)
            return True
        try:
            message_id = await self.telegram.send_message(chat_id, row["text"], topic_id=self.settings.target_topic_id())
        except ChatNotAllowedError as exc:
            return self._mark_failed(row, now, str(exc), final=True)
        except TelegramAuthError as exc:
            res.auth_error = True
            return self._mark_failed(row, now, str(exc))
        except TelegramError as exc:
            return self._mark_failed(row, now, str(exc))
        self._mark_sent(row, now, chat_id, message_id)
        log.info("Beitrag %s veröffentlicht (Chat %s, Nachricht %s)", row["id"], chat_id, message_id)
        return True

    def _mark_sent(self, row, now, chat_id, message_id) -> None:
        self.db.execute(
            "UPDATE publications SET status='sent', sent_at=?, chat_id=?, message_id=?, attempts=attempts+1, last_error=NULL WHERE id=?",
            (iso(now), chat_id, message_id, row["id"]),
        )
        self.db.kv_set("last_successful_publish", iso(now))
        self.db.kv_set("consecutive_publish_failures", 0)

    def _mark_failed(self, row, now, error: str, final: bool = False) -> bool:
        attempts = row["attempts"] + 1
        final = final or attempts >= self.cfg.max_send_attempts
        self.db.execute(
            "UPDATE publications SET status=?, attempts=?, last_error=?, next_attempt_at=? WHERE id=?",
            ("failed" if final else "queued", attempts, error[:500],
             iso(now + timedelta(seconds=60 * 2 ** attempts)), row["id"]),
        )
        n = (self.db.kv_get("consecutive_publish_failures") or 0) + 1
        self.db.kv_set("consecutive_publish_failures", n)
        log.error("Veröffentlichung %s fehlgeschlagen (Versuch %d%s): %s", row["id"], attempts, ", endgültig" if final else "", error)
        return False

    def _write_log(self, row, now: datetime) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"===== {iso(now)} | Modus {self.settings.mode.value} | Beitrag {row['id']} | {row['generated_by']} =====\n")
                fh.write(row["text"] + "\n\n")
        except OSError as exc:
            log.warning("Protokolldatei nicht beschreibbar: %s", exc)
