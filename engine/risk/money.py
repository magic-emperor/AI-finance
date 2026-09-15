"""
money.py — Capital survival. This is the answer to "I don't want to lose
everything in 3-4 trades."

Two separate knobs, kept separate on purpose:
  1. WHERE the stop/target sit = price levels, decided by the strategy.
  2. HOW MUCH is at risk = position SIZE, decided here = a small fixed % of capital.

With 1% risk on $500, four losses in a row = -$20 (-4%), not a wipeout. A
daily loss limit then stops the bleeding on a bad day.
"""
from __future__ import annotations

from typing import Tuple


def position_size(capital: float, risk_pct: float, entry: float, stop: float) -> Tuple[float, float]:
    """Return (units, risk_dollars). units = risk_$ / per-unit risk.

    Per-unit risk is the price distance to the stop. This guarantees that if the
    stop is hit, the loss ≈ risk_$ regardless of the instrument's price."""
    risk_dollars = capital * risk_pct
    per_unit = abs(entry - stop)
    if per_unit <= 0:
        return (0.0, 0.0)
    return (risk_dollars / per_unit, risk_dollars)


class MoneyManager:
    """Tracks equity and enforces daily limits as trades are applied in order."""

    def __init__(self, starting_capital: float, risk_pct: float,
                 max_trades_per_day: int, daily_loss_limit_pct: float):
        self.capital = float(starting_capital)
        self.risk_pct = risk_pct
        self.max_trades_per_day = max_trades_per_day
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self._day = None
        self._day_start_capital = self.capital
        self._trades_today = 0
        self._pnl_today = 0.0

    def _roll_day(self, day):
        if day != self._day:
            self._day = day
            self._day_start_capital = self.capital
            self._trades_today = 0
            self._pnl_today = 0.0

    def can_trade(self, day) -> bool:
        self._roll_day(day)
        if self._trades_today >= self.max_trades_per_day:
            return False
        if self._pnl_today <= -self.daily_loss_limit_pct * self._day_start_capital:
            return False
        return True

    def size(self, entry: float, stop: float) -> Tuple[float, float]:
        return position_size(self.capital, self.risk_pct, entry, stop)

    def apply(self, pnl_dollars: float, day) -> None:
        self._roll_day(day)
        self.capital += pnl_dollars
        self._trades_today += 1
        self._pnl_today += pnl_dollars
