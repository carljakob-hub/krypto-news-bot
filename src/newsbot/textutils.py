"""Textverarbeitung: Bereinigung, Normalisierung, Ähnlichkeit, Zahlen und Signalwörter.

Alle Funktionen behandeln Eingaben als nicht vertrauenswürdige Daten.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_WS_RE = re.compile(r"\s+")
_INVISIBLE = {"​", "‌", "‍", "⁠", "﻿", "‮", "‭", "⁦", "⁧", "⁨", "⁩"}
_TRACKING_PARAMS = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|cmpid|mod)$", re.I)


def clean_text(raw: str | None, limit: int | None = None) -> str:
    """HTML entfernen, unsichtbare Steuerzeichen löschen, Leerraum normalisieren."""
    if not raw:
        return ""
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = "".join(
        ch for ch in text
        if ch not in _INVISIBLE and (unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    )
    text = _WS_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " …"
    return text


def extract_links(raw_html: str | None) -> list[str]:
    if not raw_html:
        return []
    out = []
    for href in _HREF_RE.findall(raw_html):
        href = html.unescape(href).strip()
        if href.startswith(("http://", "https://")) and href not in out:
            out.append(href)
    return out


def host_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def domain_matches(host: str, domains: list[str]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in (x.lower() for x in domains))


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = host_of(url)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Tokenisierung und Ähnlichkeit ----------------------------------------

_STOPWORDS = set(
    """
    a an and are as at be been but by for from has have he her his how in into is it its of on or our
    says said say that the their them they this to was were what when which who will with would after
    over about amid new more than up down out its just also could may might can report reports
    der die das den dem des ein eine einer eines und oder ist sind war wird werden mit von für auf im
    in zu zum zur bei nach über unter aus als auch nicht noch nun wie was wer dass sich laut
    """.split()
)

_SYNONYMS = {
    "bitcoin": "btc", "bitcoins": "btc", "btc": "btc",
    "ether": "eth", "ethereum": "eth", "eth": "eth",
    "solana": "sol", "ripple": "xrp",
    "exchange-traded": "etf", "etfs": "etf", "etf": "etf", "fund": "etf", "funds": "etf",
    "approves": "approv", "approved": "approv", "approval": "approv", "approve": "approv",
    "genehmigt": "approv", "zugelassen": "approv", "zulassung": "approv", "greenlights": "approv",
    "hack": "hack", "hacked": "hack", "hacker": "hack", "hackers": "hack", "exploit": "hack",
    "exploited": "hack", "drained": "hack", "gehackt": "hack", "breach": "hack", "attack": "hack",
    "rate": "rate", "rates": "rate", "zins": "rate", "zinsen": "rate", "leitzins": "rate",
    "cut": "cut", "cuts": "cut", "lowers": "cut", "senkt": "cut", "senkung": "cut",
    "hike": "hike", "hikes": "hike", "raises": "hike", "erhöht": "hike", "anhebung": "hike",
    "inflows": "inflow", "inflow": "inflow", "zuflüsse": "inflow",
    "outflows": "outflow", "outflow": "outflow", "abflüsse": "outflow",
    "sec": "sec", "fed": "fed", "federal": "fed",
}

_WORD_RE = re.compile(r"[a-z0-9äöüß][a-z0-9äöüß\-]*", re.I)


def tokens(text: str) -> set[str]:
    out = set()
    for w in _WORD_RE.findall(text.lower()):
        w = w.strip("-")
        if len(w) < 2 or w in _STOPWORDS:
            continue
        w = _SYNONYMS.get(w, w)
        if w.isdigit():
            continue
        out.add(w[:6])
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap(a: set, b: set) -> float:
    """Anteil der Überschneidung bezogen auf die kleinere Menge."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def shingles(text: str, k: int = 4) -> set[str]:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def text_similarity(a: str, b: str) -> float:
    """Wörtliche Ähnlichkeit (für Kopien/Syndizierung)."""
    return jaccard(shingles(a), shingles(b))


def content_hash(title: str, summary: str) -> str:
    return sha(" ".join(sorted(tokens(title + " " + summary[:400]))))


# --- Zahlen ---------------------------------------------------------------

@dataclass(frozen=True)
class Number:
    value: float
    unit: str | None   # usd, eur, pct, bps, btc, eth oder None
    raw: str


_MULT = {
    "trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9, "mrd": 1e9, "milliarden": 1e9,
    "milliarde": 1e9, "million": 1e6, "millionen": 1e6, "mn": 1e6, "m": 1e6, "mio": 1e6,
    "thousand": 1e3, "k": 1e3, "tsd": 1e3, "tausend": 1e3,
}
_UNIT = {
    "%": "pct", "percent": "pct", "prozent": "pct", "pct": "pct",
    "basis points": "bps", "bps": "bps", "basispunkte": "bps", "basispunkt": "bps",
    "btc": "btc", "bitcoin": "btc", "eth": "eth", "ether": "eth",
    "usd": "usd", "dollar": "usd", "dollars": "usd", "us-dollar": "usd",
    "eur": "eur", "euro": "eur",
}
_NUM_RE = re.compile(
    r"(?P<cur>[$€])?\s?"
    r"(?P<num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?:\s?(?P<mult>trillion|billion|milliarden|milliarde|millionen|million|thousand|tausend|mrd|mio|tsd|bn|tn|mn|[bmk])\b\.?)?"
    r"(?:\s?(?P<unit>%|(?:percent|prozent|pct|basis points|basispunkte|basispunkt|bps|btc|bitcoin|eth|ether|us-dollar|usd|dollars|dollar|eur|euro)(?![a-zäöü])))?",
    re.I,
)


def _to_float(s: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        return float(re.sub(r"[.,]", "", s))
    m = re.fullmatch(r"(\d{1,3}(?:([.,])\d{3})+)([.,])(\d+)", s)
    if m and m.group(2) != m.group(3):
        return float(re.sub(r"[.,]", "", m.group(1)) + "." + m.group(4))
    return float(s.replace(",", "."))


def extract_numbers(text: str) -> list[Number]:
    out = []
    for m in _NUM_RE.finditer(text):
        try:
            value = _to_float(m.group("num"))
        except ValueError:
            continue
        mult = (m.group("mult") or "").lower().rstrip(".")
        unit_raw = (m.group("unit") or "").lower()
        cur = m.group("cur")
        if mult:
            value *= _MULT.get(mult, 1)
        unit = _UNIT.get(unit_raw) if unit_raw else None
        if cur == "$":
            unit = "usd"
        elif cur == "€":
            unit = "eur"
        out.append(Number(value=value, unit=unit, raw=m.group(0).strip()))
    return out


def numbers_close(a: float, b: float, tolerance: float) -> bool:
    if a == b:
        return True
    return abs(a - b) <= tolerance * max(abs(a), abs(b))


# --- Signalwörter -----------------------------------------------------------

_RUMOR_PATTERNS = [
    r"\brumou?r(s|ed)?\b", r"\breportedly\b", r"\ballegedly\b", r"\bunconfirmed\b", r"\bsources? (say|said|claim)",
    r"\bpeople familiar with\b", r"\baccording to (a )?(leak|insider)", r"\bleak(ed)?\b", r"\bscreenshot\b",
    r"\bspeculat", r"\bcould be\b", r"\bmay soon\b", r"\bgerücht", r"\bunbestätigt", r"\bangeblich",
    r"\bsoll(en)? (angeblich|laut)", r"\binsider\b", r"\bkreisen zufolge\b", r"\bnicht bestätigt\b",
]
_DEBUNK_PATTERNS = [
    r"\bfake\b", r"\bhoax\b", r"\bdebunk", r"\bfalse (report|claim|news|rumou?r)", r"\bnot true\b",
    r"\bdenie[sd] (the )?(report|rumou?r|claim)", r"\bdismiss(es|ed)? (the )?(report|rumou?r)",
    r"\bfabricated\b", r"\bdoctored\b", r"\bmanipulated (image|screenshot)", r"\bcompromised (x |twitter )?account",
    r"\bhacked (x |twitter )?account", r"\bfalschmeldung", r"\bdementi", r"\bgefälscht", r"\bfrei erfunden",
]
_CORRECTION_PATTERNS = [
    r"^\s*(correction|clarification|update|korrektur|richtigstellung|klarstellung)\b", r"\bretract(s|ed|ion)\b",
    r"\bzurückgezogen\b", r"\bwithdrawn\b", r"\berratum\b",
]
_INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules|prompts?)", r"disregard (the )?(system|previous)",
    r"\bsystem prompt\b", r"\byou are now\b", r"\bact as\b", r"\bnew instructions?\b", r"\bdeveloper mode\b",
    r"\bjailbreak\b", r"</?(system|assistant|user|instructions?)>", r"\bignoriere (alle )?(vorherigen|bisherigen)",
    r"\bsende (diese|folgende) nachricht\b", r"\bpost (this|the following) to\b", r"\bexecute\b.*\bcommand\b",
    r"\bmark (this|it) as verified\b", r"\bals verifiziert\b",
]


def _any(patterns: list[str], text: str) -> list[str]:
    low = text.lower()
    return [p for p in patterns if re.search(p, low)]


def rumor_signals(text: str) -> list[str]:
    return _any(_RUMOR_PATTERNS, text)


def debunk_signals(text: str) -> list[str]:
    return _any(_DEBUNK_PATTERNS, text)


def correction_signals(title: str) -> list[str]:
    return _any(_CORRECTION_PATTERNS, title)


def injection_signals(text: str) -> list[str]:
    return _any(_INJECTION_PATTERNS, text)


def is_question_headline(title: str) -> bool:
    return title.strip().endswith("?")
