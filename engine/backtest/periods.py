"""
periods.py — Granular robustness: slice the edge MANY different ways.

One aggregate number can hide a strategy that only worked in one lucky stretch.
So we look at the edge through many independent windows:
  - every calendar YEAR in the data, and
  - ROLLING 6-month windows stepping 3 months, which deliberately straddle year
    boundaries (e.g. Oct–Mar = 3 months of one year + 3 of the next).

Headline robustness score = "X of N six-month windows were profitable." Positive
across most years AND most windows = a durable edge; weak slices show where to fix.
"""
from __future__ import annotations

from typing import List, Tuple, Dict
import pandas as pd

from engine.backtest.metrics import compute_metrics
from engine.report.scorecard import _fmt


def by_year(trades) -> List[Tuple[int, Dict]]:
    years = sorted({t.signal_time.year for t in trades})
    return [(y, compute_metrics([t for t in trades if t.signal_time.year == y])) for y in years]


def rolling_windows(trades, window_months: int = 6, step_months: int = 3):
    if not trades:
        return [], {}
    ts = sorted(trades, key=lambda t: t.signal_time)
    first, last = ts[0].signal_time, ts[-1].signal_time
    cur = pd.Timestamp(year=first.year, month=first.month, day=1, tz=first.tz)

    wins = []
    while cur <= last:
        end = cur + pd.DateOffset(months=window_months)
        sub = [t for t in ts if cur <= t.signal_time < end]
        if sub:
            wins.append((cur, end, compute_metrics(sub)))
        cur = cur + pd.DateOffset(months=step_months)

    exps = [m["expectancy_r"] for _, _, m in wins]
    pos = sum(1 for e in exps if e > 0)
    summary = {
        "n_windows": len(wins),
        "n_positive": pos,
        "pct_positive": (pos / len(wins) * 100) if wins else 0.0,
        "worst": min(exps) if exps else 0.0,
        "median": sorted(exps)[len(exps) // 2] if exps else 0.0,
        "best": max(exps) if exps else 0.0,
    }
    return wins, summary


def format_periods(trades, window_months: int = 6, step_months: int = 3) -> str:
    lines = ["── PER-YEAR (every year in the data) ──"]
    for y, m in by_year(trades):
        lines.append(f"  {y}  n={m['n_trades']:<4} exp_R={m['expectancy_r']:+.3f}  "
                     f"PF={_fmt(m['profit_factor'])}  totR={m['total_r']:+.1f}")

    wins, summary = rolling_windows(trades, window_months, step_months)
    lines.append(f"── ROLLING {window_months}-MONTH WINDOWS (step {step_months}m, straddle year boundaries) ──")
    for cur, end, m in wins:
        flag = "+" if m["expectancy_r"] > 0 else "-"
        lines.append(f"  [{flag}] {cur.date()}..{end.date()}  n={m['n_trades']:<3} "
                     f"exp_R={m['expectancy_r']:+.3f}  PF={_fmt(m['profit_factor'])}")
    if summary:
        lines.append(f"  SUMMARY: {summary['n_positive']}/{summary['n_windows']} windows profitable "
                     f"({summary['pct_positive']:.0f}%)   "
                     f"worst={summary['worst']:+.2f}R  median={summary['median']:+.2f}R  "
                     f"best={summary['best']:+.2f}R")
    return "\n".join(lines)
