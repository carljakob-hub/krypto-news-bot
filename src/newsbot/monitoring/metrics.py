"""Kennzahlen aus der Datenbank. Fehlende Werte werden als 'nicht verfügbar' ausgewiesen, nie geschätzt."""

from __future__ import annotations

from datetime import datetime, timedelta

from newsbot.config import Settings
from newsbot.db.database import Database, iso, parse_iso

NA = "nicht verfügbar"


def collect_metrics(db: Database, settings: Settings, now: datetime, hours: int = 24) -> dict:
    since = iso(now - timedelta(hours=hours))
    runs = db.one(
        "SELECT COUNT(*) AS runs, SUM(items_found) AS found, SUM(items_new) AS new, SUM(fetch_failures) AS ff, "
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_runs FROM runs WHERE started_at >= ?",
        (since,),
    )
    status_rows = db.query(
        "SELECT status, COUNT(*) AS n FROM clusters WHERE updated_at >= ? GROUP BY status", (since,)
    )
    by_status = {r["status"]: r["n"] for r in status_rows}
    pubs = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM publications WHERE created_at >= ? GROUP BY status", (since,)
    )}
    top_sources = [
        (r["source_id"], r["n"]) for r in db.query(
            "SELECT source_id, COUNT(*) AS n FROM items WHERE fetched_at >= ? GROUP BY source_id ORDER BY n DESC LIMIT 5",
            (since,),
        )
    ]
    verified_sources = [
        (r["source_id"], r["n"]) for r in db.query(
            "SELECT e.source_id, COUNT(*) AS n FROM evidence e JOIN verifications v ON v.id = e.verification_id "
            "WHERE v.created_at >= ? AND e.supports = 1 AND e.source_id IS NOT NULL GROUP BY e.source_id ORDER BY n DESC LIMIT 5",
            (since,),
        )
    ]
    fetch_fail = [
        (r["source_id"], r["n"], r["last_error"]) for r in db.query(
            "SELECT source_id, COUNT(*) AS n, MAX(error) AS last_error FROM fetch_log WHERE at >= ? AND ok = 0 "
            "GROUP BY source_id ORDER BY n DESC",
            (since,),
        )
    ]
    llm = db.one(
        "SELECT COUNT(*) AS calls, SUM(input_tokens) AS inp, SUM(output_tokens) AS outp, SUM(cost_usd) AS cost "
        "FROM llm_usage WHERE at >= ?",
        (since,),
    )
    hosting = settings.config.monitoring.hosting_cost_usd_per_month
    return {
        "window_hours": hours,
        "runs": runs["runs"] or 0,
        "failed_runs": runs["failed_runs"] or 0,
        "items_found": runs["found"] or 0,
        "items_new": runs["new"] or 0,
        "fetch_failures": runs["ff"] or 0,
        "clusters": {
            "VERIFIZIERT": by_status.get("VERIFIZIERT", 0),
            "TEILWEISE BESTÄTIGT": by_status.get("TEILWEISE BESTÄTIGT", 0),
            "UNBESTÄTIGT": by_status.get("UNBESTÄTIGT", 0),
            "WIDERLEGT": by_status.get("WIDERLEGT", 0),
            "ausstehend": by_status.get("PENDING", 0),
        },
        "publications": {
            "gesendet": pubs.get("sent", 0),
            "wartend": pubs.get("queued", 0),
            "fehlgeschlagen": pubs.get("failed", 0),
            "duplikat_verworfen": pubs.get("skipped_duplicate", 0),
            "verfallen": pubs.get("expired", 0),
        },
        "top_sources": top_sources,
        "top_confirming_sources": verified_sources,
        "fetch_failures_by_source": fetch_fail,
        "last_successful_research": db.kv_get("last_successful_research") or NA,
        "last_successful_publish": db.kv_get("last_successful_publish") or NA,
        "llm": (
            {"calls": llm["calls"], "input_tokens": llm["inp"] or 0, "output_tokens": llm["outp"] or 0,
             "cost_usd": round(llm["cost"] or 0.0, 4), "note": "berechnet aus gemeldeten Tokens × konfigurierten Preisen"}
            if llm["calls"] else NA + " (kein Sprachmodell-Aufruf im Zeitraum)"
        ),
        "hosting_cost_usd_month": hosting if hosting is not None else "nicht konfiguriert",
        "telegram_cost": "0 (Telegram Bot API ist kostenlos)",
        "data_source_cost": "0 (nur frei zugängliche RSS-Feeds konfiguriert)",
        "mode": settings.mode.value,
        "llm_active": settings.llm_active(),
    }


def render_report(m: dict) -> str:
    lines = [
        f"Statusbericht (letzte {m['window_hours']} h) – Modus: {m['mode']}",
        f"  Durchläufe: {m['runs']} (fehlgeschlagen: {m['failed_runs']})",
        f"  Gefundene Meldungen: {m['items_found']} (neu: {m['items_new']})",
        "  Ereignisse nach Prüfstatus: " + ", ".join(f"{k}: {v}" for k, v in m["clusters"].items()),
        "  Veröffentlichungen: " + ", ".join(f"{k}: {v}" for k, v in m["publications"].items()),
        "  Häufigste Quellen: " + (", ".join(f"{s} ({n})" for s, n in m["top_sources"]) or NA),
        "  Häufigste Bestätiger: " + (", ".join(f"{s} ({n})" for s, n in m["top_confirming_sources"]) or NA),
        f"  Fehlgeschlagene Abrufe: {m['fetch_failures']}",
    ]
    for s, n, err in m["fetch_failures_by_source"]:
        lines.append(f"    - {s}: {n}× (zuletzt: {err})")
    lines += [
        f"  Letzte erfolgreiche Recherche: {m['last_successful_research']}",
        f"  Letzte erfolgreiche Veröffentlichung: {m['last_successful_publish']}",
        f"  Sprachmodell aktiv: {'ja' if m['llm_active'] else 'nein (Textvorlagen)'}",
        f"  Sprachmodell-Kosten: {m['llm'] if isinstance(m['llm'], str) else str(m['llm']['cost_usd']) + ' USD, ' + str(m['llm']['calls']) + ' Aufrufe (' + m['llm']['note'] + ')'}",
        f"  Hosting (USD/Monat): {m['hosting_cost_usd_month']}",
        f"  Telegram: {m['telegram_cost']}",
        f"  Datenquellen: {m['data_source_cost']}",
    ]
    return "\n".join(lines)


def is_healthy(db: Database, settings: Settings, now: datetime) -> tuple[bool, str]:
    last = db.kv_get("last_successful_research")
    if not last:
        return False, "Noch kein erfolgreicher Durchlauf"
    age = now - parse_iso(last)
    limit = timedelta(minutes=settings.config.monitoring.health_max_age_minutes)
    if age > limit:
        return False, f"Letzter erfolgreicher Durchlauf vor {int(age.total_seconds() // 60)} min"
    return True, f"OK – letzter erfolgreicher Durchlauf vor {int(age.total_seconds() // 60)} min"
