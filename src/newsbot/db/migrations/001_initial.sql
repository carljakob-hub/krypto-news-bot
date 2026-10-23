-- Grundschema: Quellen, Meldungen, Ereignisse (Cluster), Belege, Veröffentlichungen, Monitoring

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    tier TEXT NOT NULL,
    trust REAL NOT NULL,
    owner_group TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    etag TEXT,
    last_modified TEXT,
    last_polled_at TEXT,
    last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL,
    signature TEXT NOT NULL,             -- normalisierte Schlüsselwörter für den Ähnlichkeitsvergleich
    status TEXT NOT NULL DEFAULT 'PENDING',
    verification_score REAL,
    relevance_level TEXT,
    relevance_score REAL,
    assets_json TEXT NOT NULL DEFAULT '[]',
    categories_json TEXT NOT NULL DEFAULT '[]',
    eval_count INTEGER NOT NULL DEFAULT 0,
    needs_eval INTEGER NOT NULL DEFAULT 1,
    decided_at TEXT
);
CREATE INDEX idx_clusters_updated ON clusters(updated_at);
CREATE INDEX idx_clusters_needs_eval ON clusters(needs_eval);

CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id),
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    links_json TEXT NOT NULL DEFAULT '[]',
    flags_json TEXT NOT NULL DEFAULT '[]',
    cluster_id INTEGER REFERENCES clusters(id)
);
CREATE INDEX idx_items_cluster ON items(cluster_id);
CREATE INDEX idx_items_content_hash ON items(content_hash);
CREATE INDEX idx_items_fetched ON items(fetched_at);

CREATE TABLE verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL REFERENCES clusters(id),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL,
    independent_groups INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    flags_json TEXT NOT NULL
);
CREATE INDEX idx_verifications_cluster ON verifications(cluster_id);

CREATE TABLE evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verification_id INTEGER NOT NULL REFERENCES verifications(id),
    cluster_id INTEGER NOT NULL REFERENCES clusters(id),
    kind TEXT NOT NULL,
    supports INTEGER NOT NULL,
    weight REAL NOT NULL,
    source_id TEXT,
    url TEXT,
    detail TEXT NOT NULL
);
CREATE INDEX idx_evidence_cluster ON evidence(cluster_id);

CREATE TABLE link_checks (
    url TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    status_code INTEGER,
    text_excerpt TEXT NOT NULL DEFAULT '',
    error TEXT
);

CREATE TABLE publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL UNIQUE REFERENCES clusters(id),  -- verhindert Doppelveröffentlichung
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,             -- queued | sent | failed | skipped_duplicate
    priority INTEGER NOT NULL,
    mode TEXT NOT NULL,
    relevance_level TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    text TEXT NOT NULL,
    generated_by TEXT NOT NULL,
    chat_id INTEGER,
    message_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TEXT,
    sent_at TEXT
);
CREATE INDEX idx_publications_status ON publications(status);
CREATE INDEX idx_publications_sent ON publications(sent_at);

CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,              -- running | ok | partial | failed
    sources_polled INTEGER NOT NULL DEFAULT 0,
    fetch_failures INTEGER NOT NULL DEFAULT 0,
    items_found INTEGER NOT NULL DEFAULT 0,
    items_new INTEGER NOT NULL DEFAULT 0,
    clusters_evaluated INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 0,
    partial INTEGER NOT NULL DEFAULT 0,
    unconfirmed INTEGER NOT NULL DEFAULT 0,
    refuted INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 0,
    publish_failures INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX idx_runs_started ON runs(started_at);

CREATE TABLE fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    http_status INTEGER,
    items INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX idx_fetch_log_at ON fetch_log(at);

CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    ok INTEGER NOT NULL,
    note TEXT
);

CREATE TABLE alerts (
    key TEXT PRIMARY KEY,
    last_sent_at TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
