"""Konfiguration: YAML-Dateien für Regeln, Umgebungsvariablen für Geheimnisse."""

from __future__ import annotations

import hashlib
import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ConfigError(Exception):
    pass


class Mode(str, Enum):
    TEST = "test"
    PRODUCTION = "production"


class Tier(str, Enum):
    PRIMARY = "primary"
    ESTABLISHED = "established"
    SECONDARY = "secondary"
    SOCIAL = "social"


class ScheduleConfig(BaseModel):
    tick_seconds: int = Field(60, ge=5)
    default_poll_interval_seconds: int = Field(300, ge=30)
    cycle_timeout_seconds: int = Field(240, ge=10)
    max_backoff_seconds: int = Field(1800, ge=10)


class ResearchConfig(BaseModel):
    request_timeout_seconds: float = Field(20, gt=0)
    max_retries: int = Field(3, ge=0, le=10)
    retry_base_delay_seconds: float = Field(2, ge=0)
    max_items_per_source: int = Field(40, ge=1, le=500)
    max_response_bytes: int = Field(3_000_000, ge=10_000)
    user_agent: str = "KryptoNewsBot/0.1"


class VerificationConfig(BaseModel):
    max_item_age_hours: float = Field(24, gt=0)
    cluster_window_hours: float = Field(48, gt=0)
    cluster_similarity_threshold: float = Field(0.34, gt=0, lt=1)
    min_independent_sources: int = Field(2, ge=2)
    min_trust_score: float = Field(0.6, ge=0, le=1)
    syndication_similarity: float = Field(0.75, gt=0, le=1)
    number_tolerance: float = Field(0.05, ge=0, lt=1)
    verify_primary_links: bool = True
    max_link_checks_per_cycle: int = Field(15, ge=0)
    pending_reevaluation_hours: float = Field(6, ge=0)


Level = Literal["HOCH", "MITTEL", "NIEDRIG"]


class RelevanceConfig(BaseModel):
    min_level_to_publish: Level = "MITTEL"
    publish_partial_only_if: Level = "HOCH"
    novelty_hours: float = Field(6, gt=0)


class PublishingConfig(BaseModel):
    max_posts_per_hour: int = Field(4, ge=1)
    max_posts_per_day: int = Field(24, ge=1)
    min_seconds_between_posts: int = Field(240, ge=0)
    high_priority_bypasses_spacing: bool = True
    max_send_attempts: int = Field(3, ge=1, le=10)
    max_queue_age_hours: float = Field(6, gt=0)
    duplicate_lookback_hours: float = Field(72, gt=0)
    duplicate_similarity: float = Field(0.55, gt=0, le=1)
    post_log_file: str = "data/posts.log"
    disclaimer: str = "Keine Anlageberatung."


class MonitoringConfig(BaseModel):
    alert_no_successful_research_minutes: int = Field(45, ge=1)
    alert_consecutive_failed_cycles: int = Field(3, ge=1)
    alert_consecutive_publish_failures: int = Field(3, ge=1)
    alert_cooldown_minutes: int = Field(120, ge=1)
    health_max_age_minutes: int = Field(30, ge=1)
    hosting_cost_usd_per_month: float | None = None


class LLMConfig(BaseModel):
    enabled: bool | Literal["auto"] = "auto"
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    max_tokens: int = Field(4000, ge=256)
    price_input_usd_per_mtok: float = Field(4.0, ge=0)
    price_output_usd_per_mtok: float = Field(20.0, ge=0)
    daily_budget_usd: float = Field(2.0, ge=0)
    max_source_chars: int = Field(6000, ge=500)


class SourceConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_\-]+$")
    name: str
    url: str
    type: Literal["rss"] = "rss"
    tier: Tier
    trust: float = Field(ge=0, le=1)
    owner_group: str
    official_domains: list[str] = []
    poll_interval_seconds: int | None = None
    topics: list[str] = []
    enabled: bool = True
    conflict_of_interest: bool = False  # z. B. Börse/Unternehmen berichtet über eigene Produkte

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("Quellen müssen per HTTPS abgerufen werden")
        return v


class AppConfig(BaseModel):
    timezone: str = "Europe/Berlin"
    schedule: ScheduleConfig = ScheduleConfig()
    research: ResearchConfig = ResearchConfig()
    verification: VerificationConfig = VerificationConfig()
    relevance: RelevanceConfig = RelevanceConfig()
    publishing: PublishingConfig = PublishingConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    llm: LLMConfig = LLMConfig()
    primary_domains: list[str] = []
    sources: list[SourceConfig] = []

    @model_validator(mode="after")
    def _unique_ids(self) -> "AppConfig":
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Quellen-IDs müssen eindeutig sein")
        return self

    def source(self, source_id: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    def fingerprint(self) -> str:
        """Hash der Regeln; ein Preflight gilt nur für genau diese Konfiguration."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class Secrets(BaseModel):
    """Nur aus Umgebungsvariablen. Niemals loggen (repr ist maskiert)."""

    telegram_bot_token: str | None = None
    telegram_chat_id: int | None = None
    telegram_test_chat_id: int | None = None
    telegram_topic_id: int | None = None       # Thema (Forum-Topic) in der Produktionsgruppe
    telegram_test_topic_id: int | None = None  # Thema in der Testgruppe
    telegram_admin_chat_id: int | None = None
    anthropic_api_key: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            "Secrets(telegram_bot_token=%s, telegram_chat_id=%s, anthropic_api_key=%s)"
            % (
                "***" if self.telegram_bot_token else None,
                self.telegram_chat_id,
                "***" if self.anthropic_api_key else None,
            )
        )

    __str__ = __repr__


class Settings(BaseModel):
    mode: Mode
    config: AppConfig
    secrets: Secrets
    db_path: str
    base_dir: Path

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.base_dir / p

    def llm_active(self) -> bool:
        if self.config.llm.enabled == "auto":
            return bool(self.secrets.anthropic_api_key)
        return bool(self.config.llm.enabled) and bool(self.secrets.anthropic_api_key)

    def target_chat_id(self) -> int | None:
        if self.mode is Mode.PRODUCTION:
            return self.secrets.telegram_chat_id
        return self.secrets.telegram_test_chat_id

    def target_topic_id(self) -> int | None:
        if self.mode is Mode.PRODUCTION:
            return self.secrets.telegram_topic_id
        return self.secrets.telegram_test_topic_id

    def allowed_chat_ids(self) -> set[int]:
        ids = {self.target_chat_id(), self.secrets.telegram_admin_chat_id}
        return {i for i in ids if i is not None}


def _parse_chat_id(name: str, value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine Zahl sein") from exc


def _describe_bad_token(value: str) -> str:
    """Beschreibt einen falschen Token-Wert, ohne ihn preiszugeben."""
    if value.lstrip("-").isdigit():
        return "nur Ziffern (vermutlich eine Gruppen- oder Nutzer-ID)"
    if value.startswith("@") or value.lower().endswith("bot"):
        return "vermutlich der Benutzername des Bots"
    if ":" not in value:
        return "ohne Doppelpunkt"
    return "Schlüsselteil zu kurz"


def load_secrets(env: dict[str, str] | None = None) -> Secrets:
    env = dict(os.environ) if env is None else env
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip() or None
    if token:
        # Wer die ganze BotFather-Nachricht einfügt, soll trotzdem ans Ziel kommen.
        found = re.search(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{25,}", token)
        if found:
            token = found.group(0)
        else:
            raise ConfigError(
                "TELEGRAM_BOT_TOKEN hat ein ungültiges Format (erwartet <zahl>:<schlüssel>); "
                f"eingetragen sind {len(token)} Zeichen, {_describe_bad_token(token)}"
            )
    return Secrets(
        telegram_bot_token=token,
        telegram_chat_id=_parse_chat_id("TELEGRAM_CHAT_ID", env.get("TELEGRAM_CHAT_ID")),
        telegram_test_chat_id=_parse_chat_id("TELEGRAM_TEST_CHAT_ID", env.get("TELEGRAM_TEST_CHAT_ID")),
        telegram_topic_id=_parse_chat_id("TELEGRAM_TOPIC_ID", env.get("TELEGRAM_TOPIC_ID")),
        telegram_test_topic_id=_parse_chat_id("TELEGRAM_TEST_TOPIC_ID", env.get("TELEGRAM_TEST_TOPIC_ID")),
        telegram_admin_chat_id=_parse_chat_id("TELEGRAM_ADMIN_CHAT_ID", env.get("TELEGRAM_ADMIN_CHAT_ID")),
        anthropic_api_key=(env.get("ANTHROPIC_API_KEY") or "").strip() or None,
    )


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Konfigurationsdatei fehlt: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Ungültiges YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} muss ein YAML-Objekt enthalten")
    return data


def load_app_config(config_path: Path, sources_path: Path) -> AppConfig:
    data = _read_yaml(config_path)
    src = _read_yaml(sources_path)
    data["sources"] = src.get("sources", [])
    data["primary_domains"] = src.get("primary_domains", [])
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"Konfiguration ungültig: {exc}") from exc


def load_settings(env: dict[str, str] | None = None, base_dir: Path | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    base = base_dir or Path(env.get("NEWSBOT_HOME", Path.cwd()))
    cfg_path = base / env.get("NEWSBOT_CONFIG", "config/config.yaml")
    src_path = base / env.get("NEWSBOT_SOURCES", "config/sources.yaml")
    mode_raw = (env.get("NEWSBOT_MODE") or "test").strip().lower()
    try:
        mode = Mode(mode_raw)
    except ValueError as exc:
        raise ConfigError("NEWSBOT_MODE muss 'test' oder 'production' sein") from exc
    db = env.get("NEWSBOT_DB_PATH", "data/newsbot.sqlite3")
    db_path = db if Path(db).is_absolute() or db == ":memory:" else str(base / db)
    return Settings(
        mode=mode,
        config=load_app_config(cfg_path, src_path),
        secrets=load_secrets(env),
        db_path=db_path,
        base_dir=base,
    )


def validate_for_mode(settings: Settings) -> list[str]:
    """Liefert eine Liste von Problemen; leer bedeutet: startklar für den Modus."""
    problems: list[str] = []
    s = settings.secrets
    if not settings.config.enabled_sources():
        problems.append("Keine aktivierte Quelle konfiguriert")
    if settings.mode is Mode.PRODUCTION:
        if not s.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN fehlt")
        if s.telegram_chat_id is None:
            problems.append("TELEGRAM_CHAT_ID fehlt")
        if s.telegram_test_chat_id is not None and s.telegram_test_chat_id == s.telegram_chat_id:
            problems.append("Test- und Produktionsgruppe dürfen nicht identisch sein")
    if settings.config.llm.enabled is True and not s.anthropic_api_key:
        problems.append("llm.enabled=true, aber ANTHROPIC_API_KEY fehlt")
    return problems
