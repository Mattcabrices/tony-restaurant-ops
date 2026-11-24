-- Additive operations SQLite schema. Applied transactionally by OperatorLedger.
-- No query text, customer/employee fields, receipt results or credentials.
CREATE TABLE IF NOT EXISTS operator_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operator_queries_v1 (
    request_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    elapsed_ms INTEGER,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS operator_queries_time_v1
    ON operator_queries_v1(started_at);
-- Schema version 1 is recorded once after creation. No global user_version change.
-- Retention: last 5,000 completed records; running work is retained until finished.
