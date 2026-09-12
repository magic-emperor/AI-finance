# ============================================================
# DEPRECATED - THIS FILE IS NO LONGER ACTIVE
#
# super_brain.py was a standalone BTC-focused brain prototype.
# It was NEVER wired into the main coordinator (signal_generators.py).
#
# Its core concepts (weighted score across trend/momentum/volatility/
# volume/structure/patterns/sentiment categories) now influence the
# design of the individual brain files, but the code itself is not used.
#
# Kept for reference. DO NOT import this file into live trading.
# ============================================================
pass


# """
# Super Brain -- Single-symbol focused brain that trades like a real professional.

# Designed for BTC-USD. Combines EVERY indicator a real trader uses:
#   1. Trend:      EMA 9/21/50, SMA 200 (long-term filter)
#   2. Momentum:   RSI 14, MACD 12/26/9, Stochastic 14/3/3
#   3. Volatility: Bollinger Bands 20/2, ATR 14, BB squeeze
#   4. Volume:     VWAP deviation, volume spike ratio, OBV trend
#   5. Structure:  Fibonacci retracement, daily hi/lo, pivots
#   6. Patterns:   Engulfing, hammer, doji, pinbar detection
#   7. Sentiment:  Gemini-powered news scoring (immediate + daily)
#   8. Multi-TF:   1h primary, 4h confirmation

# Decision model: WEIGHTED SCORE across all 7 categories.
# Each category votes BUY (+1), SELL (-1), or NEUTRAL (0).
# Weighted sum > threshold → trade. Confidence = score strength.

# ISOLATION: uses only local computation + optional Gemini call.
# """
# from __future__ import annotations

# import numpy as np
# import pandas as pd
# import structlog
# from typing import Dict, Optional, Tuple

# logger = structlog.get_logger()


# # ═══════════════════════════════════════════════════════════════
# # INDICATOR CALCULATIONS
# # ═══════════════════════════════════════════════════════════════

# def _ema(series: pd.Series, period: int) -> pd.Series:
#     return series.ewm(span=period, adjust=False).mean()


# def _rsi(close: pd.Series, period: int = 14) -> float:
#     delta = close.diff()
#     gain = delta.where(delta > 0, 0.0).rolling(period).mean()
#     loss = (-delta).where(delta < 0, 0.0).rolling(period).mean()
#     rs = gain / loss.replace(0, float('nan'))
#     rsi = 100 - (100 / (1 + rs))
#     return float(rsi.iloc[-1]) if not rsi.empty and not pd.isna(rsi.iloc[-1]) else 50.0


# def _stochastic(hist: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Tuple[float, float]:
#     """Stochastic %K and %D."""
#     low_min = hist['Low'].rolling(k_period).min()
#     high_max = hist['High'].rolling(k_period).max()
#     denom = high_max - low_min
#     k = ((hist['Close'] - low_min) / denom.replace(0, float('nan'))) * 100
#     d = k.rolling(d_period).mean()
#     return float(k.iloc[-1] or 50), float(d.iloc[-1] or 50)


# def _atr(hist: pd.DataFrame, period: int = 14) -> float:
#     high, low = hist['High'], hist['Low']
#     prev_close = hist['Close'].shift(1)
#     tr = pd.concat([
#         high - low,
#         (high - prev_close).abs(),
#         (low - prev_close).abs(),
#     ], axis=1).max(axis=1)
#     return float(tr.rolling(period).mean().iloc[-1] or 0)


# def _obv_trend(hist: pd.DataFrame, lookback: int = 10) -> float:
#     """On-Balance Volume trend: +1 if OBV rising, -1 if falling."""
#     close = hist['Close']
#     volume = hist['Volume']
#     direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
#     obv = (volume * direction).cumsum()
#     if len(obv) < lookback + 1:
#         return 0.0
#     obv_slope = float(obv.iloc[-1] - obv.iloc[-lookback]) / max(float(obv.iloc[-lookback:].std()), 1)
#     return max(-1, min(1, obv_slope / 3))


# # ═══════════════════════════════════════════════════════════════
# # CANDLESTICK PATTERN DETECTION
# # ═══════════════════════════════════════════════════════════════

# def _detect_candlestick_patterns(hist: pd.DataFrame) -> Dict[str, int]:
#     """
#     Detect basic candlestick patterns on last 3 candles.
#     Returns dict of pattern_name -> signal (+1 = bullish, -1 = bearish, 0 = none).
#     """
#     if len(hist) < 3:
#         return {}
    
#     c = hist.iloc[-1]  # current candle
#     p = hist.iloc[-2]  # previous candle
    
#     o, h, l, cl = float(c['Open']), float(c['High']), float(c['Low']), float(c['Close'])
#     po, ph, pl, pcl = float(p['Open']), float(p['High']), float(p['Low']), float(p['Close'])
    
#     body = abs(cl - o)
#     upper_wick = h - max(o, cl)
#     lower_wick = min(o, cl) - l
#     total_range = h - l if h > l else 0.001
    
#     patterns = {}
    
#     # Bullish engulfing: prev red, current green, current body engulfs prev
#     if pcl < po and cl > o and cl > po and o < pcl:
#         patterns['bullish_engulfing'] = 1
    
#     # Bearish engulfing
#     if pcl > po and cl < o and cl < po and o > pcl:
#         patterns['bearish_engulfing'] = -1
    
#     # Hammer (bullish): small body at top, long lower wick
#     if body / total_range < 0.35 and lower_wick > body * 2 and upper_wick < body:
#         patterns['hammer'] = 1
    
#     # Shooting star (bearish): small body at bottom, long upper wick
#     if body / total_range < 0.35 and upper_wick > body * 2 and lower_wick < body:
#         patterns['shooting_star'] = -1
    
#     # Doji: very small body
#     if body / total_range < 0.10:
#         patterns['doji'] = 0  # indecision, not directional
    
#     # Pin bar (bullish): long lower wick, close near high
#     if lower_wick > total_range * 0.6 and (h - cl) < total_range * 0.15:
#         patterns['bullish_pinbar'] = 1
    
#     # Pin bar (bearish): long upper wick, close near low
#     if upper_wick > total_range * 0.6 and (cl - l) < total_range * 0.15:
#         patterns['bearish_pinbar'] = -1
    
#     return patterns


# # ═══════════════════════════════════════════════════════════════
# # FIBONACCI & SUPPORT/RESISTANCE
# # ═══════════════════════════════════════════════════════════════

# def _calc_sr_levels(hist: pd.DataFrame) -> Dict[str, float]:
#     """Compute support/resistance from Fibonacci + daily hi-lo + round numbers."""
#     swing_high = float(hist['High'].max())
#     swing_low = float(hist['Low'].min())
#     fib_range = swing_high - swing_low
    
#     levels = {}
#     if fib_range > 0:
#         levels['fib_236'] = swing_high - fib_range * 0.236
#         levels['fib_382'] = swing_high - fib_range * 0.382
#         levels['fib_500'] = swing_high - fib_range * 0.500
#         levels['fib_618'] = swing_high - fib_range * 0.618
#         levels['swing_high'] = swing_high
#         levels['swing_low'] = swing_low
    
#     if len(hist) >= 24:
#         levels['daily_high'] = float(hist['High'].iloc[-24:].max())
#         levels['daily_low'] = float(hist['Low'].iloc[-24:].min())
    
#     # Round numbers (psychological levels) for BTC
#     price = float(hist['Close'].iloc[-1])
#     # Find nearest $1000 levels
#     base = int(price / 1000) * 1000
#     for offset in [-2000, -1000, 0, 1000, 2000]:
#         lvl = base + offset
#         if lvl > 0:
#             levels[f'round_{lvl}'] = float(lvl)
    
#     return levels


# def _price_near_sr(price: float, levels: Dict[str, float], atr: float) -> Tuple[str, float]:
#     """Check if price is near any S/R level. Returns (level_name, distance_in_atr)."""
#     if atr <= 0:
#         return ('none', 99)
#     nearest_name, nearest_dist = 'none', 99.0
#     for name, lvl in levels.items():
#         dist_atr = abs(price - lvl) / atr
#         if dist_atr < nearest_dist:
#             nearest_dist = dist_atr
#             nearest_name = name
#     return nearest_name, nearest_dist


# # ═══════════════════════════════════════════════════════════════
# # SUPER BRAIN CORE
# # ═══════════════════════════════════════════════════════════════

# def super_brain_signal(
#     hist: pd.DataFrame,
#     symbol: str = "BTC-USD",
#     gemini_client=None,
# ) -> Dict:
#     """
#     The Super Brain: one brain, all tools, focused on one symbol.
    
#     Returns a comprehensive analysis dict with:
#     - direction: BUY / SELL / HOLD
#     - confidence: 0.0-1.0
#     - score: weighted vote total
#     - analysis: detailed breakdown of every indicator
#     - reasoning: human-readable summary
#     """
#     if hist is None or len(hist) < 50:
#         return {
#             'direction': 'HOLD', 'confidence': 0.0,
#             'reasoning': f'Insufficient data ({len(hist) if hist is not None else 0} candles, need 50)',
#             'analysis': {},
#         }
    
#     close = hist['Close']
#     price = float(close.iloc[-1])
#     atr_val = _atr(hist)
    
#     # Category weights (total = 1.0)
#     WEIGHTS = {
#         'trend':      0.25,
#         'momentum':   0.20,
#         'volatility': 0.10,
#         'volume':     0.10,
#         'structure':  0.15,
#         'patterns':   0.10,
#         'sentiment':  0.10,
#     }
    
#     votes = {}       # category -> vote (-1 to +1)
#     analysis = {}    # category -> detail dict
    
#     # ── 1. TREND (EMA 9/21/50, SMA 200 filter) ──────────────
#     ema9 = float(_ema(close, 9).iloc[-1])
#     ema21 = float(_ema(close, 21).iloc[-1])
#     ema50 = float(_ema(close, 50).iloc[-1])
#     sma200 = float(close.rolling(min(200, len(close))).mean().iloc[-1])
    
#     trend_score = 0
#     # EMA alignment: 9 > 21 > 50 = strong bullish
#     if ema9 > ema21 > ema50:
#         trend_score = 0.8
#     elif ema9 < ema21 < ema50:
#         trend_score = -0.8
#     elif ema9 > ema21:
#         trend_score = 0.4
#     elif ema9 < ema21:
#         trend_score = -0.4
    
#     # SMA 200 filter (long-term trend)
#     above_200 = price > sma200
#     if above_200 and trend_score > 0:
#         trend_score = min(1.0, trend_score + 0.2)  # confirm
#     elif not above_200 and trend_score < 0:
#         trend_score = max(-1.0, trend_score - 0.2)  # confirm
    
#     votes['trend'] = trend_score
#     analysis['trend'] = {
#         'ema9': ema9, 'ema21': ema21, 'ema50': ema50,
#         'sma200': sma200, 'above_200': above_200,
#         'alignment': 'BULL' if ema9 > ema21 > ema50 else ('BEAR' if ema9 < ema21 < ema50 else 'MIXED'),
#         'vote': trend_score,
#     }
    
#     # ── 2. MOMENTUM (RSI, MACD, Stochastic) ──────────────────
#     rsi = _rsi(close)
#     stoch_k, stoch_d = _stochastic(hist)
    
#     ema12 = _ema(close, 12)
#     ema26 = _ema(close, 26)
#     macd = ema12 - ema26
#     macd_signal = _ema(macd, 9)
#     macd_hist = float((macd - macd_signal).iloc[-1])
#     macd_bullish = float(macd.iloc[-1]) > float(macd_signal.iloc[-1])
#     macd_hist_slope = float(macd_hist - float((macd - macd_signal).iloc[-3]))
    
#     momentum_score = 0
#     # RSI
#     if rsi < 30:
#         momentum_score += 0.4  # oversold = bullish
#     elif rsi > 70:
#         momentum_score -= 0.4  # overbought = bearish
#     elif rsi < 45:
#         momentum_score += 0.1
#     elif rsi > 55:
#         momentum_score -= 0.1
    
#     # MACD
#     if macd_bullish and macd_hist_slope > 0:
#         momentum_score += 0.3
#     elif not macd_bullish and macd_hist_slope < 0:
#         momentum_score -= 0.3
#     elif macd_bullish:
#         momentum_score += 0.1
#     elif not macd_bullish:
#         momentum_score -= 0.1
    
#     # Stochastic
#     if stoch_k < 20 and stoch_d < 20:
#         momentum_score += 0.3  # oversold
#     elif stoch_k > 80 and stoch_d > 80:
#         momentum_score -= 0.3  # overbought
    
#     momentum_score = max(-1, min(1, momentum_score))
#     votes['momentum'] = momentum_score
#     analysis['momentum'] = {
#         'rsi': rsi, 'stoch_k': stoch_k, 'stoch_d': stoch_d,
#         'macd_bullish': macd_bullish, 'macd_hist': macd_hist,
#         'macd_hist_slope': macd_hist_slope, 'vote': momentum_score,
#     }
    
#     # ── 3. VOLATILITY (BB, ATR, squeeze) ─────────────────────
#     bb_sma = float(close.rolling(20).mean().iloc[-1])
#     bb_std = float(close.rolling(20).std().iloc[-1])
#     upper_bb = bb_sma + 2 * bb_std
#     lower_bb = bb_sma - 2 * bb_std
#     pct_b = (price - lower_bb) / (upper_bb - lower_bb) if (upper_bb - lower_bb) > 0 else 0.5
#     bb_width = (upper_bb - lower_bb) / bb_sma if bb_sma > 0 else 0.02
#     bb_avg_width = float(((hist['Close'].rolling(20).mean() + 2 * hist['Close'].rolling(20).std()) - 
#                            (hist['Close'].rolling(20).mean() - 2 * hist['Close'].rolling(20).std())).div(
#                                hist['Close'].rolling(20).mean()).rolling(20).mean().iloc[-1])
#     is_squeeze = bb_width < bb_avg_width * 0.70
    
#     vol_score = 0
#     if pct_b < 0.05 and rsi < 35:
#         vol_score = 0.8  # at lower BB + oversold
#     elif pct_b > 0.95 and rsi > 65:
#         vol_score = -0.8  # at upper BB + overbought
#     elif pct_b < 0.20:
#         vol_score = 0.4  # near lower band
#     elif pct_b > 0.80:
#         vol_score = -0.4  # near upper band
#     elif is_squeeze:
#         vol_score = 0  # squeeze = wait for breakout
    
#     votes['volatility'] = vol_score
#     analysis['volatility'] = {
#         'pct_b': pct_b, 'bb_width': bb_width, 'is_squeeze': is_squeeze,
#         'upper_bb': upper_bb, 'lower_bb': lower_bb,
#         'atr': atr_val, 'atr_pct': atr_val / price * 100 if price > 0 else 0,
#         'vote': vol_score,
#     }
    
#     # ── 4. VOLUME (VWAP, volume spike, OBV) ──────────────────
#     typical = (hist['High'] + hist['Low'] + hist['Close']) / 3
#     vwap_series = (typical * hist['Volume']).rolling(20).sum() / hist['Volume'].rolling(20).sum()
#     vwap_val = float(vwap_series.iloc[-1]) if not pd.isna(vwap_series.iloc[-1]) else price
#     vwap_dev_atr = (price - vwap_val) / atr_val if atr_val > 0 else 0
    
#     avg_vol = float(hist['Volume'].rolling(20).mean().iloc[-1]) or 1
#     cur_vol = float(hist['Volume'].iloc[-1])
#     # Handle zero-volume candles (weekends, after-hours)
#     vol_ratio = cur_vol / avg_vol if avg_vol > 0 and cur_vol > 0 else 1.0
#     obv = _obv_trend(hist)
    
#     volume_score = 0
#     # VWAP deviation (ATR-based)
#     if vwap_dev_atr < -0.5:
#         volume_score += 0.3  # below VWAP = potential buy
#     elif vwap_dev_atr > 0.5:
#         volume_score -= 0.3  # above VWAP = potential sell
    
#     # Volume spike confirms direction
#     if vol_ratio > 2.0:
#         volume_score += 0.3 * (1 if price > vwap_val else -1)
    
#     # OBV trend
#     volume_score += obv * 0.3
#     volume_score = max(-1, min(1, volume_score))
    
#     votes['volume'] = volume_score
#     analysis['volume'] = {
#         'vwap': vwap_val, 'vwap_dev_atr': vwap_dev_atr,
#         'vol_ratio': vol_ratio, 'obv_trend': obv, 'vote': volume_score,
#     }
    
#     # ── 5. STRUCTURE (Fibonacci, daily hi/lo, round numbers) ──
#     sr_levels = _calc_sr_levels(hist)
#     nearest_sr, dist_to_sr = _price_near_sr(price, sr_levels, atr_val)
    
#     structure_score = 0
#     if dist_to_sr < 0.5:
#         # Price is at a key level
#         sr_price = sr_levels.get(nearest_sr, price)
#         if price < sr_price:
#             structure_score = 0.3  # at support → buy
#         else:
#             structure_score = -0.3  # at resistance → sell
    
#     # Check Fibonacci alignment with trend
#     fib_618 = sr_levels.get('fib_618')
#     fib_382 = sr_levels.get('fib_382')
#     if fib_618 and fib_382:
#         if price < fib_618 and trend_score > 0:
#             structure_score += 0.4  # price pulled back to golden ratio in uptrend
#         elif price > fib_382 and trend_score < 0:
#             structure_score -= 0.4  # price bounced to fib in downtrend
    
#     structure_score = max(-1, min(1, structure_score))
#     votes['structure'] = structure_score
#     analysis['structure'] = {
#         'nearest_sr': nearest_sr, 'dist_to_sr_atr': dist_to_sr,
#         'fib_levels': {k: v for k, v in sr_levels.items() if 'fib' in k},
#         'daily_high': sr_levels.get('daily_high'),
#         'daily_low': sr_levels.get('daily_low'),
#         'vote': structure_score,
#     }
    
#     # ── 6. CANDLESTICK PATTERNS ──────────────────────────────
#     patterns = _detect_candlestick_patterns(hist)
#     pattern_score = 0
#     for pname, psignal in patterns.items():
#         if psignal == 1:
#             pattern_score += 0.4
#         elif psignal == -1:
#             pattern_score -= 0.4
#     pattern_score = max(-1, min(1, pattern_score))
    
#     votes['patterns'] = pattern_score
#     analysis['patterns'] = {
#         'detected': list(patterns.keys()) if patterns else ['none'],
#         'signals': patterns,
#         'vote': pattern_score,
#     }
    
#     # ── 7. NEWS SENTIMENT (Gemini) ───────────────────────────
#     sentiment_score = 0
#     sentiment_evidence = 'No Gemini client available'
#     if gemini_client is not None:
#         try:
#             from market_agent.brain.sentiment_engine import SentimentEngine
#             engine = SentimentEngine(gemini_client=gemini_client)
#             result = engine.score(symbol, strategy_mode='Intraday (Scalp)')
#             sentiment_score = result.get('composite', 0)
#             sentiment_evidence = result.get('evidence', '')
#         except Exception as e:
#             sentiment_evidence = f'Sentiment error: {str(e)[:60]}'
#             logger.debug('super_brain_sentiment_error', error=str(e)[:80])
    
#     votes['sentiment'] = sentiment_score
#     analysis['sentiment'] = {
#         'score': sentiment_score,
#         'evidence': sentiment_evidence,
#         'vote': sentiment_score,
#     }
    
#     # ═══════════════════════════════════════════════════════════
#     # WEIGHTED DECISION
#     # ═══════════════════════════════════════════════════════════
    
#     weighted_score = sum(votes[cat] * WEIGHTS[cat] for cat in WEIGHTS)
    
#     # Direction from weighted score
#     if weighted_score > 0.15:
#         direction = 'BUY'
#     elif weighted_score < -0.15:
#         direction = 'SELL'
#     else:
#         direction = 'HOLD'
    
#     # Confidence from score magnitude + agreement count
#     agreeing = sum(1 for v in votes.values() if (v > 0.1) == (weighted_score > 0) and abs(v) > 0.1)
#     total_voting = sum(1 for v in votes.values() if abs(v) > 0.1)
#     agreement_pct = agreeing / total_voting if total_voting > 0 else 0.5
    
#     confidence = min(0.95, abs(weighted_score) * 1.5 * agreement_pct + 0.30)
#     if direction == 'HOLD':
#         confidence = min(confidence, 0.45)
    
#     # Build reasoning
#     bull_reasons = [f"{cat}({votes[cat]:+.2f})" for cat in votes if votes[cat] > 0.1]
#     bear_reasons = [f"{cat}({votes[cat]:+.2f})" for cat in votes if votes[cat] < -0.1]
    
#     reasoning = (
#         f"Score={weighted_score:+.3f} | "
#         f"Agreement={agreeing}/{total_voting} | "
#         f"BULL: {', '.join(bull_reasons) or 'none'} | "
#         f"BEAR: {', '.join(bear_reasons) or 'none'}"
#     )
    
#     return {
#         'direction': direction,
#         'confidence': round(confidence, 3),
#         'score': round(weighted_score, 4),
#         'reasoning': reasoning,
#         'analysis': analysis,
#         'votes': {k: round(v, 3) for k, v in votes.items()},
#         'weights': WEIGHTS,
#         'agreement_pct': round(agreement_pct, 2),
#         'patterns_detected': list(patterns.keys()) if patterns else [],
#         'nearest_sr': nearest_sr,
#         'dist_to_sr_atr': round(dist_to_sr, 2),
#     }