"""Ausgabeprüfung für Sprachmodell-Texte.

Das Sprachmodell darf nur formulieren. Diese Regeln stellen sicher, dass es keine
Fakten erfindet, keine Kursprognosen abgibt und keine Inhalte aus Webseiten
(Prompt Injection) in die Veröffentlichung schmuggelt.
"""

from __future__ import annotations

import re

from newsbot.textutils import extract_numbers, injection_signals, numbers_close

_FORBIDDEN = [
    r"kursziel", r"price target", r"\bwird (sicher|garantiert|definitiv) (steigen|fallen)",
    r"\b(sicher|garantiert|definitiv) (steigen|fallen|steigt|fällt)", r"\bguaranteed\b", r"\bgarantiert\b",
    r"\bwill (rise|fall|pump|dump|moon)\b", r"\bto the moon\b", r"\bkaufempfehlung\b", r"\bjetzt kaufen\b",
    r"\bjetzt verkaufen\b", r"\bbuy now\b", r"\bsell now\b", r"\d+\s?%\s?(wahrscheinlich|chance|probability)",
    r"wahrscheinlichkeit von \d", r"\bnot financial advice\b.*\bbut\b",
]
_URL_RE = re.compile(r"https?://|www\.|t\.me/|\b[a-z0-9-]+\.(com|io|org|net|xyz|gg|me)\b", re.I)
_MENTION_RE = re.compile(r"(^|\s)[@/][A-Za-z0-9_]{3,}")
_MAX_FIELD = {"headline": 140, "what_happened": 900, "market_impact": 600, "risks": 500, "reaction": 300, "outlook_reason": 300, "watch": 300}


class GuardViolation(Exception):
    pass


def check_llm_output(fields: dict[str, str], source_texts: list[str], allowed_assets: list[str], tolerance: float = 0.01) -> None:
    """Wirft GuardViolation, wenn der Text gegen eine Regel verstößt."""
    source_numbers = [n for t in source_texts for n in extract_numbers(t)]
    for name, text in fields.items():
        if not isinstance(text, str) or not text.strip():
            raise GuardViolation(f"Feld '{name}' ist leer")
        limit = _MAX_FIELD.get("reaction" if name.startswith("reaction_") else name)
        if limit and len(text) > limit:
            raise GuardViolation(f"Feld '{name}' ist zu lang ({len(text)} > {limit})")
        low = text.lower()
        for pat in _FORBIDDEN:
            if re.search(pat, low):
                raise GuardViolation(f"Unzulässige Prognose/Empfehlung in '{name}'")
        if _URL_RE.search(text):
            raise GuardViolation(f"Links/Domains in '{name}' sind nicht erlaubt (Quellen setzt das System)")
        if _MENTION_RE.search(text):
            raise GuardViolation(f"Erwähnungen oder Befehle in '{name}' sind nicht erlaubt")
        if injection_signals(text):
            raise GuardViolation(f"Verdacht auf übernommene Anweisungen in '{name}'")
        for n in extract_numbers(text):
            if n.unit is None and n.value <= 12 and float(n.value).is_integer():
                continue  # kleine Zählwerte ("zwei Quellen", "Q3")
            if n.unit is None and 1990 <= n.value <= 2100:
                if any(numbers_close(n.value, s.value, 0) for s in source_numbers):
                    continue
            if not any(numbers_close(n.value, s.value, tolerance) for s in source_numbers):
                raise GuardViolation(f"Zahl '{n.raw}' in '{name}' ist in keiner Quelle belegt")


def check_assets(assets: list[str], allowed: list[str]) -> list[str]:
    bad = [a for a in assets if a not in allowed]
    if bad:
        raise GuardViolation(f"Nicht belegte Assets: {', '.join(bad)}")
    return assets
