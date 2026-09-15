"""
backfill_trades_db.py — one-time: persist the NSE and FX/commodity Donchian runs'
individual trades (+decision-time features) into engine/output/trades.db.

Why this exists: trades.db is only ever written by run_improve.py (the exit-
improvement comparison), which has only ever been run once, on the original
13-symbol US/crypto/etf watchlist. Neither run_backtest.py nor run_backtest_nse.py
writes to it at all -- so the NSE and FX/commodity Donchian results this session
existed only as printed console output and a registry.py summary row, never as
queryable per-trade rows. This closes that gap using the exact same DonchianStrategy
config (unmodified) and the exact same log_trades()/trade_store.py machinery
run_improve.py already uses -- no new persistence code paths, just wiring the
existing one to these two extra runs.

exit_mode is set to a distinguishing label ("baseline_nse" / "baseline_fx_commodity")
so these rows are never confused with the original "baseline 2.5R" market run
already in the table, and can be filtered independently.

Run once: python -X utf8 -m engine.backfill_trades_db
"""
from __future__ import annotations

from datetime import datetime, timezone

from engine import config
from engine.strategy.donchian import DonchianStrategy
from engine.backtest.simulator import simulate
from engine.backtest import walk_forward as wf
from engine.data.feed import CryptoUSFeed, MarketAgentPostgresFeed
from engine.data import trade_store
from engine.report.trade_log import log_trades
from engine.run_backtest_nse import NSE_SYMBOLS

DB_PATH = f"{config.OUTPUT_DIR}/trades.db"


def build_and_log(label: str, symbols, feed, exit_mode: str):
    strategy = DonchianStrategy()
    max_hold = strategy.p["max_hold_bars"]
    interval = strategy.interval

    df_by_symbol, full, boundary_by_symbol = {}, [], {}
    for sym in symbols:
        df = feed.get_ohlcv(sym, interval, config.FETCH_BARS)
        if df is None or len(df) < 200:
            print(f"  skip {sym} (no/insufficient data)")
            continue
        df_by_symbol[sym] = df
        costs = config.cost_for(sym)
        trades = simulate(df, strategy, sym, costs, max_hold)
        full.extend(trades)
        boundary_by_symbol[sym] = wf.oos_boundary_time(df, config.OOS_FRACTION)

    full.sort(key=lambda t: t.signal_time)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    n = log_trades(DB_PATH, run_id, "donchian", exit_mode, full, df_by_symbol, boundary_by_symbol)
    print(f"  {label}: logged {n} trades (run_id={run_id}, exit_mode={exit_mode})")
    return n


def main():
    print("=" * 64)
    print("  BACKFILL trades.db — NSE + FX/commodity Donchian runs")
    print("=" * 64)

    before = trade_store.count(DB_PATH)
    print(f"\nrows before: {before}")

    print("\n-- NSE --")
    build_and_log("NSE", NSE_SYMBOLS, MarketAgentPostgresFeed(), "baseline_nse")

    fx_commodity_symbols = config.WATCHLISTS.get("fx", []) + config.WATCHLISTS.get("commodity", [])
    print("\n-- FX/commodity --")
    build_and_log("FX/commodity", fx_commodity_symbols, CryptoUSFeed(), "baseline_fx_commodity")

    after = trade_store.count(DB_PATH)
    print(f"\nrows after: {after}  (+{after - before})")


if __name__ == "__main__":
    main()
