"""Mehrstufige, deterministische Echtheitsprüfung eines Ereignisses.

Das Sprachmodell ist hier bewusst NICHT beteiligt: Der Status ergibt sich ausschließlich
aus abrufbaren Quellen und nachvollziehbaren Regeln. Jede Entscheidung wird als Liste
von Belegen (Evidence) gespeichert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from newsbot.config import AppConfig, SourceConfig, Tier
from newsbot.models import Evidence, LinkCheck, RawItem, VerificationResult, VerificationStatus
from newsbot.textutils import (
    correction_signals,
    debunk_signals,
    domain_matches,
    extract_numbers,
    host_of,
    is_question_headline,
    numbers_close,
    overlap,
    rumor_signals,
    text_similarity,
    tokens,
)

LinkCheckFn = Callable[[str, datetime], Awaitable[LinkCheck]]

_ATTRIBUTION_RE = re.compile(
    r"\b(?i:according to|reported by|first reported by|citing|as reported by|laut|berichtet von)\s+"
    r"(?i:a |an |the |der |die |dem |einem )?(?i:report (?:by|from) )?([A-Z][A-Za-z0-9&\.\-]+(?: [A-Z][A-Za-z0-9&\.\-]+)?)"
)
_GENERIC_LABELS = {"blog", "www", "news", "api", "europa", "gov", "org", "com", "net", "co", "uk", "eu", "de", "jp", "or", "io"}
_UNITS_COMPARED = {"usd", "eur", "pct", "bps", "btc", "eth"}
_MAX_LINKS_PER_CLUSTER = 3


@dataclass
class _ItemView:
    item: RawItem
    src: SourceConfig
    text: str
    fresh: bool
    rumor: list[str]
    debunk: list[str]
    correction: list[str]
    suspicious: bool


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


class Verifier:
    def __init__(self, config: AppConfig, link_check: LinkCheckFn | None = None):
        self.cfg = config
        self.vcfg = config.verification
        self.link_check = link_check

    # ------------------------------------------------------------------
    async def verify(self, items: list[RawItem], now: datetime) -> VerificationResult:
        evidence: list[Evidence] = []
        flags: set[str] = set()
        reasons: list[str] = []
        max_age = timedelta(hours=self.vcfg.max_item_age_hours)

        views = []
        for it in items:
            src = self.cfg.source(it.source_id)
            if src is None:
                evidence.append(Evidence("unknown_source", "Quelle nicht konfiguriert – ignoriert", 0, it.source_id, it.url))
                continue
            text = f"{it.title}. {it.summary}"
            fresh = it.published_at is not None and timedelta(0) <= now - it.published_at <= max_age
            suspicious = "prompt_injection_suspected" in it.flags
            if suspicious:
                flags.add("prompt_injection_suspected")
                evidence.append(Evidence("suspicious_content", "Text enthält Anweisungen an KI-Systeme – nicht als Beleg verwendet", 0, src.id, it.url))
            views.append(_ItemView(
                it, src, text, fresh,
                rumor_signals(text) + (["question_headline"] if is_question_headline(it.title) else []),
                debunk_signals(text), correction_signals(it.title), suspicious,
            ))

        if not views:
            return VerificationResult(VerificationStatus.UNCONFIRMED, 0.0, evidence, sorted(flags), ["Keine bewertbaren Quellen"])

        # --- Schritt 5: Aktualität -------------------------------------
        dated = [v for v in views if v.item.published_at]
        fresh_views = [v for v in views if v.fresh]
        if not dated:
            flags.add("missing_dates")
            reasons.append("Keine Quelle nennt ein Veröffentlichungsdatum")
        stale_views = [v for v in dated if not v.fresh]
        for v in stale_views:
            evidence.append(Evidence("stale", f"Veröffentlicht {v.item.published_at:%Y-%m-%d %H:%M} UTC – älter als {self.vcfg.max_item_age_hours:g} h", 0, v.src.id, v.item.url))
        trusted_stale = [v for v in stale_views if v.src.trust >= self.vcfg.min_trust_score and v.src.tier is not Tier.SOCIAL]
        if trusted_stale and fresh_views:
            flags.add("old_story_recirculated")
            reasons.append("Dieselbe Meldung wurde bereits früher veröffentlicht (wieder verbreitet)")
        if self._mentions_old_year(views, now):
            flags.add("old_year_reference")
            evidence.append(Evidence("old_year_reference", "Überschrift nennt ein zurückliegendes Jahr", 0))

        # --- Schritt 6: Gerüchte, Widerlegung, Korrekturen ------------------
        for v in views:
            if v.rumor:
                evidence.append(Evidence("rumor_language", "Spekulative Formulierung erkannt", 0, v.src.id, v.item.url))
            if v.correction:
                flags.add("correction_or_update")
                evidence.append(Evidence("correction", "Korrektur/Aktualisierung/Rückzug gemeldet", 0, v.src.id, v.item.url))
            if v.src.tier is Tier.SOCIAL:
                evidence.append(Evidence("social_media", "Soziale Medien zählen nicht als Bestätigung", 0, v.src.id, v.item.url))
        refuting = [
            v for v in views
            if v.debunk and not v.suspicious and v.src.tier is not Tier.SOCIAL and v.src.trust >= self.vcfg.min_trust_score
        ]
        for v in refuting:
            evidence.append(Evidence("refutation", "Vertrauenswürdige Quelle widerspricht der Behauptung", -1, v.src.id, v.item.url, v.src.trust))
        retracted = any(re.search(r"retract|zurückgezogen|withdrawn", " ".join(v.correction)) for v in views)

        # --- Schritt 1: Originalquelle ------------------------------------
        primary_items = []
        for v in fresh_views:
            if v.src.tier is Tier.PRIMARY and not v.suspicious:
                official = v.src.official_domains or self.cfg.primary_domains
                if domain_matches(host_of(v.item.url), official):
                    primary_items.append(v)
                    detail = f"Originalmitteilung direkt von {v.src.name}"
                    if v.src.conflict_of_interest:
                        detail += " (Selbstauskunft – mögliches Eigeninteresse)"
                        flags.add("self_reported")
                    evidence.append(Evidence("primary_source", detail, 1, v.src.id, v.item.url, 1.0))
                else:
                    flags.add("primary_url_mismatch")
                    evidence.append(Evidence("primary_url_mismatch", "Link der Primärquelle zeigt nicht auf deren offizielle Domain", -1, v.src.id, v.item.url))

        lookalikes = self._lookalike_links(views)
        for url in lookalikes:
            flags.add("lookalike_domain")
            evidence.append(Evidence("lookalike_domain", f"Verdächtige Domain imitiert offizielle Quelle: {host_of(url)}", -1, url=url))

        title_refs = [v.item.title for v in fresh_views if not v.rumor]
        primary_link_ok, primary_link_texts, primary_urls = await self._check_primary_links(views, title_refs, now, evidence, flags)
        primary_urls = [v.item.url for v in primary_items] + primary_urls
        primary_ok = bool(primary_items) or primary_link_ok

        # --- Schritt 2+3: Unabhängige Bestätigungen und Quellenbewertung -----
        candidates = [
            v for v in fresh_views
            if v.src.tier is not Tier.SOCIAL and v.src.trust >= self.vcfg.min_trust_score
            and not v.rumor and not v.suspicious and not v.debunk
        ]
        weak_support = [
            v for v in fresh_views
            if v not in candidates and v.src.tier is not Tier.SOCIAL and not v.rumor and not v.suspicious and not v.debunk
        ]
        groups = self._independence_groups(candidates, evidence, flags)
        n_groups = len(set(groups.values()))
        seen_groups: set[str] = set()
        confirmation_urls = []
        for v in candidates:
            g = groups[self._key(v)]
            if v in primary_items:
                seen_groups.add(g)
                continue
            if g in seen_groups:
                continue
            seen_groups.add(g)
            confirmation_urls.append(v.item.url)
            evidence.append(Evidence("independent_confirmation", f"Unabhängige Meldung von {v.src.name} (Vertrauen {v.src.trust:.2f})", 1, v.src.id, v.item.url, v.src.trust))
        if primary_link_ok and not primary_items:
            n_groups += 1  # das abgerufene Primärdokument ist eine eigene, unabhängige Quelle

        # --- Schritt 4: Inhalt (Zahlen) prüfen -----------------------------
        contradictions = self._number_conflicts(candidates, groups, evidence)
        if primary_link_texts:
            contradictions += self._conflicts_with_primary(candidates, primary_link_texts, evidence)
        if contradictions:
            flags.add("conflicting_numbers")

        # --- Entscheidung --------------------------------------------------
        score = self._score(primary_ok, n_groups, contradictions, refuting, flags)
        if refuting:
            status = VerificationStatus.REFUTED
            reasons.append("Vertrauenswürdige Quellen widersprechen der Behauptung")
        elif not fresh_views:
            status = VerificationStatus.UNCONFIRMED
            reasons.append("Keine aktuelle Quelle (veraltet oder ohne Datum)")
        elif retracted:
            status = VerificationStatus.UNCONFIRMED
            reasons.append("Ursprungsmeldung wurde zurückgezogen")
        elif "old_story_recirculated" in flags and not primary_items:
            status = VerificationStatus.UNCONFIRMED
        elif primary_ok and not contradictions:
            status = VerificationStatus.VERIFIED
            reasons.append("Durch authentische Primärquelle bestätigt")
        elif n_groups >= self.vcfg.min_independent_sources and not contradictions:
            status = VerificationStatus.VERIFIED
            reasons.append(f"{n_groups} voneinander unabhängige vertrauenswürdige Quellen")
        elif primary_ok or n_groups >= self.vcfg.min_independent_sources:
            status = VerificationStatus.PARTIAL
            reasons.append("Kernaussage bestätigt, aber Angaben (z. B. Zahlen) widersprechen sich")
        elif n_groups == 1 and weak_support and not lookalikes:
            status = VerificationStatus.PARTIAL
            reasons.append("Nur eine vertrauenswürdige Quelle, ergänzt durch schwächere Quellen")
        else:
            status = VerificationStatus.UNCONFIRMED
            if all(v.rumor for v in views):
                reasons.append("Nur spekulative Meldungen / Gerüchte")
            else:
                reasons.append("Nicht ausreichend unabhängig bestätigt")
        if lookalikes and status is VerificationStatus.VERIFIED and not primary_items:
            status = VerificationStatus.PARTIAL
            reasons.append("Verdächtige Links in den Quellen")

        return VerificationResult(
            status=status,
            score=score,
            evidence=evidence,
            flags=sorted(flags),
            reasons=reasons,
            primary_urls=list(dict.fromkeys(primary_urls)),
            confirmation_urls=confirmation_urls,
            independent_groups=n_groups,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _key(v: _ItemView) -> str:
        return f"{v.src.id}|{v.item.url}"

    def _independence_groups(self, cands: list[_ItemView], evidence: list[Evidence], flags: set[str]) -> dict[str, str]:
        uf = _UnionFind()
        names = {s.name.lower(): s for s in self.cfg.sources}
        for v in cands:
            uf.find(self._key(v))
            uf.union(self._key(v), "owner:" + v.src.owner_group)
            for cited in _ATTRIBUTION_RE.findall(v.item.title + ". " + v.item.summary):
                cited_l = cited.lower()
                match = next((s for n, s in names.items() if cited_l in n or n.split(" ")[0] == cited_l.split(" ")[0]), None)
                target = "owner:" + match.owner_group if match else "cited:" + cited_l.split(" ")[0]
                if target != "owner:" + v.src.owner_group:
                    uf.union(self._key(v), target)
                    flags.add("derived_reporting")
                    evidence.append(Evidence("derived_reporting", f"{v.src.name} beruft sich auf {cited} – keine eigenständige Bestätigung", 0, v.src.id, v.item.url))
        for i, a in enumerate(cands):
            for b in cands[i + 1 :]:
                if a.src.owner_group == b.src.owner_group:
                    continue
                if text_similarity(a.text, b.text) >= self.vcfg.syndication_similarity:
                    uf.union(self._key(a), self._key(b))
                    flags.add("syndicated")
                    evidence.append(Evidence("syndicated", f"{b.src.name} übernimmt den Text von {a.src.name} nahezu wörtlich", 0, b.src.id, b.item.url))
        return {self._key(v): uf.find(self._key(v)) for v in cands}

    @staticmethod
    def _title_numbers(text: str) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for n in extract_numbers(text):
            if n.unit in _UNITS_COMPARED:
                out.setdefault(n.unit, []).append(n.value)
        return out

    def _number_conflicts(self, cands, groups, evidence) -> int:
        tol = self.vcfg.number_tolerance
        conflicts = 0
        parsed = [(v, self._title_numbers(v.item.title)) for v in cands]
        for i, (a, na) in enumerate(parsed):
            for b, nb in parsed[i + 1 :]:
                if groups[self._key(a)] == groups[self._key(b)]:
                    continue
                for unit in set(na) & set(nb):
                    if not any(numbers_close(x, y, tol) for x in na[unit] for y in nb[unit]):
                        conflicts += 1
                        evidence.append(Evidence(
                            "contradiction",
                            f"Zahlen widersprechen sich: {a.src.name} vs. {b.src.name} ({unit})",
                            -1, b.src.id, b.item.url,
                        ))
        return conflicts

    def _conflicts_with_primary(self, cands, primary_texts: list[str], evidence) -> int:
        tol = self.vcfg.number_tolerance
        primary_nums: dict[str, list[float]] = {}
        for t in primary_texts:
            for unit, vals in self._title_numbers(t).items():
                primary_nums.setdefault(unit, []).extend(vals)
        conflicts = 0
        for v in cands:
            for unit, vals in self._title_numbers(v.item.title).items():
                if unit in primary_nums and not any(numbers_close(x, y, tol) for x in vals for y in primary_nums[unit]):
                    conflicts += 1
                    evidence.append(Evidence("contradiction", f"Zahl in {v.src.name} findet sich nicht im Primärdokument", -1, v.src.id, v.item.url))
        return conflicts

    def _lookalike_links(self, views: list[_ItemView]) -> list[str]:
        brands = {
            label for d in self.cfg.primary_domains for label in d.split(".")
            if len(label) >= 3 and label not in _GENERIC_LABELS
        }
        out = []
        for v in views:
            for url in [v.item.url, *v.item.links]:
                host = host_of(url)
                if not host or domain_matches(host, self.cfg.primary_domains):
                    continue
                labels = re.split(r"[.\-]", host)
                if any(b in labels for b in brands) and not domain_matches(host, [s for src in self.cfg.sources for s in [host_of(src.url)]]):
                    out.append(url)
        return list(dict.fromkeys(out))

    async def _check_primary_links(self, views, title_refs, now, evidence, flags):
        """Verlinkte Dokumente auf offiziellen Domains abrufen und mit der Meldung abgleichen."""
        if not self.vcfg.verify_primary_links or self.link_check is None:
            return False, [], []
        urls = []
        for v in views:
            if v.src.tier is Tier.PRIMARY or v.suspicious:
                continue
            for url in v.item.links:
                if url.startswith("https://") and domain_matches(host_of(url), self.cfg.primary_domains) and url not in urls:
                    urls.append(url)
        ref_tokens = set().union(*(tokens(t) for t in title_refs)) if title_refs else set()
        ok_any, texts, ok_urls = False, [], []
        for url in urls[:_MAX_LINKS_PER_CLUSTER]:
            res = await self.link_check(url, now)
            if not res.ok:
                flags.add("primary_link_unreachable")
                evidence.append(Evidence("primary_link_unreachable", f"Primärdokument nicht abrufbar ({res.error or res.status_code})", 0, url=url))
                continue
            match = overlap(ref_tokens, tokens(res.text)) if ref_tokens else 0.0
            if match >= 0.5:
                ok_any = True
                texts.append(res.text)
                ok_urls.append(url)
                evidence.append(Evidence("primary_link", f"Primärdokument abgerufen, Inhalt stimmt überein ({match:.0%} Schlüsselbegriffe)", 1, url=url, weight=1.0))
            else:
                flags.add("primary_link_mismatch")
                evidence.append(Evidence("primary_link_mismatch", f"Verlinktes Dokument behandelt anderes Thema ({match:.0%} Übereinstimmung)", -1, url=url))
        return ok_any, texts, ok_urls

    @staticmethod
    def _mentions_old_year(views, now: datetime) -> bool:
        for v in views:
            for y in re.findall(r"\b(20\d{2})\b", v.item.title):
                if int(y) < now.year - 1:
                    return True
        return False

    @staticmethod
    def _score(primary_ok, n_groups, contradictions, refuting, flags) -> float:
        score = (0.6 if primary_ok else 0.0) + min(n_groups, 4) * 0.15
        score -= 0.2 * contradictions + (0.8 if refuting else 0.0)
        score -= 0.1 * len({"syndicated", "derived_reporting", "lookalike_domain", "old_story_recirculated"} & flags)
        return round(max(0.0, min(1.0, score)), 2)
