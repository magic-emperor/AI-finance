"""
Technical Analysis Engine
Full suite: Fibonacci, RSI, MACD, Bollinger Bands, Support/Resistance,
EMA Ribbons, Volume Profile, Pivot Points.

All calculations are pure math — no hardcoded values.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
import structlog

logger = structlog.get_logger()


@dataclass
class FibonacciLevels:
    """Fibonacci retracement and extension levels."""
    swing_high: float
    swing_low: float
    direction: str  # 'UP' or 'DOWN'

    # Retracement levels (pullback targets)
    r_236: float = 0.0
    r_382: float = 0.0
    r_500: float = 0.0
    r_618: float = 0.0
    r_786: float = 0.0

    # Extension levels (profit targets)
    e_1272: float = 0.0
    e_1618: float = 0.0
    e_2000: float = 0.0
    e_2618: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "swing_high": self.swing_high,
            "swing_low": self.swing_low,
            "direction": self.direction,
            "retracements": {
                "0.236": round(self.r_236, 2),
                "0.382": round(self.r_382, 2),
                "0.500": round(self.r_500, 2),
                "0.618": round(self.r_618, 2),
                "0.786": round(self.r_786, 2),
            },
            "extensions": {
                "1.272": round(self.e_1272, 2),
                "1.618": round(self.e_1618, 2),
                "2.000": round(self.e_2000, 2),
                "2.618": round(self.e_2618, 2),
            }
        }


class TechnicalAnalysisEngine:
    """
    Comprehensive technical analysis suite.
    All methods work on pandas DataFrames with columns:
    Open, High, Low, Close, Volume (standard yfinance format).
    """

    @staticmethod
    def _sf(val):
        """Safely convert a pandas scalar/Series element to float."""
        if hasattr(val, 'item'):
            return float(val.item())
        return float(val)

    # ═══════════════════════════════════════════════════════════
    # FIBONACCI
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def find_swing_points(df: pd.DataFrame, lookback: int = 20) -> Tuple[float, float, str]:
        """
        Find the most recent swing high and swing low.
        Returns (swing_high, swing_low, trend_direction).
        """
        if len(df) < lookback:
            lookback = max(5, len(df) // 2)

        recent = df.tail(lookback)
        swing_high = TechnicalAnalysisEngine._sf(recent['High'].max())
        swing_low = TechnicalAnalysisEngine._sf(recent['Low'].min())

        high_idx = recent['High'].idxmax()
        low_idx = recent['Low'].idxmin()

        # If the high came AFTER the low, trend is UP (low→high)
        # If the low came AFTER the high, trend is DOWN (high→low)
        if high_idx >= low_idx:
            direction = "UP"
        else:
            direction = "DOWN"

        return swing_high, swing_low, direction

    @staticmethod
    def calculate_fibonacci_levels(swing_high: float, swing_low: float, direction: str = "UP") -> FibonacciLevels:
        """
        Calculate Fibonacci retracement and extension levels.

        For UPTREND (pullback from high):
          Retracement = High - (High - Low) * ratio
          Extension = High + (High - Low) * (ratio - 1)

        For DOWNTREND (pullback from low):
          Retracement = Low + (High - Low) * ratio
          Extension = Low - (High - Low) * (ratio - 1)
        """
        diff = swing_high - swing_low

        if direction == "UP":
            # Retracements: price pulling back from the high
            r_236 = swing_high - diff * 0.236
            r_382 = swing_high - diff * 0.382
            r_500 = swing_high - diff * 0.500
            r_618 = swing_high - diff * 0.618
            r_786 = swing_high - diff * 0.786

            # Extensions: price breaking above the high
            e_1272 = swing_low + diff * 1.272
            e_1618 = swing_low + diff * 1.618
            e_2000 = swing_low + diff * 2.000
            e_2618 = swing_low + diff * 2.618
        else:
            # Retracements: price pulling back from the low
            r_236 = swing_low + diff * 0.236
            r_382 = swing_low + diff * 0.382
            r_500 = swing_low + diff * 0.500
            r_618 = swing_low + diff * 0.618
            r_786 = swing_low + diff * 0.786

            # Extensions: price breaking below the low
            e_1272 = swing_high - diff * 1.272
            e_1618 = swing_high - diff * 1.618
            e_2000 = swing_high - diff * 2.000
            e_2618 = swing_high - diff * 2.618

        return FibonacciLevels(
            swing_high=swing_high,
            swing_low=swing_low,
            direction=direction,
            r_236=r_236, r_382=r_382, r_500=r_500, r_618=r_618, r_786=r_786,
            e_1272=e_1272, e_1618=e_1618, e_2000=e_2000, e_2618=e_2618,
        )

    # ═══════════════════════════════════════════════════════════
    # RSI (Relative Strength Index)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        RSI using Wilder's smoothing method (exponential moving average).
        Returns Series of RSI values (0-100).
        """
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()

        rs = avg_gain / avg_loss.replace(0, np.inf)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def get_rsi_signal(rsi_value: float) -> Dict[str, Any]:
        """Interpret RSI value."""
        if rsi_value >= 70:
            return {"signal": "OVERBOUGHT", "strength": min(1.0, (rsi_value - 70) / 30), "bias": "SELL"}
        elif rsi_value <= 30:
            return {"signal": "OVERSOLD", "strength": min(1.0, (30 - rsi_value) / 30), "bias": "BUY"}
        elif rsi_value >= 60:
            return {"signal": "BULLISH_MOMENTUM", "strength": (rsi_value - 50) / 20, "bias": "BUY"}
        elif rsi_value <= 40:
            return {"signal": "BEARISH_MOMENTUM", "strength": (50 - rsi_value) / 20, "bias": "SELL"}
        else:
            return {"signal": "NEUTRAL", "strength": 0.0, "bias": "HOLD"}

    # ═══════════════════════════════════════════════════════════
    # MACD (Moving Average Convergence Divergence)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal_period: int = 9) -> Dict[str, pd.Series]:
        """
        Calculate MACD line, signal line, and histogram.
        """
        ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line

        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram
        }

    @staticmethod
    def get_macd_signal(macd_val: float, signal_val: float, hist_val: float, prev_hist: float) -> Dict[str, Any]:
        """Interpret MACD values."""
        crossover = None
        if prev_hist <= 0 < hist_val:
            crossover = "BULLISH_CROSSOVER"
        elif prev_hist >= 0 > hist_val:
            crossover = "BEARISH_CROSSOVER"

        bias = "BUY" if macd_val > signal_val else "SELL" if macd_val < signal_val else "HOLD"
        strength = min(1.0, abs(hist_val) / max(abs(macd_val), 0.001))

        return {
            "crossover": crossover,
            "bias": bias,
            "strength": strength,
            "macd": round(macd_val, 4),
            "signal": round(signal_val, 4),
            "histogram": round(hist_val, 4)
        }

    # ═══════════════════════════════════════════════════════════
    # BOLLINGER BANDS
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> Dict[str, pd.Series]:
        """Calculate Bollinger Bands."""
        sma = df['Close'].rolling(window=period).mean()
        rolling_std = df['Close'].rolling(window=period).std()

        upper = sma + (rolling_std * std_dev)
        lower = sma - (rolling_std * std_dev)
        bandwidth = ((upper - lower) / sma) * 100  # Percentage bandwidth

        return {
            "upper": upper,
            "middle": sma,
            "lower": lower,
            "bandwidth": bandwidth,
        }

    @staticmethod
    def get_bollinger_signal(price: float, upper: float, lower: float, middle: float, bandwidth: float) -> Dict[str, Any]:
        """Interpret Bollinger Band position."""
        position = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

        if position > 0.95:
            signal = "UPPER_BAND_TOUCH"
            bias = "SELL"
        elif position < 0.05:
            signal = "LOWER_BAND_TOUCH"
            bias = "BUY"
        elif bandwidth < 3.0:  # Squeeze
            signal = "SQUEEZE"
            bias = "BREAKOUT_PENDING"
        elif price > middle:
            signal = "ABOVE_MIDDLE"
            bias = "BUY"
        else:
            signal = "BELOW_MIDDLE"
            bias = "SELL"

        return {
            "signal": signal,
            "bias": bias,
            "band_position": round(position, 3),
            "bandwidth": round(bandwidth, 2),
            "squeeze": bandwidth < 3.0
        }

    # ═══════════════════════════════════════════════════════════
    # EMA RIBBONS (8, 21, 55 — trend confirmation)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def calculate_ema_ribbon(df: pd.DataFrame, periods: List[int] = None) -> Dict[str, pd.Series]:
        """Calculate EMA ribbon for trend detection."""
        if periods is None:
            periods = [8, 21, 55]
        return {f"ema_{p}": df['Close'].ewm(span=p, adjust=False).mean() for p in periods}

    @staticmethod
    def get_ema_trend(emas: Dict[str, float]) -> Dict[str, Any]:
        """Determine trend from EMA ribbon."""
        values = list(emas.values())
        # Perfect bullish: EMA8 > EMA21 > EMA55
        if all(values[i] > values[i + 1] for i in range(len(values) - 1)):
            return {"trend": "STRONG_BULLISH", "strength": 1.0, "bias": "BUY"}
        # Perfect bearish: EMA8 < EMA21 < EMA55
        elif all(values[i] < values[i + 1] for i in range(len(values) - 1)):
            return {"trend": "STRONG_BEARISH", "strength": 1.0, "bias": "SELL"}
        # Mixed
        elif values[0] > values[1]:
            return {"trend": "WEAK_BULLISH", "strength": 0.5, "bias": "BUY"}
        elif values[0] < values[1]:
            return {"trend": "WEAK_BEARISH", "strength": 0.5, "bias": "SELL"}
        else:
            return {"trend": "FLAT", "strength": 0.0, "bias": "HOLD"}

    # ═══════════════════════════════════════════════════════════
    # SUPPORT / RESISTANCE (Pivot Points + recent swing levels)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def calculate_pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
        """
        Classic pivot points: P, R1, R2, R3, S1, S2, S3.
        Uses the previous candle's H/L/C.
        """
        pivot = (high + low + close) / 3
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)
        r3 = high + 2 * (pivot - low)
        s3 = low - 2 * (high - pivot)

        return {
            "pivot": round(pivot, 2),
            "r1": round(r1, 2), "r2": round(r2, 2), "r3": round(r3, 2),
            "s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2),
        }

    @staticmethod
    def detect_support_resistance(df: pd.DataFrame, lookback: int = 50, tolerance_pct: float = 0.5) -> Dict[str, List[float]]:
        """
        Detect support and resistance levels from recent price clusters.
        Groups nearby prices (within tolerance_pct) into levels.
        """
        if len(df) < lookback:
            lookback = len(df)

        recent = df.tail(lookback)
        current_price = TechnicalAnalysisEngine._sf(recent['Close'].iloc[-1])
        tolerance = current_price * (tolerance_pct / 100)

        # Collect all swing highs and lows
        price_levels = []
        for i in range(2, len(recent) - 2):
            h_i = TechnicalAnalysisEngine._sf(recent['High'].iloc[i])
            h_prev = TechnicalAnalysisEngine._sf(recent['High'].iloc[i-1])
            h_next = TechnicalAnalysisEngine._sf(recent['High'].iloc[i+1])
            l_i = TechnicalAnalysisEngine._sf(recent['Low'].iloc[i])
            l_prev = TechnicalAnalysisEngine._sf(recent['Low'].iloc[i-1])
            l_next = TechnicalAnalysisEngine._sf(recent['Low'].iloc[i+1])
            if h_i > h_prev and h_i > h_next:
                price_levels.append(h_i)
            if l_i < l_prev and l_i < l_next:
                price_levels.append(l_i)

        if not price_levels:
            return {"support": [TechnicalAnalysisEngine._sf(recent['Low'].min())], "resistance": [TechnicalAnalysisEngine._sf(recent['High'].max())]}

        # Cluster nearby levels
        price_levels.sort()
        clusters = []
        current_cluster = [price_levels[0]]
        for p in price_levels[1:]:
            if p - current_cluster[-1] < tolerance:
                current_cluster.append(p)
            else:
                clusters.append(np.mean(current_cluster))
                current_cluster = [p]
        clusters.append(np.mean(current_cluster))

        support = [round(c, 2) for c in clusters if c < current_price]
        resistance = [round(c, 2) for c in clusters if c > current_price]

        # Keep top 3 nearest each
        support = sorted(support, reverse=True)[:3]
        resistance = sorted(resistance)[:3]

        return {"support": support, "resistance": resistance}

    # ═══════════════════════════════════════════════════════════
    # VOLUME ANALYSIS
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def analyze_volume(df: pd.DataFrame, period: int = 20) -> Dict[str, Any]:
        """Analyze volume relative to average."""
        if 'Volume' not in df.columns:
            return {"relative_volume": 1.0, "signal": "NO_VOLUME_DATA", "trend": "UNKNOWN"}
        
        vol_sum = df['Volume'].sum()
        if hasattr(vol_sum, 'item'):
            vol_sum = vol_sum.item()
        if vol_sum == 0:
            return {"relative_volume": 1.0, "signal": "NO_VOLUME_DATA", "trend": "UNKNOWN"}

        avg_volume = TechnicalAnalysisEngine._sf(df['Volume'].rolling(period).mean().iloc[-1])
        current_volume = TechnicalAnalysisEngine._sf(df['Volume'].iloc[-1])

        if avg_volume == 0 or np.isnan(avg_volume):
            return {"relative_volume": 1.0, "signal": "INSUFFICIENT_DATA", "trend": "UNKNOWN"}

        relative = current_volume / avg_volume

        if relative > 2.0:
            signal = "VOLUME_SPIKE"
        elif relative > 1.3:
            signal = "ABOVE_AVERAGE"
        elif relative < 0.5:
            signal = "DRY_UP"
        else:
            signal = "NORMAL"

        # Price-Volume divergence
        price_change = TechnicalAnalysisEngine._sf(df['Close'].pct_change().iloc[-1])
        if price_change > 0 and relative < 0.7:
            trend = "BEARISH_DIVERGENCE"  # Price up on low volume
        elif price_change < 0 and relative > 1.5:
            trend = "CAPITULATION"  # Price down on high volume
        elif price_change > 0 and relative > 1.5:
            trend = "STRONG_ACCUMULATION"
        else:
            trend = "CONFIRMING"

        return {
            "relative_volume": round(relative, 2),
            "signal": signal,
            "trend": trend,
            "current": int(current_volume),
            "average": int(avg_volume)
        }

    # ═══════════════════════════════════════════════════════════
    # SMART MONEY CONCEPTS (FVG, Sweeps, Sessions)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def detect_fair_value_gaps(df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Detect Fair Value Gaps (Imbalances).
        """
        gaps = []
        if len(df) < 5: return []
        
        # Check last 50 candles for relevant gaps
        recent = df.tail(50)
        # We need index integer access, reset index temporarily
        closes = recent['Close'].values
        highs = recent['High'].values
        lows = recent['Low'].values
        
        for i in range(2, len(recent)):
            # Bullish FVG: Candle i-2 High < Candle i Low (Gap Up)
            # The gap is between High[i-2] and Low[i]
            if lows[i] > highs[i-2]:
                gap_size = lows[i] - highs[i-2]
                if gap_size > 0:
                    gaps.append({
                        "type": "BULLISH_FVG",
                        "top": float(lows[i]),
                        "bottom": float(highs[i-2]),
                        "size": float(gap_size),
                        "index": i  # Relative index
                    })
            
            # Bearish FVG: Candle i-2 Low > Candle i High (Gap Down)
            # The gap is between Low[i-2] and High[i]
            if highs[i] < lows[i-2]:
                gap_size = lows[i-2] - highs[i]
                if gap_size > 0:
                    gaps.append({
                        "type": "BEARISH_FVG",
                        "top": float(lows[i-2]),
                        "bottom": float(highs[i]),
                        "size": float(gap_size),
                        "index": i
                    })
        
        # Return only unmitigated gaps (simplification: return last 3)
        return gaps[-3:]

    @staticmethod
    def detect_liquidity_sweeps(df: pd.DataFrame) -> Dict[str, Any]:
        """
        Detect stop-hunts (Liquidity Sweeps).
        Price breaks a swing point but closes back inside.
        """
        if len(df) < 10: return {}
        
        curr_high = TechnicalAnalysisEngine._sf(df['High'].iloc[-1])
        curr_low = TechnicalAnalysisEngine._sf(df['Low'].iloc[-1])
        curr_close = TechnicalAnalysisEngine._sf(df['Close'].iloc[-1])
        
        prev_high = TechnicalAnalysisEngine._sf(df['High'].iloc[-2])
        prev_low = TechnicalAnalysisEngine._sf(df['Low'].iloc[-2])
        
        # Simple IDM (Inducement) Logic: 
        # Did we sweep the previous high but close below it?
        if curr_high > prev_high and curr_close < prev_high:
            return {"pattern": "BEARISH_SWEEP", "level": prev_high, "bias": "SELL"}
            
        # Did we sweep the previous low but close above it?
        if curr_low < prev_low and curr_close > prev_low:
            return {"pattern": "BULLISH_SWEEP", "level": prev_low, "bias": "BUY"}
            
        return {}

    @staticmethod
    def detect_market_phase(current_utc_hour: int = None) -> Dict[str, str]:
        """
        Identify AMD Phase based on Session Time (UTC).
        Asian (0-8): Accumulation
        London (8-13): Manipulation (Judas Swing)
        NY (13-20): Distribution (Trend)
        """
        if current_utc_hour is None:
            from datetime import datetime, timezone
            current_utc_hour = datetime.now(timezone.utc).hour
            
        if 0 <= current_utc_hour < 8:
            return {"phase": "ACCUMULATION", "session": "ASIAN", "bias": "RANGE"}
        elif 8 <= current_utc_hour < 13:
            return {"phase": "MANIPULATION", "session": "LONDON", "bias": "BREAKOUT/FAKE"}
        elif 13 <= current_utc_hour < 21:
            return {"phase": "DISTRIBUTION", "session": "NEW_YORK", "bias": "TREND"}
        else:
            return {"phase": "SLOW", "session": "OFF_HOURS", "bias": "HOLD"}

    # ═══════════════════════════════════════════════════════════
    # FULL ANALYSIS (Combines everything)
    # ═══════════════════════════════════════════════════════════

    def full_analysis(self, df: pd.DataFrame, current_price: float = None) -> Dict[str, Any]:
        """
        Run the complete technical analysis suite.
        Returns a structured dict with all signals and levels.
        """
        if df is None or len(df) < 10:
            return {"error": "Insufficient data for analysis", "data_points": len(df) if df is not None else 0}

        # Flatten MultiIndex columns from yfinance (e.g., ('Close', 'ITC.NS') -> 'Close')
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = df.columns.get_level_values(0)

        if current_price is None:
            current_price = df['Close'].iloc[-1]
            if hasattr(current_price, 'item'):
                current_price = current_price.item()
            current_price = float(current_price)

        result = {"price": current_price, "data_points": len(df)}

        try:
            # Fibonacci
            swing_high, swing_low, direction = self.find_swing_points(df)
            fib = self.calculate_fibonacci_levels(swing_high, swing_low, direction)
            result["fibonacci"] = fib.to_dict()
        except Exception as e:
            logger.error("fibonacci_calc_failed", error=str(e))
            result["fibonacci"] = None

        try:
            # RSI
            rsi_series = self.calculate_rsi(df)
            rsi_val = rsi_series.iloc[-1]
            if hasattr(rsi_val, 'item'):
                rsi_val = rsi_val.item()
            rsi_val = float(rsi_val) if not np.isnan(rsi_val) else 50.0
            result["rsi"] = {"value": round(rsi_val, 2), **self.get_rsi_signal(rsi_val)}
        except Exception as e:
            logger.error("rsi_calc_failed", error=str(e))
            result["rsi"] = None

        try:
            # MACD
            macd_data = self.calculate_macd(df)
            macd_val = self._sf(macd_data["macd"].iloc[-1])
            signal_val = self._sf(macd_data["signal"].iloc[-1])
            hist_val = self._sf(macd_data["histogram"].iloc[-1])
            prev_hist = self._sf(macd_data["histogram"].iloc[-2]) if len(macd_data["histogram"]) > 1 else 0.0
            result["macd"] = self.get_macd_signal(macd_val, signal_val, hist_val, prev_hist)
        except Exception as e:
            logger.error("macd_calc_failed", error=str(e))
            result["macd"] = None

        try:
            # Bollinger Bands
            bb = self.calculate_bollinger_bands(df)
            upper_val = self._sf(bb["upper"].iloc[-1])
            lower_val = self._sf(bb["lower"].iloc[-1])
            middle_val = self._sf(bb["middle"].iloc[-1])
            bw_val = self._sf(bb["bandwidth"].iloc[-1])
            result["bollinger"] = self.get_bollinger_signal(current_price, upper_val, lower_val, middle_val, bw_val)
            result["bollinger"]["upper"] = round(upper_val, 2)
            result["bollinger"]["lower"] = round(lower_val, 2)
            result["bollinger"]["middle"] = round(middle_val, 2)
        except Exception as e:
            logger.error("bollinger_calc_failed", error=str(e))
            result["bollinger"] = None

        try:
            # EMA Ribbon
            ema_series = self.calculate_ema_ribbon(df)
            ema_latest = {k: self._sf(v.iloc[-1]) for k, v in ema_series.items()}
            result["ema_ribbon"] = {**self.get_ema_trend(ema_latest), "values": {k: round(v, 2) for k, v in ema_latest.items()}}
        except Exception as e:
            logger.error("ema_calc_failed", error=str(e))
            result["ema_ribbon"] = None

        try:
            # Support/Resistance
            sr = self.detect_support_resistance(df)
            result["support_resistance"] = sr
        except Exception as e:
            logger.error("sr_calc_failed", error=str(e))
            result["support_resistance"] = None

        try:
            # Pivot Points (from previous candle)
            prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
            result["pivot_points"] = self.calculate_pivot_points(self._sf(prev['High']), self._sf(prev['Low']), self._sf(prev['Close']))
        except Exception as e:
            logger.error("pivot_calc_failed", error=str(e))
            result["pivot_points"] = None

        try:
            # Volume
            result["volume"] = self.analyze_volume(df)
        except Exception as e:
            logger.error("volume_calc_failed", error=str(e))
            result["volume"] = None

        try:
            # Smart Money Concepts
            result["fvg"] = self.detect_fair_value_gaps(df)
            result["liquidity_sweep"] = self.detect_liquidity_sweeps(df)
            result["market_phase"] = self.detect_market_phase()
        except Exception as e:
            logger.error("smc_calc_failed", error=str(e))
            result["fvg"] = []


        # Overall bias consensus
        result["consensus"] = self._compute_consensus(result)

        return result

    def _compute_consensus(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Compute an overall consensus from all indicators."""
        buy_votes = 0
        sell_votes = 0
        total_weight = 0
        reasons = []

        # RSI (weight: 2)
        if analysis.get("rsi"):
            w = 2
            total_weight += w
            rsi_bias = analysis["rsi"].get("bias")
            if rsi_bias == "BUY":
                buy_votes += w
                reasons.append(f"RSI {analysis['rsi']['value']}: {analysis['rsi']['signal']}")
            elif rsi_bias == "SELL":
                sell_votes += w
                reasons.append(f"RSI {analysis['rsi']['value']}: {analysis['rsi']['signal']}")

        # MACD (weight: 2)
        if analysis.get("macd"):
            w = 2
            total_weight += w
            if analysis["macd"]["bias"] == "BUY":
                buy_votes += w
                if analysis["macd"].get("crossover") == "BULLISH_CROSSOVER":
                    buy_votes += 1  # Bonus for crossover
                    reasons.append("MACD: BULLISH CROSSOVER ⬆️")
            elif analysis["macd"]["bias"] == "SELL":
                sell_votes += w
                if analysis["macd"].get("crossover") == "BEARISH_CROSSOVER":
                    sell_votes += 1
                    reasons.append("MACD: BEARISH CROSSOVER ⬇️")

        # Bollinger (weight: 1.5)
        if analysis.get("bollinger"):
            w = 1.5
            total_weight += w
            bb_bias = analysis["bollinger"]["bias"]
            if bb_bias == "BUY":
                buy_votes += w
                reasons.append(f"Bollinger: Near lower band ({analysis['bollinger']['band_position']})")
            elif bb_bias == "SELL":
                sell_votes += w
                reasons.append(f"Bollinger: Near upper band ({analysis['bollinger']['band_position']})")

        # EMA Ribbon (weight: 2.5 — trend is king)
        if analysis.get("ema_ribbon"):
            w = 2.5
            total_weight += w
            ema_bias = analysis["ema_ribbon"]["bias"]
            if ema_bias == "BUY":
                buy_votes += w * analysis["ema_ribbon"]["strength"]
                reasons.append(f"EMA Ribbon: {analysis['ema_ribbon']['trend']}")
            elif ema_bias == "SELL":
                sell_votes += w * analysis["ema_ribbon"]["strength"]
                reasons.append(f"EMA Ribbon: {analysis['ema_ribbon']['trend']}")

        # Volume confirmation (weight: 1.5)
        if analysis.get("volume"):
            w = 1.5
            total_weight += w
            vol_trend = analysis["volume"].get("trend", "UNKNOWN")
            if vol_trend == "STRONG_ACCUMULATION":
                buy_votes += w
                reasons.append("Volume: Strong accumulation detected")
            elif vol_trend in ("CAPITULATION", "BEARISH_DIVERGENCE"):
                sell_votes += w
                reasons.append(f"Volume: {vol_trend}")

        # Smart Money (High Weight: 3.0)
        # FVG
        for fvg in analysis.get("fvg", []):
            # Check if current price is IN the gap
            curr_price = analysis['price']
            if fvg['type'] == 'BULLISH_FVG' and fvg['bottom'] <= curr_price <= fvg['top']:
                # Price retraced into bullish gap -> Buy
                buy_votes += 3.0
                reasons.append(f"SMC: Retest of Bullish FVG at {fvg['bottom']}")
            elif fvg['type'] == 'BEARISH_FVG' and fvg['bottom'] <= curr_price <= fvg['top']:
                # Price retraced into bearish gap -> Sell
                sell_votes += 3.0
                reasons.append(f"SMC: Retest of Bearish FVG at {fvg['top']}")
        
        # Liquidity Sweep
        sweep = analysis.get("liquidity_sweep")
        if sweep:
            w = 4.0 # Very strong signal
            total_weight += w
            if sweep['bias'] == 'BUY':
                buy_votes += w
                reasons.append(f"SMC: Bullish Liquidity Sweep at {sweep['level']}")
            elif sweep['bias'] == 'SELL':
                sell_votes += w
                reasons.append(f"SMC: Bearish Liquidity Sweep at {sweep['level']}")

        # Market Phase (Context Bias)
        phase = analysis.get("market_phase")
        if phase:
            if phase['phase'] == 'ACCUMULATION':
                # Range market, lower trend signals
                total_weight += 1
            elif phase['phase'] == 'MANIPULATION':
                # London Open - look for sweeps
                pass 
            elif phase['phase'] == 'DISTRIBUTION':
                # NY - Trend following is good
                pass

        if total_weight == 0:
            return {"direction": "HOLD", "confidence": 0.0, "reasons": ["Insufficient indicator data"]}

        buy_pct = buy_votes / total_weight
        sell_pct = sell_votes / total_weight

        if buy_pct > sell_pct and buy_pct > 0.4:
            direction = "BUY"
            confidence = min(0.95, buy_pct)
        elif sell_pct > buy_pct and sell_pct > 0.4:
            direction = "SELL"
            confidence = min(0.95, sell_pct)
        else:
            direction = "HOLD"
            confidence = max(buy_pct, sell_pct)

        return {
            "direction": direction,
            "confidence": round(confidence, 3),
            "buy_score": round(buy_pct, 3),
            "sell_score": round(sell_pct, 3),
            "reasons": reasons
        }

    def generate_analysis_summary(self, analysis: Dict[str, Any], symbol: str) -> str:
        """Generate a human-readable summary of the technical analysis."""
        if "error" in analysis:
            return f"Cannot analyze {symbol}: {analysis['error']}"

        parts = [f"📊 Technical Analysis for {symbol} @ {analysis['price']:,.2f}"]

        consensus = analysis.get("consensus", {})
        parts.append(f"Overall: {consensus.get('direction', 'N/A')} (Confidence: {consensus.get('confidence', 0):.1%})")

        if analysis.get("fibonacci"):
            fib = analysis["fibonacci"]
            parts.append(f"Fibonacci ({fib['direction']} trend): Key levels at {fib['retracements']['0.382']}, {fib['retracements']['0.618']}")

        if analysis.get("rsi"):
            parts.append(f"RSI: {analysis['rsi']['value']} ({analysis['rsi']['signal']})")

        if analysis.get("macd"):
            macd = analysis["macd"]
            xover = f" | {macd['crossover']}" if macd.get("crossover") else ""
            parts.append(f"MACD: {macd['bias']}{xover}")

        if analysis.get("ema_ribbon"):
            parts.append(f"Trend: {analysis['ema_ribbon']['trend']}")

        if analysis.get("volume"):
            parts.append(f"Volume: {analysis['volume']['signal']} (x{analysis['volume']['relative_volume']})")

        for reason in consensus.get("reasons", []):
            parts.append(f"  → {reason}")

        return " | ".join(parts[:4]) + "\n" + "\n".join(parts[4:])


# Module-level singleton
ta_engine = TechnicalAnalysisEngine()
