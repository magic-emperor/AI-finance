"""
run_paper.py — Phase 2: daily PAPER-TRADING monitor (honest forward validation).

Divergence-free by construction: it replays the EXACT validated strategy
(long-only Donchian + partial-trail) through the latest data using the same
`simulator_v2` the backtest uses — there is no separate "live" exit logic to drift.

HONEST forward validation: paper starts FLAT on your start date (recorded on first
run). Forward equity counts ONLY trades entered on/after that date — so day 1 is
$500 flat, and the record grows as real forward signals fire. (Positions the
strategy entered BEFORE your start are shown as informational context, not counted.)

Run once a day after market close:
    python -X utf8 -m engine.run_paper            # daily snapshot (sets start date on first run)
    python -X utf8 -m engine.run_paper --no-log   # just look; don't set start / don't append
"""
from __future__ import annotations

import os
import csv
import json
import argparse
import logging
from datetime import datetime, timezone, date

from engine import config
from engine.data.feed import CryptoUSFeed
from engine.strategy.donchian import DonchianStrategy
from engine.strategy.donchian_filtered import DonchianFiltered
from engine.backtest.simulator_v2 import simulate_v2
from engine.backtest.portfolio import simulate_portfolio

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

STATE_PATH = os.path.join(config.OUTPUT_DIR, "paper_state.json")
EQUITY_PATH = os.path.join(config.OUTPUT_DIR, "paper_equity.csv")


def _load_start(today: str, persist: bool) -> str:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f).get("paper_start", today)
    if persist:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump({"paper_start": today}, f)
    return today


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-log", action="store_true", help="don't set start / don't append equity")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    paper_start = _load_start(today, persist=not args.no_log)
    start_date = date.fromisoformat(paper_start)

    universe = config.PAPER["universe"]
    if config.PAPER.get("use_volume_filter"):
        strategy = DonchianFiltered(use_volume=True, params={"long_only": True})
    else:
        strategy = DonchianStrategy({"long_only": True})
    exit_mode = config.PAPER["exit_mode"]
    max_hold = config.EXIT_V2["max_hold_bars"]
    feed = CryptoUSFeed()

    all_trades, last_bar, per_symbol = [], {}, {}
    for sym in universe:
        df = feed.get_ohlcv(sym, config.DONCHIAN["interval"], config.FETCH_BARS)
        if df is None or len(df) < config.DONCHIAN["sma_trend"] + 10:
            continue
        tr = simulate_v2(df, strategy, sym, config.cost_for(sym), exit_mode, config.EXIT_V2)
        per_symbol[sym] = tr
        all_trades.extend(tr)
        last_bar[sym] = df.index[-1]
    all_trades.sort(key=lambda t: t.signal_time)

    def is_open(t, lb):
        return t.exit_time == lb and t.outcome == "TIME" and t.bars_held < max_hold

    forward_open, preexisting_open, entered_today, exited_today = [], [], [], []
    for sym, trs in per_symbol.items():
        lb = last_bar[sym]
        for t in trs:
            if t.entry_time.date() == lb.date() and t.entry_time.date() >= start_date:
                entered_today.append(t)
            if t.exit_time == lb and not is_open(t, lb) and t.entry_time.date() >= start_date:
                exited_today.append(t)
        if trs and is_open(trs[-1], lb):
            (forward_open if trs[-1].entry_time.date() >= start_date else preexisting_open).append(trs[-1])

    # FORWARD paper account: only trades entered on/after the paper start date.
    forward_trades = [t for t in all_trades if t.entry_time.date() >= start_date]
    P = config.PORTFOLIO
    port = simulate_portfolio(forward_trades, P["starting_capital"], P["risk_pct"],
                              P["max_concurrent"], P["fractional"], P["min_position_cash"])

    print("\n" + "=" * 70)
    print(f"  PAPER MONITOR — long-only Donchian + partial-trail   {today} UTC")
    print(f"  paper start: {paper_start}   universe: {', '.join(universe)}   start ${P['starting_capital']:.0f}")
    print("=" * 70)
    days = (date.fromisoformat(today) - start_date).days
    print(f"\n  FORWARD PAPER EQUITY: ${port.final_capital:.2f}  ({port.return_pct:+.1f}%)  "
          f"over {days} day(s), {port.taken} trade(s) taken")
    if days == 0:
        print("  (day 1 — starting flat; the forward record builds from here)")

    print(f"\n── YOUR OPEN PAPER POSITIONS ({len(forward_open)}) ──")
    if not forward_open:
        print("  (none — flat, waiting for a breakout)")
    for t in forward_open:
        print(f"  {t.symbol:<9} LONG since {t.entry_time.date()}  entry {t.entry_fill:.2f}  "
              f"trail/stop ~{t.stop:.2f}  unrealised {t.R:+.2f}R")

    print(f"\n── ENTER TODAY ({len(entered_today)}) ──")
    if not entered_today:
        print("  (no new breakouts today)")
    for t in entered_today:
        print(f"  BUY {t.symbol:<9} ~{t.entry_fill:.2f}  stop {t.stop:.2f}  "
              f"first target {t.target:.2f}  (conv {t.conviction:.2f})")

    print(f"\n── EXITED TODAY ({len(exited_today)}) ──")
    if not exited_today:
        print("  (no exits today)")
    for t in exited_today:
        print(f"  SELL {t.symbol:<9} {t.outcome}  realised {t.R:+.2f}R")

    if preexisting_open:
        names = ", ".join(f"{t.symbol}({t.R:+.1f}R)" for t in preexisting_open)
        print(f"\n  (info: strategy is mid-trend on {names} from before your start — NOT counted in your paper P&L)")

    if not args.no_log:
        new = not os.path.exists(EQUITY_PATH)
        with open(EQUITY_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "forward_equity", "return_pct", "open", "entered", "exited"])
            w.writerow([today, port.final_capital, port.return_pct,
                        len(forward_open), len(entered_today), len(exited_today)])
        print(f"\n  appended snapshot → {EQUITY_PATH}")
    print()


if __name__ == "__main__":
    main()
