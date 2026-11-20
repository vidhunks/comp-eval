"""SQLite storage. Each call opens its own connection, so it is safe to use
from the Streamlit script thread and from the background evaluation thread."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.config import DATA_DIR

# EVALUATOR_DB overrides the database file (e.g. for a dry run on a scratch database).
DB_PATH = Path(os.environ.get("EVALUATOR_DB") or DATA_DIR / "competition.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS Teams (
    team_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name       TEXT NOT NULL UNIQUE,
    endpoint_url    TEXT NOT NULL,
    http_method     TEXT NOT NULL DEFAULT 'POST' CHECK (http_method IN ('GET', 'POST')),
    request_config  TEXT NOT NULL DEFAULT '{}',
    active_status   INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS Test_Cases (
    test_id           TEXT PRIMARY KEY,
    query             TEXT NOT NULL,
    expected_results  TEXT NOT NULL DEFAULT '[]',
    category          TEXT NOT NULL DEFAULT 'functional',
    relevance_grades  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS Evaluation_Runs (
    run_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time         TEXT NOT NULL,
    end_time           TEXT,
    evaluation_status  TEXT NOT NULL,
    configuration      TEXT NOT NULL,
    phases             TEXT NOT NULL DEFAULT '[]',
    progress           TEXT NOT NULL DEFAULT '{}',
    error_log          TEXT NOT NULL DEFAULT '[]',
    cancel_requested   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS Evaluation_Results (
    result_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             INTEGER NOT NULL REFERENCES Evaluation_Runs(run_id) ON DELETE CASCADE,
    team_id            INTEGER NOT NULL,
    test_id            TEXT,
    phase              TEXT NOT NULL,
    request_index      INTEGER,
    concurrency_level  INTEGER,
    started_at         TEXT,
    latency_ms         REAL,
    status_code        INTEGER,
    success            INTEGER NOT NULL,
    outcome            TEXT NOT NULL,
    attempts           INTEGER NOT NULL DEFAULT 1,
    error_message      TEXT,
    response_payload   TEXT,
    precision_score    REAL,
    recall_score       REAL,
    ndcg_score         REAL,
    details            TEXT
);

CREATE TABLE IF NOT EXISTS Team_Scores (
    score_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES Evaluation_Runs(run_id) ON DELETE CASCADE,
    team_id             INTEGER NOT NULL,
    functional_score    REAL,
    performance_score   REAL,
    reliability_score   REAL,
    scalability_score   REAL,
    cost_score          REAL,
    architecture_score  REAL,
    final_score         REAL,
    details             TEXT,
    computed_at         TEXT,
    UNIQUE (run_id, team_id)
);

CREATE TABLE IF NOT EXISTS Architecture_Reviews (
    review_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          INTEGER NOT NULL,
    run_id           INTEGER,
    review_category  TEXT NOT NULL,
    awarded_points   REAL NOT NULL,
    comments         TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS Team_Costs (
    team_id                    INTEGER PRIMARY KEY,
    ai_model                   TEXT,
    embedding_model            TEXT,
    paid_api_usage             INTEGER NOT NULL DEFAULT 0,
    api_cost_per_1k_usd        REAL,
    infra_cost_per_1k_usd      REAL,
    uses_local_models          INTEGER NOT NULL DEFAULT 0,
    notes                      TEXT,
    updated_at                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_run_team ON Evaluation_Results(run_id, team_id, phase);
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_unique
    ON Architecture_Reviews(team_id, IFNULL(run_id, -1), review_category);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def get_connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a connection that commits on success and rolls back on error."""
    conn = _connect(path or DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    conn = _connect(path or DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def set_db_path(path: Path) -> None:
    """Point the module at a different database file (used by tests)."""
    global DB_PATH
    DB_PATH = path
