"""Einfache, deterministische Einschätzung der wahrscheinlichen Marktrichtung einer Meldung.

Dient als Rückfall ohne Sprachmodell: zählt eindeutige positive und negative Signalwörter
in Titel und Zusammenfassung. Bei Gleichstand oder ohne Signal lautet die Einschätzung "neutral".
"""

from __future__ import annotations

import re

POSITIVE = "positiv"
NEGATIVE = "negativ"
NEUTRAL = "neutral"

_POS = [
    r"\binflows?\b", r"zuflüsse", r"\bapprov(e|es|ed|al)\b", r"genehmig", r"zugelassen", r"\bgreen ?light",
    r"\blaunch(es|ed)?\b", r"record high", r"all-time high", r"rekordhoch", r"\bsurg(e|es|ed|ing)\b",
    r"\brall(y|ies|ied)\b", r"\bjump(s|ed)?\b", r"\bsoar(s|ed)?\b", r"\bgains?\b", r"\brises?\b",
    r"\brate cuts?\b", r"zinssenkung", r"\bdovish\b", r"\bdismiss(es|ed)\b", r"\bdrops? (the )?(lawsuit|case|charges)",
    r"\bsettle(s|d|ment)?\b", r"\badopt(s|ed|ion)\b", r"\bpartnership\b", r"\bbuys?\b", r"\bpurchase[sd]?\b",
    r"\bupgrade[sd]?\b", r"\bclarity\b",
]
_NEG = [
    r"\boutflows?\b", r"abflüsse", r"\bbleed(s|ing)?\b", r"\bplung(e|es|ed)\b", r"\bslump(s|ed)?\b",
    r"\btumbl(e|es|ed)\b", r"\bcrash(es|ed)?\b", r"\bfalls?\b", r"\bfell\b", r"\bdeclin(e|es|ed)\b", r"\bsell-?off\b",
    r"\bhack(s|ed|er|ers)?\b", r"\bexploit(s|ed)?\b", r"\bstolen\b", r"\bdrain(s|ed)?\b", r"\blawsuits?\b", r"\bsues?\b",
    r"\bsued\b", r"\bcharged?\b", r"\bfraud\b", r"\bban(s|ned)?\b", r"verbot", r"\breject(s|ed|ion)?\b", r"abgelehnt",
    r"\bdelay(s|ed)?\b", r"verschoben", r"\bliquidat", r"\binvestigat", r"\bprobe\b", r"\bsubpoena", r"\bbankrupt",
    r"insolven", r"\brate hikes?\b", r"zinserhöhung", r"\bhawkish\b", r"\boutage\b", r"\bhalt(s|ed)?\b",
    r"\bdelist", r"\bsanction", r"\bscrutiny\b", r"\bseeks answers\b", r"\bwarn(s|ed|ing)?\b", r"\bturns red\b",
]
_POS_RE = [re.compile(p, re.I) for p in _POS]
_NEG_RE = [re.compile(p, re.I) for p in _NEG]


def detect_outlook(text: str) -> str:
    pos = sum(1 for r in _POS_RE if r.search(text))
    neg = sum(1 for r in _NEG_RE if r.search(text))
    # "drops lawsuit" o. Ä. enthält auch "lawsuit" – das positive Muster soll dann überwiegen
    if re.search(r"\b(dismiss(es|ed)|drops? (the )?(lawsuit|case|charges))", text, re.I):
        neg = max(0, neg - 1)
    if pos > neg:
        return POSITIVE
    if neg > pos:
        return NEGATIVE
    return NEUTRAL
