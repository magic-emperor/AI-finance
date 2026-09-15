"""
run_quality.py — Phase 1.8: can we improve trade quality?

Side-by-side OOS comparison:
  - trend baseline      (Donchian + partial-trail)
  - trend + filters     (volume / momentum / 2-close confirmation, and combos)
  - mean-reversion       (Connors RSI-2 — naturally high win rate, fatter tail)

The deciders are EXPECTANCY and TAIL (worst single trade), NOT win rate. Win rate
is shown so you can SEE the tradeoff, but we adopt only what raises expectancy
without worsening the tail. If nothing beats the baseline, the baseline stays.

Usage: python -X utf8 -m engine.run_quality  [--markets crypto,us,etf]
"""
from __future__ import annotations

import argparse
import logging

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.strategy.donchian import DonchianStrategy
from engine.strategy.donchian_filtered import DonchianFiltered
from engine.strategy.meanrev import MeanRevStrategy
from engine.backtest.simulator import simulate
from engine.backtest.simulator_v2 import simulate_v2
from engine.backtest.metrics import compute_metrics
from engine.backtest import walk_forward as wf, periods
from engine.report.scorecard import _fmt

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def collect(df_by_symbol, strat, kind):
    full, bbs = [], {}
    for sym, df in df_by_symbol.items():
        if kind == "v2":
            tr = simulate_v2(df, strat, sym, config.cost_for(sym), "partial_trail", config.EXIT_V2)
        else:  # mean-reversion via the baseline fixed stop+target simulator
            tr = simulate(df, strat, sym, config.cost_for(sym), config.MEANREV["max_hold_bars"])
        full.extend(tr)
        bbs[sym] = wf.oos_boundary_time(df, config.OOS_FRACTION)
    oos = [t for t in full if t.signal_time >= bbs[t.symbol]]
    oos.sort(key=lambda t: t.signal_time)
    return oos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="crypto,us,etf")
    args = ap.parse_args()

    symbols = []
    for m in [x.strip() for x in args.markets.split(",") if x.strip()]:
        symbols.extend(config.WATCHLISTS.get(m, []))

    feed = CryptoUSFeed()
    df_by_symbol = {}
    for sym in symbols:
        df = feed.get_ohlcv(sym, config.DONCHIAN["interval"], config.FETCH_BARS)
        if df is not None and len(df) >= config.DONCHIAN["sma_trend"] + 10:
            df_by_symbol[sym] = df

    variants = [
        ("trend baseline",  DonchianStrategy({"long_only": True}), "v2"),
        ("trend +volume",   DonchianFiltered(use_volume=True), "v2"),
        ("trend +momentum", DonchianFiltered(use_momentum=True), "v2"),
        ("trend +2close",   DonchianFiltered(use_two_close=True), "v2"),
        ("trend +vol+mom",  DonchianFiltered(use_volume=True, use_momentum=True), "v2"),
        ("trend +all3",     DonchianFiltered(use_volume=True, use_momentum=True, use_two_close=True), "v2"),
        ("mean-reversion",  MeanRevStrategy(), "mr"),
    ]

    print("\n" + "=" * 84)
    print(f"  TRADE-QUALITY COMPARISON — OOS  ({len(df_by_symbol)} instruments: {args.markets})")
    print("  deciders = EXPECTANCY + worst-trade (TAIL); win rate shown for context only")
    print("=" * 84)
    print(f"\n  {'variant':<18} {'trades':>6} {'win%':>5} {'exp_R':>7} {'PF':>6} "
          f"{'maxDD_R':>8} {'worstR':>7}")
    print("  " + "-" * 78)

    results = {}
    for label, strat, kind in variants:
        oos = collect(df_by_symbol, strat, kind)
        m = compute_metrics(oos)
        worst = min((t.R for t in oos), default=0.0)
        results[label] = (m, worst, oos)
        print(f"  {label:<18} {m['n_trades']:>6} {m['win_rate']*100:>4.0f}% "
              f"{m['expectancy_r']:>+7.3f} {_fmt(m['profit_factor']):>6} "
              f"{m['max_drawdown_r']:>8.1f} {worst:>+7.2f}")

    base_exp = results["trend baseline"][0]["expectancy_r"]
    base_worst = results["trend baseline"][1]
    print(f"\n  baseline: expectancy {base_exp:+.3f}R, worst trade {base_worst:+.2f}R")

    # A variant "wins" only if it beats baseline expectancy AND doesn't worsen the tail.
    winners = [(lbl, m["expectancy_r"], worst) for lbl, (m, worst, _) in results.items()
               if lbl != "trend baseline" and m["expectancy_r"] > base_exp and worst >= base_worst - 0.01]
    print("\n── VERDICT (expectancy-up AND tail-not-worse) ──")
    if winners:
        winners.sort(key=lambda x: -x[1])
        for lbl, exp, worst in winners:
            print(f"  {lbl}: exp {exp:+.3f}R (vs {base_exp:+.3f}), worst {worst:+.2f}R → improvement")
        best = winners[0][0]
    else:
        print("  No variant beat the baseline on expectancy without worsening the tail.")
        print("  → Honest outcome: keep the trend baseline.")
        best = "trend baseline"

    print(f"\n── robustness of '{best}' across years ──")
    print(periods.format_periods(results[best][2]))
    print()


if __name__ == "__main__":
    main()
