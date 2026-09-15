"""
portfolio.py — Realistic, capital-constrained portfolio simulation.

The per-symbol backtest assumes you can take every trade at full risk. Real life
on $500 can't: you can only hold a few positions at once, and cash is finite.
This sim trades the way you actually would:

  - One shared account; positions across symbols compete for capital.
  - At most `max_concurrent` open positions; signals are staggered in time, so the
    real need is usually far below the universe size.
  - Each position sized by risk (1% of current equity), notional capped to available
    cash (fractional shares → no whole-share rounding waste).
  - When more entries fire than capacity, take the HIGHEST-CONVICTION ones first.

It reuses the honest per-symbol fills (CompletedTrade) and just decides which to
take and tracks the dollar account. Cash account, no leverage: a position reserves
its full notional from cash and returns it (± P&L) on exit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List
from engine.backtest.simulator import CompletedTrade


@dataclass
class PortfolioResult:
    starting_capital: float
    final_capital: float
    return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    max_concurrent_used: int
    taken: int
    skipped_slot: int
    skipped_cash: int
    n_candidates: int


def simulate_portfolio(trades: List[CompletedTrade], starting_capital: float,
                       risk_pct: float, max_concurrent: int,
                       fractional: bool = True, min_position_cash: float = 5.0) -> PortfolioResult:
    # Build a time-ordered event stream. At a given timestamp, process EXITs first
    # (free capital), then ENTRYs ranked by conviction (best first).
    events = []
    for idx, t in enumerate(trades):
        events.append((t.entry_time, 1, -t.conviction, idx))   # 1 = ENTRY (after exits)
        events.append((t.exit_time, 0, 0.0, idx))              # 0 = EXIT (first)
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    cash = float(starting_capital)
    open_pos = {}   # idx -> (notional, risk_dollars)
    taken = skipped_slot = skipped_cash = 0
    max_conc = 0
    peak = starting_capital
    max_dd_pct = 0.0
    first_time = last_time = None

    def equity_now():
        return cash + sum(n for n, _ in open_pos.values())

    for time, kind, _, idx in events:
        if first_time is None:
            first_time = time
        last_time = time

        if kind == 0:  # EXIT
            if idx in open_pos:
                notional, risk_d = open_pos.pop(idx)
                pnl = trades[idx].R * risk_d
                cash += notional + pnl
        else:          # ENTRY
            if idx in open_pos:
                continue
            if len(open_pos) >= max_concurrent:
                skipped_slot += 1
            else:
                t = trades[idx]
                rpu = t.risk_per_unit
                if rpu > 0:
                    equity = equity_now()
                    risk_d = risk_pct * equity
                    shares = risk_d / rpu
                    notional = shares * t.entry_fill
                    if notional > cash:                  # cash-limited
                        if cash >= min_position_cash and fractional:
                            notional = cash              # use remaining cash
                            shares = notional / t.entry_fill
                            risk_d = shares * rpu        # under-risked, honestly
                        else:
                            skipped_cash += 1
                            notional = None
                    if notional is not None:
                        cash -= notional
                        open_pos[idx] = (notional, risk_d)
                        taken += 1
                        max_conc = max(max_conc, len(open_pos))

        eq = equity_now()
        peak = max(peak, eq)
        if peak > 0:
            max_dd_pct = min(max_dd_pct, (eq - peak) / peak * 100)

    final = equity_now()  # all positions closed by their exit events
    years = max(((last_time - first_time).days / 365.25) if first_time else 1e-9, 1e-9)
    cagr = (((final / starting_capital) ** (1 / years) - 1) * 100) if final > 0 else -100.0

    return PortfolioResult(
        starting_capital=round(starting_capital, 2),
        final_capital=round(final, 2),
        return_pct=round((final / starting_capital - 1) * 100, 2),
        cagr_pct=round(cagr, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        max_concurrent_used=max_conc,
        taken=taken, skipped_slot=skipped_slot, skipped_cash=skipped_cash,
        n_candidates=len(trades),
    )
