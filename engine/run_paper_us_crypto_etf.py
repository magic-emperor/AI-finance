"""
run_paper_us_crypto_etf.py — forward paper-trading monitor for the US/crypto/etf
Donchian trial (registry.py hypothesis id=2, trial id=7 -- the CORRECTED
plain-baseline measurement; the old n=182 trial id=2 conflated the pyramid
exit variant with the baseline and has been superseded, not deleted).

A separate track from the very first paper monitor (run_paper.py, archived
2026-09-15 -- it used the partial-trail exit mode, which was never the
registered/gate-tested configuration, and accumulated zero trades in its
only run anyway). Uses the EXACT frozen baseline: plain Donchian
(simulate() v1, config.DONCHIAN unmodified, long_only=True) -- same
discipline as run_paper_fx_commodity.py.

Robustness context as of 2026-09-16 (see registry.py trial id=7): OOS
n=233, expectancy +0.413R, PF 1.802. Survives 2x-cost stress (+0.413 ->
+0.389R) and +3-bar latency stress (+0.413 -> +0.479R, actually improved)
cleanly. Deflated Sharpe against the corrected 3-trial trend-following
family = 0.9622 -- clears the ~0.95 usual confidence bar, materially
stronger evidence than the FX/commodity trial's 0.53. Positive in all 4
OOS years and all 3 thirds of the full 2017-2026 history. Still missing:
PBO (not built, same infra gap as fx_commodity). This is the strongest
single result of the project so far -- still not "validated" without PBO,
but the best-supported hypothesis being paper-traded.

Run once a day:
    python -X utf8 -m engine.run_paper_us_crypto_etf            # daily snapshot
    python -X utf8 -m engine.run_paper_us_crypto_etf --no-log   # look only
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
from engine.backtest.simulator import simulate
from engine.backtest.portfolio import simulate_portfolio

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

UNIVERSE = config.WATCHLISTS["crypto"] + config.WATCHLISTS["us"] + config.WATCHLISTS["etf"]
STATE_PATH = os.path.join(config.OUTPUT_DIR, "paper_state_us_crypto_etf.json")
EQUITY_PATH = os.path.join(config.OUTPUT_DIR, "paper_equity_us_crypto_etf.csv")
KILL_FLAG_PATH = os.path.join(config.OUTPUT_DIR, "paper_us_crypto_etf_KILLED.json")

# Same pre-committed kill criteria as run_paper_fx_commodity.py, same reasoning.
HARD_STOP_DRAWDOWN_PCT = -20.0
TIME_STOP_DAYS = 180
TIME_STOP_MIN_TRADES = 15


def check_kill_criteria(days: int, port, forward_trades) -> str | None:
    if port.max_drawdown_pct <= HARD_STOP_DRAWDOWN_PCT:
        return (f"HARD STOP: paper drawdown {port.max_drawdown_pct:.1f}% breached "
                f"{HARD_STOP_DRAWDOWN_PCT:.0f}% ceiling.")
    if days >= TIME_STOP_DAYS:
        closed = [t for t in forward_trades if t.exit_time is not None]
        mean_r = (sum(t.R for t in closed) / len(closed)) if closed else None
        if len(closed) < TIME_STOP_MIN_TRADES or (mean_r is not None and mean_r <= 0):
            return (f"TIME STOP: {days} days elapsed, {len(closed)} forward trades "
                    f"closed (need >={TIME_STOP_MIN_TRADES}), mean forward R="
                    f"{mean_r if mean_r is not None else 'n/a'} -- inconclusive-"
                    f"leaning-negative, not extending further.")
    return None


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
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    paper_start = _load_start(today, persist=not args.no_log)
    start_date = date.fromisoformat(paper_start)

    strategy = DonchianStrategy()
    max_hold = strategy.p["max_hold_bars"]
    feed = CryptoUSFeed()

    all_trades, last_bar, per_symbol = [], {}, {}
    for sym in UNIVERSE:
        df = feed.get_ohlcv(sym, strategy.interval, config.FETCH_BARS)
        if df is None or len(df) < config.DONCHIAN["sma_trend"] + 10:
            continue
        tr = simulate(df, strategy, sym, config.cost_for(sym), max_hold)
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

    forward_trades = [t for t in all_trades if t.entry_time.date() >= start_date]
    P = config.PORTFOLIO
    port = simulate_portfolio(forward_trades, P["starting_capital"], P["risk_pct"],
                              P["max_concurrent"], P["fractional"], P["min_position_cash"])
    days = (date.fromisoformat(today) - start_date).days

    if os.path.exists(KILL_FLAG_PATH):
        with open(KILL_FLAG_PATH) as f:
            prior = json.load(f)
        print("\n" + "!" * 70)
        print(f"  ALREADY KILLED on {prior['killed_on']}: {prior['reason']}")
        print("  (clearing this flag is a deliberate human decision, not automatic)")
        print("!" * 70)
    else:
        kill_reason = check_kill_criteria(days, port, forward_trades)
        if kill_reason:
            with open(KILL_FLAG_PATH, "w") as f:
                json.dump({"killed_on": today, "reason": kill_reason}, f, indent=2)
            print("\n" + "!" * 70)
            print(f"  KILL CRITERION TRIGGERED: {kill_reason}")
            print("!" * 70)

    print("\n" + "=" * 70)
    print(f"  PAPER MONITOR (US/crypto/etf) — long-only Donchian, baseline 2.5R   {today} UTC")
    print(f"  paper start: {paper_start}   universe: {', '.join(UNIVERSE)}   start ${P['starting_capital']:.0f}")
    print("=" * 70)
    print(f"\n  FORWARD PAPER EQUITY: ${port.final_capital:.2f}  ({port.return_pct:+.1f}%)  "
          f"over {days} day(s), {port.taken} trade(s) taken")
    if days == 0:
        print("  (day 1 -- starting flat; the forward record builds from here)")

    print(f"\n-- YOUR OPEN PAPER POSITIONS ({len(forward_open)}) --")
    if not forward_open:
        print("  (none -- flat, waiting for a breakout)")
    for t in forward_open:
        print(f"  {t.symbol:<10} {t.direction:<5} since {t.entry_time.date()}  entry {t.entry_fill:.2f}  "
              f"stop {t.stop:.2f}  target {t.target:.2f}  unrealised {t.R:+.2f}R")

    print(f"\n-- ENTER TODAY ({len(entered_today)}) --")
    if not entered_today:
        print("  (no new breakouts today)")
    for t in entered_today:
        print(f"  {t.direction} {t.symbol:<10} ~{t.entry_fill:.2f}  stop {t.stop:.2f}  "
              f"target {t.target:.2f}  (conv {t.conviction:.2f})")

    print(f"\n-- EXITED TODAY ({len(exited_today)}) --")
    if not exited_today:
        print("  (no exits today)")
    for t in exited_today:
        print(f"  {t.symbol:<10} {t.outcome}  realised {t.R:+.2f}R")

    if preexisting_open:
        names = ", ".join(f"{t.symbol}({t.R:+.1f}R)" for t in preexisting_open)
        print(f"\n  (info: strategy is mid-trend on {names} from before paper start -- NOT counted)")

    if not args.no_log:
        new = not os.path.exists(EQUITY_PATH)
        with open(EQUITY_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "forward_equity", "return_pct", "open", "entered", "exited"])
            w.writerow([today, port.final_capital, port.return_pct,
                        len(forward_open), len(entered_today), len(exited_today)])
        print(f"\n  appended snapshot -> {EQUITY_PATH}")
    print()


if __name__ == "__main__":
    main()
