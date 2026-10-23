"""Telegram: Erfolg, Ausfälle, Rate-Limits, ungültige Zugangsdaten, Zielbeschränkung, Rechteprüfung."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from newsbot.logging_setup import RedactingFilter, redact
from newsbot.publishing.telegram import (
    ChatNotAllowedError,
    TelegramAuthError,
    TelegramClient,
    TelegramError,
    TelegramPermissionError,
    TelegramTransientError,
)
from tests.conftest import no_sleep, telegram_transport

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmn"
CHAT = -1001234567890


def client(handler, **kw):
    return TelegramClient(TOKEN, {CHAT}, client=telegram_transport(handler), sleep=no_sleep, **kw)


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


async def test_send_message_success():
    sent = {}

    def handler(req):
        sent.update(json.loads(req.content))
        return ok({"message_id": 42})

    assert await client(handler).send_message(CHAT, "<b>Hallo</b>") == 42
    assert sent["parse_mode"] == "HTML" and sent["chat_id"] == CHAT


async def test_refuses_unconfigured_chat():
    with pytest.raises(ChatNotAllowedError):
        await client(lambda r: ok({"message_id": 1})).send_message(-100999, "x")


async def test_rate_limit_respects_retry_after():
    calls = {"n": 0}
    waits = []

    async def sleep(s):
        waits.append(s)

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 7}})
        return ok({"message_id": 5})

    c = TelegramClient(TOKEN, {CHAT}, client=telegram_transport(handler), sleep=sleep)
    assert await c.send_message(CHAT, "x") == 5
    assert waits == [7.0]


async def test_outage_retries_then_raises():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(502, text="Bad Gateway")

    with pytest.raises(TelegramTransientError):
        await client(handler, max_retries=2).send_message(CHAT, "x")
    assert calls["n"] == 3


async def test_network_failure_is_transient():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(TelegramTransientError):
        await client(handler, max_retries=0).send_message(CHAT, "x")


async def test_invalid_token_raises_auth_error_without_retry():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

    with pytest.raises(TelegramAuthError) as exc:
        await client(handler).send_message(CHAT, "x")
    assert calls["n"] == 1
    assert TOKEN not in str(exc.value)


async def test_bot_removed_from_group_is_permission_error():
    def handler(req):
        return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was kicked"})

    with pytest.raises(TelegramPermissionError):
        await client(handler).send_message(CHAT, "x")


async def test_invalid_json_response():
    with pytest.raises(TelegramError):
        await client(lambda r: httpx.Response(200, text="<html>proxy</html>")).send_message(CHAT, "x")


async def test_response_without_message_id():
    with pytest.raises(TelegramError):
        await client(lambda r: ok({"unexpected": True})).send_message(CHAT, "x")


def _setup_handler(chat_type="supergroup", status="administrator", member_extra=None, chat_perms=None):
    def handler(req):
        method = req.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return ok({"id": 999, "is_bot": True, "username": "news_bot"})
        if method == "getChat":
            return ok({"id": CHAT, "type": chat_type, "title": "Trading", "permissions": chat_perms or {"can_send_messages": True}})
        if method == "getChatMember":
            return ok({"status": status, **(member_extra or {})})
        return ok({})
    return handler


async def test_setup_check_ok_for_admin_in_supergroup():
    res = await client(_setup_handler()).check_chat(CHAT, require_group=True)
    assert res.ok and res.bot_username == "news_bot"


async def test_setup_check_detects_missing_membership():
    res = await client(_setup_handler(status="left")).check_chat(CHAT, require_group=True)
    assert not res.ok


async def test_setup_check_requires_admin_in_channel():
    res = await client(_setup_handler(chat_type="channel", status="member")).check_chat(CHAT, require_group=True)
    assert not res.ok


async def test_setup_check_rejects_private_chat_as_production_target():
    res = await client(_setup_handler(chat_type="private")).check_chat(CHAT, require_group=True)
    assert not res.ok


async def test_setup_check_chat_not_found():
    def handler(req):
        if req.url.path.endswith("getMe"):
            return ok({"id": 1, "username": "b"})
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})

    res = await client(handler).check_chat(CHAT, require_group=True)
    assert not res.ok and "nicht erreichbar" in res.problems[0]


def test_secrets_are_redacted_from_logs(caplog):
    assert TOKEN not in redact(f"POST https://api.telegram.org/bot{TOKEN}/sendMessage")
    assert "sk-ant-" not in redact("key sk-ant-api03-abcdefghijklmnop")
    logger = logging.getLogger("redact-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="redact-test"):
        logger.info("Token ist %s", TOKEN)
    assert TOKEN not in caplog.text


async def test_proxy_error_is_transient_and_hides_token():
    def handler(req):
        raise httpx.ProxyError(f"403 for https://api.telegram.org/bot{TOKEN}/getMe")

    with pytest.raises(TelegramTransientError) as exc:
        await client(handler, max_retries=0).get_me()
    assert TOKEN not in str(exc.value) and exc.value.__cause__ is None
