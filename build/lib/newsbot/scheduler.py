"""Dauerbetrieb: periodische Durchläufe mit Zeitlimit, Backoff und sauberem Herunterfahren."""

from __future__ import annotations

import asyncio
import logging
import signal

from newsbot.db.database import iso, utcnow
from newsbot.pipeline import Pipeline

log = logging.getLogger(__name__)


async def run_forever(pipeline: Pipeline, stop: asyncio.Event | None = None, max_cycles: int | None = None) -> None:
    cfg = pipeline.cfg.schedule
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    pipeline.db.kv_set("started_at", iso(utcnow()))
    failures = 0
    cycles = 0
    while not stop.is_set():
        try:
            stats = await asyncio.wait_for(pipeline.run_cycle(), timeout=cfg.cycle_timeout_seconds)
            failures = failures + 1 if stats.status == "failed" else 0
            log.info(
                "Durchlauf %s: %s | Quellen %d (Fehler %d) | neu %d | geprüft %d %s | veröffentlicht %d",
                stats.run_id, stats.status, stats.sources_polled, stats.fetch_failures, stats.items_new,
                stats.clusters_evaluated, stats.by_status, stats.published,
            )
        except asyncio.TimeoutError:
            failures += 1
            log.error("Durchlauf überschritt das Zeitlimit von %ss", cfg.cycle_timeout_seconds)
        except Exception:
            failures += 1
            log.exception("Unerwarteter Fehler im Scheduler")
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        delay = cfg.tick_seconds if failures == 0 else min(cfg.tick_seconds * 2 ** failures, cfg.max_backoff_seconds)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
    log.info("Scheduler beendet")
