"""
run_backtest_nse.py — Donchian trend strategy on NSE equities, real Indian costs.

Reuses the SAME strategy code, simulator, walk-forward split, and metrics as
run_backtest.py -- only the data feed differs (MarketAgentPostgresFeed instead
of CryptoUSFeed), per the DataFeed abstraction's own stated purpose ("an
IndianFeed implements the SAME interface and nothing downstream changes").

Data: market_agent's validated Postgres store, daily bars, 2016-2026 (cross-
validated against ICICI's broker feed, 11 integrity gates passing).
Costs: config.COSTS["nse_eq"] -- 3bps fee + 5bps slippage + 6bps extra
(STT/stamp/GST), already wired via config.cost_for() -> classify_symbol()
recognizing the .NS suffix.

This does NOT change config.DONCHIAN's frozen parameters (55-day channel,
200-day SMA, 2R stop, 2.5R target) -- reusing the exact parameters already
committed to, not re-tuning them for NSE, which would be exactly the kind
of post-hoc curve-fit this whole project has been trying to eliminate.

Usage:
    python -X utf8 -m engine.run_backtest_nse
"""
from __future__ import annotations

import logging
import pandas as pd

from engine.data.feed import MarketAgentPostgresFeed
from engine.strategy.donchian import DonchianStrategy
from engine.backtest.metrics import compute_metrics
from engine.backtest.diagnostics import diagnose
from engine.report import scorecard
from engine.run_backtest import collect_trades

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("engine.run_backtest_nse")

NSE_SYMBOLS = [
    "ITC.NS", "HDFCBANK.NS", "RELIANCE.NS", "TATASTEEL.NS",
    "LT.NS", "M&M.NS", "ADANIENT.NS", "ADANIPORTS.NS",
]


def year_breakdown(trades, label: str):
    """
    Per-year breakdown regardless of train/oos bucket -- the walk-forward
    OOS boundary (most recent 40%) starts ~2022-09 on a 10y span, which
    would miss most of 2022's actual drawdown (Jan-Jun). This answers
    "does 2022 break it" directly, the way the 2026-09-14 US/crypto
    re-analysis did, rather than relying on where the OOS cut happens to
    fall.
    """
    if not trades:
        print(f"  {label}: no trades")
        return
    df = pd.DataFrame([{"year": t.signal_time.year, "r": t.R} for t in trades])
    g = df.groupby("year")["r"].agg(n="size", total="sum", mean="mean").round(3)
    print(f"\n  {label} -- per year (ALL trades, train+oos combined):")
    print(g.to_string())


def main():
    strategy = DonchianStrategy()
    max_hold = strategy.p["max_hold_bars"]
    interval = strategy.interval
    feed = MarketAgentPostgresFeed()

    print("\n" + "=" * 64)
    print("  HONEST BACKTEST — donchian on NSE (real Indian costs)")
    print(f"  interval={interval}  costs=nse_eq (3bps fee+5bps slip+6bps STT/stamp/GST)")
    print(f"  symbols={NSE_SYMBOLS}")
    print("=" * 64 + "\n")

    train_all, oos_all, per_symbol = collect_trades(
        NSE_SYMBOLS, strategy, max_hold, feed, interval
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
        remainder = [r for r in rs[10:]]
        if remainder:
            print(f"  without top 10: {sum(remainder):+.1f}R over {len(remainder)} trades "
                  f"= {sum(remainder)/len(remainder):+.4f}R/trade")

    print("\n  -- OOS per-symbol --")
    for sym, d in per_symbol.items():
        mm = d["oos"]
        print(f"  {sym:<16} trades={mm['n_trades']:<3} "
              f"WR={mm['win_rate']*100:4.0f}%  exp_R={mm['expectancy_r']:+.3f}  "
              f"PF={scorecard._fmt(mm['profit_factor'])}  total_R={mm['total_r']:+.1f}")

    print("\n" + "=" * 64)


if __name__ == "__main__":
    main()
