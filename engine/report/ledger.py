"""
ledger.py — Translate R-multiple trades into a real dollar account, applying the
money-management rules (1% risk, daily loss limit, max trades/day).

This is the "am I actually making/losing money, and would I survive?" view.
pnl_dollars for a trade = R * risk_dollars, where risk_dollars = 1% of the
capital at the time of the trade. Trades blocked by the daily limit are skipped
(not taken), exactly as the live system would behave.
"""
from __future__ import annotations

from typing import List, Dict
from engine.backtest.simulator import CompletedTrade
from engine.risk.money import MoneyManager


def simulate_dollars(trades: List[CompletedTrade], starting_capital: float,
                     risk_pct: float, max_trades_per_day: int,
                     daily_loss_limit_pct: float) -> Dict:
    mm = MoneyManager(starting_capital, risk_pct, max_trades_per_day, daily_loss_limit_pct)
    ordered = sorted(trades, key=lambda t: t.signal_time)

    rows = []
    taken = skipped = 0
    wins = losses = 0
    peak = starting_capital
    max_dd_dollars = 0.0

    for t in ordered:
        day = t.signal_time.date()
        if not mm.can_trade(day):
            skipped += 1
            continue
        _, risk_dollars = mm.size(t.entry_fill, t.stop)
        pnl = t.R * risk_dollars
        mm.apply(pnl, day)
        taken += 1
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        peak = max(peak, mm.capital)
        max_dd_dollars = min(max_dd_dollars, mm.capital - peak)
        rows.append({
            "time": str(t.signal_time), "symbol": t.symbol, "dir": t.direction,
            "R": round(t.R, 3), "risk_$": round(risk_dollars, 2),
            "pnl_$": round(pnl, 2), "equity_$": round(mm.capital, 2),
        })

    final = mm.capital
    return {
        "starting_capital": starting_capital,
        "final_capital": round(final, 2),
        "return_pct": round((final / starting_capital - 1.0) * 100, 2),
        "trades_taken": taken,
        "trades_skipped_by_limits": skipped,
        "wins": wins, "losses": losses,
        "win_rate_pct": round((wins / taken * 100) if taken else 0.0, 1),
        "max_drawdown_$": round(max_dd_dollars, 2),
        "rows": rows,
    }
