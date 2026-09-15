"""
run_shorts.py — Phase 1.7: does adding SHORTS (profit from downtrends) actually
help, on the instruments where it should (crypto, FX, commodities)?

Compares, on the chosen partial+trail exit, OOS:
  - long-only   (current production)
  - long+short  (symmetric — also short downtrend breakouts)

Plus: the SHORT-ONLY subset edge, per-instrument, and per-year (does the short
side earn its keep in down periods?). We adopt shorts only where long+short beats
long-only OOS. (Underlying only — no options; options are a Phase-3 execution
overlay for Indian equities.)

Usage: python -X utf8 -m engine.run_shorts  [--markets crypto,fx,commodity]
"""
from __future__ import annotations

import argparse
import logging

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.strategy.donchian import DonchianStrategy
from engine.backtest.simulator_v2 import simulate_v2
from engine.backtest.metrics import compute_metrics
from engine.backtest.portfolio import simulate_portfolio
from engine.backtest import walk_forward as wf, periods
from engine.report.scorecard import _fmt

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def collect(df_by_symbol, strategy):
    full, bbs = [], {}
    for sym, df in df_by_symbol.items():
        tr = simulate_v2(df, strategy, sym, config.cost_for(sym), "partial_trail", config.EXIT_V2)
        full.extend(tr)
        bbs[sym] = wf.oos_boundary_time(df, config.OOS_FRACTION)
    full.sort(key=lambda t: t.signal_time)
    oos = [t for t in full if t.signal_time >= bbs[t.symbol]]
    return full, oos


def line(label, m, port=None):
    s = (f"  {label:<22} trades={m['n_trades']:<4} exp_R={m['expectancy_r']:+.3f} "
         f"PF={_fmt(m['profit_factor']):>5} maxDD={m['max_drawdown_r']:>6.1f}R "
         f"win={m['win_rate']*100:>3.0f}%")
    if port is not None:
        s += f"  ${port.final_capital:>7.0f} ({port.return_pct:+.1f}%)"
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="crypto,fx,commodity")
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

    P = config.PORTFOLIO
    print("\n" + "=" * 76)
    print(f"  SHORT-SIDE TEST — donchian + partial_trail, OOS  "
          f"({len(df_by_symbol)} instruments: {args.markets})")
    print("=" * 76)

    lo_full, lo_oos = collect(df_by_symbol, DonchianStrategy({"long_only": True}))
    ls_full, ls_oos = collect(df_by_symbol, DonchianStrategy({"long_only": False}))

    def port(trades):
        return simulate_portfolio(trades, P["starting_capital"], P["risk_pct"],
                                  P["max_concurrent"], P["fractional"], P["min_position_cash"])

    print("\n── LONG-ONLY  vs  LONG+SHORT (OOS) ──")
    print(line("long-only", compute_metrics(lo_oos), port(lo_oos)))
    print(line("long+short", compute_metrics(ls_oos), port(ls_oos)))

    longs = [t for t in ls_oos if t.direction == "LONG"]
    shorts = [t for t in ls_oos if t.direction == "SHORT"]
    print("\n── inside long+short: each side on its own ──")
    print(line("  long trades", compute_metrics(longs)))
    print(line("  short trades", compute_metrics(shorts)))

    print("\n── long+short per instrument (OOS) ──")
    for sym in sorted(df_by_symbol):
        sub = [t for t in ls_oos if t.symbol == sym]
        sh = sum(1 for t in sub if t.direction == "SHORT")
        m = compute_metrics(sub)
        print(f"  {sym:<10} n={m['n_trades']:<3} ({sh} short)  exp_R={m['expectancy_r']:+.3f}  "
              f"PF={_fmt(m['profit_factor'])}")

    print("\n── long+short robustness across time ──")
    print(periods.format_periods(ls_oos))

    base = compute_metrics(lo_oos)["expectancy_r"]
    ls_exp = compute_metrics(ls_oos)["expectancy_r"]
    sh_exp = compute_metrics(shorts)["expectancy_r"] if shorts else 0.0
    print("\n── VERDICT ──")
    print(f"  long-only exp_R {base:+.3f}  vs  long+short exp_R {ls_exp:+.3f}  "
          f"→ {'ADD shorts (beats long-only)' if ls_exp > base else 'shorts do NOT help here'}")
    print(f"  short-only subset exp_R {sh_exp:+.3f} on {len(shorts)} trades "
          f"({'short side has its own edge' if sh_exp > 0 else 'short side weak on its own'})")
    print()


if __name__ == "__main__":
    main()
