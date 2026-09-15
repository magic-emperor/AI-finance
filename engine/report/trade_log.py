"""
trade_log.py — Turn CompletedTrades into ML-ready rows (outcome + decision-time
features) and persist them to SQLite.

Features are RECOMPUTED from the cached OHLCV at each trade's signal_time — so we
never change the baseline Trade/simulator contract, and we never use any
information from after the decision bar (no leakage).
"""
from __future__ import annotations

from typing import List, Dict
import pandas as pd

from engine.indicators import calc_sma, calc_atr, calc_adx, calc_rsi_float
from engine.data import trade_store


def _features_at(df: pd.DataFrame, signal_time) -> Dict:
    """Decision-time features using ONLY bars up to and including signal_time."""
    window = df.loc[:signal_time]
    if len(window) < 50:
        return {"trend_strength": None, "atr_pct": None, "adx": None, "rsi": None, "dow": None}
    close = float(window["Close"].iloc[-1])
    sma = calc_sma(window, 50)
    atr = calc_atr(window, 14)
    return {
        "trend_strength": (close / sma - 1.0) if sma > 0 else None,   # how far above/below trend
        "atr_pct": (atr / close) if close > 0 else None,              # volatility regime
        "adx": calc_adx(window, 14),                                  # trend strength
        "rsi": calc_rsi_float(window, 14),
        "dow": int(pd.Timestamp(signal_time).dayofweek),
    }


def log_trades(db_path: str, run_id: str, strategy: str, exit_mode: str,
               trades: List, df_by_symbol: Dict[str, pd.DataFrame],
               boundary_by_symbol: Dict = None) -> int:
    rows = []
    for t in trades:
        df = df_by_symbol.get(t.symbol)
        feats = _features_at(df, t.signal_time) if df is not None else {}
        b = (boundary_by_symbol or {}).get(t.symbol)
        sample = "oos" if (b is not None and t.signal_time >= b) else "train"
        rows.append({
            "run_id": run_id, "strategy": strategy, "exit_mode": exit_mode,
            "symbol": t.symbol, "sample": sample,
            "signal_time": str(t.signal_time), "entry_time": str(t.entry_time),
            "exit_time": str(t.exit_time), "direction": t.direction,
            "entry": round(t.entry_fill, 6), "stop": round(t.stop, 6),
            "target": round(t.target, 6), "exit_price": round(t.exit_price, 6),
            "R": round(t.R, 4), "gross_R": round(t.gross_R, 4),
            "mfe_r": round(t.mfe_r, 4), "mae_r": round(t.mae_r, 4),
            "outcome": t.outcome, "bars_held": int(t.bars_held),
            "conviction": round(t.conviction, 4),
            **feats,
        })
    return trade_store.insert_trades(db_path, rows)
