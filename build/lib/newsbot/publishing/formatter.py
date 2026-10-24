"""Baut den fertigen Telegram-Beitrag (HTML-Modus). Alle externen Texte werden maskiert."""

from __future__ import annotations

import html
from datetime import datetime

from newsbot.config import AppConfig
from newsbot.models import PostContent, RelevanceLevel, RelevanceResult, VerificationResult, VerificationStatus
from newsbot.textutils import host_of

TELEGRAM_LIMIT = 4096

_HEADER = {
    RelevanceLevel.HIGH: "🚨🚨 <b>SEHR WICHTIG</b>",
    RelevanceLevel.MEDIUM: "📰 <b>NEWS</b>",
    RelevanceLevel.LOW: "ℹ️ <b>INFO</b>",
}
_OUTLOOK = {
    "positiv": "📈 <b>Einschätzung: eher positiv</b>",
    "negativ": "📉 <b>Einschätzung: eher negativ</b>",
    "neutral": "↔️ <b>Einschätzung: Richtung offen</b>",
}


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _link(url: str, label: str) -> str:
    if not url.startswith(("https://", "http://")):
        return esc(label)
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def _verification_line(v: VerificationResult) -> str:
    if v.status is VerificationStatus.VERIFIED:
        return "✅ Verifiziert"
    if v.status is VerificationStatus.PARTIAL:
        return "⚠️ Teilweise bestätigt"
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
    links = []
    for url in [*verification.primary_urls[:2], *verification.confirmation_urls[:3]]:
        links.append(_link(url, source_names.get(url) or host_of(url)))
    if not links:
        raise ValueError("Beitrag ohne Quellen wird nicht veröffentlicht")
    assets = ", ".join(content.assets) or "den Gesamtmarkt"
    horizon = (content.time_horizon or "").strip()

    def build(what: str, reason: str, watch: str, risks: str) -> str:
        parts = []
        if test_mode:
            parts.append("🧪 <b>[TESTMODUS]</b>")
        parts += [
            f"{_HEADER[relevance.level]} | <b>{esc(content.headline)}</b>",
            "",
            esc(what),
            "",
            f"{_OUTLOOK.get(content.outlook, _OUTLOOK['neutral'])} für {esc(assets)}"
            + (f" ({esc(horizon.lower())})" if horizon else ""),
        ]
        if reason:
            parts.append(esc(reason[:1].upper() + reason[1:]))
        if watch:
            parts.append(f"👀 <b>Worauf achten:</b> {esc(watch)}")
        if verification.status is VerificationStatus.PARTIAL and risks:
            parts += ["", f"⚠️ <b>Noch unklar:</b> {esc(risks)}"]
        parts += ["", f"{_verification_line(verification)} · " + " · ".join(links)]
        if cfg.publishing.disclaimer:
            parts.append(f"<i>{esc(cfg.publishing.disclaimer)}</i>")
        return "\n".join(parts)

    reason = content.outlook_reason or content.market_impact
    text = build(content.what_happened, reason, content.watch, content.risks)
    if len(text) > TELEGRAM_LIMIT:
        overflow = len(text) - TELEGRAM_LIMIT + 20
        what = content.what_happened[: max(100, len(content.what_happened) - overflow)].rsplit(" ", 1)[0] + " …"
        text = build(what, reason[:300], content.watch[:300], content.risks[:300])
    return text[:TELEGRAM_LIMIT]
