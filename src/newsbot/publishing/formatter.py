"""Baut den fertigen Telegram-Beitrag (HTML-Modus). Alle externen Texte werden maskiert."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from newsbot.config import AppConfig
from newsbot.models import PostContent, RelevanceLevel, RelevanceResult, VerificationResult, VerificationStatus
from newsbot.textutils import host_of

TELEGRAM_LIMIT = 4096

_HEADER = {
    RelevanceLevel.HIGH: "🚨 KRYPTONEWS | HOHE RELEVANZ",
    RelevanceLevel.MEDIUM: "📰 KRYPTONEWS | MITTLERE RELEVANZ",
    RelevanceLevel.LOW: "ℹ️ KRYPTONEWS | NIEDRIGE RELEVANZ",
}
_LEVEL_WORD = {RelevanceLevel.HIGH: "Hoch", RelevanceLevel.MEDIUM: "Mittel", RelevanceLevel.LOW: "Niedrig"}


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _link(url: str, label: str) -> str:
    if not url.startswith(("https://", "http://")):
        return esc(label)
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def _verification_line(v: VerificationResult, cfg: AppConfig) -> str:
    if v.status is VerificationStatus.VERIFIED:
        if v.primary_urls:
            hosts = ", ".join(dict.fromkeys(host_of(u) for u in v.primary_urls))
            text = f"✅ VERIFIZIERT – bestätigt durch Primärquelle ({hosts})"
            if v.confirmation_urls:
                text += f" und {len(v.confirmation_urls)} unabhängige Bestätigung(en)"
            return text
        return f"✅ VERIFIZIERT – bestätigt durch {v.independent_groups} voneinander unabhängige Quellen"
    if v.status is VerificationStatus.PARTIAL:
        return "⚠️ TEILWEISE BESTÄTIGT – Kernaussage belegt, einzelne Angaben noch unklar (siehe Risiken)"
    raise ValueError(f"Status {v.status.value} darf nicht veröffentlicht werden")


def format_post(
    content: PostContent,
    verification: VerificationResult,
    relevance: RelevanceResult,
    source_names: dict[str, str],
    cfg: AppConfig,
    now: datetime,
    test_mode: bool,
) -> str:
    tz = ZoneInfo(cfg.timezone)
    local = now.astimezone(tz)
    sources = []
    for url in verification.primary_urls[:2]:
        sources.append("• Primärquelle: " + _link(url, source_names.get(url) or host_of(url)))
    for url in verification.confirmation_urls[:3]:
        sources.append("• Unabhängige Bestätigung: " + _link(url, source_names.get(url) or host_of(url)))
    if not sources:
        raise ValueError("Beitrag ohne Quellen wird nicht veröffentlicht")

    reactions = [f"• {esc(r)}" for r in content.reactions[:3]]

    def build(what: str, impact: str, risks: str) -> str:
        parts = []
        if test_mode:
            parts.append("🧪 <b>[TESTMODUS]</b>")
        parts += [
            f"<b>{_HEADER[relevance.level]}</b>",
            "",
            f"<b>Thema:</b> {esc(content.headline)}",
            "",
            "<b>Was ist passiert?</b>",
            esc(what),
            "",
            f"<b>Verifizierung:</b> {esc(_verification_line(verification, cfg))}",
            f"<b>Betroffene Assets:</b> {esc(', '.join(content.assets) or 'Gesamtmarkt / nicht eindeutig')}",
            f"<b>Trading-Relevanz:</b> {_LEVEL_WORD[relevance.level]}",
            f"<b>Mögliche Marktauswirkung:</b> {esc(impact)}",
            *(["<b>Mögliche Reaktionen (Szenarien, keine Prognose):</b>", *reactions] if reactions else []),
            f"<b>Zeithorizont:</b> {esc(content.time_horizon)}",
            f"<b>Risiken und Unsicherheiten:</b> {esc(risks)}",
            "",
            "<b>Quellen:</b>",
            *sources,
            "",
            f"<b>Zeitpunkt:</b> {local:%d.%m.%Y, %H:%M} {local.tzname()} ({cfg.timezone})",
        ]
        if cfg.publishing.disclaimer:
            parts += ["", f"<i>{esc(cfg.publishing.disclaimer)}</i>"]
        return "\n".join(parts)

    text = build(content.what_happened, content.market_impact, content.risks)
    if len(text) > TELEGRAM_LIMIT:
        overflow = len(text) - TELEGRAM_LIMIT + 20
        what = content.what_happened[: max(100, len(content.what_happened) - overflow)].rsplit(" ", 1)[0] + " …"
        text = build(what, content.market_impact[:400], content.risks[:300])
    return text[:TELEGRAM_LIMIT]
