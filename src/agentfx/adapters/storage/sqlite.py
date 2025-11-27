from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import timezone, datetime


@dataclass
class Registry:
    db_path: str = "data/registry.sqlite"

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def init(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    instrument TEXT NOT NULL,
                    granularity TEXT NOT NULL,
                    start_ts TEXT NOT NULL,
                    end_ts TEXT NOT NULL,
                    PRIMARY KEY (instrument, granularity)
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument TEXT NOT NULL,
                    granularity TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    rows_ingested INTEGER DEFAULT 0,
                    gaps_fixed INTEGER DEFAULT 0,
                    status TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair TEXT NOT NULL,
                    start_ts TEXT,
                    end_ts TEXT,
                    n INTEGER,
                    metrics_json TEXT,
                    policy_json TEXT,
                    params_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Lightweight migration: ensure params_json exists
            cols = [r[1] for r in c.execute("PRAGMA table_info(backtest_runs)").fetchall()]
            if "params_json" not in cols:
                c.execute("ALTER TABLE backtest_runs ADD COLUMN params_json TEXT")

    def upsert_dataset(self, instrument: str, granularity: str, start_ts: str, end_ts: str) -> None:
        with self._conn() as c:
            cur = c.execute(
                "SELECT start_ts, end_ts FROM datasets WHERE instrument=? AND granularity=?",
                (instrument, granularity),
            ).fetchone()
            if cur:
                old_start, old_end = cur
                new_start = min(old_start, start_ts)
                new_end = max(old_end, end_ts)
                c.execute(
                    "UPDATE datasets SET start_ts=?, end_ts=? WHERE instrument=? AND granularity=?",
                    (new_start, new_end, instrument, granularity),
                )
            else:
                c.execute(
                    "INSERT INTO datasets (instrument, granularity, start_ts, end_ts) VALUES (?, ?, ?, ?)",
                    (instrument, granularity, start_ts, end_ts),
                )

    def start_run(self, instrument: str, granularity: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO ingestion_runs (instrument, granularity, started_at, status) VALUES (?, ?, ?, ?)",
                (instrument, granularity, datetime.now(timezone.utc).isoformat(), "running"),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, rows_ingested: int, gaps_fixed: int, status: str = "success") -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE ingestion_runs SET finished_at=?, rows_ingested=?, gaps_fixed=?, status=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), rows_ingested, gaps_fixed, status, run_id),
            )

    # --- Backtest persistence ---
    def record_backtest(self, pair: str, start_ts: str | None, end_ts: str | None, n: int, metrics: dict, policy: dict, params: dict) -> None:
        import json
        with self._conn() as c:
            c.execute(
                "INSERT INTO backtest_runs (pair, start_ts, end_ts, n, metrics_json, policy_json, params_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pair,
                    start_ts,
                    end_ts,
                    n,
                    json.dumps(metrics),
                    json.dumps(policy),
                    json.dumps(params),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
