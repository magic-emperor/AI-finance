"""
Paper trader — Path A.
Consumes signals (from DB or scanner), tracks virtual positions, applies
slippage/commission on entry/exit, records P&L. Optionally persist closed trades for dashboard.
"""

from datetime import datetime
from typing import Dict, List, Optional, Any

import structlog

logger = structlog.get_logger()


def _load_slippage_commission() -> tuple:
    try:
        from market_agent.config import (
            BACKTEST_SLIPPAGE_BPS,
            BACKTEST_COMMISSION_BPS,
            BACKTEST_COMMISSION_PER_TRADE,
        )
        slip_pct = BACKTEST_SLIPPAGE_BPS / 10000.0
        return slip_pct, BACKTEST_COMMISSION_BPS, BACKTEST_COMMISSION_PER_TRADE
    except Exception:
        return 0.0005, 10.0, 0.0


class PaperTrader:
    """
    In-memory paper trading: open/close positions with slippage and commission, track P&L.
    One position per symbol (new signal on same symbol closes previous first).
    """

    def __init__(self):
        self.slippage_pct, self.commission_bps, self.commission_per_trade = _load_slippage_commission()
        self.positions: List[Dict[str, Any]] = []   # open: symbol, direction, entry_price, size, entry_time, signal_id, commission_paid
        self.closed_trades: List[Dict[str, Any]] = []  # closed: same + exit_price, exit_time, pnl, pnl_pct, reason

    def _effective_entry(self, side: str, quote_price: float) -> float:
        """Apply slippage: BUY fills higher, SELL fills lower."""
        slip = quote_price * self.slippage_pct
        if side == "BUY":
            return quote_price + slip
        return quote_price - slip

    def _effective_exit(self, side: str, quote_price: float) -> float:
        """Exit slippage: BUY exits (sell) lower, SELL exits (cover) higher."""
        slip = quote_price * self.slippage_pct
        if side == "BUY":
            return quote_price - slip
        return quote_price + slip

    def _commission_for_trade(self, notional: float) -> float:
        if self.commission_per_trade > 0:
            return self.commission_per_trade * 2  # entry + exit
        return notional * (self.commission_bps / 10000.0) * 2

    def open_position(self, signal: Dict[str, Any], size: float = 1.0) -> Optional[Dict[str, Any]]:
        """
        Open a paper position from a signal dict.
        If there is already an open position for this symbol, it is closed at current_price first.

        signal: dict with symbol, direction (BUY/SELL), entry_price or current_price, target_1, target_2, stop_loss, etc.
        size: notional units (e.g. 1 = 1 share or 1 unit).

        Returns: position record or None if direction is WAIT or no price.
        """
        direction = signal.get("direction", "WAIT")
        if direction == "WAIT":
            return None
        entry_quote = signal.get("entry_price") or signal.get("current_price", 0)
        if not entry_quote or entry_quote <= 0:
            return None
        symbol = signal.get("symbol", "")
        if not symbol:
            return None

        # Close any existing position for this symbol at same price (simplified: close at entry for new signal)
        self.close_position(symbol, exit_price=entry_quote, reason="REPLACED")

        entry_price = self._effective_entry(direction, entry_quote)
        notional = entry_price * size
        commission = self._commission_for_trade(notional) / 2  # half at entry
        pos = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "entry_quote": entry_quote,
            "size": size,
            "entry_time": datetime.utcnow(),
            "signal_id": signal.get("prediction_id"),
            "commission_paid": commission,
            "target_1": signal.get("target_1"),
            "target_2": signal.get("target_2"),
            "stop_loss": signal.get("stop_loss"),
        }
        self.positions.append(pos)
        logger.info("paper_position_opened", symbol=symbol, direction=direction, entry=round(entry_price, 4), commission=round(commission, 4))
        return pos

    def close_position(self, symbol: str, exit_price: float, reason: str = "MANUAL") -> Optional[Dict[str, Any]]:
        """
        Close the open position for symbol at exit_price (quote). Applies exit slippage and commission.

        Returns: closed trade record or None if no open position.
        """
        pos = None
        idx = None
        for i, p in enumerate(self.positions):
            if p["symbol"] == symbol:
                pos = p
                idx = i
                break
        if not pos:
            return None
        self.positions.pop(idx)

        exit_quote = exit_price
        exit_fill = self._effective_exit(pos["direction"], exit_quote)
        notional = pos["entry_price"] * pos["size"]
        commission_exit = self._commission_for_trade(notional) / 2
        total_commission = pos["commission_paid"] + commission_exit

        if pos["direction"] == "BUY":
            pnl = (exit_fill - pos["entry_price"]) * pos["size"]
        else:
            pnl = (pos["entry_price"] - exit_fill) * pos["size"]
        pnl_net = pnl - total_commission
        pnl_pct = (pnl_net / notional) * 100 if notional else 0

        closed = {
            **pos,
            "exit_price": exit_fill,
            "exit_quote": exit_quote,
            "exit_time": datetime.utcnow(),
            "pnl": pnl_net,
            "pnl_pct": pnl_pct,
            "commission_total": total_commission,
            "reason": reason,
        }
        self.closed_trades.append(closed)
        logger.info("paper_position_closed", symbol=symbol, reason=reason, pnl=round(pnl_net, 4), pnl_pct=round(pnl_pct, 2))
        return closed

    def get_open_positions(self) -> List[Dict[str, Any]]:
        return list(self.positions)

    def get_closed_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Most recent first."""
        return list(reversed(self.closed_trades[-limit:]))

    def get_pnl_summary(self) -> Dict[str, Any]:
        total_pnl = sum(t["pnl"] for t in self.closed_trades)
        total_commission = sum(t["commission_total"] for t in self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t["pnl"] > 0)
        losses = sum(1 for t in self.closed_trades if t["pnl"] <= 0)
        return {
            "total_pnl": total_pnl,
            "total_commission": total_commission,
            "net_pnl": total_pnl,
            "trade_count": len(self.closed_trades),
            "wins": wins,
            "losses": losses,
            "open_count": len(self.positions),
        }
