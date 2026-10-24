"""Gruppiert Meldungen verschiedener Quellen zu Ereignissen (Duplikaterkennung über Quellen hinweg)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from newsbot.db.database import Database, iso
from newsbot.models import RawItem
from newsbot.textutils import content_hash, jaccard, overlap, tokens


def title_similarity(a: set[str], b: set[str]) -> float:
    if len(a & b) < 2:
        return 0.0
    return 0.5 * jaccard(a, b) + 0.5 * overlap(a, b)


class Clusterer:
    def __init__(self, db: Database, threshold: float, window_hours: float):
        self.db = db
        self.threshold = threshold
        self.window = timedelta(hours=window_hours)

    def assign(self, item: RawItem, now: datetime) -> int:
        item_tokens = tokens(item.title)
        c_hash = content_hash(item.title, item.summary)
        same = self.db.one(
            "SELECT cluster_id FROM items WHERE content_hash = ? AND cluster_id IS NOT NULL AND id != ? LIMIT 1",
            (c_hash, item.id or -1),
        )
        cluster_id = same["cluster_id"] if same else self._best_match(item_tokens, now)
        if cluster_id is None:
            cur = self.db.execute(
                "INSERT INTO clusters(created_at, updated_at, title, signature) VALUES (?,?,?,?)",
                (iso(now), iso(now), item.title, json.dumps(sorted(item_tokens))),
            )
            cluster_id = cur.lastrowid
        else:
            row = self.db.one("SELECT signature FROM clusters WHERE id = ?", (cluster_id,))
            sig = set(json.loads(row["signature"])) | item_tokens
            self.db.execute(
                "UPDATE clusters SET updated_at = ?, signature = ?, needs_eval = 1 WHERE id = ?",
                (iso(now), json.dumps(sorted(sig)[:200]), cluster_id),
            )
        self.db.execute("UPDATE items SET cluster_id = ? WHERE id = ?", (cluster_id, item.id))
        item.cluster_id = cluster_id
        return cluster_id

    def _best_match(self, item_tokens: set[str], now: datetime) -> int | None:
        rows = self.db.query(
            "SELECT i.cluster_id, i.title FROM items i JOIN clusters c ON c.id = i.cluster_id "
            "WHERE c.updated_at >= ?",
            (iso(now - self.window),),
        )
        best_id, best = None, 0.0
        for r in rows:
            sim = title_similarity(item_tokens, tokens(r["title"]))
            if sim > best:
                best_id, best = r["cluster_id"], sim
        return best_id if best >= self.threshold else None
