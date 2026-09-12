# """
# Path A: Signal coordination — imports from individual brain files.
# Each brain lives in its own file under market_agent/brain/:
#   amv_lstm.py           — Brain 1: Temporal trend memory
#   regime_ensemble.py    — Brain 2: Market regime classifier (meta brain)
#   multi_modal_fusion.py — Brain 3: RSI + MACD + divergence
#   multi_timeframe.py    — Brain 4: Cross-timeframe alignment
#   cross_stock_gnn.py    — Brain 5: Institutional flow (VWAP + volume)
#   casual_ensemble.py    — Brain 7: Mean-reversion (Bollinger + RSI)
#   liquidity_sweep.py    — Brain: Stop-hunt structure reversal
#   brain_funding.py      — Brain: Funding rate signal (crypto)

# signal_generators.py = COORDINATOR only. It:
#   1. Imports all brains
#   2. Applies regime gates, momentum gates, delta filter
#   3. Builds signal dicts with ATR-based T1/T2/SL + Fibonacci S/R

# For each brain's logic, confidence formula, and design rationale: read that brain's file.
# """
# from __future__ import annotations

# import numpy as np
# import pandas as pd
# from typing import Dict, Any, List, Optional
# import structlog

# logger = structlog.get_logger()

# from market_agent.data.ingestion.unified_market_data import market_data
# from dataclasses import replace
# from market_agent.brain.health_monitor import get_brain_accuracy

# # ── Import individual brain signal functions (each brain in its own canonical file) ──
# from market_agent.brain.amv_lstm import amv_lstm_signal
# from market_agent.brain.amv_lstm_uptrend import amv_lstm_uptrend_signal
# from market_agent.brain.regime_ensemble import regime_ensemble_signal
# from market_agent.brain.multi_modal_fusion import multi_modal_fusion_signal
# from market_agent.brain.multi_timeframe import multi_timeframe_signal
# from market_agent.brain.cross_stock_gnn import cross_stock_gnn_signal
# from market_agent.brain.causal_ensemble import causal_ensemble_signal   # ← correct spelling
# from market_agent.brain.rl_weighter import rl_weighter_signal
# from market_agent.brain.liquidity_sweep import liquidity_sweep_signal

# # ── Regime gate — per IMPL-PLAN-V3 Fix 2A & Regime Fix #3 ─────────────────
# # Unified Taxonomy from `mff_and_regime.py`
# UNIFIED_REGIMES = {
#     'TRENDING_UP':    {'trust_trend_brains': 0.90, 'trust_mean_rev': 0.15, 'volatility_brains': 0.50},
#     'TRENDING_DOWN':  {'trust_trend_brains': 0.90, 'trust_mean_rev': 0.15, 'volatility_brains': 0.50},
#     'RANGING':        {'trust_trend_brains': 0.25, 'trust_mean_rev': 0.90, 'volatility_brains': 0.60},
#     'VOLATILE':       {'trust_trend_brains': 0.40, 'trust_mean_rev': 0.10, 'volatility_brains': 0.85},
#     'SQUEEZE':        {'trust_trend_brains': 0.20, 'trust_mean_rev': 0.70, 'volatility_brains': 0.95},
#     'CHAOS':          {'trust_trend_brains': 0.00, 'trust_mean_rev': 0.00, 'volatility_brains': 0.00},  # NO trades
# }

# REGIME_MIGRATION_MAP = {
#     'STABLE_TRADING':    'TRENDING_UP',
#     'SCANNING_INTRADAY': 'RANGING',
#     'HYBRID_SCAN':       'RANGING',
#     'VOLATILE_CHAOS':    'CHAOS',
#     'STRONG_TREND_UP':   'TRENDING_UP',
#     'STRONG_TREND_DOWN': 'TRENDING_DOWN',
#     'VOLATILE_BREAKOUT': 'VOLATILE',
#     'LOW_VOLATILITY':    'SQUEEZE',
# }

# BRAIN_REGIME_GATES_UNIFIED = {
#     'AMV-LSTM':           ['TRENDING_DOWN'],
#     'AMV-LSTM-Uptrend':   ['TRENDING_UP'],
#     'Multi-Modal-Fusion': ['RANGING', 'SQUEEZE'],
#     'Multi-Timeframe':    ['TRENDING_UP', 'TRENDING_DOWN'],
#     'Cross-Stock-GNN':    ['VOLATILE', 'RANGING'],
#     'Causal-Ensemble':    ['RANGING', 'SQUEEZE', 'VOLATILE'],
#     'Super-Brain':        ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING'],
#     'RL-Weighter':        ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING'],
#     'Liquidity-Sweep':    ['VOLATILE', 'TRENDING_UP', 'TRENDING_DOWN'],
#     'Regime-Ensemble':    ['ALL'],
# }

# def normalize_regime(regime: str) -> str:
#     """Convert any regime string (old or new) to unified taxonomy."""
#     if regime in UNIFIED_REGIMES: return regime
#     return REGIME_MIGRATION_MAP.get(regime, 'RANGING')

# def regime_allows_brain(brain_name: str, regime: str) -> bool:
#     """Single function to check if a brain can trade in a given regime."""
#     unified = normalize_regime(regime)
#     if unified == 'CHAOS': return False
#     allowed = BRAIN_REGIME_GATES_UNIFIED.get(brain_name, ['ALL'])
#     if 'ALL' in allowed: return True
#     return unified in allowed



# # Brain ID → (name, formula description)
# # NOTE: names use hyphens — must match BRAIN_ADAPTERS in brain_adapters.py
# COUNCIL_BRAIN_FORMULAS = [
#     ("AMV-LSTM",           "temporal"),
#     ("AMV-LSTM-Uptrend",   "pullback_bounce"),
#     ("Regime-Ensemble",    "regime"),
#     ("Multi-Modal-Fusion", "ta_sentiment"),
#     ("Multi-Timeframe",    "multi_tf"),
#     ("Cross-Stock-GNN",    "volume_structure"),
#     ("RL-Weighter",        "momentum_weighted"),
#     ("Causal-Ensemble",    "rsi_bollinger"),
# ]


# def _calc_rsi(series: pd.Series, period: int = 14) -> float:
#     """RSI from close series; returns 0-100 or 50 if insufficient data."""
#     if series is None or len(series) < period + 1:
#         return 50.0
#     delta = series.diff()
#     gain = delta.where(delta > 0, 0.0)
#     loss = (-delta).where(delta < 0, 0.0)
#     avg_gain = gain.rolling(period).mean().iloc[-1]
#     avg_loss = loss.rolling(period).mean().iloc[-1]
#     if avg_loss == 0:
#         return 100.0 if avg_gain > 0 else 50.0
#     rs = avg_gain / avg_loss
#     return float(100 - (100 / (1 + rs)))


# def _calc_sma_cross(close: pd.Series, short: int = 5, long: int = 20) -> Optional[str]:
#     """BUY if short > long, SELL if short < long, else None."""
#     if close is None or len(close) < long:
#         return None
#     s = close.rolling(short).mean().iloc[-1]
#     l = close.rolling(long).mean().iloc[-1]
#     if s > l:
#         return "BUY"
#     if s < l:
#         return "SELL"
#     return "WAIT"


# def _calc_macd_signal(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[str]:
#     """BUY if MACD line > signal line, SELL if below."""
#     if close is None or len(close) < slow + signal:
#         return None
#     ema_fast = close.ewm(span=fast, adjust=False).mean()
#     ema_slow = close.ewm(span=slow, adjust=False).mean()
#     macd = ema_fast - ema_slow
#     sig = macd.ewm(span=signal, adjust=False).mean()
#     if macd.iloc[-1] > sig.iloc[-1]:
#         return "BUY"
#     if macd.iloc[-1] < sig.iloc[-1]:
#         return "SELL"
#     return "WAIT"


# def _calc_bollinger_pos(close: pd.Series, period: int = 20, k: float = 2.0) -> Optional[str]:
#     """BUY near lower band, SELL near upper band."""
#     if close is None or len(close) < period:
#         return None
#     mid = close.rolling(period).mean().iloc[-1]
#     std = close.rolling(period).std().iloc[-1]
#     if std == 0:
#         return "WAIT"
#     upper = mid + k * std
#     lower = mid - k * std
#     last = close.iloc[-1]
#     if last <= lower:
#         return "BUY"
#     if last >= upper:
#         return "SELL"
#     return "WAIT"


# def _calc_volatility_regime(close: pd.Series, period: int = 20) -> Optional[str]:
#     """Trend up → BUY, trend down → SELL (regime)."""
#     if close is None or len(close) < period:
#         return None
#     sma = close.rolling(period).mean()
#     last_close = close.iloc[-1]
#     last_sma = sma.iloc[-1]
#     if last_close > last_sma * 1.002:
#         return "BUY"
#     if last_close < last_sma * 0.998:
#         return "SELL"
#     return "WAIT"


# def _order_flow_proxy(hist: pd.DataFrame, window: int = 20) -> Optional[Dict[str, float]]:
#     """
#     Order flow proxy from OHLCV (Plan A §8): VWAP deviation and volume spike vs 20-bar average.
#     Returns dict with vwap_dev_pct (current price vs VWAP), volume_spike_ratio (last vol / avg).
#     """
#     if hist is None or len(hist) < window:
#         return None
#     need = ["High", "Low", "Close", "Volume"]
#     if not all(c in hist.columns for c in need):
#         return None
#     df = hist.tail(window)
#     typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
#     vwap = (typical * df["Volume"]).sum() / (df["Volume"].sum() or 1e-9)
#     current = float(df["Close"].iloc[-1])
#     vwap_dev_pct = (current - vwap) / vwap * 100 if vwap else 0.0
#     vol_avg = df["Volume"].mean()
#     vol_last = float(df["Volume"].iloc[-1])
#     volume_spike_ratio = (vol_last / vol_avg) if vol_avg and vol_avg > 0 else 1.0
#     return {"vwap_dev_pct": vwap_dev_pct, "volume_spike_ratio": volume_spike_ratio, "vwap": vwap}


# def _calc_volume_direction(
#     close: pd.Series, volume: pd.Series, period: int = 5,
#     order_flow: Optional[Dict[str, float]] = None,
# ) -> Optional[str]:
#     """Volume spike + price direction; when order_flow proxy present, use VWAP deviation + volume spike."""
#     if order_flow is not None and order_flow.get("volume_spike_ratio", 0) >= 1.2:
#         vd = order_flow.get("vwap_dev_pct", 0)
#         if vd > 0.1:
#             return "BUY"
#         if vd < -0.1:
#             return "SELL"
#     if close is None or volume is None or len(close) < period:
#         return None
#     vol_avg = volume.rolling(period).mean().iloc[-1]
#     vol_last = volume.iloc[-1]
#     if vol_avg == 0:
#         return "WAIT"
#     if vol_last > vol_avg * 1.5:
#         ret = (close.iloc[-1] - close.iloc[-period]) / close.iloc[-period] if close.iloc[-period] else 0
#         return "BUY" if ret > 0 else "SELL"
#     return "WAIT"


# def _calc_momentum(close: pd.Series, lookback: int = 10) -> Optional[str]:
#     """Simple momentum: price change over lookback."""
#     if close is None or len(close) < lookback + 1:
#         return None
#     ret = (close.iloc[-1] - close.iloc[-lookback - 1]) / close.iloc[-lookback - 1] if close.iloc[-lookback - 1] else 0
#     if ret > 0.005:
#         return "BUY"
#     if ret < -0.005:
#         return "SELL"
#     return "WAIT"


# def _calc_fibonacci_levels(hist: pd.DataFrame) -> Dict[str, float]:
#     """
#     Compute Fibonacci retracement levels from the swing high/low
#     of the given OHLCV data. Returns dict of level_name -> price.
#     """
#     swing_high = float(hist['High'].max())
#     swing_low  = float(hist['Low'].min())
#     fib_range  = swing_high - swing_low
#     if fib_range <= 0:
#         return {}
#     return {
#         'fib_0':    swing_high,
#         'fib_236':  swing_high - fib_range * 0.236,
#         'fib_382':  swing_high - fib_range * 0.382,
#         'fib_500':  swing_high - fib_range * 0.500,
#         'fib_618':  swing_high - fib_range * 0.618,
#         'fib_786':  swing_high - fib_range * 0.786,
#         'fib_100':  swing_low,
#         'daily_high': float(hist['High'].iloc[-24:].max()) if len(hist) >= 24 else swing_high,
#         'daily_low':  float(hist['Low'].iloc[-24:].min())  if len(hist) >= 24 else swing_low,
#     }


# def _snap_target_to_sr(
#     entry: float, raw_target: float, direction: str,
#     sr_levels: Dict[str, float], min_dist: float,
# ) -> float:
#     """
#     Snap T1 to the nearest support/resistance level if it's between
#     entry and the raw ATR target, AND leaves at least min_dist room.
#     """
#     if direction == "BUY":
#         # For BUY: look for resistance above entry but below raw_target
#         candidates = [
#             (name, lvl) for name, lvl in sr_levels.items()
#             if entry + min_dist < lvl < raw_target
#         ]
#         if candidates:
#             # Pick the nearest one above entry (first resistance hit)
#             nearest = min(candidates, key=lambda x: x[1])
#             return nearest[1]
#     else:
#         # For SELL: look for support below entry but above raw_target
#         candidates = [
#             (name, lvl) for name, lvl in sr_levels.items()
#             if raw_target < lvl < entry - min_dist
#         ]
#         if candidates:
#             # Pick the nearest one below entry (first support hit)
#             nearest = max(candidates, key=lambda x: x[1])
#             return nearest[1]
#     return raw_target


# def _build_signal_dict(
#     symbol: str,
#     direction: str,
#     current_price: float,
#     atr: float,
#     confidence: float,
#     model_id: str,
#     regime: str = "PATH_A",
#     timeframe_min: Optional[int] = None,
#     tech_analysis: Optional[Dict] = None,
#     hist: Optional[pd.DataFrame] = None,
#     rr_t1_mult: Optional[float] = None,
#     rr_t2_mult: Optional[float] = None,
#     rr_sl_mult: Optional[float] = None,
# ) -> Dict[str, Any]:
#     """Build a signal dict with S/R-aware targets."""
#     from market_agent.signal_params import (
#         BASE_ATR_T1_MULT,
#         BASE_ATR_T2_MULT,
#         BASE_ATR_SL_MULT,
#     )
#     from market_agent.asset_class import get_params

#     if direction == "WAIT":
#         return None
#     # Entry = current price (no fake slippage)
#     entry = current_price

#     # ATR-based targets — base from signal_params, scaled by asset class
#     params = get_params(symbol)
    
#     t1_base = rr_t1_mult if rr_t1_mult is not None else BASE_ATR_T1_MULT
#     t2_base = rr_t2_mult if rr_t2_mult is not None else BASE_ATR_T2_MULT
#     sl_base = rr_sl_mult if rr_sl_mult is not None else BASE_ATR_SL_MULT
    
#     t1_dist = atr * t1_base * params['atr_t1_factor'] if atr and atr > 0 else current_price * 0.01
#     t2_dist = atr * t2_base * params['atr_t2_factor'] if atr and atr > 0 else current_price * 0.02
#     sl_dist = atr * sl_base * params['atr_sl_factor'] if atr and atr > 0 else current_price * 0.007

#     if direction == "BUY":
#         target_1  = entry + t1_dist
#         target_2  = entry + t2_dist
#         stop_loss = entry - sl_dist
#     else:
#         target_1  = entry - t1_dist
#         target_2  = entry - t2_dist
#         stop_loss = entry + sl_dist

#     # ── Fibonacci + S/R target snapping ──────────────────────────
#     # If we have historical data, compute Fibonacci and daily high/low
#     # then snap T1 to nearest S/R if it improves the target.
#     # CRITICAL: Minimum R:R guard — only snap if result keeps R:R >= 1.8:1
#     # Without this, snapping pulls T1 too close to entry (R:R < 0.5:1) while SL stays full.
#     # This was the #1 cause of: high WR but still losing money.
#     # Changed from 1.2 → 1.8: must keep meaningful R:R even after S/R snapping.
#     MIN_RR_AFTER_SNAP = 1.8   # trade must still be at least 1.8:1 after snapping
#     sr_notes = ""
#     if hist is not None and len(hist) >= 24:
#         sr_levels = _calc_fibonacci_levels(hist)
#         min_profitable_dist = max(t1_dist * 0.5, atr * 0.3) if atr > 0 else t1_dist * 0.5

#         snapped_t1 = _snap_target_to_sr(entry, target_1, direction, sr_levels, min_profitable_dist)
#         if snapped_t1 != target_1:
#             # Enforce minimum R:R after snap
#             snapped_t1_dist = abs(snapped_t1 - entry)
#             snap_rr = snapped_t1_dist / sl_dist if sl_dist > 0 else 0.0
#             if snap_rr >= MIN_RR_AFTER_SNAP:
#                 sr_notes = f"T1 snapped {target_1:.2f}→{snapped_t1:.2f} S/R (R:R={snap_rr:.2f}:1)"
#                 target_1 = snapped_t1
#             # else: snap would destroy R:R — keep original ATR target, don't snap

#     tf = min(20, max(1, timeframe_min)) if timeframe_min is not None else 15
#     result = {
#         "symbol": symbol,
#         "direction": direction,
#         "entry_price": entry,
#         "current_price": current_price,
#         "target_1": target_1,
#         "target_2": target_2,
#         "stop_loss": stop_loss,
#         "confidence": confidence,
#         "regime": regime,
#         "model_used": model_id,
#         "model_name": model_id,
#         "vol_z_score": 1.0,
#         "timeframe_min": tf,
#         "tech_analysis": tech_analysis,
#     }
#     if sr_notes:
#         result["sr_notes"] = sr_notes
#     return result


# def _passes_momentum_gate(
#     hist: pd.DataFrame,
#     direction: str,
#     atr: float,
#     lookback: int = 10,
#     max_chase_pct: float = 0.30,
# ) -> bool:
#     """
#     Per IMPL-PLAN-V3 Fix 2B.
#     Returns False if entry is chasing a move that already happened.
#     Applied ONLY to trend-following brains (AMV-LSTM, Multi-Timeframe,
#     Cross-Stock-GNN). NOT applied to mean-reversion brains (Causal-Ensemble,
#     Multi-Modal-Fusion) — those brains WANT to enter near extremes.

#     Logic:
#       BUY  signal: block if current price is already within 30% of ATR×T1_MULT
#                    from the recent 10-bar high (move already ran)
#       SELL signal: mirror logic using recent 10-bar low
#     """
#     if hist is None or len(hist) < lookback + 1:
#         return True  # not enough data — allow entry

#     current_close = float(hist['Close'].iloc[-1])
#     prior_bars    = hist.iloc[-(lookback + 1):-1]  # exclude current bar

#     try:
#         from market_agent.signal_params import BASE_ATR_T1_MULT
#     except ImportError:
#         BASE_ATR_T1_MULT = 0.75

#     allowed_chase = atr * BASE_ATR_T1_MULT * max_chase_pct

#     if direction == 'BUY':
#         pivot_high = float(prior_bars['High'].max())
#         if current_close > pivot_high - allowed_chase:
#             return False  # already at recent high — chasing
#     elif direction == 'SELL':
#         pivot_low = float(prior_bars['Low'].min())
#         if current_close < pivot_low + allowed_chase:
#             return False  # already at recent low — chasing

#     return True  # momentum intact — allow entry


# from market_agent.brain.liquidity_sweep import liquidity_sweep_signal
# from market_agent.brain.brain_funding import funding_rate_signal


# # ════════════════════════════════════════════════════════
# # DELTA FLOW FILTER
# # Checks if buy/sell pressure (taker flow) CONFIRMS a brain signal.
# # Only applies to crypto (Binance data has TakerBase column).
# # Indian equity / forex: returns NEUTRAL (no block, no boost).
# #
# # Delta per candle = (TakerBuy - TakerSell) / TotalVolume
# #   +1.0 = 100% aggressive buying  (buyers chasing = bullish)
# #   -1.0 = 100% aggressive selling (sellers hitting = bearish)
# #    0.0 = perfectly balanced      (no edge)
# # ════════════════════════════════════════════════════════

# def _compute_delta_flow(hist: pd.DataFrame) -> Optional[float]:
#     """
#     Compute cumulative delta (buying vs selling pressure) over last 5 candles.
#     Returns float -1.0 to +1.0, or None if TakerBase column not available.

#     +0.30 to +1.0 = buyers in control
#     -0.30 to -1.0 = sellers in control
#     -0.30 to +0.30 = balanced / no edge
#     """
#     if hist is None or 'TakerBase' not in hist.columns:
#         return None   # Non-crypto data — filter not applicable

#     if len(hist) < 5:
#         return None   # Not enough candles

#     recent    = hist.iloc[-5:]
#     total_vol = recent['Volume'].replace(0, float('nan'))
#     taker_buy = recent['TakerBase']
#     taker_sell = total_vol - taker_buy

#     # Delta per candle: positive = buy pressure, negative = sell pressure
#     delta_per_candle = (taker_buy - taker_sell) / total_vol

#     # Cumulative (mean) over last 5 candles
#     cumulative = float(delta_per_candle.mean())

#     if pd.isna(cumulative):
#         return None

#     return round(cumulative, 3)


# def _apply_delta_filter(
#     direction: str,
#     confidence: float,
#     delta: Optional[float],
#     brain_name: str,
# ) -> tuple:
#     """
#     Brain-type-aware delta flow filter.

#     MOMENTUM brains: standard block + boost
#       Block if flow OPPOSES signal (falling knife / short squeeze)
#       Boost if flow CONFIRMS signal (buyers/sellers piling in)

#     REVERSION brains (Causal-Ensemble, Multi-Modal-Fusion):
#       NEVER block — opposing flow is EXPECTED at the extreme
#       Boost only on the rare occasion flow CONFIRMS (sellers exhausted)

#     HYBRID brains (Liquidity-Sweep):
#       Standard filter applies — by the time sweep brain fires,
#       the sweep candle is complete and buyers should be stepping in

#     Returns: (allow: bool, adjusted_confidence: float, note: str)
#     """
#     REVERSION_BRAINS = {'Causal-Ensemble', 'Multi-Modal-Fusion'}

#     # ── No delta data (equity/forex — no TakerBase column) ────────────────
#     if delta is None:
#         return True, confidence, 'delta_unavailable'

#     # ── REVERSION brains — never block, only boost on confirmation ──────
#     if brain_name in REVERSION_BRAINS:
#         if direction == 'BUY' and delta > 0.30:
#             boosted = min(0.92, confidence + 0.10)
#             return True, boosted, f'REVERSION_BOOST: delta={delta:.3f} (buyers absorbing at oversold extreme)'
#         if direction == 'SELL' and delta < -0.30:
#             boosted = min(0.92, confidence + 0.10)
#             return True, boosted, f'REVERSION_BOOST: delta={delta:.3f} (sellers absorbing at overbought extreme)'
#         # Opposing flow is expected — let through unchanged
#         return True, confidence, f'REVERSION_PASS: delta={delta:.3f} (opposing flow expected for mean-reversion)'

#     # ── MOMENTUM + HYBRID brains — standard block and boost ─────────
#     if direction == 'BUY' and delta < -0.35:
#         logger.debug('delta_filter_blocked',
#                      brain=brain_name, direction=direction,
#                      delta=delta, reason='sellers_in_control')
#         return False, confidence, f'BLOCKED: delta={delta:.3f} (sellers in control — falling knife risk)'

#     if direction == 'SELL' and delta > 0.35:
#         logger.debug('delta_filter_blocked',
#                      brain=brain_name, direction=direction,
#                      delta=delta, reason='buyers_in_control')
#         return False, confidence, f'BLOCKED: delta={delta:.3f} (buyers in control — short squeeze risk)'

#     if direction == 'BUY' and delta > 0.30:
#         boosted = min(0.92, confidence + 0.08)
#         return True, boosted, f'BOOSTED: delta={delta:.3f} (buyers confirming momentum)'

#     if direction == 'SELL' and delta < -0.30:
#         boosted = min(0.92, confidence + 0.08)
#         return True, boosted, f'BOOSTED: delta={delta:.3f} (sellers confirming momentum)'

#     return True, confidence, f'NEUTRAL: delta={delta:.3f}'



# def generate_brain_signals(
#     symbol: str,
#     current_price: float,
#     atr: float,
#     hist: pd.DataFrame,
#     tech_analysis: Optional[Dict[str, Any]] = None,
#     regime: str = 'RANGING',   # Changed from 'PATH_A': PATH_A is not in UNIFIED_REGIMES,
#                                # causing normalize_regime() to fall through to RANGING anyway,
#                                # but silently. 'RANGING' as default is honest and predictable.
# ) -> List[Dict[str, Any]]:
#     """
#     Per IMPL-PLAN-V3 Fix 3.
#     Run all brain functions. Returns signal dicts using each brain's
#     OWN computed confidence. Applies regime gate and momentum gate.
#     """
#     if hist is None or hist.empty:
#         return []

#     # ── Time-of-day filter (Indian equities only) ──────────────────────────────
#     # Block first 15 min open (gap fills + manipulation) and last 5 min (pre-close).
#     # Crypto = 24/7. Forex = continuous. Neither filtered.
#     if symbol.endswith('.NS') or symbol.endswith('.BO'):
#         from datetime import datetime, timezone, timedelta
#         _IST = timezone(timedelta(hours=5, minutes=30))
#         _m   = datetime.now(_IST).hour * 60 + datetime.now(_IST).minute
#         # 555 = 09:15, 570 = 09:30, 925 = 15:25, 930 = 15:30
#         if _m < 555 or _m > 930:
#             return []                              # market closed
#         if 555 <= _m < 570:
#             logger.debug('tod_filter_open_noise', symbol=symbol)
#             return []                              # opening 15 min noise
#         if _m >= 925:
#             logger.debug('tod_filter_close_noise', symbol=symbol)
#             return []                              # pre-close 5 min noise
#     # ───────────────────────────────────────────────────────────────────

#     # ── Brain accuracy cache ──────────────────────────────────────────────
#     # Reads retrain_results.json + live DB. Cached in memory. Near-zero cost.
#     # Returns None per brain if no data yet — graceful degradation.
#     _acc = {b: get_brain_accuracy(b) for b in [
#         'AMV-LSTM', 'AMV-LSTM-Uptrend', 'Multi-Modal-Fusion', 'Cross-Stock-GNN', 'Multi-Timeframe',
#         'Regime-Ensemble', 'RL-Weighter', 'Causal-Ensemble',
#         'Liquidity-Sweep', 'Funding-Rate',
#     ]}

#     # ── Compute delta flow ONCE for all brains ───────────────────────────────
#     # Only available for crypto (Binance data has TakerBase).
#     # Returns None for equity/forex — filter gracefully skips.
#     delta_flow = _compute_delta_flow(hist)
#     if delta_flow is not None:
#         logger.debug('delta_flow_computed', symbol=symbol, delta=delta_flow)
#     # ────────────────────────────────────────────────────────────

#     TREND_BRAINS = {'AMV-LSTM', 'AMV-LSTM-Uptrend', 'Multi-Timeframe', 'Cross-Stock-GNN'}

#     signals       = []
#     brain_results = []  # BrainSignal objects — consumed by RL Weighter

#     # ── Run Regime-Ensemble FIRST and extract computed regime for gates ─────
#     # This overrides the externally-passed regime if Regime-Ensemble has enough data.
#     # The external regime (from scanner/cortex) is the fallback only.
#     try:
#         bs_regime = regime_ensemble_signal(hist)
#         bs_regime = replace(bs_regime, recent_accuracy=_acc.get('Regime-Ensemble'))
#         brain_results.append(bs_regime)
#         # Extract clean regime string — no string parsing needed (see regime_ensemble.py)
#         computed_regime = bs_regime.measurements.get('computed_regime')
#         if computed_regime and computed_regime in UNIFIED_REGIMES:
#             if computed_regime != regime:
#                 logger.debug('regime_overridden_by_ensemble',
#                              external=regime, computed=computed_regime)
#             regime = computed_regime   # ← gate now uses brain-computed regime
#     except Exception as e:
#         logger.warning('brain_failed', brain='Regime-Ensemble', error=str(e)[:80])

#     # ── Standard brain runners ────────────────────────────────────────────
#     # ── AMV-LSTM CRYPTO EXCLUSION ──────────────────────────────────────────────
#     # 3 backtests confirm structural WR=24.9% on BTC/ETH — not a param problem.
#     # BTC is the most algo-efficient 24/7 market: SMA crossover arb'd away within
#     # minutes. No gate fixes this. Exclusion is permanent.
#     _AMV_LSTM_EXCLUDED_SYMBOLS = {'BTC-USD', 'ETH-USD', 'BTC-USDT', 'ETH-USDT'}
#     _amv_lstm_allowed = symbol not in _AMV_LSTM_EXCLUDED_SYMBOLS

#     def _amv_lstm_excluded_signal(h: pd.DataFrame) -> 'BrainSignal':
#         """Immediate HOLD for AMV-LSTM on crypto symbols — structural no-edge."""
#         from market_agent.brain.brain_contract import BrainSignal as _BS
#         return _BS(
#             brain_name='AMV-LSTM', specialization='Temporal Trend Memory',
#             method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
#             direction='HOLD', confidence=0.0, signal_strength=0.0,
#             signal_age_candles=0,
#             primary_evidence=f'Crypto symbol excluded ({symbol}) — structural no edge on BTC/ETH.',
#             supporting_factors=[], contra_factors=['crypto_excluded'],
#             method_confidence=0.0, regime_suitability='NONE',
#             reliability_flags={'crypto_excluded': True},
#             measurements={'decision_factor': 'CRYPTO_EXCLUSION'},
#             rr_t1_mult=1.5, rr_sl_mult=0.75,
#             recent_accuracy=None, regime_accuracy=None,
#         )

#     runners = [
#         # Bug fix: pass `regime` so TRENDING_DOWN R:R (T1=1.5×, SL=0.75×) is used.
#         # Previously fn(hist) defaulted regime='TRENDING_UP' on every call.
#         # Crypto exclusion: if symbol is BTC/ETH, return immediate HOLD.
#         (
#             (lambda h: amv_lstm_signal(h, regime=regime))
#             if _amv_lstm_allowed
#             else _amv_lstm_excluded_signal,
#             'AMV-LSTM', 12
#         ),
#         (lambda h: amv_lstm_uptrend_signal(h, regime=regime), 'AMV-LSTM-Uptrend', 25),
#         (multi_modal_fusion_signal, 'Multi-Modal-Fusion', 10),
#         # A5/A7: GNN needs symbol (VWAP type) and regime — use lambda wrapper
#         (lambda h: cross_stock_gnn_signal(h, symbol=symbol, regime=regime),
#                                     'Cross-Stock-GNN',    11),
#     ]


#     for fn, brain_name, tf_min in runners:
#         try:
#             bs = fn(hist)
#             bs = replace(bs, recent_accuracy=_acc.get(brain_name))  # inject accuracy
#             brain_results.append(bs)

#             # Gate 1 — brain itself not confident enough
#             if bs.direction in ('HOLD', 'WAIT'):
#                 continue
#             if bs.effective_confidence() < 0.50:
#                 logger.debug('brain_low_confidence',
#                              brain=brain_name, conf=round(bs.effective_confidence(), 3))
#                 continue

#             # Gate 2 — wrong regime for this brain
#             if not regime_allows_brain(brain_name, regime):
#                 logger.debug('brain_regime_blocked',
#                              brain=brain_name, regime=regime)
#                 continue

#             # Gate 3 — chasing a move (trend brains only)
#             if brain_name in TREND_BRAINS:
#                 if not _passes_momentum_gate(hist, bs.direction, atr):
#                     logger.debug('brain_momentum_blocked',
#                                  brain=brain_name, direction=bs.direction)
#                     continue

#             # Gate 4 — delta flow filter
#             allow, adj_conf, delta_note = _apply_delta_filter(
#                 bs.direction, bs.effective_confidence(), delta_flow, brain_name
#             )
#             if not allow:
#                 continue

#             sig = _build_signal_dict(
#                 symbol, bs.direction, current_price, atr,
#                 adj_conf, brain_name,
#                 regime, tf_min, tech_analysis, hist,
#                 rr_t1_mult=bs.rr_t1_mult,      # ← per-brain R:R from BrainSignal
#                 rr_t2_mult=bs.rr_t2_mult,
#                 rr_sl_mult=bs.rr_sl_mult,
#             )
#             if sig:
#                 sig['delta_note'] = delta_note
#                 signals.append(sig)

#         except Exception as e:
#             logger.warning('brain_failed', brain=brain_name, error=str(e)[:80])

#     # ── Liquidity-Sweep ────────────────────────────────────────────────────────────
#     try:
#         bs_ls = liquidity_sweep_signal(hist=hist, symbol=symbol, regime=regime)
#         bs_ls = replace(bs_ls, recent_accuracy=_acc.get('Liquidity-Sweep'))  # inject accuracy
#         brain_results.append(bs_ls)
#         # NOTE: NO momentum gate here — Liquidity-Sweep enters NEAR recent highs/lows by design
#         # (stop-hunt = price punches through then reverses — momentum gate would block every valid entry)
#         if (bs_ls.direction not in ('HOLD', 'WAIT')
#                 and bs_ls.effective_confidence() >= 0.50
#                 and regime_allows_brain('Liquidity-Sweep', regime)):

#             allow, adj_conf, delta_note = _apply_delta_filter(
#                 bs_ls.direction, bs_ls.effective_confidence(), delta_flow, 'Liquidity-Sweep'
#             )
#             if allow:
#                 sig = _build_signal_dict(
#                     symbol, bs_ls.direction, current_price, atr,
#                     adj_conf, 'Liquidity-Sweep',
#                     regime, 30, tech_analysis, hist,
#                 )
#                 if sig and bs_ls.measurements.get('swept_level'):
#                     meas = bs_ls.measurements
#                     if meas.get('rr_achieved', 0) >= 2.0:
#                         sig['sr_notes'] = f'Liquidity-Sweep: structural targets (R:R={meas["rr_achieved"]:.1f})'
#                 if sig:
#                     sig['delta_note'] = delta_note
#                     signals.append(sig)
#     except Exception as e:
#         logger.warning('brain_failed', brain='Liquidity-Sweep', error=str(e)[:80])

#     # ── Causal-Ensemble (reversion — delta never blocks, only boosts on confirmation) ──
#     try:
#         bs_causal = causal_ensemble_signal(hist, regime=regime)
#         bs_causal = replace(bs_causal, recent_accuracy=_acc.get('Causal-Ensemble'))  # inject accuracy
#         brain_results.append(bs_causal)
#         if (bs_causal.direction not in ('HOLD', 'WAIT')
#                 and bs_causal.effective_confidence() >= 0.50
#                 and regime_allows_brain('Causal-Ensemble', regime)):

#             allow, adj_conf, delta_note = _apply_delta_filter(
#                 bs_causal.direction, bs_causal.effective_confidence(), delta_flow, 'Causal-Ensemble'
#             )
#             if allow:   # always True for Causal-Ensemble (reversion brain)
#                 sig = _build_signal_dict(
#                     symbol, bs_causal.direction, current_price, atr,
#                     adj_conf, 'Causal-Ensemble',
#                     regime, 16, tech_analysis, hist,
#                     rr_t1_mult=bs_causal.rr_t1_mult,
#                     rr_t2_mult=bs_causal.rr_t2_mult,
#                     rr_sl_mult=bs_causal.rr_sl_mult,
#                 )
#                 if sig:
#                     sig['delta_note'] = delta_note
#                     signals.append(sig)
#     except Exception as e:
#         logger.warning('brain_failed', brain='Causal-Ensemble', error=str(e)[:80])

#     # ── Multi-Timeframe (fetch_fn wired — now fetches 4H and 1D from market_data) ───
#     try:
#         def _mtf_fetch(sym, interval='1h', period='10d'):
#             """Adapter: converts yfinance period string → bar count for market_data.get_ohlcv"""
#             period_to_bars = {
#                 '10d': 60,    # 4H bars: 10 days × 6 bars/day = 60 bars
#                 '1mo': 30,    # 1D bars: 30 days = 30 bars
#                 '5d':  30,
#                 '3mo': 90,
#             }
#             bars = period_to_bars.get(period, 50)
#             return market_data.get_ohlcv(sym, interval=interval, bars=bars)

#         bs = multi_timeframe_signal(symbol, hist, fetch_fn=_mtf_fetch)
#         bs = replace(bs, recent_accuracy=_acc.get('Multi-Timeframe'))  # inject accuracy
#         brain_results.append(bs)
#         if (bs.direction not in ('HOLD', 'WAIT')
#                 and bs.effective_confidence() >= 0.50
#                 and regime_allows_brain('Multi-Timeframe', regime)
#                 and _passes_momentum_gate(hist, bs.direction, atr)):

#             allow, adj_conf, delta_note = _apply_delta_filter(
#                 bs.direction, bs.effective_confidence(), delta_flow, 'Multi-Timeframe'
#             )
#             if allow:
#                 sig = _build_signal_dict(
#                     symbol, bs.direction, current_price, atr,
#                     adj_conf, 'Multi-Timeframe',
#                     regime, 15, tech_analysis, hist,
#                 )
#                 if sig:
#                     sig['delta_note'] = delta_note
#                     signals.append(sig)
#     except Exception as e:
#         logger.warning('brain_failed', brain='Multi-Timeframe', error=str(e)[:80])

#     # ── RL Weighter (runs last — SIZER ONLY, never a directional vote) ─────────
#     # CRITICAL: RL-Weighter is NOT added to signals[]. It is ONLY added to
#     # brain_results[] for council context. Its risk_multiplier is stored in
#     # measurements and should be read by the execution layer for position sizing.
#     #
#     # Reason: RL-Weighter mirrors the majority direction → adding it to signals[]
#     # would give a false impression of N+1 brains agreeing when it's really N.
#     try:
#         votes = [bs.direction for bs in brain_results
#                  if bs.direction not in ('HOLD', 'WAIT')]
#         if votes:
#             maj_dir  = max(set(votes), key=votes.count)
#             maj_conf = votes.count(maj_dir) / len(votes)
#             perf     = _load_brain_performance(symbol, limit=20)

#             bs_rl = rl_weighter_signal(
#                 base_signal         = {'direction': maj_dir, 'confidence': maj_conf},
#                 performance_history = perf,
#                 current_regime      = regime,
#             )
#             bs_rl = replace(bs_rl, recent_accuracy=_acc.get('RL-Weighter'))
#             brain_results.append(bs_rl)   # ← added to brain_results for council context
#             # ← NOT added to signals[] — risk_multiplier drives sizing, not direction
#             risk_multiplier = bs_rl.measurements.get('risk_multiplier', 1.0)
#             logger.debug('rl_weighter_sizing',
#                          symbol=symbol, risk_multiplier=risk_multiplier,
#                          direction=maj_dir, regime=regime)
#     except Exception as e:
#         logger.warning('brain_failed', brain='RL Weighter', error=str(e)[:80])

#     # ── Funding-Rate (crypto only — self-filters for non-crypto) ──────────────
#     try:
#         from market_agent.data.ingestion.unified_market_data import classify_symbol
#         if classify_symbol(symbol) == 'crypto':
#             buy_count  = sum(1 for s in signals if s and s.get('direction') == 'BUY')
#             sell_count = sum(1 for s in signals if s and s.get('direction') == 'SELL')
#             price_dir  = 'BUY' if buy_count > sell_count else ('SELL' if sell_count > buy_count else 'NEUTRAL')

#             bs_fund = funding_rate_signal(symbol, price_direction=price_dir)
#             if (bs_fund.get('applicable', False)
#                     and bs_fund.get('direction') not in ('HOLD',)
#                     and bs_fund.get('confidence', 0) >= 0.60):

#                 allow, adj_conf, delta_note = _apply_delta_filter(
#                     bs_fund['direction'], bs_fund['confidence'], delta_flow, 'Funding-Rate'
#                 )
#                 if allow:
#                     sig = _build_signal_dict(
#                         symbol, bs_fund['direction'], current_price, atr,
#                         adj_conf, 'Funding-Rate',
#                         regime, 480,   # 8-hour timeframe (funding cycle)
#                         tech_analysis, hist,
#                     )
#                     if sig:
#                         sig['delta_note'] = delta_note
#                         signals.append(sig)
#     except Exception as e:
#         logger.warning('brain_failed', brain='Funding-Rate', error=str(e)[:80])

#     out = [s for s in signals if s is not None]
#     if out:
#         logger.debug('brain_signals_generated', symbol=symbol, regime=regime,
#                      count=len(out), brains=[s['model_used'] for s in out])
#     return out


# def _load_brain_performance(symbol: str, limit: int = 20) -> list:
#     """
#     Per IMPL-PLAN-V3 Fix 2C.
#     Load last N resolved signal outcomes from DB for RL Weighter.
#     Returns list of {'outcome': str, 'regime': str} dicts.
#     outcome values: 'TARGET' (T1 or T2 hit), 'SL' (stop loss hit), 'EXPIRED'
#     """
#     try:
#         from market_agent.data.storage.postgres import PostgresStorage
#         from sqlalchemy import text
#         storage = PostgresStorage()
#         with storage.engine.connect() as conn:
#             rows = conn.execute(text('''
#                 SELECT
#                     CASE
#                         WHEN outcome IN ('T1_HIT', 'T2_HIT') THEN 'TARGET'
#                         WHEN outcome = 'SL_HIT'              THEN 'SL'
#                         ELSE                                      'EXPIRED'
#                     END  AS outcome_mapped,
#                     COALESCE(regime, 'UNKNOWN') AS regime
#                 FROM signal_predictions
#                 WHERE symbol  = :sym
#                   AND outcome IS NOT NULL
#                 ORDER BY created_at DESC
#                 LIMIT :lim
#             '''), {'sym': symbol, 'lim': limit}).fetchall()
#         return [{'outcome': r[0], 'regime': r[1]} for r in rows]
#     except Exception as e:
#         logger.debug('load_brain_performance_failed', error=str(e)[:80])
#         return []


# # ═══════════════════════════════════════════════════════════
# # ORDER 12 (Sec 6.4): Brain 8 — Sentiment Oracle
# # Completely uncorrelated with all 7 technical brains.
# # Uses Gemini AI sentiment score as a direct voting brain.
# # ═══════════════════════════════════════════════════════════

# def sentiment_oracle_signal(sentiment_score: float, headlines: list) -> dict:
#     """
#     Brain 8: Sentiment Oracle
#     Input : sentiment_score from Gemini (-1.0 to +1.0)
#             headlines: list of recent news strings
#     Output: signal dict with direction and confidence

#     Completely uncorrelated with all 7 technical brains.
#     Thresholds: |score| > 0.30 = directional signal
#     """
#     abs_score = abs(sentiment_score)

#     if sentiment_score > 0.30:
#         direction = "BUY"
#     elif sentiment_score < -0.30:
#         direction = "SELL"
#     else:
#         direction = "HOLD"

#     confidence = min(0.90, 0.50 + abs_score * 0.40)
#     top_headline = headlines[0] if headlines else "No recent headlines"

#     return {
#         "name":            "Sentiment Oracle",
#         "method":          "Gemini AI News Scoring + LLM Sentiment Analysis",
#         "direction":       direction,
#         "confidence":      round(confidence, 3),
#         "sentiment_score": round(sentiment_score, 3),
#         "evidence":        f"Sentiment={sentiment_score:.2f}. Top: {top_headline[:100]}",
#     }


# # ═══════════════════════════════════════════════════════════
# # ORDER 13 (Sec 7.3): FII/DII Daily Signal — Free, NSE India
# # Strongest leading indicator for large-cap Indian stocks.
# # NOT correlated with any technical brain.
# # ═══════════════════════════════════════════════════════════

# def get_fii_dii_signal() -> dict:
#     """
#     Get today's FII/DII institutional flow as a trading signal.
#     Data source: nsepython (free, official NSE India data).
#     FII net > +500 Cr = bullish for large-caps.
#     FII net < -500 Cr = bearish.
#     """
#     try:
#         from nsepython import fii_dii_data  # type: ignore
#         data = fii_dii_data()
#         fii_net = float(data.get("fii_net_purchase_sales", 0))
#         dii_net = float(data.get("dii_net_purchase_sales", 0))

#         # FII weighted heavier — they move markets more
#         combined = fii_net + (dii_net * 0.5)

#         if combined > 500:
#             return {
#                 "direction":  "BUY",
#                 "confidence": 0.70,
#                 "fii_net":    fii_net,
#                 "dii_net":    dii_net,
#                 "evidence":   f"FII net buy: {fii_net:,.0f} Cr | DII: {dii_net:,.0f} Cr",
#             }
#         elif combined < -500:
#             return {
#                 "direction":  "SELL",
#                 "confidence": 0.70,
#                 "fii_net":    fii_net,
#                 "dii_net":    dii_net,
#                 "evidence":   f"FII net sell: {fii_net:,.0f} Cr | DII: {dii_net:,.0f} Cr",
#             }
#         else:
#             return {
#                 "direction":  "HOLD",
#                 "confidence": 0.50,
#                 "fii_net":    fii_net,
#                 "dii_net":    dii_net,
#                 "evidence":   f"FII/DII neutral ({combined:,.0f} Cr combined)",
#             }

#     except Exception as e:
#         logger.debug("fii_dii_unavailable", error=str(e)[:60])
#         return {
#             "direction":  "HOLD",
#             "confidence": 0.0,
#             "evidence":   "FII/DII data unavailable",
#         }


# # ═══════════════════════════════════════════════════════════
# # ORDER 14 (Sec 7.4): Options PCR Signal — Free, NSE India
# # Put-Call Ratio for Nifty / BankNifty.
# # PCR > 1.2 = bearish fear | PCR < 0.7 = bullish greed
# # ═══════════════════════════════════════════════════════════

# def get_pcr_signal(symbol: str = "NIFTY") -> dict:
#     """
#     Get Put-Call Ratio from NSE options chain.
#     Applies to market indices: NIFTY, BANKNIFTY, FINNIFTY.

#     PCR > 1.2 → SELL signal (excessive put buying = fear)
#     PCR < 0.7 → BUY signal (call dominance = greed / bullish)
#     """
#     import requests
#     try:
#         url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
#         headers = {
#             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
#             "Referer":    "https://www.nseindia.com",
#             "Accept":     "application/json",
#         }
#         resp = requests.get(url, headers=headers, timeout=10)
#         resp.raise_for_status()
#         data = resp.json()

#         records = data.get("records", {}).get("data", [])
#         total_put_oi  = sum(r.get("PE", {}).get("openInterest", 0) for r in records if r.get("PE"))
#         total_call_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in records if r.get("CE"))

#         pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0

#         if pcr > 1.2:
#             return {
#                 "direction":  "SELL",
#                 "confidence": 0.65,
#                 "pcr":        pcr,
#                 "evidence":   f"PCR={pcr:.2f} — elevated put buying = bearish",
#             }
#         elif pcr < 0.7:
#             return {
#                 "direction":  "BUY",
#                 "confidence": 0.65,
#                 "pcr":        pcr,
#                 "evidence":   f"PCR={pcr:.2f} — call dominance = bullish",
#             }
#         else:
#             return {
#                 "direction":  "HOLD",
#                 "confidence": 0.50,
#                 "pcr":        pcr,
#                 "evidence":   f"PCR={pcr:.2f} — neutral zone",
#             }

#     except Exception as e:
#         logger.debug("pcr_unavailable", error=str(e)[:60])
#         return {
#             "direction":  "HOLD",
#             "confidence": 0.0,
#             "evidence":   "PCR data unavailable",
#         }


# # ===========================================================================
# # Phase 4 block removed — all brain functions now live in their canonical
# # individual files (amv_lstm.py, regime_ensemble.py, multi_modal_fusion.py,
# # multi_timeframe.py, cross_stock_gnn.py, causal_ensemble.py, rl_weighter.py)
# # and are imported at the top of this file.
# #
# # signal_generators.py is now a COORDINATOR only:
# #   - Imports from individual brain files
# #   - Applies regime gates, momentum gates, delta filter
# #   - Builds signal dicts with ATR-based targets + S/R snapping
# #   - Wires Regime-Ensemble output into the gate
# # ===========================================================================

"""
Path A: Signal coordination — imports from individual brain files.
Each brain lives in its own file under market_agent/brain/:
  amv_lstm.py           — Brain 1: Temporal trend memory
  regime_ensemble.py    — Brain 2: Market regime classifier (meta brain)
  multi_modal_fusion.py — Brain 3: RSI + MACD + divergence
  multi_timeframe.py    — Brain 4: Cross-timeframe alignment
  cross_stock_gnn.py    — Brain 5: Institutional flow (VWAP + volume)
  casual_ensemble.py    — Brain 7: Mean-reversion (Bollinger + RSI)
  liquidity_sweep.py    — Brain: Stop-hunt structure reversal
  brain_funding.py      — Brain: Funding rate signal (crypto)

signal_generators.py = COORDINATOR only. It:
  1. Imports all brains
  2. Applies regime gates, momentum gates, delta filter
  3. Builds signal dicts with ATR-based T1/T2/SL + Fibonacci S/R

For each brain's logic, confidence formula, and design rationale: read that brain's file.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional
import structlog

logger = structlog.get_logger()

from market_agent.data.ingestion.unified_market_data import market_data
from dataclasses import replace
from market_agent.brain.health_monitor import get_brain_accuracy

# ── Import individual brain signal functions (each brain in its own canonical file) ──
from market_agent.brain.amv_lstm import amv_lstm_signal, AMV_LSTM_EXCLUDED_SYMBOLS
from market_agent.brain.regime_ensemble import regime_ensemble_signal
from market_agent.brain.multi_modal_fusion import multi_modal_fusion_signal
from market_agent.brain.multi_timeframe import multi_timeframe_signal
from market_agent.brain.cross_stock_gnn import cross_stock_gnn_signal
from market_agent.brain.causal_ensemble import causal_ensemble_signal   # ← correct spelling
from market_agent.brain.rl_weighter import rl_weighter_signal
from market_agent.brain.liquidity_sweep import liquidity_sweep_signal

# ── Regime gate — per IMPL-PLAN-V3 Fix 2A & Regime Fix #3 ─────────────────
# Unified Taxonomy from `mff_and_regime.py`
UNIFIED_REGIMES = {
    'TRENDING_UP':    {'trust_trend_brains': 0.90, 'trust_mean_rev': 0.15, 'volatility_brains': 0.50},
    'TRENDING_DOWN':  {'trust_trend_brains': 0.90, 'trust_mean_rev': 0.15, 'volatility_brains': 0.50},
    'RANGING':        {'trust_trend_brains': 0.25, 'trust_mean_rev': 0.90, 'volatility_brains': 0.60},
    'VOLATILE':       {'trust_trend_brains': 0.40, 'trust_mean_rev': 0.10, 'volatility_brains': 0.85},
    'SQUEEZE':        {'trust_trend_brains': 0.20, 'trust_mean_rev': 0.70, 'volatility_brains': 0.95},
    'CHAOS':          {'trust_trend_brains': 0.00, 'trust_mean_rev': 0.00, 'volatility_brains': 0.00},  # NO trades
}

REGIME_MIGRATION_MAP = {
    'STABLE_TRADING':    'TRENDING_UP',
    'SCANNING_INTRADAY': 'RANGING',
    'HYBRID_SCAN':       'RANGING',
    'VOLATILE_CHAOS':    'CHAOS',
    'STRONG_TREND_UP':   'TRENDING_UP',
    'STRONG_TREND_DOWN': 'TRENDING_DOWN',
    'VOLATILE_BREAKOUT': 'VOLATILE',
    'LOW_VOLATILITY':    'SQUEEZE',
}

BRAIN_REGIME_GATES_UNIFIED = {
    'AMV-LSTM':           ['TRENDING_DOWN'],         # TRENDING_DOWN specialist only
    'Multi-Modal-Fusion': ['RANGING', 'SQUEEZE'],
    'Multi-Timeframe':    ['TRENDING_UP', 'TRENDING_DOWN'],
    'Cross-Stock-GNN':    ['VOLATILE', 'RANGING'],
    'Causal-Ensemble':    ['RANGING', 'SQUEEZE', 'VOLATILE'],
    'Super-Brain':        ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING'],
    'RL-Weighter':        ['TRENDING_UP', 'TRENDING_DOWN', 'RANGING'],
    'Liquidity-Sweep':    ['VOLATILE', 'TRENDING_UP', 'TRENDING_DOWN'],
    'Regime-Ensemble':    ['ALL'],
}

def normalize_regime(regime: str) -> str:
    """Convert any regime string (old or new) to unified taxonomy."""
    if regime in UNIFIED_REGIMES: return regime
    return REGIME_MIGRATION_MAP.get(regime, 'RANGING')

def regime_allows_brain(brain_name: str, regime: str) -> bool:
    """Single function to check if a brain can trade in a given regime."""
    unified = normalize_regime(regime)
    if unified == 'CHAOS': return False
    allowed = BRAIN_REGIME_GATES_UNIFIED.get(brain_name, ['ALL'])
    if 'ALL' in allowed: return True
    return unified in allowed



# Brain ID → (name, formula description)
# NOTE: names use hyphens — must match BRAIN_ADAPTERS in brain_adapters.py
COUNCIL_BRAIN_FORMULAS = [
    ("AMV-LSTM",           "temporal"),
    ("Regime-Ensemble",    "regime"),
    ("Multi-Modal-Fusion", "ta_sentiment"),
    ("Multi-Timeframe",    "multi_tf"),
    ("Cross-Stock-GNN",    "volume_structure"),
    ("RL-Weighter",        "momentum_weighted"),
    ("Causal-Ensemble",    "rsi_bollinger"),
]


def _calc_rsi(series: pd.Series, period: int = 14) -> float:
    """RSI from close series; returns 0-100 or 50 if insufficient data."""
    if series is None or len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def _calc_sma_cross(close: pd.Series, short: int = 5, long: int = 20) -> Optional[str]:
    """BUY if short > long, SELL if short < long, else None."""
    if close is None or len(close) < long:
        return None
    s = close.rolling(short).mean().iloc[-1]
    l = close.rolling(long).mean().iloc[-1]
    if s > l:
        return "BUY"
    if s < l:
        return "SELL"
    return "WAIT"


def _calc_macd_signal(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[str]:
    """BUY if MACD line > signal line, SELL if below."""
    if close is None or len(close) < slow + signal:
        return None
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    if macd.iloc[-1] > sig.iloc[-1]:
        return "BUY"
    if macd.iloc[-1] < sig.iloc[-1]:
        return "SELL"
    return "WAIT"


def _calc_bollinger_pos(close: pd.Series, period: int = 20, k: float = 2.0) -> Optional[str]:
    """BUY near lower band, SELL near upper band."""
    if close is None or len(close) < period:
        return None
    mid = close.rolling(period).mean().iloc[-1]
    std = close.rolling(period).std().iloc[-1]
    if std == 0:
        return "WAIT"
    upper = mid + k * std
    lower = mid - k * std
    last = close.iloc[-1]
    if last <= lower:
        return "BUY"
    if last >= upper:
        return "SELL"
    return "WAIT"


def _calc_volatility_regime(close: pd.Series, period: int = 20) -> Optional[str]:
    """Trend up → BUY, trend down → SELL (regime)."""
    if close is None or len(close) < period:
        return None
    sma = close.rolling(period).mean()
    last_close = close.iloc[-1]
    last_sma = sma.iloc[-1]
    if last_close > last_sma * 1.002:
        return "BUY"
    if last_close < last_sma * 0.998:
        return "SELL"
    return "WAIT"


def _order_flow_proxy(hist: pd.DataFrame, window: int = 20) -> Optional[Dict[str, float]]:
    """
    Order flow proxy from OHLCV (Plan A §8): VWAP deviation and volume spike vs 20-bar average.
    Returns dict with vwap_dev_pct (current price vs VWAP), volume_spike_ratio (last vol / avg).
    """
    if hist is None or len(hist) < window:
        return None
    need = ["High", "Low", "Close", "Volume"]
    if not all(c in hist.columns for c in need):
        return None
    df = hist.tail(window)
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vwap = (typical * df["Volume"]).sum() / (df["Volume"].sum() or 1e-9)
    current = float(df["Close"].iloc[-1])
    vwap_dev_pct = (current - vwap) / vwap * 100 if vwap else 0.0
    vol_avg = df["Volume"].mean()
    vol_last = float(df["Volume"].iloc[-1])
    volume_spike_ratio = (vol_last / vol_avg) if vol_avg and vol_avg > 0 else 1.0
    return {"vwap_dev_pct": vwap_dev_pct, "volume_spike_ratio": volume_spike_ratio, "vwap": vwap}


def _calc_volume_direction(
    close: pd.Series, volume: pd.Series, period: int = 5,
    order_flow: Optional[Dict[str, float]] = None,
) -> Optional[str]:
    """Volume spike + price direction; when order_flow proxy present, use VWAP deviation + volume spike."""
    if order_flow is not None and order_flow.get("volume_spike_ratio", 0) >= 1.2:
        vd = order_flow.get("vwap_dev_pct", 0)
        if vd > 0.1:
            return "BUY"
        if vd < -0.1:
            return "SELL"
    if close is None or volume is None or len(close) < period:
        return None
    vol_avg = volume.rolling(period).mean().iloc[-1]
    vol_last = volume.iloc[-1]
    if vol_avg == 0:
        return "WAIT"
    if vol_last > vol_avg * 1.5:
        ret = (close.iloc[-1] - close.iloc[-period]) / close.iloc[-period] if close.iloc[-period] else 0
        return "BUY" if ret > 0 else "SELL"
    return "WAIT"


def _calc_momentum(close: pd.Series, lookback: int = 10) -> Optional[str]:
    """Simple momentum: price change over lookback."""
    if close is None or len(close) < lookback + 1:
        return None
    ret = (close.iloc[-1] - close.iloc[-lookback - 1]) / close.iloc[-lookback - 1] if close.iloc[-lookback - 1] else 0
    if ret > 0.005:
        return "BUY"
    if ret < -0.005:
        return "SELL"
    return "WAIT"


def _calc_fibonacci_levels(hist: pd.DataFrame) -> Dict[str, float]:
    """
    Compute Fibonacci retracement levels from the swing high/low
    of the given OHLCV data. Returns dict of level_name -> price.
    """
    swing_high = float(hist['High'].max())
    swing_low  = float(hist['Low'].min())
    fib_range  = swing_high - swing_low
    if fib_range <= 0:
        return {}
    return {
        'fib_0':    swing_high,
        'fib_236':  swing_high - fib_range * 0.236,
        'fib_382':  swing_high - fib_range * 0.382,
        'fib_500':  swing_high - fib_range * 0.500,
        'fib_618':  swing_high - fib_range * 0.618,
        'fib_786':  swing_high - fib_range * 0.786,
        'fib_100':  swing_low,
        'daily_high': float(hist['High'].iloc[-24:].max()) if len(hist) >= 24 else swing_high,
        'daily_low':  float(hist['Low'].iloc[-24:].min())  if len(hist) >= 24 else swing_low,
    }


def _snap_target_to_sr(
    entry: float, raw_target: float, direction: str,
    sr_levels: Dict[str, float], min_dist: float,
) -> float:
    """
    Snap T1 to the nearest support/resistance level if it's between
    entry and the raw ATR target, AND leaves at least min_dist room.
    """
    if direction == "BUY":
        # For BUY: look for resistance above entry but below raw_target
        candidates = [
            (name, lvl) for name, lvl in sr_levels.items()
            if entry + min_dist < lvl < raw_target
        ]
        if candidates:
            # Pick the nearest one above entry (first resistance hit)
            nearest = min(candidates, key=lambda x: x[1])
            return nearest[1]
    else:
        # For SELL: look for support below entry but above raw_target
        candidates = [
            (name, lvl) for name, lvl in sr_levels.items()
            if raw_target < lvl < entry - min_dist
        ]
        if candidates:
            # Pick the nearest one below entry (first support hit)
            nearest = max(candidates, key=lambda x: x[1])
            return nearest[1]
    return raw_target


def _build_signal_dict(
    symbol: str,
    direction: str,
    current_price: float,
    atr: float,
    confidence: float,
    model_id: str,
    regime: str = "RANGING",
    timeframe_min: Optional[int] = None,
    tech_analysis: Optional[Dict] = None,
    hist: Optional[pd.DataFrame] = None,
    rr_t1_mult: Optional[float] = None,
    rr_t2_mult: Optional[float] = None,
    rr_sl_mult: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a signal dict with S/R-aware targets."""
    from market_agent.signal_params import (
        BASE_ATR_T1_MULT,
        BASE_ATR_T2_MULT,
        BASE_ATR_SL_MULT,
    )
    from market_agent.asset_class import get_params

    if direction == "WAIT":
        return None
    # Entry = current price (no fake slippage)
    entry = current_price

    # ATR-based targets — base from signal_params, scaled by asset class
    params = get_params(symbol)
    
    t1_base = rr_t1_mult if rr_t1_mult is not None else BASE_ATR_T1_MULT
    t2_base = rr_t2_mult if rr_t2_mult is not None else BASE_ATR_T2_MULT
    sl_base = rr_sl_mult if rr_sl_mult is not None else BASE_ATR_SL_MULT
    
    t1_dist = atr * t1_base * params['atr_t1_factor'] if atr and atr > 0 else current_price * 0.01
    t2_dist = atr * t2_base * params['atr_t2_factor'] if atr and atr > 0 else current_price * 0.02
    sl_dist = atr * sl_base * params['atr_sl_factor'] if atr and atr > 0 else current_price * 0.007

    if direction == "BUY":
        target_1  = entry + t1_dist
        target_2  = entry + t2_dist
        stop_loss = entry - sl_dist
    else:
        target_1  = entry - t1_dist
        target_2  = entry - t2_dist
        stop_loss = entry + sl_dist

    # ── Fibonacci + S/R target snapping ──────────────────────────
    # If we have historical data, compute Fibonacci and daily high/low
    # then snap T1 to nearest S/R if it improves the target.
    # CRITICAL: Minimum R:R guard — only snap if result keeps R:R >= 1.8:1
    # Without this, snapping pulls T1 too close to entry (R:R < 0.5:1) while SL stays full.
    # This was the #1 cause of: high WR but still losing money.
    # Changed from 1.2 → 1.8: must keep meaningful R:R even after S/R snapping.
    MIN_RR_AFTER_SNAP = 1.8   # trade must still be at least 1.8:1 after snapping
    sr_notes = ""
    if hist is not None and len(hist) >= 24:
        sr_levels = _calc_fibonacci_levels(hist)
        min_profitable_dist = max(t1_dist * 0.5, atr * 0.3) if atr > 0 else t1_dist * 0.5

        snapped_t1 = _snap_target_to_sr(entry, target_1, direction, sr_levels, min_profitable_dist)
        if snapped_t1 != target_1:
            # Enforce minimum R:R after snap
            snapped_t1_dist = abs(snapped_t1 - entry)
            snap_rr = snapped_t1_dist / sl_dist if sl_dist > 0 else 0.0
            if snap_rr >= MIN_RR_AFTER_SNAP:
                sr_notes = f"T1 snapped {target_1:.2f}→{snapped_t1:.2f} S/R (R:R={snap_rr:.2f}:1)"
                target_1 = snapped_t1
            # else: snap would destroy R:R — keep original ATR target, don't snap

    tf = min(20, max(1, timeframe_min)) if timeframe_min is not None else 15
    result = {
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry,
        "current_price": current_price,
        "target_1": target_1,
        "target_2": target_2,
        "stop_loss": stop_loss,
        "confidence": confidence,
        "regime": regime,
        "model_used": model_id,
        "model_name": model_id,
        "vol_z_score": 1.0,
        "timeframe_min": tf,
        "tech_analysis": tech_analysis,
    }
    if sr_notes:
        result["sr_notes"] = sr_notes
    return result


def _passes_momentum_gate(
    hist: pd.DataFrame,
    direction: str,
    atr: float,
    lookback: int = 10,
    max_chase_pct: float = 0.30,
) -> bool:
    """
    Per IMPL-PLAN-V3 Fix 2B.
    Returns False if entry is chasing a move that already happened.
    Applied ONLY to trend-following brains (AMV-LSTM, Multi-Timeframe,
    Cross-Stock-GNN). NOT applied to mean-reversion brains (Causal-Ensemble,
    Multi-Modal-Fusion) — those brains WANT to enter near extremes.

    Logic:
      BUY  signal: block if current price is already within 30% of ATR×T1_MULT
                   from the recent 10-bar high (move already ran)
      SELL signal: mirror logic using recent 10-bar low
    """
    if hist is None or len(hist) < lookback + 1:
        return True  # not enough data — allow entry

    current_close = float(hist['Close'].iloc[-1])
    prior_bars    = hist.iloc[-(lookback + 1):-1]  # exclude current bar

    try:
        from market_agent.signal_params import BASE_ATR_T1_MULT
    except ImportError:
        BASE_ATR_T1_MULT = 0.75

    allowed_chase = atr * BASE_ATR_T1_MULT * max_chase_pct

    if direction == 'BUY':
        pivot_high = float(prior_bars['High'].max())
        if current_close > pivot_high - allowed_chase:
            return False  # already at recent high — chasing
    elif direction == 'SELL':
        pivot_low = float(prior_bars['Low'].min())
        if current_close < pivot_low + allowed_chase:
            return False  # already at recent low — chasing

    return True  # momentum intact — allow entry


from market_agent.brain.liquidity_sweep import liquidity_sweep_signal
from market_agent.brain.brain_funding import funding_rate_signal


# ════════════════════════════════════════════════════════
# DELTA FLOW FILTER
# Checks if buy/sell pressure (taker flow) CONFIRMS a brain signal.
# Only applies to crypto (Binance data has TakerBase column).
# Indian equity / forex: returns NEUTRAL (no block, no boost).
#
# Delta per candle = (TakerBuy - TakerSell) / TotalVolume
#   +1.0 = 100% aggressive buying  (buyers chasing = bullish)
#   -1.0 = 100% aggressive selling (sellers hitting = bearish)
#    0.0 = perfectly balanced      (no edge)
# ════════════════════════════════════════════════════════

def _compute_delta_flow(hist: pd.DataFrame) -> Optional[float]:
    """
    Compute cumulative delta (buying vs selling pressure) over last 5 candles.
    Returns float -1.0 to +1.0, or None if TakerBase column not available.

    +0.30 to +1.0 = buyers in control
    -0.30 to -1.0 = sellers in control
    -0.30 to +0.30 = balanced / no edge
    """
    if hist is None or 'TakerBase' not in hist.columns:
        return None   # Non-crypto data — filter not applicable

    if len(hist) < 5:
        return None   # Not enough candles

    recent    = hist.iloc[-5:]
    total_vol = recent['Volume'].replace(0, float('nan'))
    taker_buy = recent['TakerBase']
    taker_sell = total_vol - taker_buy

    # Delta per candle: positive = buy pressure, negative = sell pressure
    delta_per_candle = (taker_buy - taker_sell) / total_vol

    # Cumulative (mean) over last 5 candles
    cumulative = float(delta_per_candle.mean())

    if pd.isna(cumulative):
        return None

    return round(cumulative, 3)


def _apply_delta_filter(
    direction: str,
    confidence: float,
    delta: Optional[float],
    brain_name: str,
) -> tuple:
    """
    Brain-type-aware delta flow filter.

    MOMENTUM brains: standard block + boost
      Block if flow OPPOSES signal (falling knife / short squeeze)
      Boost if flow CONFIRMS signal (buyers/sellers piling in)

    REVERSION brains (Causal-Ensemble, Multi-Modal-Fusion):
      NEVER block — opposing flow is EXPECTED at the extreme
      Boost only on the rare occasion flow CONFIRMS (sellers exhausted)

    HYBRID brains (Liquidity-Sweep):
      Standard filter applies — by the time sweep brain fires,
      the sweep candle is complete and buyers should be stepping in

    Returns: (allow: bool, adjusted_confidence: float, note: str)
    """
    REVERSION_BRAINS = {'Causal-Ensemble', 'Multi-Modal-Fusion'}

    # ── No delta data (equity/forex — no TakerBase column) ────────────────
    if delta is None:
        return True, confidence, 'delta_unavailable'

    # ── REVERSION brains — never block, only boost on confirmation ──────
    if brain_name in REVERSION_BRAINS:
        if direction == 'BUY' and delta > 0.30:
            boosted = min(0.92, confidence + 0.10)
            return True, boosted, f'REVERSION_BOOST: delta={delta:.3f} (buyers absorbing at oversold extreme)'
        if direction == 'SELL' and delta < -0.30:
            boosted = min(0.92, confidence + 0.10)
            return True, boosted, f'REVERSION_BOOST: delta={delta:.3f} (sellers absorbing at overbought extreme)'
        # Opposing flow is expected — let through unchanged
        return True, confidence, f'REVERSION_PASS: delta={delta:.3f} (opposing flow expected for mean-reversion)'

    # ── MOMENTUM + HYBRID brains — standard block and boost ─────────
    if direction == 'BUY' and delta < -0.35:
        logger.debug('delta_filter_blocked',
                     brain=brain_name, direction=direction,
                     delta=delta, reason='sellers_in_control')
        return False, confidence, f'BLOCKED: delta={delta:.3f} (sellers in control — falling knife risk)'

    if direction == 'SELL' and delta > 0.35:
        logger.debug('delta_filter_blocked',
                     brain=brain_name, direction=direction,
                     delta=delta, reason='buyers_in_control')
        return False, confidence, f'BLOCKED: delta={delta:.3f} (buyers in control — short squeeze risk)'

    if direction == 'BUY' and delta > 0.30:
        boosted = min(0.92, confidence + 0.08)
        return True, boosted, f'BOOSTED: delta={delta:.3f} (buyers confirming momentum)'

    if direction == 'SELL' and delta < -0.30:
        boosted = min(0.92, confidence + 0.08)
        return True, boosted, f'BOOSTED: delta={delta:.3f} (sellers confirming momentum)'

    return True, confidence, f'NEUTRAL: delta={delta:.3f}'



def generate_brain_signals(
    symbol: str,
    current_price: float,
    atr: float,
    hist: pd.DataFrame,
    tech_analysis: Optional[Dict[str, Any]] = None,
    regime: str = 'RANGING',   # Changed from 'PATH_A': PATH_A is not in UNIFIED_REGIMES,
                               # causing normalize_regime() to fall through to RANGING anyway,
                               # but silently. 'RANGING' as default is honest and predictable.
) -> List[Dict[str, Any]]:
    """
    Per IMPL-PLAN-V3 Fix 3.
    Run all brain functions. Returns signal dicts using each brain's
    OWN computed confidence. Applies regime gate and momentum gate.
    """
    if hist is None or hist.empty:
        return []

    # ── Time-of-day filter (Indian equities only) ──────────────────────────────
    # Block first 15 min open (gap fills + manipulation) and last 5 min (pre-close).
    # Crypto = 24/7. Forex = continuous. Neither filtered.
    if symbol.endswith('.NS') or symbol.endswith('.BO'):
        from datetime import datetime, timezone, timedelta
        _IST = timezone(timedelta(hours=5, minutes=30))
        _m   = datetime.now(_IST).hour * 60 + datetime.now(_IST).minute
        # 555 = 09:15, 570 = 09:30, 925 = 15:25, 930 = 15:30
        if _m < 555 or _m > 930:
            return []                              # market closed
        if 555 <= _m < 570:
            logger.debug('tod_filter_open_noise', symbol=symbol)
            return []                              # opening 15 min noise
        if _m >= 925:
            logger.debug('tod_filter_close_noise', symbol=symbol)
            return []                              # pre-close 5 min noise
    # ───────────────────────────────────────────────────────────────────

    # ── Brain accuracy cache ──────────────────────────────────────────────
    # Reads retrain_results.json + live DB. Cached in memory. Near-zero cost.
    # Returns None per brain if no data yet — graceful degradation.
    _acc = {b: get_brain_accuracy(b) for b in [
        'AMV-LSTM', 'Multi-Modal-Fusion', 'Cross-Stock-GNN', 'Multi-Timeframe',
        'Regime-Ensemble', 'RL-Weighter', 'Causal-Ensemble',
        'Liquidity-Sweep', 'Funding-Rate',
    ]}

    # ── Compute delta flow ONCE for all brains ───────────────────────────────
    # Only available for crypto (Binance data has TakerBase).
    # Returns None for equity/forex — filter gracefully skips.
    delta_flow = _compute_delta_flow(hist)
    if delta_flow is not None:
        logger.debug('delta_flow_computed', symbol=symbol, delta=delta_flow)
    # ────────────────────────────────────────────────────────────

    try:
        from market_agent.config import BRAIN_SYMBOL_MAP
    except ImportError:
        BRAIN_SYMBOL_MAP = {}

    def _is_brain_allowed_for_symbol(b_name: str, sym: str) -> bool:
        if b_name in BRAIN_SYMBOL_MAP:
            return sym in BRAIN_SYMBOL_MAP[b_name]
        return True

    TREND_BRAINS = {'AMV-LSTM', 'AMV-LSTM-Uptrend', 'Multi-Timeframe', 'Cross-Stock-GNN'}

    signals       = []
    brain_results = []  # BrainSignal objects — consumed by RL Weighter

    # ── Run Regime-Ensemble FIRST and extract computed regime for gates ─────
    # This overrides the externally-passed regime if Regime-Ensemble has enough data.
    # The external regime (from scanner/cortex) is the fallback only.
    try:
        bs_regime = regime_ensemble_signal(hist, symbol=symbol)
        bs_regime = replace(bs_regime, recent_accuracy=_acc.get('Regime-Ensemble'))
        brain_results.append(bs_regime)
        # Extract clean regime string — no string parsing needed (see regime_ensemble.py)
        computed_regime = bs_regime.measurements.get('computed_regime')
        if computed_regime and computed_regime in UNIFIED_REGIMES:
            if computed_regime != regime:
                logger.debug('regime_overridden_by_ensemble',
                             external=regime, computed=computed_regime)
            regime = computed_regime   # ← gate now uses brain-computed regime
    except Exception as e:
        logger.warning('brain_failed', brain='Regime-Ensemble', error=str(e)[:80])

    # ── Standard brain runners ────────────────────────────────────────────
    from market_agent.brain.amv_lstm_uptrend import amv_lstm_uptrend_signal
    runners = [
        # AMV-LSTM needs regime so it can apply asymmetric R:R and Gate 7 (HTF alignment).
        # Using lambda wrapper — same pattern as Cross-Stock-GNN below.
        # Bug was: bs = fn(hist) with no regime → always defaulted to TRENDING_UP.
        (lambda h: amv_lstm_signal(h, regime=regime), 'AMV-LSTM',           12),
        (lambda h: amv_lstm_uptrend_signal(h, regime=regime), 'AMV-LSTM-Uptrend', 25),
        (multi_modal_fusion_signal,                    'Multi-Modal-Fusion', 10),
        # A5/A7: GNN needs symbol (VWAP type) and regime — use lambda wrapper
        (lambda h: cross_stock_gnn_signal(h, symbol=symbol, regime=regime),
                                    'Cross-Stock-GNN',    11),
    ]

    # ── AMV-LSTM symbol exclusion gate ────────────────────────────────────
    # Symbols where AMV-LSTM has no structural edge (4-run confirmed per brain docs).
    # Skip entire AMV-LSTM evaluation for these — don't waste compute or generate signals.
    # The exclusion list is owned by amv_lstm.py (single source of truth).
    if symbol in AMV_LSTM_EXCLUDED_SYMBOLS:
        runners = [(fn, name, tf) for fn, name, tf in runners if name != 'AMV-LSTM']
        logger.debug('amv_lstm_symbol_excluded', symbol=symbol,
                     excluded_set=list(AMV_LSTM_EXCLUDED_SYMBOLS))

    for fn, brain_name, tf_min in runners:
        if not _is_brain_allowed_for_symbol(brain_name, symbol):
            continue

        try:
            bs = fn(hist)
            bs = replace(bs, recent_accuracy=_acc.get(brain_name))  # inject accuracy
            brain_results.append(bs)

            # Gate 1 — brain itself not confident enough
            if bs.direction in ('HOLD', 'WAIT'):
                continue
            if bs.effective_confidence() < 0.50:
                logger.debug('brain_low_confidence',
                             brain=brain_name, conf=round(bs.effective_confidence(), 3))
                continue

            # Gate 2 — wrong regime for this brain
            if not regime_allows_brain(brain_name, regime):
                logger.debug('brain_regime_blocked',
                             brain=brain_name, regime=regime)
                continue

            # Gate 3 — chasing a move (trend brains only)
            if brain_name in TREND_BRAINS:
                if not _passes_momentum_gate(hist, bs.direction, atr):
                    logger.debug('brain_momentum_blocked',
                                 brain=brain_name, direction=bs.direction)
                    continue

            # Gate 4 — delta flow filter
            allow, adj_conf, delta_note = _apply_delta_filter(
                bs.direction, bs.effective_confidence(), delta_flow, brain_name
            )
            if not allow:
                continue

            sig = _build_signal_dict(
                symbol, bs.direction, current_price, atr,
                adj_conf, brain_name,
                regime, tf_min, tech_analysis, hist,
                rr_t1_mult=bs.rr_t1_mult,      # ← per-brain R:R from BrainSignal
                rr_t2_mult=bs.rr_t2_mult,
                rr_sl_mult=bs.rr_sl_mult,
            )
            if sig:
                sig['delta_note'] = delta_note
                signals.append(sig)

        except Exception as e:
            logger.warning('brain_failed', brain=brain_name, error=str(e)[:80])

    # ── Liquidity-Sweep ────────────────────────────────────────────────────────────
    try:
        if not _is_brain_allowed_for_symbol('Liquidity-Sweep', symbol):
            raise UserWarning('brain_symbol_excluded')
        bs_ls = liquidity_sweep_signal(hist=hist, symbol=symbol, regime=regime)
        bs_ls = replace(bs_ls, recent_accuracy=_acc.get('Liquidity-Sweep'))  # inject accuracy
        brain_results.append(bs_ls)
        # NOTE: NO momentum gate here — Liquidity-Sweep enters NEAR recent highs/lows by design
        # (stop-hunt = price punches through then reverses — momentum gate would block every valid entry)
        if (bs_ls.direction not in ('HOLD', 'WAIT')
                and bs_ls.effective_confidence() >= 0.50
                and regime_allows_brain('Liquidity-Sweep', regime)):

            allow, adj_conf, delta_note = _apply_delta_filter(
                bs_ls.direction, bs_ls.effective_confidence(), delta_flow, 'Liquidity-Sweep'
            )
            if allow:
                sig = _build_signal_dict(
                    symbol, bs_ls.direction, current_price, atr,
                    adj_conf, 'Liquidity-Sweep',
                    regime, 30, tech_analysis, hist,
                )
                if sig and bs_ls.measurements.get('swept_level'):
                    meas = bs_ls.measurements
                    if meas.get('rr_achieved', 0) >= 2.0:
                        sig['sr_notes'] = f'Liquidity-Sweep: structural targets (R:R={meas["rr_achieved"]:.1f})'
                if sig:
                    sig['delta_note'] = delta_note
                    signals.append(sig)
    except UserWarning:
        pass
    except Exception as e:
        logger.warning('brain_failed', brain='Liquidity-Sweep', error=str(e)[:80])

    # ── Causal-Ensemble (reversion — delta never blocks, only boosts on confirmation) ──
    try:
        if not _is_brain_allowed_for_symbol('Causal-Ensemble', symbol):
            raise UserWarning('brain_symbol_excluded')
        bs_causal = causal_ensemble_signal(hist, regime=regime)
        bs_causal = replace(bs_causal, recent_accuracy=_acc.get('Causal-Ensemble'))  # inject accuracy
        brain_results.append(bs_causal)
        if (bs_causal.direction not in ('HOLD', 'WAIT')
                and bs_causal.effective_confidence() >= 0.50
                and regime_allows_brain('Causal-Ensemble', regime)):

            allow, adj_conf, delta_note = _apply_delta_filter(
                bs_causal.direction, bs_causal.effective_confidence(), delta_flow, 'Causal-Ensemble'
            )
            if allow:   # always True for Causal-Ensemble (reversion brain)
                sig = _build_signal_dict(
                    symbol, bs_causal.direction, current_price, atr,
                    adj_conf, 'Causal-Ensemble',
                    regime, 16, tech_analysis, hist,
                    rr_t1_mult=bs_causal.rr_t1_mult,
                    rr_t2_mult=bs_causal.rr_t2_mult,
                    rr_sl_mult=bs_causal.rr_sl_mult,
                )
                if sig:
                    sig['delta_note'] = delta_note
                    signals.append(sig)
    except UserWarning:
        pass
    except Exception as e:
        logger.warning('brain_failed', brain='Causal-Ensemble', error=str(e)[:80])

    # ── Multi-Timeframe (fetch_fn wired — now fetches 4H and 1D from market_data) ───
    try:
        if not _is_brain_allowed_for_symbol('Multi-Timeframe', symbol):
            raise UserWarning('brain_symbol_excluded')
        def _mtf_fetch(sym, interval='1h', period='10d'):
            """Adapter: converts yfinance period string → bar count for market_data.get_ohlcv"""
            period_to_bars = {
                '10d': 60,    # 4H bars: 10 days × 6 bars/day = 60 bars
                '1mo': 30,    # 1D bars: 30 days = 30 bars
                '5d':  30,
                '3mo': 90,
            }
            bars = period_to_bars.get(period, 50)
            return market_data.get_ohlcv(sym, interval=interval, bars=bars)

        bs = multi_timeframe_signal(symbol, hist, fetch_fn=_mtf_fetch)
        bs = replace(bs, recent_accuracy=_acc.get('Multi-Timeframe'))  # inject accuracy
        brain_results.append(bs)
        if (bs.direction not in ('HOLD', 'WAIT')
                and bs.effective_confidence() >= 0.50
                and regime_allows_brain('Multi-Timeframe', regime)
                and _passes_momentum_gate(hist, bs.direction, atr)):

            allow, adj_conf, delta_note = _apply_delta_filter(
                bs.direction, bs.effective_confidence(), delta_flow, 'Multi-Timeframe'
            )
            if allow:
                sig = _build_signal_dict(
                    symbol, bs.direction, current_price, atr,
                    adj_conf, 'Multi-Timeframe',
                    regime, 15, tech_analysis, hist,
                )
                if sig:
                    sig['delta_note'] = delta_note
                    signals.append(sig)
    except UserWarning:
        pass
    except Exception as e:
        logger.warning('brain_failed', brain='Multi-Timeframe', error=str(e)[:80])

    # ── RL Weighter (runs last — SIZER ONLY, never a directional vote) ─────────
    # CRITICAL: RL-Weighter is NOT added to signals[]. It is ONLY added to
    # brain_results[] for council context. Its risk_multiplier is stored in
    # measurements and should be read by the execution layer for position sizing.
    #
    # Reason: RL-Weighter mirrors the majority direction → adding it to signals[]
    # would give a false impression of N+1 brains agreeing when it's really N.
    try:
        votes = [bs.direction for bs in brain_results
                 if bs.direction not in ('HOLD', 'WAIT')]
        if votes:
            maj_dir  = max(set(votes), key=votes.count)
            maj_conf = votes.count(maj_dir) / len(votes)
            perf     = _load_brain_performance(symbol, limit=20)

            # N3b: inject macro context for position sizing adjustment
            try:
                from market_agent.learning.macro_circuit_breaker import get_macro_breaker
                _macro_ctx = get_macro_breaker().get_state().to_dict()
            except Exception:
                _macro_ctx = None
            bs_rl = rl_weighter_signal(
                base_signal         = {'direction': maj_dir, 'confidence': maj_conf},
                performance_history = perf,
                current_regime      = regime,
                macro_context       = _macro_ctx,
            )
            bs_rl = replace(bs_rl, recent_accuracy=_acc.get('RL-Weighter'))
            brain_results.append(bs_rl)   # ← added to brain_results for council context
            # ← NOT added to signals[] — risk_multiplier drives sizing, not direction
            risk_multiplier = bs_rl.measurements.get('risk_multiplier', 1.0)
            logger.debug('rl_weighter_sizing',
                         symbol=symbol, risk_multiplier=risk_multiplier,
                         direction=maj_dir, regime=regime)
    except Exception as e:
        logger.warning('brain_failed', brain='RL Weighter', error=str(e)[:80])

    # ── Funding-Rate (crypto only — self-filters for non-crypto) ──────────────
    try:
        from market_agent.data.ingestion.unified_market_data import classify_symbol
        if classify_symbol(symbol) == 'crypto':
            buy_count  = sum(1 for s in signals if s and s.get('direction') == 'BUY')
            sell_count = sum(1 for s in signals if s and s.get('direction') == 'SELL')
            price_dir  = 'BUY' if buy_count > sell_count else ('SELL' if sell_count > buy_count else 'NEUTRAL')

            bs_fund = funding_rate_signal(symbol, price_direction=price_dir)
            if (bs_fund.get('applicable', False)
                    and bs_fund.get('direction') not in ('HOLD',)
                    and bs_fund.get('confidence', 0) >= 0.60):

                allow, adj_conf, delta_note = _apply_delta_filter(
                    bs_fund['direction'], bs_fund['confidence'], delta_flow, 'Funding-Rate'
                )
                if allow:
                    sig = _build_signal_dict(
                        symbol, bs_fund['direction'], current_price, atr,
                        adj_conf, 'Funding-Rate',
                        regime, 480,   # 8-hour timeframe (funding cycle)
                        tech_analysis, hist,
                    )
                    if sig:
                        sig['delta_note'] = delta_note
                        signals.append(sig)
    except Exception as e:
        logger.warning('brain_failed', brain='Funding-Rate', error=str(e)[:80])

    out = [s for s in signals if s is not None]
    if out:
        logger.debug('brain_signals_generated', symbol=symbol, regime=regime,
                     count=len(out), brains=[s['model_used'] for s in out])
    return out


def _load_brain_performance(symbol: str, limit: int = 20) -> list:
    """
    Per IMPL-PLAN-V3 Fix 2C.
    Load last N resolved signal outcomes from DB for RL Weighter.
    Returns list of {'outcome': str, 'regime': str} dicts.
    outcome values: 'TARGET' (T1 or T2 hit), 'SL' (stop loss hit), 'EXPIRED'
    """
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from sqlalchemy import text
        storage = PostgresStorage()
        with storage.engine.connect() as conn:
            rows = conn.execute(text('''
                SELECT
                    CASE
                        WHEN outcome IN ('T1_HIT', 'T2_HIT') THEN 'TARGET'
                        WHEN outcome = 'SL_HIT'              THEN 'SL'
                        ELSE                                      'EXPIRED'
                    END  AS outcome_mapped,
                    COALESCE(regime, 'UNKNOWN') AS regime
                FROM signal_predictions
                WHERE symbol  = :sym
                  AND outcome IS NOT NULL
                ORDER BY created_at DESC
                LIMIT :lim
            '''), {'sym': symbol, 'lim': limit}).fetchall()
        return [{'outcome': r[0], 'regime': r[1]} for r in rows]
    except Exception as e:
        logger.debug('load_brain_performance_failed', error=str(e)[:80])
        return []


# ═══════════════════════════════════════════════════════════
# ORDER 12 (Sec 6.4): Brain 8 — Sentiment Oracle
# Completely uncorrelated with all 7 technical brains.
# Uses Gemini AI sentiment score as a direct voting brain.
# ═══════════════════════════════════════════════════════════

def sentiment_oracle_signal(sentiment_score: float, headlines: list) -> dict:
    """
    Brain 8: Sentiment Oracle
    Input : sentiment_score from Gemini (-1.0 to +1.0)
            headlines: list of recent news strings
    Output: signal dict with direction and confidence

    Completely uncorrelated with all 7 technical brains.
    Thresholds: |score| > 0.30 = directional signal
    """
    abs_score = abs(sentiment_score)

    if sentiment_score > 0.30:
        direction = "BUY"
    elif sentiment_score < -0.30:
        direction = "SELL"
    else:
        direction = "HOLD"

    confidence = min(0.90, 0.50 + abs_score * 0.40)
    top_headline = headlines[0] if headlines else "No recent headlines"

    return {
        "name":            "Sentiment Oracle",
        "method":          "Gemini AI News Scoring + LLM Sentiment Analysis",
        "direction":       direction,
        "confidence":      round(confidence, 3),
        "sentiment_score": round(sentiment_score, 3),
        "evidence":        f"Sentiment={sentiment_score:.2f}. Top: {top_headline[:100]}",
    }


# ═══════════════════════════════════════════════════════════
# ORDER 13 (Sec 7.3): FII/DII Daily Signal — Free, NSE India
# Strongest leading indicator for large-cap Indian stocks.
# NOT correlated with any technical brain.
# ═══════════════════════════════════════════════════════════

def get_fii_dii_signal() -> dict:
    """
    Get today's FII/DII institutional flow as a trading signal.
    Data source: nsepython (free, official NSE India data).
    FII net > +500 Cr = bullish for large-caps.
    FII net < -500 Cr = bearish.
    """
    try:
        from nsepython import fii_dii_data  # type: ignore
        data = fii_dii_data()
        fii_net = float(data.get("fii_net_purchase_sales", 0))
        dii_net = float(data.get("dii_net_purchase_sales", 0))

        # FII weighted heavier — they move markets more
        combined = fii_net + (dii_net * 0.5)

        if combined > 500:
            return {
                "direction":  "BUY",
                "confidence": 0.70,
                "fii_net":    fii_net,
                "dii_net":    dii_net,
                "evidence":   f"FII net buy: {fii_net:,.0f} Cr | DII: {dii_net:,.0f} Cr",
            }
        elif combined < -500:
            return {
                "direction":  "SELL",
                "confidence": 0.70,
                "fii_net":    fii_net,
                "dii_net":    dii_net,
                "evidence":   f"FII net sell: {fii_net:,.0f} Cr | DII: {dii_net:,.0f} Cr",
            }
        else:
            return {
                "direction":  "HOLD",
                "confidence": 0.50,
                "fii_net":    fii_net,
                "dii_net":    dii_net,
                "evidence":   f"FII/DII neutral ({combined:,.0f} Cr combined)",
            }

    except Exception as e:
        logger.debug("fii_dii_unavailable", error=str(e)[:60])
        return {
            "direction":  "HOLD",
            "confidence": 0.0,
            "evidence":   "FII/DII data unavailable",
        }


# ═══════════════════════════════════════════════════════════
# ORDER 14 (Sec 7.4): Options PCR Signal — Free, NSE India
# Put-Call Ratio for Nifty / BankNifty.
# PCR > 1.2 = bearish fear | PCR < 0.7 = bullish greed
# ═══════════════════════════════════════════════════════════

def get_pcr_signal(symbol: str = "NIFTY") -> dict:
    """
    Get Put-Call Ratio from NSE options chain.
    Applies to market indices: NIFTY, BANKNIFTY, FINNIFTY.

    PCR > 1.2 → SELL signal (excessive put buying = fear)
    PCR < 0.7 → BUY signal (call dominance = greed / bullish)
    """
    import requests
    try:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer":    "https://www.nseindia.com",
            "Accept":     "application/json",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        records = data.get("records", {}).get("data", [])
        total_put_oi  = sum(r.get("PE", {}).get("openInterest", 0) for r in records if r.get("PE"))
        total_call_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in records if r.get("CE"))

        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0

        if pcr > 1.2:
            return {
                "direction":  "SELL",
                "confidence": 0.65,
                "pcr":        pcr,
                "evidence":   f"PCR={pcr:.2f} — elevated put buying = bearish",
            }
        elif pcr < 0.7:
            return {
                "direction":  "BUY",
                "confidence": 0.65,
                "pcr":        pcr,
                "evidence":   f"PCR={pcr:.2f} — call dominance = bullish",
            }
        else:
            return {
                "direction":  "HOLD",
                "confidence": 0.50,
                "pcr":        pcr,
                "evidence":   f"PCR={pcr:.2f} — neutral zone",
            }

    except Exception as e:
        logger.debug("pcr_unavailable", error=str(e)[:60])
        return {
            "direction":  "HOLD",
            "confidence": 0.0,
            "evidence":   "PCR data unavailable",
        }


# ===========================================================================
# Phase 4 block removed — all brain functions now live in their canonical
# individual files (amv_lstm.py, regime_ensemble.py, multi_modal_fusion.py,
# multi_timeframe.py, cross_stock_gnn.py, causal_ensemble.py, rl_weighter.py)
# and are imported at the top of this file.
#
# signal_generators.py is now a COORDINATOR only:
#   - Imports from individual brain files
#   - Applies regime gates, momentum gates, delta filter
#   - Builds signal dicts with ATR-based targets + S/R snapping
#   - Wires Regime-Ensemble output into the gate
# ===========================================================================