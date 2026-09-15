"""
Phase 3.5: Trading Idea Generator

Generates actionable trade ideas from signals, including:
- Equity trades (direct BUY/SELL)
- Options strategies (CE/PE for directional, spreads for hedged)
- Strategy selection based on regime + confidence + volatility

Indian NSE focus: lot sizes, CE/PE naming, monthly expiry awareness.
"""

import structlog
import json
import math
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════
# NSE LOT SIZES (common F&O stocks, updated periodically)
# ═══════════════════════════════════════════════════════════════

NSE_LOT_SIZES = {
    "RELIANCE.NS": 250, "TCS.NS": 175, "INFY.NS": 300,
    "HDFCBANK.NS": 550, "ICICIBANK.NS": 700, "SBIN.NS": 750,
    "ITC.NS": 1600, "KOTAKBANK.NS": 400, "BAJFINANCE.NS": 125,
    "HINDUNILVR.NS": 300, "BHARTIARTL.NS": 475, "TATAMOTORS.NS": 1400,
    "LT.NS": 150, "AXISBANK.NS": 600, "MARUTI.NS": 100,
    "WIPRO.NS": 1500, "ADANIENT.NS": 250, "TATASTEEL.NS": 550,
    "SUNPHARMA.NS": 700, "HCLTECH.NS": 350, "TITAN.NS": 175,
    "NIFTY": 50, "BANKNIFTY": 15,
}

# Strike interval standards
STRIKE_INTERVALS = {
    "default": 50,
    "NIFTY": 50,
    "BANKNIFTY": 100,
}


@dataclass
class TradeIdea:
    """A complete trade idea with strategy, entry, and risk management."""
    symbol: str
    strategy: str           # "EQUITY_BUY", "BUY_CE", "BUY_PE", "BULL_CALL_SPREAD", etc.
    direction: str          # "BULLISH", "BEARISH", "NEUTRAL"
    confidence: float
    regime: str

    # Equity leg
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float

    # Options leg (if applicable)
    options: Optional[Dict] = None

    # Risk
    risk_amount: float = 0.0
    max_loss: float = 0.0
    max_profit: float = 0.0
    risk_reward: float = 0.0

    # Context
    reasoning: str = ""
    timeframe: str = "Intraday"
    created_at: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


class IdeaGenerator:
    """
    Generates trade ideas from signals.

    Strategy selection logic:
    - High confidence + trending regime → Direct equity or naked CE/PE
    - Medium confidence → Spreads (limited risk)
    - Low confidence + volatile → Iron condor / straddle
    - Always: risk per trade capped at 2% of portfolio
    """

    def __init__(self, portfolio_value: float = 500000):
        self.portfolio_value = portfolio_value
        self.max_risk_pct = 0.02  # 2% max risk per trade
        self._gemini = None

    @property
    def gemini(self):
        if self._gemini is None:
            try:
                from market_agent.brain.gemini_client import gemini_client
                self._gemini = gemini_client
            except Exception:
                pass
        return self._gemini

    # ═══════════════════════════════════════════════════════════
    # MAIN: GENERATE IDEAS FROM SIGNAL
    # ═══════════════════════════════════════════════════════════

    def generate_ideas(self, signal: Dict) -> List[TradeIdea]:
        """
        Takes a signal dict and produces 1-3 trade ideas:
        1. Always: Equity trade
        2. If F&O eligible: Options strategy
        3. If volatile: Hedged variant
        """
        ideas = []
        symbol = signal.get("symbol", "")
        direction = signal.get("direction", "WAIT")
        confidence = signal.get("confidence", 0)
        regime = signal.get("regime", "UNKNOWN")
        entry = signal.get("entry_price", signal.get("current_price", 0))
        sl = signal.get("stop_loss", 0)
        t1 = signal.get("target_1", 0)
        t2 = signal.get("target_2", 0)
        atr = signal.get("atr", 0)

        if direction in ("WAIT", "HOLD") or entry <= 0:
            return ideas

        is_bullish = direction == "BUY"
        now = datetime.now()

        # ── Idea 1: Equity trade (always) ──
        equity_idea = self._build_equity_idea(
            symbol, is_bullish, confidence, regime,
            entry, sl, t1, t2, now
        )
        ideas.append(equity_idea)

        # ── Idea 2: Options (if F&O eligible) ──
        lot_size = self._get_lot_size(symbol)
        if lot_size > 0:
            options_idea = self._select_options_strategy(
                symbol, is_bullish, confidence, regime,
                entry, sl, t1, t2, atr, lot_size, now
            )
            if options_idea:
                ideas.append(options_idea)

            # ── Idea 3: Hedged variant for volatile regimes ──
            if regime in ("VOLATILE_CHAOS", "VOLATILE_BEARISH"):
                hedged = self._build_spread_idea(
                    symbol, is_bullish, confidence, regime,
                    entry, sl, t1, t2, atr, lot_size, now
                )
                if hedged:
                    ideas.append(hedged)

        return ideas

    # ═══════════════════════════════════════════════════════════
    # EQUITY IDEA
    # ═══════════════════════════════════════════════════════════

    def _build_equity_idea(
        self, symbol, is_bullish, confidence, regime,
        entry, sl, t1, t2, now
    ) -> TradeIdea:
        """Direct equity BUY or SELL."""
        risk_per_share = abs(entry - sl)
        max_risk = self.portfolio_value * self.max_risk_pct
        shares = max(1, int(max_risk / risk_per_share)) if risk_per_share > 0 else 1
        risk_amount = shares * risk_per_share

        rr = abs(t1 - entry) / risk_per_share if risk_per_share > 0 else 0

        return TradeIdea(
            symbol=symbol,
            strategy="EQUITY_BUY" if is_bullish else "EQUITY_SELL",
            direction="BULLISH" if is_bullish else "BEARISH",
            confidence=confidence,
            regime=regime,
            entry_price=entry,
            stop_loss=sl,
            target_1=t1,
            target_2=t2,
            risk_amount=round(risk_amount, 2),
            max_loss=round(risk_amount, 2),
            max_profit=round(shares * abs(t1 - entry), 2),
            risk_reward=round(rr, 2),
            reasoning=f"Direct {'buy' if is_bullish else 'sell'} | {shares} shares | RR {rr:.1f}:1",
            timeframe="Intraday" if regime != "TRENDING_STRONG" else "Swing",
            created_at=now.isoformat(),
        )

    # ═══════════════════════════════════════════════════════════
    # OPTIONS STRATEGY SELECTION
    # ═══════════════════════════════════════════════════════════

    def _select_options_strategy(
        self, symbol, is_bullish, confidence, regime,
        entry, sl, t1, t2, atr, lot_size, now
    ) -> Optional[TradeIdea]:
        """
        Select best options strategy based on confidence + regime:
        - High confidence (>0.75) + trending → Naked CE/PE
        - Medium confidence (0.55-0.75) → Debit spread
        - Any confidence + volatile → Buy straddle
        """
        if confidence > 0.75 and regime in ("TRENDING_STRONG", "TRENDING_UP", "TRENDING_DOWN"):
            return self._build_naked_option(
                symbol, is_bullish, confidence, regime,
                entry, sl, t1, atr, lot_size, now
            )
        elif confidence >= 0.55:
            return self._build_spread_idea(
                symbol, is_bullish, confidence, regime,
                entry, sl, t1, t2, atr, lot_size, now
            )
        elif regime in ("VOLATILE_CHAOS",):
            return self._build_straddle(
                symbol, confidence, regime, entry, atr, lot_size, now
            )
        return None

    # ═══════════════════════════════════════════════════════════
    # NAKED CE / PE
    # ═══════════════════════════════════════════════════════════

    def _build_naked_option(
        self, symbol, is_bullish, confidence, regime,
        entry, sl, t1, atr, lot_size, now
    ) -> TradeIdea:
        """Buy CE (bullish) or PE (bearish) — high conviction plays."""
        strike = self._nearest_strike(entry, symbol)
        option_type = "CE" if is_bullish else "PE"

        # Estimate premium as ~ATR * 0.6 (rough proxy)
        est_premium = round(atr * 0.6, 2) if atr > 0 else round(entry * 0.01, 2)
        cost = est_premium * lot_size

        # Target premium (price reaches T1)
        intrinsic_at_target = abs(t1 - strike) if is_bullish else abs(strike - t1)
        target_premium = max(intrinsic_at_target, est_premium * 1.5)
        profit = (target_premium - est_premium) * lot_size

        return TradeIdea(
            symbol=symbol,
            strategy=f"BUY_{option_type}",
            direction="BULLISH" if is_bullish else "BEARISH",
            confidence=confidence,
            regime=regime,
            entry_price=entry,
            stop_loss=sl,
            target_1=t1,
            target_2=0,
            options={
                "type": option_type,
                "strike": strike,
                "premium_est": est_premium,
                "lot_size": lot_size,
                "total_cost": round(cost, 2),
                "expiry": self._next_expiry().isoformat(),
            },
            risk_amount=round(cost, 2),
            max_loss=round(cost, 2),  # premium paid = max loss
            max_profit=round(profit, 2),
            risk_reward=round(profit / cost, 2) if cost > 0 else 0,
            reasoning=(
                f"Buy {strike} {option_type} @ ~{est_premium} | "
                f"Lot: {lot_size} | Cost: {cost:.0f} | "
                f"Target premium: {target_premium:.0f}"
            ),
            timeframe="Intraday",
            created_at=now.isoformat(),
        )

    # ═══════════════════════════════════════════════════════════
    # SPREAD (BULL CALL / BEAR PUT)
    # ═══════════════════════════════════════════════════════════

    def _build_spread_idea(
        self, symbol, is_bullish, confidence, regime,
        entry, sl, t1, t2, atr, lot_size, now
    ) -> Optional[TradeIdea]:
        """
        Debit spread — limited risk, limited reward.
        Bull call spread: Buy ATM CE, Sell OTM CE
        Bear put spread: Buy ATM PE, Sell OTM PE
        """
        strike_interval = self._get_strike_interval(symbol)
        atm = self._nearest_strike(entry, symbol)

        if is_bullish:
            buy_strike = atm
            sell_strike = atm + strike_interval * 2  # 2 strikes OTM
            option_type = "CE"
            strategy = "BULL_CALL_SPREAD"
        else:
            buy_strike = atm
            sell_strike = atm - strike_interval * 2
            option_type = "PE"
            strategy = "BEAR_PUT_SPREAD"

        # Spread cost ≈ half of naked option
        est_premium_buy = round(atr * 0.6, 2) if atr > 0 else round(entry * 0.01, 2)
        est_premium_sell = round(est_premium_buy * 0.4, 2)
        net_debit = est_premium_buy - est_premium_sell
        cost = net_debit * lot_size
        max_profit = (abs(sell_strike - buy_strike) - net_debit) * lot_size

        return TradeIdea(
            symbol=symbol,
            strategy=strategy,
            direction="BULLISH" if is_bullish else "BEARISH",
            confidence=confidence,
            regime=regime,
            entry_price=entry,
            stop_loss=sl,
            target_1=t1,
            target_2=t2,
            options={
                "type": option_type,
                "buy_strike": buy_strike,
                "sell_strike": sell_strike,
                "net_debit": round(net_debit, 2),
                "lot_size": lot_size,
                "total_cost": round(cost, 2),
                "expiry": self._next_expiry().isoformat(),
            },
            risk_amount=round(cost, 2),
            max_loss=round(cost, 2),
            max_profit=round(max_profit, 2),
            risk_reward=round(max_profit / cost, 2) if cost > 0 else 0,
            reasoning=(
                f"{strategy}: Buy {buy_strike}{option_type} / Sell {sell_strike}{option_type} | "
                f"Debit: {net_debit:.0f} x {lot_size} = {cost:.0f} | "
                f"Max profit: {max_profit:.0f}"
            ),
            timeframe="Intraday",
            created_at=now.isoformat(),
        )

    # ═══════════════════════════════════════════════════════════
    # STRADDLE (VOLATILE REGIME)
    # ═══════════════════════════════════════════════════════════

    def _build_straddle(
        self, symbol, confidence, regime,
        entry, atr, lot_size, now
    ) -> TradeIdea:
        """
        Long straddle — profit from big move in either direction.
        Used in VOLATILE_CHAOS when direction is uncertain.
        """
        atm = self._nearest_strike(entry, symbol)
        est_ce_premium = round(atr * 0.5, 2) if atr > 0 else round(entry * 0.008, 2)
        est_pe_premium = est_ce_premium  # ATM approx equal
        total_premium = est_ce_premium + est_pe_premium
        cost = total_premium * lot_size

        # Breakevens
        upper_be = atm + total_premium
        lower_be = atm - total_premium

        return TradeIdea(
            symbol=symbol,
            strategy="LONG_STRADDLE",
            direction="NEUTRAL",
            confidence=confidence,
            regime=regime,
            entry_price=entry,
            stop_loss=0,
            target_1=upper_be,
            target_2=lower_be,
            options={
                "type": "STRADDLE",
                "strike": atm,
                "ce_premium": est_ce_premium,
                "pe_premium": est_pe_premium,
                "lot_size": lot_size,
                "total_cost": round(cost, 2),
                "upper_breakeven": round(upper_be, 2),
                "lower_breakeven": round(lower_be, 2),
                "expiry": self._next_expiry().isoformat(),
            },
            risk_amount=round(cost, 2),
            max_loss=round(cost, 2),
            max_profit=0,  # Unlimited in theory
            risk_reward=0,
            reasoning=(
                f"Long Straddle {atm}: CE@{est_ce_premium} + PE@{est_pe_premium} | "
                f"Cost: {cost:.0f} | BEs: {lower_be:.0f} / {upper_be:.0f}"
            ),
            timeframe="Intraday",
            created_at=now.isoformat(),
        )

    # ═══════════════════════════════════════════════════════════
    # AI-ENHANCED IDEA (uses Gemini for narrative)
    # ═══════════════════════════════════════════════════════════

    def enrich_with_ai(self, idea: TradeIdea, market_context: str = "") -> TradeIdea:
        """Add AI-generated reasoning and risk notes."""
        if not self.gemini or not self.gemini.is_available:
            return idea

        try:
            prompt = (
                f"Trading idea for {idea.symbol}:\n"
                f"Strategy: {idea.strategy} | Direction: {idea.direction}\n"
                f"Entry: {idea.entry_price} | SL: {idea.stop_loss} | T1: {idea.target_1}\n"
                f"Regime: {idea.regime} | Confidence: {idea.confidence:.0%}\n"
                f"Options: {json.dumps(idea.options) if idea.options else 'N/A'}\n"
                f"Context: {market_context[:200]}\n\n"
                f"In 2-3 sentences, give rationale and key risks."
            )

            response = self.gemini._call_ai(prompt, f"idea_{idea.symbol}")
            if response:
                idea.reasoning = response[:300]
        except Exception:
            pass

        return idea

    # ═══════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════

    def _get_lot_size(self, symbol: str) -> int:
        """Get F&O lot size. Returns 0 if not F&O eligible."""
        # Strip .NS suffix for lookup
        return NSE_LOT_SIZES.get(symbol, 0)

    def _get_strike_interval(self, symbol: str) -> float:
        """Get strike price interval for a symbol."""
        base = symbol.replace(".NS", "")
        return STRIKE_INTERVALS.get(base, STRIKE_INTERVALS["default"])

    def _nearest_strike(self, price: float, symbol: str) -> float:
        """Round price to nearest strike interval."""
        interval = self._get_strike_interval(symbol)
        return round(price / interval) * interval

    def _next_expiry(self) -> datetime:
        """Get next Thursday (NSE weekly expiry)."""
        now = datetime.now()
        days_ahead = 3 - now.weekday()  # Thursday = 3
        if days_ahead <= 0:
            days_ahead += 7
        return (now + timedelta(days=days_ahead)).replace(
            hour=15, minute=30, second=0, microsecond=0
        )


# Singleton
_generator = None

def get_idea_generator(portfolio_value: float = 500000) -> IdeaGenerator:
    global _generator
    if _generator is None:
        _generator = IdeaGenerator(portfolio_value=portfolio_value)
    return _generator
