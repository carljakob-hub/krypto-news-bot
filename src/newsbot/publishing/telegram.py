"""Minimaler, robuster Client für die Telegram Bot API (nur benötigte Methoden)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from newsbot.logging_setup import redact

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, message: str, *, code: int | None = None, retry_after: float | None = None):
        super().__init__(redact(message))
        self.code = code
        self.retry_after = retry_after


class TelegramAuthError(TelegramError):
    """Token ungültig oder widerrufen (401)."""


class TelegramPermissionError(TelegramError):
    """Bot darf nicht in den Chat schreiben bzw. Chat existiert nicht (400/403)."""


class TelegramTransientError(TelegramError):
    """Vorübergehender Fehler (Netzwerk, 5xx, 429)."""


class ChatNotAllowedError(TelegramError):
    """Versuch, in einen nicht konfigurierten Chat zu senden."""


@dataclass
class ChatCheck:
    ok: bool
    problems: list[str]
    bot_username: str | None = None
    chat_title: str | None = None
    chat_type: str | None = None


class TelegramClient:
    def __init__(
        self,
        token: str,
        allowed_chat_ids: set[int],
        *,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        sleep=asyncio.sleep,
        base_url: str = "https://api.telegram.org",
    ):
        if not token:
            raise TelegramAuthError("Kein Bot-Token konfiguriert")
        self._token = token
        self.allowed = set(allowed_chat_ids)
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20))
        self.max_retries = max_retries
        self.sleep = sleep
        self.base_url = base_url

    def __repr__(self) -> str:
        return f"TelegramClient(allowed={sorted(self.allowed)})"

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/bot{self._token}/{method}"
        attempt = 0
        while True:
            try:
                return await self._call_once(url, method, payload or {})
            except TelegramTransientError as exc:
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                delay = exc.retry_after if exc.retry_after is not None else min(2 ** attempt, 30)
                log.info("Telegram %s: vorübergehender Fehler (%s), neuer Versuch in %.0fs", method, exc, delay)
                await self.sleep(delay)

    async def _call_once(self, url: str, method: str, payload: dict) -> Any:
        try:
            resp = await self.client.post(url, json=payload)
        except httpx.TransportError as exc:  # Timeout, Netzwerk, Proxy, Protokoll
            raise TelegramTransientError(f"Netzwerkfehler bei {method}: {type(exc).__name__}") from None
        except httpx.HTTPError as exc:
            raise TelegramError(f"HTTP-Fehler bei {method}: {type(exc).__name__}") from None
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or "ok" not in data:
            if resp.status_code >= 500 or resp.status_code == 429:
                raise TelegramTransientError(f"{method}: HTTP {resp.status_code}", code=resp.status_code)
            raise TelegramError(f"{method}: ungültige Antwort (HTTP {resp.status_code})", code=resp.status_code)
        if data.get("ok"):
            return data.get("result")
        code = data.get("error_code", resp.status_code)
        desc = str(data.get("description", "unbekannter Fehler"))[:300]
        if code == 401:
            raise TelegramAuthError(f"{method}: Token ungültig oder widerrufen ({desc})", code=code)
        if code == 429:
            retry_after = float((data.get("parameters") or {}).get("retry_after", 5))
            raise TelegramTransientError(f"{method}: Rate-Limit", code=code, retry_after=retry_after)
        if code in (400, 403):
            raise TelegramPermissionError(f"{method}: {desc}", code=code)
        if code >= 500:
            raise TelegramTransientError(f"{method}: {desc}", code=code)
        raise TelegramError(f"{method}: {desc}", code=code)

    # --- API ------------------------------------------------------------
    async def get_me(self) -> dict:
        return await self._call("getMe")

    async def get_chat(self, chat_id: int) -> dict:
        return await self._call("getChat", {"chat_id": chat_id})

    async def get_chat_member(self, chat_id: int, user_id: int) -> dict:
        return await self._call("getChatMember", {"chat_id": chat_id, "user_id": user_id})

    async def send_message(self, chat_id: int, text: str, *, topic_id: int | None = None,
                           disable_preview: bool = True) -> int:
        if chat_id not in self.allowed:
            raise ChatNotAllowedError(f"Chat {chat_id} ist nicht als Ziel konfiguriert – Versand verweigert")
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": disable_preview},
        }
        if topic_id is not None:
            payload["message_thread_id"] = topic_id  # nur in dieses Thema der Gruppe
        result = await self._call("sendMessage", payload)
        if not isinstance(result, dict) or "message_id" not in result:
            raise TelegramError("sendMessage: Antwort ohne message_id")
        return int(result["message_id"])

    async def check_chat(self, chat_id: int, *, require_group: bool) -> ChatCheck:
        """Prüft Token, Chat-Existenz, Chat-Typ und Schreibrechte des Bots."""
        problems: list[str] = []
        me = await self.get_me()
        bot_id, username = me.get("id"), me.get("username")
        try:
            chat = await self.get_chat(chat_id)
        except TelegramPermissionError as exc:
            return ChatCheck(False, [f"Chat {chat_id} nicht erreichbar: {exc}. Ist der Bot Mitglied der Gruppe?"], username)
        ctype = chat.get("type")
        title = chat.get("title") or chat.get("username")
        if require_group and ctype not in ("group", "supergroup", "channel"):
            problems.append(f"Chat {chat_id} ist vom Typ '{ctype}', erwartet wird eine Gruppe oder ein Kanal")
        member = await self.get_chat_member(chat_id, bot_id)
        status = member.get("status")
        if ctype == "channel":
            if status != "administrator" or not member.get("can_post_messages", False):
                problems.append("Bot ist kein Administrator mit dem Recht 'Nachrichten senden' im Kanal")
        elif ctype in ("group", "supergroup"):
            if status in ("left", "kicked"):
                problems.append("Bot ist kein Mitglied der Gruppe")
            elif status == "restricted" and not member.get("can_send_messages", False):
                problems.append("Bot darf in der Gruppe keine Nachrichten senden")
            elif (chat.get("permissions") or {}).get("can_send_messages") is False and status != "administrator":
                problems.append("In der Gruppe dürfen nur Admins schreiben – Bot als Administrator hinzufügen")
        return ChatCheck(not problems, problems, username, title, ctype)
