"""Strukturierte Datenmodelle, die zwischen den Modulen ausgetauscht werden."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIZIERT"
    PARTIAL = "TEILWEISE BESTÄTIGT"
    UNCONFIRMED = "UNBESTÄTIGT"
    REFUTED = "WIDERLEGT"


class RelevanceLevel(str, Enum):
    HIGH = "HOCH"
    MEDIUM = "MITTEL"
    LOW = "NIEDRIG"

    @property
    def rank(self) -> int:
        return {"HOCH": 3, "MITTEL": 2, "NIEDRIG": 1}[self.value]


@dataclass
class RawItem:
    """Eine einzelne Meldung, wie sie aus einer Quelle abgerufen wurde (nicht vertrauenswürdig)."""

    source_id: str
    url: str
    title: str
    summary: str
    published_at: datetime | None
    fetched_at: datetime
    links: list[str] = field(default_factory=list)
    id: int | None = None
    cluster_id: int | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class Evidence:
    """Ein nachvollziehbarer Beleg für oder gegen eine Meldung."""

    kind: str          # primary_source, primary_link, independent_confirmation, syndicated, contradiction, ...
    detail: str
    supports: int      # +1 stützt, 0 neutral, -1 widerspricht
    source_id: str | None = None
    url: str | None = None
    weight: float = 0.0


@dataclass
class VerificationResult:
    status: VerificationStatus
    score: float
    evidence: list[Evidence]
    flags: list[str]
    reasons: list[str]
    primary_urls: list[str] = field(default_factory=list)
    confirmation_urls: list[str] = field(default_factory=list)
    independent_groups: int = 0


@dataclass
class RelevanceResult:
    level: RelevanceLevel
    score: float
    assets: list[str]
    categories: list[str]
    time_horizon: str
    reasons: list[str]
    novelty: str


@dataclass
class PostContent:
    headline: str
    what_happened: str
    market_impact: str
    risks: str
    time_horizon: str
    assets: list[str]
    generated_by: str  # "template" oder "llm:<modell>"
    reactions: list[str] = field(default_factory=list)  # mögliche Marktreaktionen als Szenarien


@dataclass
class LinkCheck:
    url: str
    ok: bool
    status_code: int | None
    text: str
    error: str | None = None
