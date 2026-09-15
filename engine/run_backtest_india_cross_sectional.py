"""
run_backtest_india_cross_sectional.py — the actual registered India cross-
sectional momentum test (registry.py id=4), not a smoke test.

Universe: 8 liquid Nifty50 large-caps (already tested with Donchian trend --
REJECTED, engine.run_backtest_nse) + 50 Nifty Midcap150 names, sourced from
the official index list and backfilled 2026-09-15 specifically to give this
test a genuine illiquid-tilt universe (see market_agent/scripts/rebuild_db.py
NSE_MIDCAP_CANDIDATES). Turnover is MEASURED from the fetched data (average
daily Close*Volume, trailing ~2y), not assumed from index membership --
tercile split by that measured value, not by market-cap label.

Params frozen before this run (config.INDIA_CS): formation=189 trading days
(~9mo, inside the literature's 6-12mo window), hold=42 (~2mo, inside 1-3mo),
top_frac=0.30 (~top 17 of 58). Long-only: the research is about buying past
winners, and NSE short-selling/borrow constraints make a symmetric long-short
implementation a different, harder question this test doesn't need to answer
to check the core claim.

Usage:
    python -X utf8 -m engine.run_backtest_india_cross_sectional
"""
from __future__ import annotations

import pandas as pd

from engine import config
from engine.data.feed import MarketAgentPostgresFeed
from engine.backtest.cross_sectional import (
    simulate_cross_sectional, compute_cs_metrics, portfolio_equity_curve, max_drawdown_pct,
)
from engine.run_backtest_nse import NSE_SYMBOLS as LARGECAP_SYMBOLS
from market_agent.scripts.rebuild_db import NSE_MIDCAP_CANDIDATES as MIDCAP_SYMBOLS

FORMATION_BARS = 189
HOLD_BARS = 42
TOP_FRAC = 0.30
COST_BPS_ROUNDTRIP = 14.0  # nse_eq: 3bps fee + 5bps slip + 6bps STT/stamp/GST, both sides


def measure_turnover(universe: dict) -> pd.DataFrame:
    rows = []
    for sym, df in universe.items():
        recent = df.tail(500)
        rows.append({"symbol": sym, "turnover": float((recent["Close"] * recent["Volume"]).mean())})
    return pd.DataFrame(rows).sort_values("turnover").reset_index(drop=True)


def main():
    feed = MarketAgentPostgresFeed()
    all_symbols = LARGECAP_SYMBOLS + MIDCAP_SYMBOLS
    universe = {}
    for sym in all_symbols:
        df = feed.get_ohlcv(sym, "1d", 5000)
        if df is not None and len(df) >= FORMATION_BARS + HOLD_BARS + 10:
            universe[sym] = df
        else:
            print(f"  skip {sym} (no/insufficient data)")

    turnover = measure_turnover(universe)
    n = len(turnover)
    low_tercile = set(turnover["symbol"].iloc[: n // 3])
    high_tercile = set(turnover["symbol"].iloc[-(n // 3):])

    print("\n" + "=" * 64)
    print(f"  INDIA CROSS-SECTIONAL MOMENTUM (registered hypothesis, n={n} symbols)")
    print(f"  formation={FORMATION_BARS}d  hold={HOLD_BARS}d  top_frac={TOP_FRAC}")
    print("=" * 64 + "\n")

    trades = simulate_cross_sectional(
        universe, FORMATION_BARS, HOLD_BARS, top_frac=TOP_FRAC, bottom_frac=0.0,
        cost_bps_roundtrip=COST_BPS_ROUNDTRIP,
    )
    m = compute_cs_metrics(trades)
    print("── ALL (full 58-symbol universe) ──")
    print(f"  n_trades      : {m['n_trades']}")
    print(f"  win_rate      : {m['win_rate']*100:.1f}%")
    print(f"  expectancy    : {m['expectancy_pct']:+.2f}% per position")
    print(f"  profit_factor : {m['profit_factor']:.2f}" if m["profit_factor"] is not None else "  profit_factor : n/a")

    curve = portfolio_equity_curve(trades, starting_capital=100.0)
    print(f"  portfolio (equal-weight, compounded): start=100.0 -> end={curve[-1][1]:.2f}  "
          f"max_dd={max_drawdown_pct(curve):+.1f}%  ({len(curve)-1} rebalances)")

    # The actual claim under test: does the edge concentrate in low-turnover names?
    low_trades = [t for t in trades if t.symbol in low_tercile]
    high_trades = [t for t in trades if t.symbol in high_tercile]
    ml, mh = compute_cs_metrics(low_trades), compute_cs_metrics(high_trades)

    print("\n── LOW-turnover tercile (illiquid, the literature's claimed edge) ──")
    print(f"  n_trades={ml['n_trades']}  win_rate={ml['win_rate']*100:.1f}%  "
          f"expectancy={ml['expectancy_pct']:+.2f}%  "
          f"PF={ml['profit_factor']:.2f}" if ml["n_trades"] else "  no trades")
    print("\n── HIGH-turnover tercile (liquid large-caps, the literature's claimed weak spot) ──")
    print(f"  n_trades={mh['n_trades']}  win_rate={mh['win_rate']*100:.1f}%  "
          f"expectancy={mh['expectancy_pct']:+.2f}%  "
          f"PF={mh['profit_factor']:.2f}" if mh["n_trades"] else "  no trades")

    print("\n── per-symbol pick frequency (top 15 by n picks) ──")
    from collections import Counter
    picks = Counter(t.symbol for t in trades)
    for sym, cnt in picks.most_common(15):
        sub = [t for t in trades if t.symbol == sym]
        avg = sum(t.net_return for t in sub) / len(sub) * 100
        tier = "LOW-turnover" if sym in low_tercile else ("HIGH-turnover" if sym in high_tercile else "mid")
        print(f"  {sym:<16} picked={cnt:<3} avg_return={avg:+.2f}%  [{tier}]")

    print("\n" + "=" * 64)
    print(f"  turnover range measured: {turnover['turnover'].min():,.0f} -> "
          f"{turnover['turnover'].max():,.0f} INR/day  "
          f"(low tercile n={len(low_tercile)}, high tercile n={len(high_tercile)})")
    print("=" * 64)


if __name__ == "__main__":
    main()
