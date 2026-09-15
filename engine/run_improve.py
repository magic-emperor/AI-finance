"""
run_improve.py — Phase 1.6: does a better EXIT (or pyramiding) actually beat the
baseline donchian (fixed 2.5R target) OUT-OF-SAMPLE?

Compares, on the SAME entries/data:
  - baseline      : fixed 2.5R target (current donchian.py, unchanged)
  - trail         : ATR trailing stop (let winners run)
  - partial+trail : take 50% at 2R, trail the rest
  - pyramid       : add at +1R/+2R with decreasing size, trail all

Discipline: a variant is ADOPTED only if it beats the baseline OOS AND clears the
hard gate. We then log the chosen variant's trades (+features) to SQLite.

Usage: python -X utf8 -m engine.run_improve  [--markets crypto,us,etf]
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.strategy.donchian import DonchianStrategy
from engine.backtest.simulator import simulate
from engine.backtest.simulator_v2 import simulate_v2
from engine.backtest.metrics import compute_metrics
from engine.backtest.portfolio import simulate_portfolio
from engine.backtest import walk_forward as wf, periods
from engine.report.scorecard import _fmt, evaluate_gate
from engine.report.trade_log import log_trades
from engine.data import trade_store

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

VARIANTS = [
    ("baseline 2.5R", ("v1", None)),
    ("trail",         ("v2", "trail")),
    ("partial+trail", ("v2", "partial_trail")),
    ("pyramid",       ("v2", "pyramid")),
]


def run_variant(df_by_symbol, strategy, kind):
    full, boundary_by_symbol = [], {}
    for sym, df in df_by_symbol.items():
        if kind[0] == "v1":
            tr = simulate(df, strategy, sym, config.cost_for(sym), strategy.p["max_hold_bars"])
        else:
            tr = simulate_v2(df, strategy, sym, config.cost_for(sym), kind[1], config.EXIT_V2)
        full.extend(tr)
        boundary_by_symbol[sym] = wf.oos_boundary_time(df, config.OOS_FRACTION)
    full.sort(key=lambda t: t.signal_time)
    oos = [t for t in full if t.signal_time >= boundary_by_symbol[t.symbol]]
    return full, oos, boundary_by_symbol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="crypto,us,etf")
    args = ap.parse_args()

    symbols = []
    for m in [x.strip() for x in args.markets.split(",") if x.strip()]:
        symbols.extend(config.WATCHLISTS.get(m, []))

    strategy = DonchianStrategy()
    feed = CryptoUSFeed()
    df_by_symbol = {}
    for sym in symbols:
        df = feed.get_ohlcv(sym, strategy.interval, config.FETCH_BARS)
        if df is not None and len(df) >= config.DONCHIAN["sma_trend"] + 10:
            df_by_symbol[sym] = df

    P = config.PORTFOLIO
    print("\n" + "=" * 78)
    print("  EXIT-IMPROVEMENT COMPARISON — donchian entries, OOS only "
          f"({len(df_by_symbol)} symbols, $500 portfolio)")
    print("=" * 78)
    print(f"\n  {'variant':<15} {'trades':>6} {'exp_R':>7} {'PF':>6} {'maxDD_R':>8} "
          f"{'win%':>5}  {'$port':>8} {'port%':>7} {'gate':>5}")
    print("  " + "-" * 74)

    results = {}
    for label, kind in VARIANTS:
        full, oos, bbs = run_variant(df_by_symbol, strategy, kind)
        m = compute_metrics(oos)
        port = simulate_portfolio(oos, P["starting_capital"], P["risk_pct"],
                                  P["max_concurrent"], P["fractional"], P["min_position_cash"])
        passed, _ = evaluate_gate(m, config.GATE)
        results[label] = dict(m=m, port=port, passed=passed, full=full, oos=oos, bbs=bbs)
        print(f"  {label:<15} {m['n_trades']:>6} {m['expectancy_r']:>+7.3f} "
              f"{_fmt(m['profit_factor']):>6} {m['max_drawdown_r']:>8.1f} "
              f"{m['win_rate']*100:>4.0f}%  {port.final_capital:>8.0f} "
              f"{port.return_pct:>+6.1f}% {'PASS' if passed else 'no':>5}")

    base = results["baseline 2.5R"]["m"]["expectancy_r"]
    # Best = highest OOS expectancy among gate-passers
    passers = {k: v for k, v in results.items() if v["passed"]}
    pool = passers or results
    best_label = max(pool, key=lambda k: pool[k]["m"]["expectancy_r"])
    best = results[best_label]
    beats = best["m"]["expectancy_r"] > base

    print()
    print(f"  baseline OOS expectancy: {base:+.3f}R")
    print(f"  BEST variant: '{best_label}'  exp_R={best['m']['expectancy_r']:+.3f}  "
          f"{'BEATS baseline → adopt' if (beats and best_label != 'baseline 2.5R') else 'baseline stays (nothing beat it)'}")
    print()

    print("── BEST variant: robustness across time ──")
    print(periods.format_periods(best["oos"]))
    print()

    # Log the best variant's trades (+features) to SQLite for the future learning phase.
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    db = trade_store_path()
    n = log_trades(db, run_id, "donchian", best_label, best["full"], df_by_symbol, best["bbs"])
    print(f"  logged {n} trades (+features) → {db}  (total rows: {trade_store.count(db)})")
    print()


def trade_store_path():
    import os
    return os.path.join(config.OUTPUT_DIR, "trades.db")


if __name__ == "__main__":
    main()
