"""
trade_store.py — Thin SQLite data-access layer for the trade/learning dataset.

SQLite = a real SQL database that's just one file (no server). When we go live and
need continuous multi-process writes, the same interface can be re-pointed at
PostgreSQL. NOT MongoDB — this is structured tabular data.

One row = one trade, with its outcome (label) AND the decision-time features.
This is the ML-ready dataset for the future meta-labeling phase (Phase 5).
"""
from __future__ import annotations

import os
import sqlite3
from typing import List, Dict

COLUMNS = [
    "run_id", "strategy", "exit_mode", "symbol", "sample",
    "signal_time", "entry_time", "exit_time", "direction",
    "entry", "stop", "target", "exit_price",
    "R", "gross_R", "mfe_r", "mae_r", "outcome", "bars_held", "conviction",
    # decision-time features (the inputs a meta-label model would learn from)
    "trend_strength", "atr_pct", "adx", "rsi", "dow",
]


def _connect(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return sqlite3.connect(path)


def init_db(path: str) -> None:
    cols_sql = ",\n  ".join(f"{c} REAL" if c in (
        "entry", "stop", "target", "exit_price", "R", "gross_R", "mfe_r", "mae_r",
        "conviction", "trend_strength", "atr_pct", "adx", "rsi") else f"{c} TEXT"
        for c in COLUMNS)
    # bars_held and dow are ints
    cols_sql = cols_sql.replace("bars_held TEXT", "bars_held INTEGER").replace("dow TEXT", "dow INTEGER")
    with _connect(path) as con:
        con.execute(f"""CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  {cols_sql}
)""")
        con.commit()


def insert_trades(path: str, rows: List[Dict]) -> int:
    if not rows:
        return 0
    init_db(path)
    placeholders = ",".join(["?"] * len(COLUMNS))
    with _connect(path) as con:
        con.executemany(
            f"INSERT INTO trades ({','.join(COLUMNS)}) VALUES ({placeholders})",
            [[r.get(c) for c in COLUMNS] for r in rows],
        )
        con.commit()
    return len(rows)


def count(path: str, where: str = "") -> int:
    if not os.path.exists(path):
        return 0
    with _connect(path) as con:
        cur = con.execute(f"SELECT COUNT(*) FROM trades {where}")
        return int(cur.fetchone()[0])
