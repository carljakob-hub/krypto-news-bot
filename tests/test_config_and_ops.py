"""Konfiguration, Preflight-Sperre, Monitoring, Warnungen, Scheduler."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from newsbot.cli import preflight_key, preflight_valid
from newsbot.config import ConfigError, Mode, load_secrets, validate_for_mode
from newsbot.db.database import iso
from newsbot.monitoring.alerts import AlertManager
from newsbot.monitoring.metrics import collect_metrics, is_healthy, render_report
from tests.conftest import make_settings

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmn"


def test_defaults_to_test_mode(base_dir):
    assert make_settings(base_dir, NEWSBOT_MODE="").mode is Mode.TEST


def test_invalid_mode_rejected(base_dir):
    with pytest.raises(ConfigError):
        make_settings(base_dir, NEWSBOT_MODE="live")


def test_invalid_token_format_rejected():
    with pytest.raises(ConfigError):
        load_secrets({"TELEGRAM_BOT_TOKEN": "not-a-token"})


def test_invalid_chat_id_rejected():
    with pytest.raises(ConfigError):
        load_secrets({"TELEGRAM_CHAT_ID": "@meinegruppe"})


def test_production_requires_credentials(base_dir):
    s = make_settings(base_dir, NEWSBOT_MODE="production")
    problems = validate_for_mode(s)
    assert any("TELEGRAM_BOT_TOKEN" in p for p in problems) and any("TELEGRAM_CHAT_ID" in p for p in problems)


def test_test_and_production_chat_must_differ(base_dir):
    s = make_settings(base_dir, NEWSBOT_MODE="production", TELEGRAM_BOT_TOKEN=TOKEN,
                      TELEGRAM_CHAT_ID="-1001", TELEGRAM_TEST_CHAT_ID="-1001")
    assert validate_for_mode(s)


def test_only_configured_chats_are_allowed(base_dir):
    test = make_settings(base_dir, TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1001", TELEGRAM_TEST_CHAT_ID="-1002")
    assert test.allowed_chat_ids() == {-1002}  # Testmodus erreicht die Produktionsgruppe nie
    prod = make_settings(base_dir, NEWSBOT_MODE="production", TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1001",
                         TELEGRAM_TEST_CHAT_ID="-1002", TELEGRAM_ADMIN_CHAT_ID="7")
    assert prod.allowed_chat_ids() == {-1001, 7}


def test_secrets_not_in_repr(base_dir):
    s = make_settings(base_dir, TELEGRAM_BOT_TOKEN=TOKEN, ANTHROPIC_API_KEY="sk-ant-secret-value-123")
    assert TOKEN not in repr(s.secrets) and "sk-ant-secret" not in str(s.secrets)


def test_llm_auto_only_with_key(base_dir):
    assert not make_settings(base_dir).llm_active()
    assert make_settings(base_dir, ANTHROPIC_API_KEY="sk-ant-x").llm_active()


def test_sources_must_be_https(base_dir):
    p = base_dir / "config" / "sources.yaml"
    p.write_text(p.read_text().replace("https://www.sec.gov/news/pressreleases.rss", "http://www.sec.gov/news/pressreleases.rss"))
    with pytest.raises(ConfigError):
        make_settings(base_dir)


def test_preflight_gate(base_dir, db):
    s = make_settings(base_dir, NEWSBOT_MODE="production", TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1001")
    assert not preflight_valid(db, s)[0]
    db.kv_set("preflight", {"key": preflight_key(s), "at": "x", "passed": False})
    assert not preflight_valid(db, s)[0]
    db.kv_set("preflight", {"key": preflight_key(s), "at": "x", "passed": True})
    assert preflight_valid(db, s)[0]
    # Jede Änderung an Regeln oder Zielgruppe macht den Preflight ungültig
    s2 = make_settings(base_dir, NEWSBOT_MODE="production", TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1002")
    assert not preflight_valid(db, s2)[0]
    cfg = base_dir / "config" / "config.yaml"
    cfg.write_text(cfg.read_text().replace("min_independent_sources: 2", "min_independent_sources: 3"))
    s3 = make_settings(base_dir, NEWSBOT_MODE="production", TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1001")
    assert not preflight_valid(db, s3)[0]


def test_metrics_mark_missing_values(settings, db, now):
    m = collect_metrics(db, settings, now)
    report = render_report(m)
    assert "nicht verfügbar" in report and "nicht konfiguriert" in report
    assert m["items_found"] == 0


def test_health(settings, db, now):
    assert not is_healthy(db, settings, now)[0]
    db.kv_set("last_successful_research", iso(now - timedelta(minutes=5)))
    assert is_healthy(db, settings, now)[0]
    assert not is_healthy(db, settings, now + timedelta(hours=2))[0]


async def test_outage_alert_with_cooldown_and_recovery(settings, db, now):
    db.kv_set("last_successful_research", iso(now - timedelta(hours=2)))
    am = AlertManager(settings, db, telegram=None)
    assert await am.evaluate(now) == ["no_research"]
    assert await am.evaluate(now + timedelta(minutes=10)) == []           # Abkühlzeit
    assert await am.evaluate(now + timedelta(hours=3)) == ["no_research"]  # erneut nach Abkühlzeit
    db.kv_set("last_successful_research", iso(now + timedelta(hours=3)))
    await am.evaluate(now + timedelta(hours=3, minutes=1))
    assert db.one("SELECT 1 FROM alerts WHERE key='no_research'") is None


async def test_failed_cycles_alert(settings, db, now):
    db.kv_set("consecutive_failed_cycles", settings.config.monitoring.alert_consecutive_failed_cycles)
    assert "failed_cycles" in await AlertManager(settings, db, None).evaluate(now)


async def test_scheduler_survives_errors_and_stops(settings, db):
    from newsbot.scheduler import run_forever

    class FlakyPipeline:
        def __init__(self):
            self.cfg = settings.config.model_copy(deep=True)
            self.cfg.schedule.tick_seconds = 0
            self.db = db
            self.calls = 0

        async def run_cycle(self):
            self.calls += 1
            raise RuntimeError("boom")

    p = FlakyPipeline()
    await run_forever(p, stop=asyncio.Event(), max_cycles=3)
    assert p.calls == 3  # Fehler beenden den Dauerbetrieb nicht

    stop = asyncio.Event()
    stop.set()
    p2 = FlakyPipeline()
    await run_forever(p2, stop=stop)
    assert p2.calls == 0


def test_migrations_idempotent(db):
    assert db.migrate() == []


def test_topic_changes_invalidate_preflight(base_dir, db):
    a = make_settings(base_dir, NEWSBOT_MODE="production", TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1001", TELEGRAM_TOPIC_ID="5")
    b = make_settings(base_dir, NEWSBOT_MODE="production", TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_CHAT_ID="-1001", TELEGRAM_TOPIC_ID="6")
    assert a.target_topic_id() == 5
    db.kv_set("preflight", {"key": preflight_key(a), "at": "x", "passed": True})
    assert preflight_valid(db, a)[0] and not preflight_valid(db, b)[0]
