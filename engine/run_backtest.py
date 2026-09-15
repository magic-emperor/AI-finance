"""
run_backtest.py — Phase-1 entry point.

Fetches data for the chosen markets, runs the chosen strategy through the honest
simulator, splits train/OOS per symbol, and prints the OOS scorecard + Go/No-Go
verdict + a dollar ledger. Reports OOS as the truth.

Usage:
    python -X utf8 -m engine.run_backtest --strategy breakout --markets crypto,us
    python -X utf8 -m engine.run_backtest --strategy vwap_reversion --markets us
    python -X utf8 -m engine.run_backtest --force-refresh
"""
from __future__ import annotations

import os
import sys
import argparse
import logging
from typing import List

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.data.sources import classify_symbol
from engine.strategy.breakout import BreakoutStrategy
from engine.strategy.vwap_reversion import VwapReversionStrategy
from engine.strategy.opening_range import OpeningRangeStrategy
from engine.strategy.donchian import DonchianStrategy
from engine.backtest.simulator import simulate, CompletedTrade
from engine.backtest import walk_forward as wf
from engine.backtest.metrics import compute_metrics
from engine.backtest.diagnostics import diagnose
from engine.backtest import periods
from engine.report import scorecard
from engine.report.ledger import simulate_dollars
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("engine.run_backtest")

STRATEGIES = {
    "breakout": BreakoutStrategy,
    "vwap_reversion": VwapReversionStrategy,
    "opening_range": OpeningRangeStrategy,
    "donchian": DonchianStrategy,
}


def collect_trades(symbols: List[str], strategy, max_hold_bars: int, feed, interval: str,
                   start: str = None, end: str = None):
    train_all, oos_all = [], []
    per_symbol = {}
    s0 = pd.Timestamp(start, tz="UTC") if start else None
    s1 = pd.Timestamp(end, tz="UTC") if end else None
    for sym in symbols:
        df = feed.get_ohlcv(sym, interval, config.FETCH_BARS)
        if df is None or len(df) < 200:
            log.warning("skip %s (no/insufficient data)", sym)
            continue
        if s0 is not None:
            df = df[df.index >= s0]
        if s1 is not None:
            df = df[df.index < s1]
        if len(df) < 200:
            log.warning("skip %s (insufficient data in window)", sym)
            continue
        costs = config.cost_for(sym)
        trades = simulate(df, strategy, sym, costs, max_hold_bars)
        boundary = wf.oos_boundary_time(df, config.OOS_FRACTION)
        train, oos = wf.split_trades(trades, boundary)
        per_symbol[sym] = {"all": len(trades), "oos": compute_metrics(oos)}
        train_all.extend(train)
        oos_all.extend(oos)
        log.info("%s: %d trades (%d OOS), bars=%d, OOS from %s",
                 sym, len(trades), len(oos), len(df), boundary.date())
    train_all.sort(key=lambda t: t.signal_time)
    oos_all.sort(key=lambda t: t.signal_time)
    return train_all, oos_all, per_symbol


def class_metrics(trades: List[CompletedTrade], cls: str):
    sub = [t for t in trades if classify_symbol(t.symbol) == cls
           or (cls == "us_eq" and classify_symbol(t.symbol) in ("unknown", "index"))]
    return compute_metrics(sub)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="breakout", choices=list(STRATEGIES.keys()))
    ap.add_argument("--markets", default="crypto,us")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD: only test from this date")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD: only test up to this date")
    ap.add_argument("--force-refresh", action="store_true")
    args = ap.parse_args()

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    symbols = []
    for m in markets:
        symbols.extend(config.WATCHLISTS.get(m, []))
    if not symbols:
        print("No symbols for markets:", markets)
        sys.exit(1)

    StratCls = STRATEGIES[args.strategy]
    strategy = StratCls()
    max_hold = strategy.p["max_hold_bars"]
    interval = getattr(strategy, "interval", config.INTERVAL)
    feed = CryptoUSFeed(force_refresh=args.force_refresh)

    print("\n" + "=" * 64)
    print(f"  HONEST BACKTEST — strategy={args.strategy}  markets={markets}")
    print(f"  interval={interval}  costs=symmetric  OOS={int(config.OOS_FRACTION*100)}%")
    print("=" * 64 + "\n")

    train_all, oos_all, per_symbol = collect_trades(symbols, strategy, max_hold, feed, interval,
                                                    start=args.start, end=args.end)

    print()
    print(scorecard.format_metrics("TRAIN (reference only)", compute_metrics(train_all)))
    print()
    oos_m = compute_metrics(oos_all)
    print(scorecard.format_metrics("OUT-OF-SAMPLE (the truth)", oos_m))
    print()
    print(diagnose(oos_all)["report"])
    print()

    # Per asset class (the gate wants the edge to hold in BOTH)
    crypto_m = class_metrics(oos_all, "crypto")
    us_m = class_metrics(oos_all, "us_eq")
    print(scorecard.format_metrics("OOS — crypto", crypto_m))
    print()
    print(scorecard.format_metrics("OOS — US equities", us_m))
    print()

    # Per symbol summary
    print("── OOS per-symbol ──")
    for sym, d in per_symbol.items():
        mm = d["oos"]
        print(f"  {sym:<10} trades={mm['n_trades']:<3} "
              f"WR={mm['win_rate']*100:4.0f}%  exp_R={mm['expectancy_r']:+.3f}  "
              f"PF={scorecard._fmt(mm['profit_factor'])}")
    print()

    passed, checks = scorecard.evaluate_gate(oos_m, config.GATE)
    print(scorecard.format_gate(passed, checks))
    print()
    print(scorecard.format_info(oos_m, config.GATE_INFO))
    print()

    # Robustness across time: is the edge consistent, or one lucky stretch?
    all_tr = sorted(train_all + oos_all, key=lambda t: t.signal_time)
    print("── ROBUSTNESS across time (full history in thirds) ──")
    for lab, part in zip(["early ", "middle", "recent"], wf.thirds(all_tr)):
        pm = compute_metrics(part)
        span = (f"{part[0].signal_time.date()}..{part[-1].signal_time.date()}" if part else "-")
        print(f"  {lab} n={pm['n_trades']:<4} exp_R={pm['expectancy_r']:+.3f}  "
              f"PF={scorecard._fmt(pm['profit_factor'])}  [{span}]")
    print()
    if all_tr:
        print(periods.format_periods(all_tr))
        print()

    # Dollar ledger on OOS trades
    led = simulate_dollars(
        oos_all, config.RISK["starting_capital"], config.RISK["risk_pct"],
        config.RISK["max_trades_per_day"], config.RISK["daily_loss_limit_pct"],
    )
    print("── DOLLAR LEDGER (OOS, %.0f%% risk/trade, start $%.0f) ──"
          % (config.RISK["risk_pct"] * 100, config.RISK["starting_capital"]))
    print(f"  final capital : ${led['final_capital']}   ({led['return_pct']:+.2f}%)")
    print(f"  trades taken  : {led['trades_taken']}  (skipped by limits: {led['trades_skipped_by_limits']})")
    print(f"  win rate      : {led['win_rate_pct']}%   W={led['wins']} L={led['losses']}")
    print(f"  max drawdown  : ${led['max_drawdown_$']}")
    print()

    # Save equity curve
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    eq_path = os.path.join(config.OUTPUT_DIR, f"equity_{args.strategy}_oos.csv")
    scorecard.save_equity_curve(oos_m["equity_curve"], eq_path)
    print(f"  equity curve saved → {eq_path}")
    print()


if __name__ == "__main__":
    main()
