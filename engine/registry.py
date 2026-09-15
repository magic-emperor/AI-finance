"""
registry.py — The hypothesis registry, actually built.

Implements the storage layer .claude/skills/hypothesis-registry/SKILL.md
specified (three tables: hypotheses / trials / robustness_results, with the
trading-specific version fields) but which had never actually been
instantiated -- every test this session (NSE Donchian, FX/commodity
Donchian, the liquidity-sweep mechanism test) was tracked only in
conversation and the plan file until this file existed.

A hypothesis is a research question with a pre-committed threshold, decided
BEFORE running the test. A trial is one registered run against it -- results
are appended, never overwritten, so a re-run with different parameters is a
new trial, not a silent edit to an old one.

Storage: SQLite in engine/output/, matching the existing trades.db pattern.

Usage:
    from engine.registry import Registry
    reg = Registry()
    hyp_id = reg.register_hypothesis(
        family="trend-following", name="donchian-fx-commodity",
        statement="Donchian-55/SMA200 trend entry shows positive OOS expectancy on FX+commodities",
        threshold="expectancy_R >= 0.15 AND profit_factor >= 1.3 AND n_trades >= 30 (engine GO gate)",
        literature="Moskowitz/Ooi/Pedersen 2012 Time Series Momentum (58 instruments, incl. FX+commodities)",
    )
    reg.log_trial(hyp_id, data_version="market_agent Postgres 2016-2026 / yfinance cache",
                  cost_model_version="config.COSTS v1", parameter_config=str(config.DONCHIAN),
                  result_summary="OOS n=89, exp_R=+0.242, PF=1.441, GO gate PASS",
                  verdict="ACCEPTED_PENDING_GATE")
"""
from __future__ import annotations

import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "output", "registry.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family TEXT NOT NULL,              -- e.g. "trend-following", "liquidity/microstructure"
    name TEXT NOT NULL,                -- e.g. "donchian-fx-commodity"
    statement TEXT NOT NULL,           -- the testable claim
    threshold TEXT NOT NULL,           -- pre-committed pass/fail, written before running
    literature TEXT,                   -- what published research says, if anything
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
    feature_version TEXT,
    model_version TEXT,                -- e.g. strategy file name + git-ish tag
    data_version TEXT,                 -- which store/cache, what date range
    cost_model_version TEXT,
    parameter_config TEXT,
    result_summary TEXT NOT NULL,
    verdict TEXT NOT NULL,             -- ACCEPTED_PENDING_GATE | REJECTED | INVALID | PROMOTABLE
    run_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS robustness_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id INTEGER NOT NULL REFERENCES trials(id),
    gate_name TEXT NOT NULL,           -- one of the 6 Robustness Gate checks
    passed INTEGER NOT NULL,           -- 0/1
    detail TEXT,
    checked_at TEXT NOT NULL
);
"""


class Registry:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.con = sqlite3.connect(self.db_path)
        self.con.executescript(SCHEMA)
        self.con.commit()

    def register_hypothesis(self, family: str, name: str, statement: str,
                            threshold: str, literature: str = None) -> int:
        """
        Register a hypothesis BEFORE running the test. Re-registering the
        same (family, name) returns the existing id rather than duplicating --
        register once, log many trials against it.
        """
        cur = self.con.execute(
            "SELECT id FROM hypotheses WHERE family=? AND name=?", (family, name)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur = self.con.execute(
            "INSERT INTO hypotheses (family, name, statement, threshold, literature, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (family, name, statement, threshold, literature,
             datetime.now(timezone.utc).isoformat()),
        )
        self.con.commit()
        return cur.lastrowid

    def log_trial(self, hypothesis_id: int, result_summary: str, verdict: str,
                  feature_version: str = None, model_version: str = None,
                  data_version: str = None, cost_model_version: str = None,
                  parameter_config: str = None, run_at: str = None) -> int:
        cur = self.con.execute(
            "INSERT INTO trials (hypothesis_id, feature_version, model_version, "
            "data_version, cost_model_version, parameter_config, result_summary, "
            "verdict, run_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (hypothesis_id, feature_version, model_version, data_version,
             cost_model_version, parameter_config, result_summary, verdict,
             run_at or datetime.now(timezone.utc).isoformat()),
        )
        self.con.commit()
        return cur.lastrowid

    def log_gate_result(self, trial_id: int, gate_name: str, passed: bool, detail: str = None):
        self.con.execute(
            "INSERT INTO robustness_results (trial_id, gate_name, passed, detail, checked_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (trial_id, gate_name, int(passed), detail, datetime.now(timezone.utc).isoformat()),
        )
        self.con.commit()

    def family_trial_count(self, family: str) -> int:
        """Total trials logged for a family -- required for deflated Sharpe / PBO,
        which must be computed against the FULL family history, not just the
        winning variant (hypothesis-registry SKILL.md)."""
        cur = self.con.execute(
            "SELECT COUNT(*) FROM trials t JOIN hypotheses h ON t.hypothesis_id = h.id "
            "WHERE h.family = ?", (family,)
        )
        return cur.fetchone()[0]

    def dump(self):
        import pandas as pd
        h = pd.read_sql("SELECT * FROM hypotheses", self.con)
        t = pd.read_sql("SELECT * FROM trials", self.con)
        return h, t
