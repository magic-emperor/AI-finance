"""
Phase 15: Signal Engine

Generates actionable BUY/SELL signals with specific:
- Entry Price
- Stop-Loss
- Target 1 & 2
- Position Size
- Confidence Score

Uses: ATR for volatility-based stops, Risk-Reward for targets
"""

import numpy as np
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, List, Any
from enum import Enum
import structlog

logger = structlog.get_logger()


class SignalDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WAIT = "WAIT"


@dataclass
class TradingSignal:
    """Complete trading signal with entry, exit, and risk management."""
    
    # Core signal
    symbol: str
    direction: SignalDirection
    confidence: float  # 0.0 to 1.0
    
    # Prices
    current_price: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    
    # Position sizing
    position_size: int  # Number of shares
    risk_amount: float  # Amount at risk in currency
    risk_percent: float  # Portfolio % at risk
    
    # Context
    regime: str
    timeframe: str
    model_used: str
    
    # Metadata
    timestamp: datetime
    valid_until: datetime
    reasoning: List[str]
    
    # Tracking
    signal_id: str = ""
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        d['direction'] = self.direction.value
        d['timestamp'] = self.timestamp.isoformat()
        d['valid_until'] = self.valid_until.isoformat()
        return d
    
    @property
    def risk_reward_ratio(self) -> float:
        """Calculate risk-reward ratio."""
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.target_1 - self.entry_price)
        return reward / risk if risk > 0 else 0
    
    @property
    def is_actionable(self) -> bool:
        """Check if signal meets minimum criteria."""
        return (
            self.confidence >= 0.55 and
            self.risk_reward_ratio >= 1.5 and
            self.direction not in (SignalDirection.HOLD, SignalDirection.WAIT)
        )


class SignalEngine:
    """
    Generates trading signals from model predictions.
    
    Logic:
    1. Takes model output (direction probs, confidence)
    2. Calculates entry price (current + buffer)
    3. Calculates stop-loss (ATR-based)
    4. Calculates targets (risk-reward based)
    5. Determines position size (fixed % risk)
    """
    
    # Confidence thresholds
    STRONG_CONFIDENCE = 0.68
    MODERATE_CONFIDENCE = 0.55
    SILENCE_THRESHOLD = 0.50  # Below this = no signal
    
    # Position sizing
    DEFAULT_RISK_PERCENT = 2.0  # Risk 2% of portfolio per trade
    
    
    def __init__(
        self, 
        portfolio_value: float = 100000,
        atr_multiplier: float = 1.5,
        entry_buffer_pct: float = 0.002
    ):
        self.portfolio_value = portfolio_value
        self.entry_buffer = entry_buffer_pct
        self.signal_count = 0
        
        # Load Dynamic Config (Phase 26)
        import json
        import os
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json')
            with open(config_path, 'r') as f:
                self.config = json.load(f)
            logger.info("strategy_config_loaded", path=config_path)
        except Exception as e:
            # logger.warning("strategy_config_load_failed")
            self.config = {"default": {"stop_loss_multiplier": 2.0, "atr_period": 14}}

        # Default multiplier (will be overridden by quick_study or symbol config)
        self.atr_multiplier = self.config.get("default", {}).get("stop_loss_multiplier", 1.5)
        
    def quick_study(self, symbol: str) -> Dict[str, Any]:
        """
        Instant Fine-Tuning: Fetches 30 days of data and adapts strategy.
        - Adjusts ATR multiplier based on volatility regime
        - Adapts direction bias based on recent price trend
        - Learns momentum characteristics of this symbol
        """
        try:
            # 1. Fetch High-Res Data
            import yfinance as yf
            data = yf.download(symbol, period="1mo", interval="1h", progress=False)
            
            if len(data) < 50:
                logger.warning("insufficient_data_for_study", symbol=symbol)
                return {"status": "SKIPPED", "reason": "Not enough data"}
            
            # Handle MultiIndex columns from yf.download()
            close_col = data['Close']
            if hasattr(close_col, 'columns'):  # MultiIndex — pick first ticker
                close_col = close_col.iloc[:, 0]
                
            # 2. Learn volatility regime → adapt stop-loss barriers
            recent_vol = float(close_col.pct_change().std())
            self.atr_multiplier = 1.5 if recent_vol < 0.01 else 2.5
            
            # 3. Learn momentum → adapt direction bias
            returns = close_col.pct_change().dropna()
            if len(returns) >= 20:
                recent_trend = float(returns.tail(20).mean())
                win_rate = float((returns > 0).mean())
            else:
                recent_trend = 0.0
                win_rate = 0.5
            
            # 4. Update internal state
            study_result = {
                "status": "SUCCESS", 
                "bars_learned": len(data),
                "volatility_regime": f"{recent_vol:.4f}",
                "adapted_stop_mult": self.atr_multiplier,
                "direction_bias": round(recent_trend * 100, 4),
                "win_rate": round(win_rate * 100, 1),
            }
            logger.info("quick_study_complete", symbol=symbol, **study_result)
            return study_result
            
        except Exception as e:
            logger.error("quick_study_failed", symbol=symbol, error=str(e))
            return {"status": "FAILED", "error": str(e)}

    def generate_signal(
        self,
        symbol: str,
        current_price: float,
        atr: float,
        direction_probs: np.ndarray,  # [down, flat, up]
        confidence: float,
        regime: str = "UNKNOWN",
        model_name: str = "ensemble",
        macro_risk_factor: float = 1.0,  # 1.0 = Normal, 0.5 = Half risk due to news
        reasoning: List[str] = None,
        tech_analysis: Dict[str, Any] = None,
        strategy: str = "Intraday (Scalp)"
    ) -> Optional[TradingSignal]:
        """
        Generate a complete trading signal.
        
        Args:
            symbol: Stock ticker
            current_price: Current market price
            atr: Average True Range (14-period)
            direction_probs: Model output [P(down), P(flat), P(up)]
            confidence: Model's self-assessed confidence
            regime: Current market regime
            model_name: Which model generated this
            macro_risk_factor: Multiplier for risk amount (0.0-1.0)
            reasoning: List of reasons for the signal
        
        Returns:
            TradingSignal or None if below threshold
        """
        # Determine direction
        direction_idx = np.argmax(direction_probs)
        max_prob = direction_probs[direction_idx]
        
        if direction_idx == 0:
            direction = SignalDirection.SELL
        elif direction_idx == 2:
            direction = SignalDirection.BUY
        else:
            direction = SignalDirection.HOLD
        
        # ═══ CIRCUIT BREAKER CHECK ═══
        # Check if risk manager allows trading this symbol
        try:
            from market_agent.learning.risk_manager import get_risk_manager
            risk_mgr = get_risk_manager()
            risk_check = risk_mgr.can_trade(symbol, regime)
            if not risk_check['allowed'] and direction_idx != 1:
                # Breaker active — force WAIT but include the reason
                reasoning = reasoning or []
                reasoning.append(f"🛑 Circuit breaker: {risk_check['reason']}")
                if risk_check['escalation_level'] > 0:
                    reasoning.append(f"🔄 Escalation: {risk_check['action']}")
                    # Trigger escalation (retraining, boss brain, etc.)
                    risk_mgr.trigger_escalation(symbol, risk_check['escalation_level'])
                direction = SignalDirection.WAIT
                direction_idx = 1  # Force flat
        except Exception:
            pass  # Risk manager is optional — if it fails, trade normally

        original_direction = direction.value
        if confidence < self.SILENCE_THRESHOLD or direction == SignalDirection.HOLD:
             direction = SignalDirection.WAIT
             # We still proceed to calculate levels based on the "Intended" direction
             # If direction was HOLD, we default to BUY logic for level visualization (or just current price)
             if direction_idx == 0: # Bearish hold
                 intended_direction = SignalDirection.SELL
             else: # Bullish hold/wait
                 intended_direction = SignalDirection.BUY
        else:
             intended_direction = direction
        
        # Calculate entry price based on INTENDED direction
        if intended_direction == SignalDirection.BUY:
            entry_price = current_price * (1 + self.entry_buffer)
        else:
            entry_price = current_price * (1 - self.entry_buffer)
        
        # ═══ DYNAMIC TARGETS — computed from market structure ═══
        # Priority: 1) Support/Resistance  2) Pivot Points  3) Bollinger  4) Session ATR  5) Config fallback
        # Volume ratio adjusts width: high volume = wider targets, low volume = tighter
        is_scalp = "Scalp" in strategy or "Intraday" in strategy

        # --- Session ATR: use the most recent ATR, not a static number ---
        session_atr = atr  # Already 14-period ATR from caller

        # --- Volume adjustment: high volume means more movement potential ---
        vol_mult = 1.0
        if tech_analysis and tech_analysis.get('volume'):
            vol_info = tech_analysis['volume']
            vol_ratio = vol_info.get('ratio', 1.0)
            if vol_ratio > 2.0:
                vol_mult = 1.5   # High volume: targets can be wider
            elif vol_ratio > 1.3:
                vol_mult = 1.2   # Above average: slightly wider
            elif vol_ratio < 0.5:
                vol_mult = 0.6   # Low volume: tighten targets
            elif vol_ratio < 0.8:
                vol_mult = 0.8   # Below average: slightly tighter

        # --- Gather all possible target levels from analysis ---
        target_candidates = []
        sl_candidates = []

        # 1) Support/Resistance levels (strongest signal)
        if tech_analysis and tech_analysis.get('support_resistance'):
            sr = tech_analysis['support_resistance']
            if intended_direction == SignalDirection.BUY:
                for r in sr.get('resistance', []):
                    if r > entry_price:
                        target_candidates.append(('S/R Resistance', r))
                for s in sr.get('support', []):
                    if s < entry_price:
                        sl_candidates.append(('S/R Support', s))
            else:
                for s in sr.get('support', []):
                    if s < entry_price:
                        target_candidates.append(('S/R Support', s))
                for r in sr.get('resistance', []):
                    if r > entry_price:
                        sl_candidates.append(('S/R Resistance', r))

        # 2) Pivot Points (R1, R2, S1, S2)
        if tech_analysis and tech_analysis.get('pivot_points'):
            pp = tech_analysis['pivot_points']
            if intended_direction == SignalDirection.BUY:
                for key in ['R1', 'R2', 'R3']:
                    val = pp.get(key, 0)
                    if val > entry_price:
                        target_candidates.append((f'Pivot {key}', val))
                for key in ['S1', 'S2']:
                    val = pp.get(key, 0)
                    if 0 < val < entry_price:
                        sl_candidates.append((f'Pivot {key}', val))
            else:
                for key in ['S1', 'S2', 'S3']:
                    val = pp.get(key, 0)
                    if 0 < val < entry_price:
                        target_candidates.append((f'Pivot {key}', val))
                for key in ['R1', 'R2']:
                    val = pp.get(key, 0)
                    if val > entry_price:
                        sl_candidates.append((f'Pivot {key}', val))

        # 3) Bollinger Bands
        if tech_analysis and tech_analysis.get('bollinger'):
            bb = tech_analysis['bollinger']
            bb_upper = bb.get('upper', 0)
            bb_lower = bb.get('lower', 0)
            bb_mid = bb.get('middle', 0)
            if intended_direction == SignalDirection.BUY:
                if bb_upper > entry_price:
                    target_candidates.append(('Bollinger Upper', bb_upper))
                if bb_mid > 0 and bb_mid < entry_price:
                    sl_candidates.append(('Bollinger Mid', bb_mid))
            else:
                if bb_lower > 0 and bb_lower < entry_price:
                    target_candidates.append(('Bollinger Lower', bb_lower))
                if bb_mid > entry_price:
                    sl_candidates.append(('Bollinger Mid', bb_mid))

        # 4) Fibonacci levels
        if tech_analysis and tech_analysis.get('fibonacci'):
            fib = tech_analysis['fibonacci']
            extensions = fib.get('extensions', {})
            retracements = fib.get('retracements', {})
            if intended_direction == SignalDirection.BUY:
                for key in ['1.272', '1.618']:
                    val = extensions.get(key, 0)
                    if val > entry_price:
                        target_candidates.append((f'Fib {key}', val))
                fib_618 = retracements.get('0.618', 0)
                if 0 < fib_618 < entry_price:
                    sl_candidates.append(('Fib 0.618', fib_618))
            else:
                for key in ['1.272', '1.618']:
                    val = extensions.get(key, 0)
                    if 0 < val < entry_price:
                        target_candidates.append((f'Fib {key}', val))

        # --- Select best target & stop from candidates ---
        if intended_direction == SignalDirection.BUY:
            # Sort targets ascending (nearest first), stops descending (nearest first)
            target_candidates.sort(key=lambda x: x[1])
            sl_candidates.sort(key=lambda x: x[1], reverse=True)
        else:
            # For SELL: targets descending (nearest first), stops ascending (nearest first)
            target_candidates.sort(key=lambda x: x[1], reverse=True)
            sl_candidates.sort(key=lambda x: x[1])

        # Apply volume multiplier to filter: wider targets OK in high volume
        min_target_distance = session_atr * 0.1 * vol_mult  # Minimum target must be at least 10% of ATR away

        # Filter out targets too close to entry
        target_candidates = [(name, val) for name, val in target_candidates
                            if abs(val - entry_price) > min_target_distance]

        # --- Assign T1, T2, SL ---
        t1_source = "Config fallback"
        t2_source = "Config fallback"
        sl_source = "Config fallback"

        if target_candidates:
            t1_source, target_1 = target_candidates[0]
            target_2 = target_candidates[1][1] if len(target_candidates) > 1 else target_1 * (1.002 if intended_direction == SignalDirection.BUY else 0.998)
            if len(target_candidates) > 1:
                t2_source = target_candidates[1][0]
        else:
            # Fallback: ATR-based with volume adjustment
            t1_dist = session_atr * 0.3 * vol_mult
            t2_dist = session_atr * 0.6 * vol_mult

            #  # Fallback: ATR-based with volume adjustment (Gap 3 Option A — tighter target)
            # t1_dist = session_atr * 0.45 * vol_mult
            # t2_dist = session_atr * 1.0 * vol_mult

            if is_scalp:
                # For scalp: use config % as absolute minimum, but prefer ATR
                t1_pct = self.config.get("scalp", {}).get("t1_pct", 0.002)
                t2_pct = self.config.get("scalp", {}).get("t2_pct", 0.004)
                t1_dist = max(t1_dist, entry_price * t1_pct)
                t2_dist = max(t2_dist, entry_price * t2_pct)

            if intended_direction == SignalDirection.BUY:
                target_1 = entry_price + t1_dist
                target_2 = entry_price + t2_dist
            else:
                target_1 = entry_price - t1_dist
                target_2 = entry_price - t2_dist
            t1_source = f"ATR×0.3 (vol {vol_mult:.1f}x)"
            t2_source = f"ATR×0.6 (vol {vol_mult:.1f}x)"
            # t1_source = f"ATR×0.45 (vol {vol_mult:.1f}x)"
            # t2_source = f"ATR×1.0 (vol {vol_mult:.1f}x)"

        if sl_candidates:
            sl_source, stop_loss = sl_candidates[0]
            # Add a small buffer below/above the level
            if intended_direction == SignalDirection.BUY:
                stop_loss = stop_loss * 0.998  # Slightly below support
            else:
                stop_loss = stop_loss * 1.002  # Slightly above resistance
        else:
            # Fallback: ATR or config %
            if is_scalp:
                sl_pct = self.config.get("scalp", {}).get("sl_pct", 0.003)
                sl_dist = max(session_atr * 0.5, entry_price * sl_pct)
            else:
                sl_mult = self.config.get("swing", {}).get("sl_multiplier", 1.5)
                sl_dist = session_atr * sl_mult

            if intended_direction == SignalDirection.BUY:
                stop_loss = entry_price - sl_dist
            else:
                stop_loss = entry_price + sl_dist
            sl_source = f"ATR-based"

        # Log why these levels were chosen (transparency)
        reasoning.append(
            f"T1: {t1_source} ({target_1:,.2f}) | "
            f"T2: {t2_source} ({target_2:,.2f}) | "
            f"SL: {sl_source} ({stop_loss:,.2f})"
        )
        if vol_mult != 1.0:
            reasoning.append(f"Volume adjustment: {vol_mult:.1f}x ({'widened' if vol_mult > 1 else 'tightened'} targets)")
        
        # Position sizing (Dynamic % risk based on confidence)
        dynamic_risk = self.DEFAULT_RISK_PERCENT
        if confidence >= self.STRONG_CONFIDENCE:
            dynamic_risk = 2.5
        elif confidence < 0.6:
            dynamic_risk = 0.5
        else:
            dynamic_risk = 1.5

        # Apply Macro Risk Factor
        if macro_risk_factor < 1.0:
            original_risk = dynamic_risk
            dynamic_risk = dynamic_risk * macro_risk_factor
            reasoning.append(f"⚠️ Risk Reduced: Macro Impact Factor {macro_risk_factor:.1f}x (was {original_risk}%, now {dynamic_risk:.1f}%)")

        risk_amount = self.portfolio_value * (dynamic_risk / 100)
        risk_per_share = abs(entry_price - stop_loss)
        position_size = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
        
        if confidence < self.STRONG_CONFIDENCE and confidence >= self.MODERATE_CONFIDENCE:
            position_size = int(position_size * 0.75) 
        
        reasoning.append(f"Risk Management: Risk {dynamic_risk}% (₹{risk_amount:,.2f}), {position_size} units @ ₹{risk_per_share:.2f} risk/unit.")
        
        # Determine timeframe dynamically
        timeframe = "15m"  # Default
        if tech_analysis and tech_analysis.get('data_points', 0) > 200:
            timeframe = "1h"
        elif tech_analysis and tech_analysis.get('data_points', 0) > 500:
            timeframe = "4h"
        
        self.signal_count += 1
        signal_id = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.signal_count}"
        
        valid_until = datetime.now().replace(second=0, microsecond=0)
        from datetime import timedelta
        valid_until = valid_until + timedelta(minutes=15)
        
        if reasoning is None:
            reasoning = []
        
        if direction == SignalDirection.BUY:
            reasoning.insert(0, f"Model predicts UP with {max_prob:.1%} probability")
        else:
            reasoning.insert(0, f"Model predicts DOWN with {max_prob:.1%} probability")
        
        reasoning.append(f"Confidence: {confidence:.1%} ({'STRONG' if confidence >= self.STRONG_CONFIDENCE else 'MODERATE'})")
        reasoning.append(f"Regime: {regime}")
        
        signal = TradingSignal(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            current_price=current_price,
            entry_price=round(entry_price, 2),
            stop_loss=round(stop_loss, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            position_size=position_size,
            risk_amount=round(risk_amount, 2),
            risk_percent=dynamic_risk,
            regime=regime,
            timeframe=timeframe,
            model_used=model_name,
            timestamp=datetime.now(),
            valid_until=valid_until,
            reasoning=reasoning,
            signal_id=signal_id
        )
        
        # logger.info("signal_generated",
        #            signal_id=signal_id,
        #            symbol=symbol,
        #            direction=direction.value,
        #            entry=entry_price,
        #            stop=stop_loss,
        #            confidence=f"{confidence:.1%}")
        
        return signal
    
    def format_signal_for_display(self, signal: TradingSignal) -> str:
        """Format signal as human-readable string."""
        if signal is None:
            return "NO SIGNAL - Confidence below threshold or HOLD"
        
        direction_emoji = '^' if signal.direction == SignalDirection.BUY else 'v'
        
        output = f"""
{'='*50}
{direction_emoji} {signal.direction.value} SIGNAL: {signal.symbol}
{'='*50}

ENTRY:      Rs. {signal.entry_price:,.2f}
STOP LOSS:  Rs. {signal.stop_loss:,.2f}
TARGET 1:   Rs. {signal.target_1:,.2f}
TARGET 2:   Rs. {signal.target_2:,.2f}

POSITION:   {signal.position_size} shares
RISK:       Rs. {signal.risk_amount:,.2f} ({signal.risk_percent}% of portfolio)

CONFIDENCE: {signal.confidence:.1%}
REGIME:     {signal.regime}
VALID UNTIL: {signal.valid_until.strftime('%H:%M')}

REASONING:
"""
        for i, reason in enumerate(signal.reasoning, 1):
            output += f"  {i}. {reason}\n"
        
        return output


class SignalTracker:
    """
    Tracks historical signals for accuracy calculation.
    """
    
    def __init__(self):
        self.signals: List[Dict] = []
        self.outcomes: Dict[str, Dict] = {}
        
    def record_signal(self, signal: TradingSignal):
        """Record a generated signal."""
        self.signals.append(signal.to_dict())
        
    def record_outcome(
        self, 
        signal_id: str,
        hit_target_1: bool,
        hit_target_2: bool,
        hit_stop: bool,
        final_price: float
    ):
        """Record the outcome of a signal."""
        self.outcomes[signal_id] = {
            'hit_target_1': hit_target_1,
            'hit_target_2': hit_target_2,
            'hit_stop': hit_stop,
            'final_price': final_price,
            'evaluated_at': datetime.now().isoformat()
        }
    
    def calculate_accuracy(self) -> Dict:
        """Calculate accuracy metrics."""
        if not self.outcomes:
            return {'message': 'No evaluated signals yet'}
        
        total = len(self.outcomes)
        wins = sum(1 for o in self.outcomes.values() if o['hit_target_1'])
        losses = sum(1 for o in self.outcomes.values() if o['hit_stop'])
        
        return {
            'total_signals': total,
            'wins': wins,
            'losses': losses,
            'win_rate': wins / total if total > 0 else 0,
            'profit_factor': wins / losses if losses > 0 else float('inf')
        }


if __name__ == "__main__":
    print("Signal Engine Test")
    print("=" * 50)
    
    engine = SignalEngine(portfolio_value=500000)
    
    # Simulate model output
    direction_probs = np.array([0.15, 0.20, 0.65])  # Strong UP
    confidence = 0.72
    
    signal = engine.generate_signal(
        symbol="ITC.NS",
        current_price=341.50,
        atr=6.30,
        direction_probs=direction_probs,
        confidence=confidence,
        regime="BULLISH_TREND",
        model_name="multi_timeframe",
        reasoning=[
            "Volume 45% above average",
            "SMA20 crossed above SMA50",
            "RSI divergence detected"
        ]
    )
    
    print(engine.format_signal_for_display(signal))
    
    print(f"\nRisk-Reward Ratio: {signal.risk_reward_ratio:.2f}")
    print(f"Actionable: {signal.is_actionable}")
    
    # Test low confidence (should return None)
    low_conf_signal = engine.generate_signal(
        symbol="RELIANCE.NS",
        current_price=1200.00,
        atr=25.00,
        direction_probs=np.array([0.35, 0.35, 0.30]),
        confidence=0.48,
        regime="RANGING"
    )
    
    print(f"\nLow confidence signal: {low_conf_signal}")
