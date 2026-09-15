"""
diagnostics.py — Turn each backtest into a lesson, not just a verdict.

The key question whenever a strategy fails: WHY?
  - If GROSS expectancy (before costs) is positive but NET is negative → the
    signal has edge but costs/exits are eating it (fix exits, trade less, bigger
    targets, cheaper instruments).
  - If GROSS expectancy is also negative → the signal itself has no edge (the
    entry is the problem; tweaking exits won't save it).
  - High average MFE on losers/time-stops → we're leaving profit on the table
    (targets too tight / exits too early).
  - by-hour / by-symbol → maybe the edge lives in a slice (e.g. only the open).
"""
from __future__ import annotations

from typing import List, Dict
from collections import defaultdict
import numpy as np

from engine.backtest.simulator import CompletedTrade


def _mean(xs):
    return float(np.mean(xs)) if len(xs) else 0.0


def diagnose(trades: List[CompletedTrade]) -> Dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "report": "  (no trades)"}

    net = np.array([t.R for t in trades])
    gross = np.array([t.gross_R for t in trades])
    mfe = np.array([t.mfe_r for t in trades])
    mae = np.array([t.mae_r for t in trades])

    outcomes = defaultdict(int)
    for t in trades:
        outcomes[t.outcome] += 1

    by_hour = defaultdict(list)
    by_symbol = defaultdict(list)
    for t in trades:
        by_hour[t.entry_time.hour].append(t.R)
        by_symbol[t.symbol].append(t.R)

    net_exp = float(net.mean())
    gross_exp = float(gross.mean())

    # interpretation
    if gross_exp <= 0:
        verdict = "SIGNAL has no edge even before costs — the ENTRY is the problem."
    elif net_exp < 0:
        verdict = "Signal is positive BEFORE costs but costs/exits kill it — fix exits/costs/frequency."
    else:
        verdict = "Positive after costs — promising; check robustness (segments, per-symbol)."

    lines = ["── DIAGNOSTICS ──"]
    lines.append(f"  outcomes      : " + ", ".join(f"{k}={v} ({v/n*100:.0f}%)"
                                                   for k, v in sorted(outcomes.items())))
    lines.append(f"  expectancy    : net={net_exp:+.3f}R   gross(no-cost)={gross_exp:+.3f}R"
                 f"   cost drag={gross_exp-net_exp:.3f}R")
    lines.append(f"  excursion     : avg MFE={_mean(mfe):.2f}R  avg MAE={_mean(mae):.2f}R")
    # winners vs losers MFE
    losers = [t.mfe_r for t in trades if t.R <= 0]
    if losers:
        lines.append(f"  loser MFE     : avg {_mean(losers):.2f}R  "
                     f"(how far losers ran in our favour before reversing)")
    lines.append("  by entry hour (UTC): " +
                 ", ".join(f"{h:02d}h:{_mean(rs):+.2f}R(n{len(rs)})"
                           for h, rs in sorted(by_hour.items())))
    lines.append("  by symbol     : " +
                 ", ".join(f"{s}:{_mean(rs):+.2f}R(n{len(rs)})"
                           for s, rs in sorted(by_symbol.items())))
    lines.append(f"  READ          : {verdict}")

    return {"n": n, "net_exp": net_exp, "gross_exp": gross_exp, "report": "\n".join(lines)}
