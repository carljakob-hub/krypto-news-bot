"""Kommandozeile: newsbot <befehl>."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from newsbot.app import build_app
from newsbot.config import ConfigError, Mode, Settings, load_settings, validate_for_mode
from newsbot.db.database import Database, iso, utcnow
from newsbot.logging_setup import setup_logging
from newsbot.monitoring.metrics import collect_metrics, is_healthy, render_report
from newsbot.publishing.telegram import TelegramClient, TelegramError
from newsbot.research.http import FetchError, HttpFetcher
from newsbot.research.rss import FeedParseError, parse_feed

log = logging.getLogger("newsbot")


def preflight_key(settings: Settings) -> str:
    return f"{settings.config.fingerprint()}:{settings.secrets.telegram_chat_id}:{settings.secrets.telegram_topic_id}"


def preflight_valid(db: Database, settings: Settings) -> tuple[bool, str]:
    rec = db.kv_get("preflight")
    if not rec:
        return False, "Kein Preflight vorhanden. Bitte zuerst 'newsbot preflight' ausführen."
    if not rec.get("passed"):
        return False, "Letzter Preflight ist fehlgeschlagen."
    if rec.get("key") != preflight_key(settings):
        return False, "Konfiguration oder Zielgruppe hat sich seit dem Preflight geändert. Preflight erneut ausführen."
    return True, f"Preflight vom {rec.get('at')} gültig."


def _print_problems(problems: list[str]) -> None:
    for p in problems:
        print(f"  ✗ {p}")


def cmd_check_config(settings: Settings, _args) -> int:
    problems = validate_for_mode(settings)
    print(f"Modus: {settings.mode.value}")
    print(f"Aktivierte Quellen: {len(settings.config.enabled_sources())}")
    print(f"Sprachmodell: {'aktiv (' + settings.config.llm.model + ')' if settings.llm_active() else 'aus – Textvorlagen'}")
    print(f"Telegram-Ziel: {settings.target_chat_id() or 'keins (nur lokales Protokoll)'}")
    print(f"Konfigurations-Fingerprint: {settings.config.fingerprint()}")
    if problems:
        print("Probleme:")
        _print_problems(problems)
        return 1
    print("✓ Konfiguration in Ordnung")
    return 0


async def _check_sources(settings: Settings) -> int:
    rc = settings.config.research
    fetcher = HttpFetcher.create(rc.user_agent, rc.request_timeout_seconds, max_retries=1, max_bytes=rc.max_response_bytes)
    bad = 0
    try:
        for src in settings.config.enabled_sources():
            try:
                res = await fetcher.get(src.url)
                items = parse_feed(res.content, src.id, utcnow(), 100)
                dated = sum(1 for i in items if i.published_at)
                print(f"  ✓ {src.id:<18} {len(items):>3} Meldungen ({dated} mit Datum)")
            except (FetchError, FeedParseError) as exc:
                bad += 1
                print(f"  ✗ {src.id:<18} {exc}")
    finally:
        await fetcher.aclose()
    print(f"{bad} von {len(settings.config.enabled_sources())} Quellen fehlerhaft")
    return 1 if bad else 0


async def _check_telegram(settings: Settings, send_test: bool) -> int:
    s = settings.secrets
    if not s.telegram_bot_token:
        print("✗ TELEGRAM_BOT_TOKEN fehlt")
        return 1
    targets = []
    if s.telegram_chat_id is not None:
        targets.append(("Produktionsgruppe", s.telegram_chat_id, True))
    if s.telegram_test_chat_id is not None:
        targets.append(("Testgruppe", s.telegram_test_chat_id, False))
    if s.telegram_admin_chat_id is not None:
        targets.append(("Admin-Chat", s.telegram_admin_chat_id, False))
    allowed = {t[1] for t in targets}
    client = TelegramClient(s.telegram_bot_token, allowed)
    failed = 0
    try:
        me = await client.get_me()
        print(f"✓ Token gültig – Bot @{me.get('username')}")
        for label, chat_id, require_group in targets:
            check = await client.check_chat(chat_id, require_group=require_group)
            if check.ok:
                print(f"✓ {label} {chat_id}: '{check.chat_title}' ({check.chat_type}) – Bot darf schreiben")
            else:
                failed += 1
                print(f"✗ {label} {chat_id}:")
                _print_problems(check.problems)
        if send_test:
            test_target = s.telegram_test_chat_id or s.telegram_admin_chat_id
            test_topic = s.telegram_test_topic_id if s.telegram_test_chat_id else None
            if test_target is None:
                print("✗ Für --send-test wird TELEGRAM_TEST_CHAT_ID oder TELEGRAM_ADMIN_CHAT_ID benötigt")
                failed += 1
            else:
                await client.send_message(test_target, "🧪 <b>NewsBot-Test</b>: Verbindung funktioniert.", topic_id=test_topic)
                print(f"✓ Testnachricht an {test_target} gesendet")
    except TelegramError as exc:
        print(f"✗ Telegram-Fehler: {exc}")
        return 1
    finally:
        await client.aclose()
    if not targets:
        print("Hinweis: Keine Chat-ID konfiguriert.")
    return 1 if failed else 0


def _run_tests(base: Path) -> tuple[bool, str]:
    tests = base / "tests"
    if not tests.exists():
        return False, "Testverzeichnis nicht gefunden"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", str(tests)],
        cwd=base, capture_output=True, text=True, timeout=600,
        env={**os.environ, "NEWSBOT_MODE": "test", "TELEGRAM_BOT_TOKEN": "", "ANTHROPIC_API_KEY": ""},
    )
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:] or [""]
    return proc.returncode == 0, tail[0]


async def _preflight(settings: Settings, skip_tests: bool) -> int:
    print("== Preflight für den Produktivbetrieb ==")
    ok = True
    if settings.mode is not Mode.PRODUCTION:
        print("Hinweis: NEWSBOT_MODE ist nicht 'production'; geprüft wird trotzdem die Produktionskonfiguration.")
    prod = settings.model_copy(update={"mode": Mode.PRODUCTION})
    problems = validate_for_mode(prod)
    if problems:
        ok = False
        print("✗ Konfiguration:")
        _print_problems(problems)
    else:
        print("✓ Konfiguration")
    if ok and await _check_telegram(prod, send_test=False) != 0:
        ok = False
    if skip_tests:
        print("! Tests übersprungen (--skip-tests) – Preflight wird NICHT als bestanden gespeichert")
        ok_tests = False
    else:
        ok_tests, summary = _run_tests(settings.base_dir)
        print(("✓" if ok_tests else "✗") + f" Automatisierte Tests: {summary}")
    ok = ok and ok_tests
    db = Database(settings.db_path)
    db.migrate()
    db.kv_set("preflight", {"key": preflight_key(prod), "at": iso(utcnow()), "passed": ok})
    db.close()
    print("== Preflight " + ("BESTANDEN – Produktivbetrieb freigegeben ==" if ok else "NICHT bestanden =="))
    return 0 if ok else 1


async def _run(settings: Settings, once: bool, max_cycles: int | None = None) -> int:
    from newsbot.scheduler import run_forever

    problems = validate_for_mode(settings)
    if problems:
        print("Start abgebrochen – Konfiguration unvollständig:")
        _print_problems(problems)
        return 2
    app = build_app(settings)
    try:
        if settings.mode is Mode.PRODUCTION:
            valid, msg = preflight_valid(app.db, settings)
            if not valid:
                print(f"Produktivbetrieb verweigert: {msg}")
                return 3
            log.info(msg)
        log.info("Starte im Modus '%s' (Sprachmodell: %s)", settings.mode.value, "an" if settings.llm_active() else "aus")
        if once:
            stats = await app.pipeline.run_cycle()
            print(json.dumps({
                "status": stats.status, "quellen": stats.sources_polled, "abruffehler": stats.fetch_failures,
                "gefunden": stats.items_found, "neu": stats.items_new, "geprueft": stats.clusters_evaluated,
                "status_verteilung": stats.by_status, "eingereiht": stats.queued, "veroeffentlicht": stats.published,
                "fehler": stats.errors,
            }, ensure_ascii=False, indent=2))
            return 0 if stats.status != "failed" else 1
        await run_forever(app.pipeline, max_cycles=max_cycles)
        return 0
    finally:
        await app.aclose()


def cmd_status(settings: Settings, args) -> int:
    db = Database(settings.db_path)
    db.migrate()
    print(render_report(collect_metrics(db, settings, utcnow(), args.hours)))
    valid, msg = preflight_valid(db, settings)
    print(f"  Preflight: {msg}")
    db.close()
    return 0


def cmd_health(settings: Settings, _args) -> int:
    db = Database(settings.db_path)
    ok, msg = is_healthy(db, settings, utcnow())
    db.close()
    print(msg)
    return 0 if ok else 1


def cmd_explain(settings: Settings, args) -> int:
    db = Database(settings.db_path)
    c = db.one("SELECT * FROM clusters WHERE id = ?", (args.cluster_id,))
    if not c:
        print("Ereignis nicht gefunden")
        return 1
    print(f"Ereignis {c['id']}: {c['title']}")
    print(f"Status: {c['status']} (Score {c['verification_score']}) | Relevanz: {c['relevance_level']} ({c['relevance_score']})")
    for i in db.query("SELECT source_id, url, published_at FROM items WHERE cluster_id = ?", (c["id"],)):
        print(f"  Meldung: {i['source_id']} {i['published_at']} {i['url']}")
    v = db.one("SELECT * FROM verifications WHERE cluster_id = ? ORDER BY id DESC LIMIT 1", (c["id"],))
    if v:
        print("Begründung: " + "; ".join(json.loads(v["reasons_json"])))
        print("Hinweise: " + ", ".join(json.loads(v["flags_json"])))
        for e in db.query("SELECT * FROM evidence WHERE verification_id = ?", (v["id"],)):
            sign = {1: "+", 0: "·", -1: "−"}[e["supports"]]
            print(f"  {sign} [{e['kind']}] {e['detail']} {e['url'] or ''}")
    db.close()
    return 0


async def _announce(settings: Settings, path: str) -> int:
    """Sendet eine fest hinterlegte Nachricht (z. B. Vorstellung) in die Zielgruppe."""
    if not settings.secrets.telegram_bot_token:
        print("✗ TELEGRAM_BOT_TOKEN fehlt")
        return 1
    chat_id = settings.target_chat_id()
    if chat_id is None:
        print("✗ Keine Zielgruppe konfiguriert")
        return 1
    text = Path(path).read_text(encoding="utf-8").strip()
    client = TelegramClient(settings.secrets.telegram_bot_token, {chat_id})
    try:
        await client.send_message(chat_id, text, topic_id=settings.target_topic_id())
        print(f"✓ Nachricht an {chat_id} gesendet")
        return 0
    except TelegramError as exc:
        print(f"✗ Telegram-Fehler: {exc}")
        return 1
    finally:
        await client.aclose()


TEST_MARKER = "🧪 <b>[TESTMODUS]</b>\n"


async def _strip_test_marker(settings: Settings) -> int:
    """Entfernt den Testmodus-Hinweis aus bereits gesendeten Beiträgen."""
    if not settings.secrets.telegram_bot_token:
        print("✗ TELEGRAM_BOT_TOKEN fehlt")
        return 1
    app = build_app(settings)
    rows = app.db.conn.execute(
        "SELECT id, chat_id, message_id, text FROM publications "
        "WHERE status='sent' AND message_id IS NOT NULL AND text LIKE ?", (f"%{TEST_MARKER.strip()}%",)
    ).fetchall()
    client = TelegramClient(settings.secrets.telegram_bot_token, {r["chat_id"] for r in rows})
    failed = 0
    try:
        for r in rows:
            new_text = r["text"].replace(TEST_MARKER, "", 1)
            try:
                await client.edit_message(r["chat_id"], r["message_id"], new_text)
            except TelegramError as exc:
                if "not modified" not in str(exc):
                    failed += 1
                    print(f"✗ Beitrag {r['id']}: {exc}")
                    continue
            app.db.conn.execute("UPDATE publications SET text=? WHERE id=?", (new_text, r["id"]))
            app.db.conn.commit()
            print(f"✓ Beitrag {r['id']} bereinigt")
    finally:
        await client.aclose()
        await app.aclose()
    print(f"{len(rows) - failed} von {len(rows)} Beiträgen bereinigt")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newsbot", description="Verifizierte Krypto-News für Telegram")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="Datenbank anlegen/migrieren")
    sub.add_parser("check-config", help="Konfiguration prüfen")
    sub.add_parser("check-sources", help="Alle aktivierten Quellen einmal abrufen")
    p_tg = sub.add_parser("check-telegram", help="Token, Gruppen-IDs und Bot-Rechte prüfen")
    p_tg.add_argument("--send-test", action="store_true", help="Testnachricht an Test-/Admin-Chat senden")
    p_pf = sub.add_parser("preflight", help="Vollprüfung vor dem Produktivbetrieb")
    p_pf.add_argument("--skip-tests", action="store_true")
    sub.add_parser("run-once", help="Einen Durchlauf ausführen")
    p_run = sub.add_parser("run", help="Dauerbetrieb starten")
    p_run.add_argument("--max-cycles", type=int, default=None, help="Nach N Durchläufen beenden (z. B. für GitHub Actions)")
    p_an = sub.add_parser("announce", help="Hinterlegte Nachricht in die Zielgruppe senden")
    p_an.add_argument("--file", default="config/announcement.html")
    sub.add_parser("strip-test-marker", help="[TESTMODUS] aus gesendeten Beiträgen entfernen")
    p_st = sub.add_parser("status", help="Monitoring-Bericht anzeigen")
    p_st.add_argument("--hours", type=int, default=24)
    sub.add_parser("health", help="Healthcheck (Exit-Code 0 = gesund)")
    p_ex = sub.add_parser("explain", help="Belege zu einem Ereignis anzeigen")
    p_ex.add_argument("cluster_id", type=int)
    args = parser.parse_args(argv)

    setup_logging(os.environ.get("NEWSBOT_LOG_LEVEL", "INFO"))
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}")
        return 2

    if args.cmd == "init-db":
        db = Database(settings.db_path)
        applied = db.migrate()
        db.sync_sources(settings.config.sources)
        db.close()
        print("Migrationen angewendet: " + (", ".join(applied) or "keine (aktuell)"))
        return 0
    if args.cmd == "check-config":
        return cmd_check_config(settings, args)
    if args.cmd == "check-sources":
        return asyncio.run(_check_sources(settings))
    if args.cmd == "check-telegram":
        return asyncio.run(_check_telegram(settings, args.send_test))
    if args.cmd == "preflight":
        return asyncio.run(_preflight(settings, args.skip_tests))
    if args.cmd == "run-once":
        return asyncio.run(_run(settings, once=True))
    if args.cmd == "run":
        return asyncio.run(_run(settings, once=False, max_cycles=args.max_cycles))
    if args.cmd == "announce":
        return asyncio.run(_announce(settings, args.file))
    if args.cmd == "strip-test-marker":
        return asyncio.run(_strip_test_marker(settings))
    if args.cmd == "status":
        return cmd_status(settings, args)
    if args.cmd == "health":
        return cmd_health(settings, args)
    if args.cmd == "explain":
        return cmd_explain(settings, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
