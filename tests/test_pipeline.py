"""Ende-zu-Ende: finden → prüfen → bewerten → deduplizieren → formatieren → veröffentlichen → protokollieren."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from newsbot.app import build_app
from newsbot.publishing.telegram import TelegramClient
from tests.conftest import FakeFetcher, make_settings, no_sleep, rss

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmn"
TEST_CHAT = -1009876543210
ADMIN_CHAT = 555


def story_feeds(settings, suffix=""):
    cfg = settings.config
    return {
        cfg.source("sec_press").url: rss([{
            "title": "SEC Approves Options Trading on Spot Bitcoin ETFs" + suffix,
            "link": "https://www.sec.gov/newsroom/press-releases/2026-140" + suffix.replace(" ", "-"),
            "summary": "The Securities and Exchange Commission today approved rule changes to list options on spot bitcoin exchange-traded products.",
        }]),
        cfg.source("coindesk").url: rss([{
            "title": "SEC approves options on spot bitcoin ETFs" + suffix,
            "link": "https://www.coindesk.com/policy/2026/10/23/sec-options-bitcoin-etf" + suffix.replace(" ", "-"),
            "summary": "The U.S. regulator approved options listings for spot bitcoin ETFs, a step long sought by issuers.",
        }]),
        cfg.source("theblock").url: rss([{
            "title": "Bitcoin ETF options win SEC approval" + suffix,
            "link": "https://www.theblock.co/post/123" + suffix.replace(" ", "-"),
            "summary": "Options on spot bitcoin exchange-traded funds were approved by the Securities and Exchange Commission.",
        }]),
    }


class FakeTelegram:
    def __init__(self, fail_times=0, status=502, body=None):
        self.sent: list[dict] = []
        self.fail_times = fail_times
        self.status = status
        self.body = body

    def handler(self, req):
        payload = json.loads(req.content)
        if self.fail_times > 0:
            self.fail_times -= 1
            return httpx.Response(self.status, json=self.body or {"ok": False, "error_code": self.status, "description": "x"})
        self.sent.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})

    def client(self, allowed):
        return TelegramClient(TOKEN, allowed, client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
                              sleep=no_sleep, max_retries=0)


def posts_log(settings) -> str:
    p = settings.resolve(settings.config.publishing.post_log_file)
    return p.read_text(encoding="utf-8") if p.exists() else ""


@pytest.fixture
def app_factory(base_dir):
    apps = []

    def make(feeds_fn=story_feeds, telegram=None, env=None, summarizer=None):
        settings = make_settings(base_dir, **(env or {}))
        fetcher = FakeFetcher(feeds_fn(settings) if callable(feeds_fn) else feeds_fn)
        app = build_app(settings, fetcher=fetcher, telegram=telegram.client(settings.allowed_chat_ids()) if telegram else None,
                        summarizer=summarizer)
        apps.append(app)
        return app, settings, fetcher

    yield make
    for a in apps:
        a.db.close()


async def test_verified_story_is_published_once_to_log(app_factory, now):
    app, settings, _ = app_factory()
    app.pipeline.cfg.publishing.mark_test_posts = True
    stats = await app.pipeline.run_cycle(now)
    assert stats.published == 1
    log = posts_log(settings)
    assert log.count("✅ Verifiziert") == 1
    assert "[TESTMODUS]" in log and "🚨🚨 <b>SEHR WICHTIG</b>" in log
    assert 'href="https://www.sec.gov/newsroom/press-releases/2026-140"' in log
    assert "Einschätzung:" in log and "BTC" in log and "👀" in log
    assert "Zeitpunkt:" not in log  # kurz halten: Telegram zeigt die Uhrzeit selbst
    # gespeicherte Belege
    assert app.db.one("SELECT COUNT(*) n FROM evidence WHERE kind='primary_source'")["n"] >= 1


async def test_repeated_processing_does_not_republish(app_factory, now):
    app, settings, fetcher = app_factory()
    await app.pipeline.run_cycle(now)
    for src in settings.config.sources:
        app.db.execute("UPDATE sources SET last_polled_at = NULL")
    fetcher.responses = story_feeds(settings)
    stats = await app.pipeline.run_cycle(now + timedelta(minutes=10))
    assert stats.items_new == 0 and stats.published == 0
    assert posts_log(settings).count("✅ Verifiziert") == 1


async def test_same_story_new_wording_from_new_source_not_reposted(app_factory, now):
    app, settings, fetcher = app_factory()
    await app.pipeline.run_cycle(now)
    app.db.execute("UPDATE sources SET last_polled_at = NULL")
    fetcher.responses = {settings.config.source("decrypt").url: rss([{
        "title": "SEC greenlights options for spot Bitcoin ETFs",
        "link": "https://decrypt.co/1/sec-options", "summary": "Regulators approved options on bitcoin ETFs.",
    }])}
    stats = await app.pipeline.run_cycle(now + timedelta(minutes=10))
    assert stats.items_new == 1 and stats.published == 0
    assert app.db.one("SELECT COUNT(*) n FROM clusters")["n"] == 1


async def test_rumor_is_not_published(app_factory, now):
    def feeds(settings):
        return {settings.config.source("cointelegraph").url: rss([{
            "title": "Rumor: BlackRock reportedly preparing Dogecoin ETF?", "link": "https://cointelegraph.com/r",
            "summary": "Unconfirmed reports on social media claim a filing is imminent.",
        }])}
    app, settings, _ = app_factory(feeds)
    stats = await app.pipeline.run_cycle(now)
    assert stats.published == 0 and posts_log(settings) == ""
    assert stats.by_status.get("UNBESTÄTIGT") == 1


async def test_rate_limit_and_priority(app_factory, now, base_dir):
    def feeds(settings):
        cfg = settings.config
        a = story_feeds(settings)
        a[cfg.source("decrypt").url] = rss([{
            "title": "Federal Reserve cuts interest rate by 25 basis points", "link": "https://decrypt.co/fed",
            "summary": "The FOMC lowered the federal funds rate, a move watched by bitcoin traders."}])
        a[cfg.source("cointelegraph").url] = rss([{
            "title": "Fed lowers federal funds rate by 25 basis points", "link": "https://cointelegraph.com/fed",
            "summary": "Federal Reserve officials cut interest rates; crypto markets reacted."}])
        return a
    cfg_path = base_dir / "config" / "config.yaml"
    cfg_path.write_text(cfg_path.read_text().replace("max_posts_per_hour: 4", "max_posts_per_hour: 1"))
    app, settings, _ = app_factory(feeds)
    stats = await app.pipeline.run_cycle(now)
    assert stats.published == 1
    queued = app.db.one("SELECT COUNT(*) n FROM publications WHERE status='queued'")["n"]
    assert queued == 1
    first = app.db.one("SELECT relevance_level FROM publications WHERE status='sent'")
    assert first["relevance_level"] == "HOCH"


async def test_publishes_to_test_group_when_configured(app_factory, now):
    tg = FakeTelegram()
    app, settings, _ = app_factory(telegram=tg, env={"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_TEST_CHAT_ID": str(TEST_CHAT)})
    stats = await app.pipeline.run_cycle(now)
    assert stats.published == 1
    assert [m["chat_id"] for m in tg.sent] == [TEST_CHAT]


async def test_telegram_outage_retries_later_without_duplicate(app_factory, now):
    tg = FakeTelegram(fail_times=1, status=502)
    app, settings, _ = app_factory(telegram=tg, env={"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_TEST_CHAT_ID": str(TEST_CHAT)})
    stats = await app.pipeline.run_cycle(now)
    assert stats.published == 0 and stats.publish_failures == 1
    row = app.db.one("SELECT status, attempts FROM publications")
    assert row["status"] == "queued" and row["attempts"] == 1
    flush = await app.pipeline.publisher.flush(now + timedelta(minutes=5))
    assert flush.sent == 1 and len(tg.sent) == 1
    flush = await app.pipeline.publisher.flush(now + timedelta(minutes=30))
    assert flush.sent == 0 and len(tg.sent) == 1
    assert posts_log(settings).count("✅ Verifiziert") == 1


async def test_invalid_token_triggers_alert(app_factory, now, caplog):
    tg = FakeTelegram(fail_times=10, status=401, body={"ok": False, "error_code": 401, "description": "Unauthorized"})
    app, settings, _ = app_factory(telegram=tg, env={
        "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_TEST_CHAT_ID": str(TEST_CHAT), "TELEGRAM_ADMIN_CHAT_ID": str(ADMIN_CHAT)})
    await app.pipeline.run_cycle(now)
    assert app.db.one("SELECT 1 FROM alerts WHERE key='telegram_auth'")
    assert TOKEN not in caplog.text


async def test_permanent_failure_after_max_attempts(app_factory, now):
    tg = FakeTelegram(fail_times=100, status=403)
    app, settings, _ = app_factory(telegram=tg, env={"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_TEST_CHAT_ID": str(TEST_CHAT)})
    await app.pipeline.run_cycle(now)
    t = now
    for _ in range(5):
        t += timedelta(minutes=30)
        await app.pipeline.publisher.flush(t)
    row = app.db.one("SELECT status, attempts FROM publications")
    assert row["status"] == "failed" and row["attempts"] == settings.config.publishing.max_send_attempts


async def test_all_sources_down_marks_cycle_failed(app_factory, now):
    app, settings, _ = app_factory(feeds_fn={})
    stats = await app.pipeline.run_cycle(now)
    assert stats.status == "failed"
    assert app.db.kv_get("consecutive_failed_cycles") == 1
    assert app.db.kv_get("last_successful_research") is None


async def test_old_queued_posts_expire(app_factory, now):
    tg = FakeTelegram(fail_times=1, status=502)
    app, settings, _ = app_factory(telegram=tg, env={"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_TEST_CHAT_ID": str(TEST_CHAT)})
    await app.pipeline.run_cycle(now)
    await app.pipeline.publisher.flush(now + timedelta(hours=7))
    assert app.db.one("SELECT status FROM publications")["status"] == "expired"
    assert tg.sent == []


async def test_first_run_does_not_flood_with_older_news(app_factory, now):
    """Beim ersten Start sind Feeds voller Meldungen der letzten Stunden – diese verlieren Neuheitswert."""
    def feeds(settings):
        f = story_feeds(settings)
        for url, body in list(f.items()):
            f[url] = body.replace(b"Fri, 23 Oct 2026 11:00:00", b"Fri, 23 Oct 2026 03:00:00")
        return f
    app, settings, _ = app_factory(feeds)
    stats = await app.pipeline.run_cycle(now)
    row = app.db.one("SELECT relevance_level, relevance_score FROM clusters")
    assert row["relevance_level"] != "HOCH"


async def test_verified_but_unimportant_news_is_not_posted(app_factory, now):
    """Nur MITTEL und HOCH werden veröffentlicht – verifizierte, aber unwichtige Meldungen nicht."""
    def feeds(settings):
        return {settings.config.source("sec_press").url: rss([{
            "title": "SEC Charges Investment Adviser for Misleading Clients About Fees",
            "link": "https://www.sec.gov/newsroom/press-releases/2026-150",
            "summary": "The Commission charged a registered investment adviser with failing to disclose fees.",
        }])}
    app, settings, _ = app_factory(feeds)
    stats = await app.pipeline.run_cycle(now)
    assert stats.by_status.get("VERIFIZIERT") == 1
    assert stats.published == 0 and posts_log(settings) == ""
    assert app.db.one("SELECT relevance_level FROM clusters")["relevance_level"] == "NIEDRIG"


async def test_posts_only_into_configured_topic(app_factory, now):
    tg = FakeTelegram()
    app, settings, _ = app_factory(telegram=tg, env={
        "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_TEST_CHAT_ID": str(TEST_CHAT), "TELEGRAM_TEST_TOPIC_ID": "12"})
    await app.pipeline.run_cycle(now)
    assert [(m["chat_id"], m["message_thread_id"]) for m in tg.sent] == [(TEST_CHAT, 12)]


async def test_test_mode_marker_can_be_disabled(app_factory, now):
    app, settings, _ = app_factory()
    app.pipeline.cfg.publishing.mark_test_posts = False
    stats = await app.pipeline.run_cycle(now)
    assert stats.published == 1
    log = posts_log(settings)
    assert "[TESTMODUS]" not in log and "🚨🚨 <b>SEHR WICHTIG</b>" in log
