"""
run_portfolio.py — Phase-1.5 feasibility: can the validated edge actually be
traded on $500 with fractional shares?

Prints:
  1. Full-basket portfolio result (realistic: capped concurrency, conviction-ranked,
     cash-limited) at $500.
  2. Sensitivity to max_concurrent (3 / 5 / 8) — how many slots you really need.
  3. CHERRY-PICK comparison: every single symbol alone vs a small 4-name basket vs
     the full basket — shows the variance cost of trading too few names.
  4. RECENT 12-month window result.
  5. Per-year + rolling 6-month robustness (every year, many overlapping windows).

Usage:
    python -X utf8 -m engine.run_portfolio
    python -X utf8 -m engine.run_portfolio --start 2024-01-01 --end 2025-01-01
"""
from __future__ import annotations

import argparse
import logging
import pandas as pd

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.strategy.donchian import DonchianStrategy
from engine.backtest.simulator import simulate
from engine.backtest.metrics import compute_metrics
from engine.backtest.portfolio import simulate_portfolio
from engine.backtest import periods
from engine.report.scorecard import _fmt

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _to_utc(s):
    return pd.Timestamp(s, tz="UTC") if s else None


def gather(symbols, strategy, interval, feed, start=None, end=None):
    by_sym, all_tr = {}, []
    s0, s1 = _to_utc(start), _to_utc(end)
    for sym in symbols:
        df = feed.get_ohlcv(sym, interval, config.FETCH_BARS)
        if df is None or len(df) < config.DONCHIAN["sma_trend"] + 10:
            continue
        tr = simulate(df, strategy, sym, config.cost_for(sym), strategy.p["max_hold_bars"])
        if s0:
            tr = [t for t in tr if t.signal_time >= s0]
        if s1:
            tr = [t for t in tr if t.signal_time < s1]
        if tr:
            by_sym[sym] = tr
            all_tr.extend(tr)
    all_tr.sort(key=lambda t: t.signal_time)
    return by_sym, all_tr


def pf_line(label, res):
    return (f"  {label:<16} ${res.final_capital:<9} ({res.return_pct:+7.1f}%)  "
            f"CAGR {res.cagr_pct:+6.1f}%  maxDD {res.max_drawdown_pct:6.1f}%  "
            f"took {res.taken}/{res.n_candidates}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="crypto,us,etf")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-concurrent", type=int, default=None)
    args = ap.parse_args()

    symbols = []
    for m in [x.strip() for x in args.markets.split(",") if x.strip()]:
        symbols.extend(config.WATCHLISTS.get(m, []))

    strategy = DonchianStrategy()
    feed = CryptoUSFeed()
    P = config.PORTFOLIO

    print("\n" + "=" * 70)
    print(f"  PORTFOLIO FEASIBILITY — donchian  ${P['starting_capital']:.0f}  "
          f"fractional={P['fractional']}  risk={P['risk_pct']*100:.0f}%/trade")
    if args.start or args.end:
        print(f"  window: {args.start or 'start'} .. {args.end or 'end'}")
    print("=" * 70)

    by_sym, all_tr = gather(symbols, strategy, strategy.interval, feed, args.start, args.end)
    if not all_tr:
        print("  No trades in range.")
        return

    # 1. Full basket
    mc = args.max_concurrent or P["max_concurrent"]
    full = simulate_portfolio(all_tr, P["starting_capital"], P["risk_pct"], mc,
                              P["fractional"], P["min_position_cash"])
    m = compute_metrics(all_tr)
    print(f"\n── FULL BASKET ({len(by_sym)} symbols, max_concurrent={mc}) ──")
    print(pf_line("full basket", full))
    print(f"  max positions actually held at once: {full.max_concurrent_used}")
    print(f"  skipped — no slot: {full.skipped_slot},  no cash: {full.skipped_cash}")
    print(f"  (strategy edge, R-based: exp_R={m['expectancy_r']:+.3f}  "
          f"PF={_fmt(m['profit_factor'])}  trades={m['n_trades']})")

    # 2. Sensitivity to slot count
    print("\n── SENSITIVITY to max_concurrent ──")
    for cap in (3, 5, 8):
        r = simulate_portfolio(all_tr, P["starting_capital"], P["risk_pct"], cap,
                               P["fractional"], P["min_position_cash"])
        print(pf_line(f"max={cap}", r) + f"  (peak held {r.max_concurrent_used})")

    # 3. Cherry-pick comparison
    print("\n── CHERRY-PICK vs BASKET (why trading too few names is risky) ──")
    singles = []
    for sym in sorted(by_sym):
        r = simulate_portfolio(by_sym[sym], P["starting_capital"], P["risk_pct"], 1,
                               P["fractional"], P["min_position_cash"])
        singles.append((sym, r.return_pct))
        print(pf_line(sym, r))
    if singles:
        rets = [r for _, r in singles]
        best = max(singles, key=lambda x: x[1]); worst = min(singles, key=lambda x: x[1])
        print(f"  single-name spread: best {best[0]} {best[1]:+.1f}%  ..  "
              f"worst {worst[0]} {worst[1]:+.1f}%  (you can't know which in advance)")
    small_syms = [s for s in config.SMALL_BASKET if s in by_sym]
    small_tr = sorted([t for s in small_syms for t in by_sym[s]], key=lambda t: t.signal_time)
    if small_tr:
        r = simulate_portfolio(small_tr, P["starting_capital"], P["risk_pct"], mc,
                               P["fractional"], P["min_position_cash"])
        print(pf_line(f"small basket {small_syms}", r))
    print(pf_line("FULL basket", full))

    # 4. Recent 12 months
    last = all_tr[-1].signal_time
    recent = [t for t in all_tr if t.signal_time >= last - pd.Timedelta(days=365)]
    if recent:
        r = simulate_portfolio(recent, P["starting_capital"], P["risk_pct"], mc,
                               P["fractional"], P["min_position_cash"])
        rm = compute_metrics(recent)
        print(f"\n── RECENT 12 MONTHS ({recent[0].signal_time.date()}..{last.date()}) ──")
        print(pf_line("last 12m", r))
        print(f"  (edge R-based: exp_R={rm['expectancy_r']:+.3f}  PF={_fmt(rm['profit_factor'])}  "
              f"trades={rm['n_trades']})")

    # 5. Granular robustness across every year + rolling 6-month windows
    print("\n" + periods.format_periods(all_tr))
    print()


if __name__ == "__main__":
    main()
