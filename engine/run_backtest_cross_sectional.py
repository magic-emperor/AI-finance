"""
run_backtest_cross_sectional.py — MECHANICS SMOKE TEST for the cross-sectional
ranking engine, not a validated finding.

Deliberately NOT registered as a hypothesis trial in registry.py. Why: the
India/crypto/US research this engine exists to serve (6-12mo formation, 1-3mo
hold, ranked deciles) needs a genuinely broad universe -- tens to hundreds of
symbols -- for "top decile" to mean anything. The only universe already cached
here is config.WATCHLISTS["us"] (8 names + SPY/QQQ), which is a real data-scope
gap, not a design choice: a wider universe (a broader US list, a wider NSE list
weighted toward the illiquid names the India research says the alpha lives in,
and more than 2 crypto symbols) still needs to be sourced before this engine can
run the ACTUAL registered hypotheses.

This script exists only to show the engine mechanically runs end-to-end on real
data (not just the synthetic honesty tests) and to print what a real run's report
will look like. Treat every number below as illustrative, not evidence.

Usage:
    python -X utf8 -m engine.run_backtest_cross_sectional
"""
from __future__ import annotations

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.backtest.cross_sectional import (
    simulate_cross_sectional, compute_cs_metrics, portfolio_equity_curve, max_drawdown_pct,
)

# Formation/hold in TRADING DAYS -- loosely inside the 6-12mo formation / 1-3mo hold
# window the momentum literature (Jegadeesh & Titman) validates. NOT tuned; a first,
# literature-anchored guess, same discipline as gold_meanrev's frozen params.
FORMATION_BARS = 126   # ~6 months of trading days
HOLD_BARS = 42         # ~2 months of trading days
TOP_FRAC = 0.34        # top ~1/3 of an 8-name universe = top 2-3 names
COST_BPS_ROUNDTRIP = 6.0  # us_eq: 0 fee + 3bps slippage, both sides = 6bps


def main():
    symbols = config.WATCHLISTS["us"] + ["SPY", "QQQ"]
    symbols = list(dict.fromkeys(symbols))  # de-dupe if SPY/QQQ already present
    feed = CryptoUSFeed()

    universe = {}
    for sym in symbols:
        df = feed.get_ohlcv(sym, "1d", config.FETCH_BARS)
        if df is not None and len(df) >= FORMATION_BARS + HOLD_BARS + 10:
            universe[sym] = df
        else:
            print(f"  skip {sym} (no/insufficient daily data)")

    print("\n" + "=" * 64)
    print("  MECHANICS SMOKE TEST — cross-sectional momentum "
          f"(NOT a validated finding, n={len(universe)} symbols)")
    print(f"  formation={FORMATION_BARS}d  hold={HOLD_BARS}d  top_frac={TOP_FRAC}")
    print("=" * 64 + "\n")

    trades = simulate_cross_sectional(
        universe, FORMATION_BARS, HOLD_BARS, top_frac=TOP_FRAC, bottom_frac=0.0,
        cost_bps_roundtrip=COST_BPS_ROUNDTRIP,
    )
    m = compute_cs_metrics(trades)
    print(f"  n_trades      : {m['n_trades']}")
    print(f"  win_rate      : {m['win_rate']*100:.1f}%")
    print(f"  expectancy    : {m['expectancy_pct']:+.2f}% per position")
    print(f"  profit_factor : {m['profit_factor']:.2f}" if m['profit_factor'] not in (None,)
          else "  profit_factor : n/a")
    print(f"  avg_win       : {m['avg_win_pct']:+.2f}%")
    print(f"  avg_loss      : {m['avg_loss_pct']:+.2f}%")
    print(f"  total_return  : {m['total_return_pct']:+.2f}% (sum of position returns, not compounded)")

    curve = portfolio_equity_curve(trades, starting_capital=100.0)
    print(f"\n  portfolio (equal-weight, compounded): "
          f"start=100.0 -> end={curve[-1][1]:.2f}  "
          f"max_dd={max_drawdown_pct(curve):+.1f}%  ({len(curve)-1} rebalances)")

    print("\n  -- per-symbol pick frequency --")
    from collections import Counter
    picks = Counter(t.symbol for t in trades)
    for sym, n in picks.most_common():
        sub = [t for t in trades if t.symbol == sym]
        avg = sum(t.net_return for t in sub) / len(sub) * 100
        print(f"  {sym:<8} picked={n:<3} avg_return={avg:+.2f}%")

    print("\n" + "=" * 64)
    print("  REMINDER: n=%d symbols is too small for real decile cross-sectional\n"
          "  momentum. This is a mechanics check only -- see this file's docstring." % len(universe))
    print("=" * 64)


if __name__ == "__main__":
    main()
