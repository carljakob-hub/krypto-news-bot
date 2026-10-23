"""Ein Durchlauf: finden → prüfen → bewerten → Duplikate aussortieren → Beitrag bauen → veröffentlichen → protokollieren."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from newsbot.analysis.relevance import RelevanceAnalyzer
from newsbot.config import Mode, Settings
from newsbot.db.database import Database, iso, parse_iso, utcnow
from newsbot.models import RawItem, RelevanceLevel, VerificationResult, VerificationStatus
from newsbot.monitoring.alerts import AlertManager
from newsbot.publishing.formatter import format_post
from newsbot.publishing.publisher import Publisher
from newsbot.research.collector import Collector
from newsbot.verification.clustering import Clusterer
from newsbot.verification.links import LinkChecker
from newsbot.verification.verifier import Verifier

log = logging.getLogger(__name__)

MAX_CLUSTERS_PER_CYCLE = 100


@dataclass
class RunStats:
    run_id: int = 0
    status: str = "ok"
    sources_polled: int = 0
    fetch_failures: int = 0
    items_found: int = 0
    items_new: int = 0
    clusters_evaluated: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    queued: int = 0
    published: int = 0
    publish_failures: int = 0
    errors: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, settings: Settings, db: Database, collector: Collector, summarizer, publisher: Publisher,
                 link_checker: LinkChecker | None = None, alerts: AlertManager | None = None):
        self.settings = settings
        self.cfg = settings.config
        self.db = db
        self.collector = collector
        self.summarizer = summarizer
        self.publisher = publisher
        self.link_checker = link_checker
        self.alerts = alerts
        self.clusterer = Clusterer(db, self.cfg.verification.cluster_similarity_threshold, self.cfg.verification.cluster_window_hours)
        self.verifier = Verifier(self.cfg, link_checker.check if link_checker else None)
        self.relevance = RelevanceAnalyzer(self.cfg)

    async def run_cycle(self, now: datetime | None = None) -> RunStats:
        now = now or utcnow()
        stats = RunStats()
        stats.run_id = self.db.execute("INSERT INTO runs(started_at, status) VALUES (?, 'running')", (iso(now),)).lastrowid
        if self.link_checker:
            self.link_checker.reset_budget(self.cfg.verification.max_link_checks_per_cycle)
        auth_error = False
        try:
            collected = await self.collector.collect(now)
            stats.sources_polled = collected.sources_polled
            stats.fetch_failures = collected.fetch_failures
            stats.items_found = collected.items_found
            stats.items_new = len(collected.new_items)
            stats.errors += collected.errors
            for item in collected.new_items:
                self.clusterer.assign(item, now)
            await self._evaluate_clusters(now, stats)
            flush = await self.publisher.flush(now)
            stats.published, stats.publish_failures, auth_error = flush.sent, flush.failed, flush.auth_error
            if stats.sources_polled and stats.fetch_failures == stats.sources_polled:
                stats.status = "failed"
            elif stats.fetch_failures or stats.publish_failures:
                stats.status = "partial"
        except asyncio.CancelledError:
            stats.status = "failed"
            stats.errors.append("Abgebrochen (Zeitlimit oder Herunterfahren)")
            raise
        except Exception as exc:
            stats.status = "failed"
            stats.errors.append(f"{type(exc).__name__}: {exc}")
            log.exception("Durchlauf %s fehlgeschlagen", stats.run_id)
        finally:
            self._finish(stats, now)
        if self.alerts:
            await self.alerts.evaluate(now, telegram_auth_error=auth_error)
        return stats

    # ------------------------------------------------------------------
    def _finish(self, stats: RunStats, now: datetime) -> None:
        b = stats.by_status
        self.db.execute(
            "UPDATE runs SET finished_at=?, status=?, sources_polled=?, fetch_failures=?, items_found=?, items_new=?, "
            "clusters_evaluated=?, verified=?, partial=?, unconfirmed=?, refuted=?, published=?, publish_failures=?, error=? "
            "WHERE id=?",
            (iso(utcnow()), stats.status, stats.sources_polled, stats.fetch_failures, stats.items_found, stats.items_new,
             stats.clusters_evaluated, b.get("VERIFIZIERT", 0), b.get("TEILWEISE BESTÄTIGT", 0), b.get("UNBESTÄTIGT", 0),
             b.get("WIDERLEGT", 0), stats.published, stats.publish_failures, "; ".join(stats.errors)[:2000] or None,
             stats.run_id),
        )
        if stats.status == "failed":
            self.db.kv_set("consecutive_failed_cycles", (self.db.kv_get("consecutive_failed_cycles") or 0) + 1)
        else:
            self.db.kv_set("consecutive_failed_cycles", 0)
            self.db.kv_set("last_successful_research", iso(now))

    def _load_items(self, cluster_id: int) -> list[RawItem]:
        rows = self.db.query("SELECT * FROM items WHERE cluster_id = ? ORDER BY id", (cluster_id,))
        return [
            RawItem(
                source_id=r["source_id"], url=r["url"], title=r["title"], summary=r["summary"],
                published_at=parse_iso(r["published_at"]), fetched_at=parse_iso(r["fetched_at"]),
                links=json.loads(r["links_json"]), id=r["id"], cluster_id=cluster_id, flags=json.loads(r["flags_json"]),
            )
            for r in rows
        ]

    async def _evaluate_clusters(self, now: datetime, stats: RunStats) -> None:
        since = iso(now - timedelta(hours=self.cfg.verification.pending_reevaluation_hours))
        clusters = self.db.query(
            "SELECT c.* FROM clusters c LEFT JOIN publications p ON p.cluster_id = c.id "
            "WHERE c.needs_eval = 1 AND c.created_at >= ? AND p.id IS NULL ORDER BY c.updated_at DESC LIMIT ?",
            (since, MAX_CLUSTERS_PER_CYCLE),
        )
        # Ältere, nicht mehr neu bewertete Cluster abschließen (Schutz gegen Endlosschleifen)
        self.db.execute("UPDATE clusters SET needs_eval = 0 WHERE needs_eval = 1 AND created_at < ?", (since,))
        for c in clusters:
            items = self._load_items(c["id"])
            result = await self.verifier.verify(items, now)
            self._store_verification(c["id"], result, now)
            stats.clusters_evaluated += 1
            stats.by_status[result.status.value] = stats.by_status.get(result.status.value, 0) + 1
            if await self._maybe_queue(c, items, result, now):
                stats.queued += 1

    def _store_verification(self, cluster_id: int, r: VerificationResult, now: datetime) -> None:
        with self.db.tx() as tx:
            vid = tx.execute(
                "INSERT INTO verifications(cluster_id, created_at, status, score, independent_groups, reasons_json, flags_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (cluster_id, iso(now), r.status.value, r.score, r.independent_groups, json.dumps(r.reasons, ensure_ascii=False),
                 json.dumps(r.flags)),
            ).lastrowid
            for e in r.evidence:
                tx.execute(
                    "INSERT INTO evidence(verification_id, cluster_id, kind, supports, weight, source_id, url, detail) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (vid, cluster_id, e.kind, e.supports, e.weight, e.source_id, e.url, e.detail),
                )
            tx.execute(
                "UPDATE clusters SET status=?, verification_score=?, needs_eval=0, eval_count=eval_count+1, decided_at=? WHERE id=?",
                (r.status.value, r.score, iso(now), cluster_id),
            )

    async def _maybe_queue(self, cluster, items: list[RawItem], v: VerificationResult, now: datetime) -> bool:
        if v.status not in (VerificationStatus.VERIFIED, VerificationStatus.PARTIAL):
            return False
        # Neuheit ab der frühesten bekannten Veröffentlichung, nicht erst ab dem eigenen Fund
        first_seen = min([parse_iso(cluster["created_at"])] + [i.published_at for i in items if i.published_at])
        rel = self.relevance.analyze(items, v, first_seen, now)
        self.db.execute(
            "UPDATE clusters SET relevance_level=?, relevance_score=?, assets_json=?, categories_json=? WHERE id=?",
            (rel.level.value, rel.score, json.dumps(rel.assets), json.dumps(rel.categories), cluster["id"]),
        )
        min_level = RelevanceLevel(self.cfg.relevance.min_level_to_publish)
        if rel.level.rank < min_level.rank:
            return False
        if v.status is VerificationStatus.PARTIAL and rel.level.rank < RelevanceLevel(self.cfg.relevance.publish_partial_only_if).rank:
            return False
        content = await self.summarizer.compose(items, v, rel, now)
        if (rel.level is RelevanceLevel.MEDIUM and self.cfg.relevance.medium_requires_direction
                and content.outlook not in ("positiv", "negativ")):
            log.info("Cluster %s: MITTEL ohne klare Marktrichtung – nicht veröffentlicht", cluster["id"])
            return False
        names = {i.url: self.cfg.source(i.source_id).name for i in items if self.cfg.source(i.source_id)}
        try:
            text = format_post(content, v, rel, names, self.cfg, now, test_mode=self.settings.mode is Mode.TEST and self.cfg.publishing.mark_test_posts)
        except ValueError as exc:
            log.warning("Cluster %s nicht veröffentlicht: %s", cluster["id"], exc)
            return False
        status = self.publisher.enqueue(
            cluster_id=cluster["id"], text=text, headline=content.headline + " " + cluster["title"],
            priority=rel.level.rank * 100 + int(v.score * 10), level=rel.level.value,
            verification_status=v.status.value, generated_by=content.generated_by, now=now,
        )
        log.info("Cluster %s: %s / %s → %s", cluster["id"], v.status.value, rel.level.value, status)
        return status == "queued"
