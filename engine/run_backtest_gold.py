"""
run_backtest_gold.py — gold long-horizon mean-reversion, on GC=F (gold futures)
and GLD (SPDR gold ETF) as a corroborating second vehicle for the same commodity.

Reuses the SAME simulator, walk-forward split, and metrics as run_backtest.py —
only the strategy differs. Params are FROZEN in config.GOLD_MEANREV before this
runs (see that block's comment and engine/strategy/gold_meanrev.py's docstring
for the literature grounding). The hypothesis was registered in registry.py
BEFORE this script was run.

Usage:
    python -X utf8 -m engine.run_backtest_gold
"""
from __future__ import annotations

import logging
import pandas as pd

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.strategy.gold_meanrev import GoldMeanRevStrategy
from engine.backtest.metrics import compute_metrics
from engine.backtest.diagnostics import diagnose
from engine.backtest import walk_forward as wf
from engine.report import scorecard
from engine.run_backtest import collect_trades

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("engine.run_backtest_gold")

GOLD_SYMBOLS = ["GC=F", "GLD"]


def year_breakdown(trades, label: str):
    if not trades:
        print(f"  {label}: no trades")
        return
    df = pd.DataFrame([{"year": t.signal_time.year, "r": t.R} for t in trades])
    g = df.groupby("year")["r"].agg(n="size", total="sum", mean="mean").round(3)
    print(f"\n  {label} -- per year (ALL trades, train+oos combined):")
    print(g.to_string())


def main():
    strategy = GoldMeanRevStrategy()
    max_hold = strategy.p["max_hold_bars"]
    interval = strategy.interval
    feed = CryptoUSFeed()

    print("\n" + "=" * 64)
    print("  HONEST BACKTEST — gold_meanrev on GC=F + GLD")
    print(f"  interval={interval}  symbols={GOLD_SYMBOLS}")
    print("=" * 64 + "\n")

    train_all, oos_all, per_symbol = collect_trades(
        GOLD_SYMBOLS, strategy, max_hold, feed, interval
    )
    all_trades = sorted(train_all + oos_all, key=lambda t: t.signal_time)

    print(scorecard.format_metrics("TRAIN (reference only)", compute_metrics(train_all)))
    print()
    oos_m = compute_metrics(oos_all)
    print(scorecard.format_metrics("OUT-OF-SAMPLE (the truth)", oos_m))
    print()
    print(diagnose(oos_all)["report"])

    year_breakdown(all_trades, "ALL (train+oos)")
    year_breakdown(oos_all, "OOS only")

    print("\n  -- concentration check (top-10-trade dependence) --")
    if oos_all:
        rs = sorted([t.R for t in oos_all], reverse=True)
        top10 = sum(rs[:10])
        total = sum(rs)
        print(f"  OOS total={total:+.1f}R  top-10-trades={top10:+.1f}R "
              f"({top10/total*100 if total else 0:.0f}% of total)")
        remainder = rs[10:]
        if remainder:
            print(f"  without top 10: {sum(remainder):+.1f}R over {len(remainder)} trades "
                  f"= {sum(remainder)/len(remainder):+.4f}R/trade")

    print("\n  -- OOS per-symbol --")
    for sym, d in per_symbol.items():
        mm = d["oos"]
        print(f"  {sym:<8} trades={mm['n_trades']:<3} "
              f"WR={mm['win_rate']*100:4.0f}%  exp_R={mm['expectancy_r']:+.3f}  "
              f"PF={scorecard._fmt(mm['profit_factor'])}  total_R={mm['total_r']:+.1f}")

    print("\n  -- robustness across time (thirds) --")
    for lab, part in zip(["early ", "middle", "recent"], wf.thirds(all_trades)):
        pm = compute_metrics(part)
        span = (f"{part[0].signal_time.date()}..{part[-1].signal_time.date()}" if part else "-")
        print(f"  {lab} n={pm['n_trades']:<4} exp_R={pm['expectancy_r']:+.3f}  "
              f"PF={scorecard._fmt(pm['profit_factor'])}  [{span}]")

    passed, checks = scorecard.evaluate_gate(oos_m, config.GATE)
    print()
    print(scorecard.format_gate(passed, checks))
    print("\n" + "=" * 64)


if __name__ == "__main__":
    main()
