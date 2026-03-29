"""
Brain 1: AMV-LSTM — Temporal Memory Brain (TRENDING_DOWN Specialist)
=====================================================================
SMA 5/20 crossover as trigger.
LSTM sequence prediction as primary signal (falls back to SMA when model not loaded).

SPECIALISATION: TRENDING_DOWN only.
  TRENDING_UP is handled by amv_lstm_uptrend.py (pullback-to-MA entry).
  Crypto (BTC-USD, ETH-USD) is excluded — see exclusion note below.

REGIME EXCLUSION — CRYPTO:
  Three backtests confirm this brain has NO edge on BTC-USD (implied WR=24.9%,
  avg_R=-0.189). Root cause: BTC is the most algo-efficient 24/7 market. SMA
  5/20 crossover signals are arb'd away within minutes. No parameter tuning fixes
  this — it is structural. BTC-USD and ETH-USD must be excluded at the scanner
  level (signal_generators.py or watchlist_scanner.py) before calling this brain.
  Equity symbols (NVDA, RELIANCE.NS etc.) are the intended use case.

Works best in: TRENDING_DOWN + equity/forex/commodity symbols.
Regime gate is applied in signal_generators.py before this is called.

Returns: BrainSignal (brain_contract.py)

CHANGES vs previous version:
  GATE 1 RAISED: Cross freshness threshold raised from age < 2 to age < 4.
    3-run evidence: age 2-3 has implied WR=21.8%, avg_R=-0.259R consistently.
    Root cause: first 2-3 candles post-cross = MA retest from below → SL caught.
    Age >= 4 = retest complete, move genuinely resuming. That is the edge.

  GATE 5 (PREV): TRENDING_UP BUY — ATR expansion confirmation.
  GATE 6 (PREV): Low-volume cross hard block.
  ASYMMETRIC R:R (PREV):
    TRENDING_DOWN: T1=2.0×ATR, SL=0.75×ATR
    TRENDING_UP:   T1=2.5×ATR, SL=0.65×ATR (preserved for LSTM path)
  SLOPE GATE STRENGTHENED (PREV): min slope threshold 0.02%.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from market_agent.brain.brain_contract import BrainSignal
from market_agent.brain.brain_utils import calc_atr, calc_rsi_float


# ── R:R multipliers — asymmetric per regime ──────────────────────────────────
# TRENDING_DOWN: original settings, already EV positive (avg_R=+0.072)
_RR_T1_TRENDING_DOWN = 2.0
_RR_T2_TRENDING_DOWN = 3.5
_RR_SL_TRENDING_DOWN = 0.75

# TRENDING_UP: wider T1, tighter SL — compensates for lower WR
# At 35% WR: EV = 0.35*2.5 - 0.65*0.65 = +0.455R (profitable)
# Break-even WR: 0.65/(2.5+0.65) = 20.6%
_RR_T1_TRENDING_UP   = 2.5
_RR_T2_TRENDING_UP   = 4.0
_RR_SL_TRENDING_UP   = 0.65

# Default (used when regime unknown to this function — shouldn't happen in prod)
_RR_T1_MULT = 2.0
_RR_T2_MULT = 3.5
_RR_SL_MULT = 0.75

# ── Gate thresholds — ALL named so grid and production stay in sync ───────────

# Gate 1: Cross freshness — minimum candles after cross before entry is valid.
# Evidence: age 2-3 has WR=21.8% across 3 runs (MA retest catches SL).
# Raised from 2 to 4 after Run 4 confirmation.
_MIN_CROSS_AGE = 4

# Gate 2: Stale cross window — crosses older than this have no edge (move done).
# 420 minutes = 7 hours. At 1h TF: 7 candles. Divided by timeframe_minutes at runtime.
_STALE_WINDOW_MINUTES = 420

# Gate 3: Minimum SMA gap for a valid cross (overridden by grid during optimisation)
_MIN_GAP_PCT = 0.20

# Gate 4/5: Minimum SMA20 slope — near-zero slope = flat MA = no real trend
_MIN_SLOPE_PCT = 0.02   # 0.02% over last 5 bars required

# Gate 5: ATR expansion factor — current ATR must be >= this × 5-bar ATR average
_ATR_EXPANSION_FACTOR = 0.95

# Gate 6: Minimum volume ratio for a cross to be valid (hard block below this)
_MIN_VOL_RATIO = 0.6   # below 0.6× average = thin tape, no institutional participation

# ── Confidence scoring constants ──────────────────────────────────────────────

# SMA-only confidence cap — LSTM path can go higher (up to 0.95)
_FALLBACK_CONF_CAP = 0.68

# Gap-to-confidence scaling factor (maps sma_gap_pct to confidence contribution)
# Formula: min(_GAP_CONF_MAX, sma_gap_pct × _GAP_TO_CONF_SCALE)
# At gap=0.30%: 0.30 × 0.27 = 0.081 → capped at 0.08. Max contribution = +0.08.
_GAP_TO_CONF_SCALE = 0.27
_GAP_CONF_MAX      = 0.08

# Volume confirmation threshold — vol above this gets a confidence boost after cap
# Grid-confirmed best: vol_mult_min=1.2. Using 1.2 here to match grid-confirmed params.
# NOTE: previously hardcoded at 1.5 — updated to match grid-confirmed 1.2.
_VOL_BOOST_THRESHOLD = 1.2

# LSTM agreement/disagreement multipliers
_LSTM_AGREE_BOOST      = 1.10   # LSTM and SMA agree on direction → +10% to confidence
_LSTM_DISAGREE_PENALTY = 0.80   # LSTM and SMA disagree → -20% to confidence

# LSTM fallback probability distribution [P_down, P_flat, P_up]
# Used when LSTM model is loaded but predict() fails, or when falling back to SMA
_LSTM_FALLBACK_PROBS = [0.25, 0.50, 0.25]

# LSTM confidence cap (higher than SMA — model has real predictive signal)
_LSTM_CONF_CAP = 0.95


def amv_lstm_signal(
    hist: pd.DataFrame,
    lstm_model=None,
    timeframe_minutes: int = 60,
    regime: str = 'TRENDING_UP',
) -> BrainSignal:
    """
    Brain 1: AMV-LSTM — Temporal Trend Memory.

    Logic (SMA fallback path):
      Gate 1: Cross freshness  — age < 2 candles → HOLD (whipsaw guard)
      Gate 2: Stale cross      — age > stale_candles → HOLD (move already happened)
      Gate 3: Gap minimum      — gap_pct < 0.20% → HOLD (SMAs overlapping = no trend)
      Gate 4: Slope alignment  — SMA20 slope must agree with direction AND exceed
                                  minimum threshold (> +0.02% for BUY, < -0.02% for SELL)
                                  SMA5 must also agree
      Gate 5: ATR expansion    — NEW. For TRENDING_UP BUY only:
                                  ATR must be expanding vs its 5-bar avg.
                                  Contracting ATR = no energy for breakout → HOLD.
      Gate 6: Volume hard block — NEW. vol_ratio < 0.6 → HOLD.
                                   Cross on thin tape has no institutional confirmation.

    Confidence (SMA fallback only):
      Base: 0.50 + min(0.08, sma_gap_pct × 0.27)   ← narrowed from 0.12 max
      +0.04 if both SMA slopes agree and exceed threshold
      +0.02 if cross_age >= 3
      +0.03 if ATR expanding (not just confirming, but active)
      RSI guard: -0.10 overbought BUY / oversold SELL
      Vol boost (after cap): +0.04 if vol_ratio >= _VOL_BOOST_THRESHOLD (grid-confirmed 1.2)
      RSI boost (after cap): +0.04 if favorable entry (oversold BUY / overbought SELL)
      Hard cap: 0.68 (SMA-only), 0.95 (LSTM loaded)

    R:R (asymmetric per regime):
      TRENDING_DOWN: T1=2.0×ATR, SL=0.75×ATR → 2.67:1
      TRENDING_UP:   T1=2.5×ATR, SL=0.65×ATR → 3.85:1 (compensates lower WR)
    """
    close = hist['Close']
    sma5  = close.rolling(5).mean()
    sma20 = close.rolling(20).mean()
    atr   = calc_atr(hist)
    price = float(close.iloc[-1])
    atr_val = float(atr)

    # Determine per-regime R:R
    if regime == 'TRENDING_DOWN':
        rr_t1 = _RR_T1_TRENDING_DOWN
        rr_t2 = _RR_T2_TRENDING_DOWN
        rr_sl = _RR_SL_TRENDING_DOWN
    elif regime == 'TRENDING_UP':
        rr_t1 = _RR_T1_TRENDING_UP
        rr_t2 = _RR_T2_TRENDING_UP
        rr_sl = _RR_SL_TRENDING_UP
    else:
        rr_t1 = _RR_T1_MULT
        rr_t2 = _RR_T2_MULT
        rr_sl = _RR_SL_MULT

    cross_bullish = sma5.iloc[-1] > sma20.iloc[-1]
    cross_age = 0
    for i in range(1, min(20, len(hist))):
        if (sma5.iloc[-i] > sma20.iloc[-i]) == cross_bullish:
            cross_age += 1
        else:
            break

    # Slope over last 3 and 5 candles respectively
    sma5_slope  = float((sma5.iloc[-1] - sma5.iloc[-3]) / sma5.iloc[-3] * 100) if sma5.iloc[-3]  else 0.0
    sma20_slope = float((sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100) if sma20.iloc[-5] else 0.0

    sma_gap_pct = abs(float(sma5.iloc[-1]) - float(sma20.iloc[-1])) / price * 100 if price > 0 else 0

    # Volume
    vol_series = hist['Volume']
    vol_avg    = float(vol_series.iloc[-20:].mean()) if len(vol_series) >= 20 else float(vol_series.mean())
    vol_curr   = float(vol_series.iloc[-1])
    vol_ratio  = vol_curr / vol_avg if vol_avg > 0 else 1.0

    # ATR expansion: current ATR vs 5-bar ATR average
    if len(hist) >= 25:
        atr_series = pd.Series([float(calc_atr(hist.iloc[:j])) for j in range(len(hist)-5, len(hist))])
        atr_avg_5  = float(atr_series.mean()) if not atr_series.empty else atr_val
    else:
        atr_avg_5 = atr_val
    atr_expanding = atr_val >= atr_avg_5 * _ATR_EXPANSION_FACTOR

    # ── LSTM path ─────────────────────────────────────────────────────────────
    if lstm_model is not None:
        try:
            lstm_probs = lstm_model.predict(hist)   # [P_down, P_flat, P_up]
            p_up, p_down = float(lstm_probs[2]), float(lstm_probs[0])
            lstm_dir = 'BUY' if p_up > 0.5 else ('SELL' if p_down > 0.5 else 'HOLD')
        except Exception:
            lstm_probs = _LSTM_FALLBACK_PROBS
            lstm_dir   = 'BUY' if cross_bullish else 'SELL'
    else:
        # ── Gate 1: Cross freshness ───────────────────────────────────────────
        # BACKTEST EVIDENCE (3 runs): cross age 2-3 has WR=21.8%, avg_R=-0.259.
        # Consistent loser across ALL test runs. Raised from < 2 to < 4.
        # Rationale: SMA 5/20 cross in downtrend fires right as price breaks the MA.
        # The first 2-3 candles after the cross are often a retest of SMA20 from below.
        # That retest catches the SL. Waiting for age >= 4 means the retest is DONE
        # and the move is genuinely resuming — this is where the edge is.
        if cross_age < _MIN_CROSS_AGE:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.44,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=(
                    f'Cross too fresh (age={cross_age} candles < {_MIN_CROSS_AGE}) — '
                    f'waiting for MA retest to complete before entry. '
                    f'Backtest confirms age 2-3 has WR=21.8% (avg_R=-0.259R).'
                ),
                supporting_factors=[],
                contra_factors=[f'Cross age < {_MIN_CROSS_AGE} — MA retest not yet complete'],
                method_confidence=0.20,
                regime_suitability='LOW',
                reliability_flags={'cross_too_fresh': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'lstm_p_up': 0.25, 'lstm_p_down': 0.25,
                    'decision_factor': 'FRESH_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gate 2: Stale cross ───────────────────────────────────────────────
        # amv_lstm.py uses 420/TF (Run 2 improvement). Grid uses 600/TF (old).
        # This file (amv_lstm.py) uses the improved 420/TF.
        stale_candles = max(4, int(_STALE_WINDOW_MINUTES / timeframe_minutes))
        if cross_age > stale_candles:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.40,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=(
                    f'Stale cross — age={cross_age} candles (> {stale_candles} for {timeframe_minutes}-min TF). '
                    f'Move already happened.'
                ),
                supporting_factors=[],
                contra_factors=[f'Cross age {cross_age} > {stale_candles} — stale'],
                method_confidence=0.10,
                regime_suitability='LOW',
                reliability_flags={'cross_too_old': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'lstm_p_up': 0.25, 'lstm_p_down': 0.25,
                    'decision_factor': 'STALE_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gate 3: Gap minimum ───────────────────────────────────────────────
        if sma_gap_pct < _MIN_GAP_PCT:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.42,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=f'SMA gap too small ({sma_gap_pct:.3f}% < {_MIN_GAP_PCT}%) — MAs overlapping',
                supporting_factors=[],
                contra_factors=['SMA gap < minimum — no trend separation'],
                method_confidence=0.15,
                regime_suitability='LOW',
                reliability_flags={'gap_too_small': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope, 'sma_gap_pct': sma_gap_pct,
                    'decision_factor': 'GAP_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gate 4 (enhanced): Slope alignment with minimum threshold ─────────
        # Both SMA5 and SMA20 must slope in the direction of the trade.
        # Slope must exceed minimum threshold — near-zero slope means flat MA = no momentum.
        direction_check = 'BUY' if cross_bullish else 'SELL'
        if direction_check == 'BUY':
            sma20_ok = sma20_slope > _MIN_SLOPE_PCT      # SMA20 must be rising meaningfully
            sma5_ok  = sma5_slope  > 0                   # SMA5 must be rising (any positive)
        else:
            sma20_ok = sma20_slope < -_MIN_SLOPE_PCT     # SMA20 must be falling meaningfully
            sma5_ok  = sma5_slope  < 0                   # SMA5 must be falling (any negative)

        if not sma20_ok or not sma5_ok:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.43,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=(
                    f'Slope gate failed for {direction_check}: '
                    f'SMA5={sma5_slope:+.3f}% SMA20={sma20_slope:+.3f}% '
                    f'(need SMA20 {">" if direction_check=="BUY" else "<"} '
                    f'{"+/-"}{_MIN_SLOPE_PCT}%, SMA5 same direction)'
                ),
                supporting_factors=[],
                contra_factors=[
                    f'SMA20 slope {sma20_slope:+.3f}% insufficient',
                    f'SMA5 slope {sma5_slope:+.3f}% conflicts' if not sma5_ok else '',
                ],
                method_confidence=0.15,
                regime_suitability='LOW',
                reliability_flags={'sma20_slope_conflict': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'sma_gap_pct': sma_gap_pct, 'cross_age': cross_age,
                    'decision_factor': 'SLOPE_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gate 5 (NEW): ATR expansion for TRENDING_UP BUY ──────────────────
        # Root cause confirmed by backtest: TRENDING_UP WR=27.5% was caused by
        # buying into a SMA cross that had no breakout energy — ATR was contracting.
        # Contracting ATR = consolidation, not a new trend leg. Price drifts sideways
        # or reverses to test SMA rather than pushing to T1.
        # Applied to TRENDING_UP BUY only (TRENDING_DOWN SELL works fine without this).
        if regime == 'TRENDING_UP' and direction_check == 'BUY' and not atr_expanding:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.43,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=(
                    f'ATR contracting — no breakout energy for TRENDING_UP BUY. '
                    f'ATR={atr_val:.4f} < {atr_avg_5:.4f} × {_ATR_EXPANSION_FACTOR}'
                ),
                supporting_factors=[],
                contra_factors=['ATR contracting — SMA cross has no momentum fuel'],
                method_confidence=0.15,
                regime_suitability='LOW',
                reliability_flags={'atr_contracting': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'sma_gap_pct': sma_gap_pct, 'cross_age': cross_age,
                    'atr_val': round(atr_val, 6), 'atr_avg_5': round(atr_avg_5, 6),
                    'decision_factor': 'ATR_EXPANSION_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gate 6 (NEW): Low-volume hard block ───────────────────────────────
        # Previously vol_ratio < 0.6 was only a flag. Now it is a hard HOLD.
        # Cross on thin tape = market makers absent = false breakout.
        if vol_ratio < _MIN_VOL_RATIO:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.41,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=f'Low volume cross — vol_ratio={vol_ratio:.2f} (< {_MIN_VOL_RATIO}). Thin tape, no institutional participation.',
                supporting_factors=[],
                contra_factors=[f'vol_ratio={vol_ratio:.2f} below minimum {_MIN_VOL_RATIO}'],
                method_confidence=0.15,
                regime_suitability='LOW',
                reliability_flags={'low_volume_cross': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'vol_ratio': round(vol_ratio, 3),
                    'decision_factor': 'LOW_VOLUME_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        lstm_probs = _LSTM_FALLBACK_PROBS    # SMA-only fallback probabilities
        lstm_dir   = 'BUY' if cross_bullish else 'SELL'

    p_up, p_down = float(lstm_probs[2]), float(lstm_probs[0])
    sma_dir      = 'BUY' if cross_bullish else 'SELL'
    agreement    = lstm_dir == sma_dir

    rsi = calc_rsi_float(hist)

    if lstm_model is not None:
        confidence = float(max(lstm_probs)) * (_LSTM_AGREE_BOOST if agreement else _LSTM_DISAGREE_PENALTY)
    else:
        # ── Confidence build-up (SMA-only) ───────────────────────────────────
        # Narrowed base from 0.50-0.62 to 0.50-0.58. Gap does less of the work
        # alone — volume, slope quality, and ATR expansion all contribute.
        base_conf = 0.50 + min(_GAP_CONF_MAX, sma_gap_pct * _GAP_TO_CONF_SCALE)

        # Both slopes agree AND exceed the threshold
        slope_quality = (
            (sma5_slope > 0 and sma20_slope > _MIN_SLOPE_PCT) or
            (sma5_slope < 0 and sma20_slope < -_MIN_SLOPE_PCT)
        )
        if slope_quality:
            base_conf += 0.04

        if cross_age >= 3:
            base_conf += 0.02

        # ATR expansion: real energy behind the cross → +0.03
        if atr_expanding:
            base_conf += 0.03

        # RSI guard (applied before cap)
        if lstm_dir == 'BUY':
            if rsi > 70:
                base_conf -= 0.10
            elif rsi > 60:
                base_conf -= 0.05
        else:
            if rsi < 30:
                base_conf -= 0.10
            elif rsi < 40:
                base_conf -= 0.05

        base_conf  = max(0.0, base_conf)
        confidence = base_conf

    conf_cap   = _LSTM_CONF_CAP if lstm_model is not None else _FALLBACK_CONF_CAP
    confidence = min(conf_cap, confidence)

    # Volume boost AFTER cap (genuine confirmation, not SMA noise)
    # Uses _VOL_BOOST_THRESHOLD = 1.2 (grid-confirmed best vol_mult_min)
    if lstm_model is None and vol_ratio >= _VOL_BOOST_THRESHOLD:
        confidence = min(conf_cap + 0.04, confidence + 0.04)

    # RSI favorable entry boost AFTER cap
    if lstm_model is None:
        if lstm_dir == 'BUY' and rsi < 40:
            confidence = min(conf_cap + 0.04, confidence + 0.04)
        elif lstm_dir == 'SELL' and rsi > 60:
            confidence = min(conf_cap + 0.04, confidence + 0.04)

    # Choppy market flag
    price_range = hist['High'].iloc[-20:].max() - hist['Low'].iloc[-20:].min()
    is_choppy   = (price_range / price) < (atr_val / price * 3) if price > 0 else False

    # Build entry/target/stop for measurements
    if lstm_dir == 'BUY':
        entry_p   = price
        target_1  = price + rr_t1 * atr_val
        target_2  = price + rr_t2 * atr_val
        stop_loss = price - rr_sl * atr_val
    else:
        entry_p   = price
        target_1  = price - rr_t1 * atr_val
        target_2  = price - rr_t2 * atr_val
        stop_loss = price + rr_sl * atr_val

    return BrainSignal(
        brain_name='AMV-LSTM',
        specialization='Temporal Trend Memory',
        method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
        direction=lstm_dir,
        confidence=confidence,
        signal_strength=float(max(lstm_probs)),
        signal_age_candles=cross_age,
        primary_evidence=(
            f'SMA cross age={cross_age} | gap={sma_gap_pct:.3f}% | '
            f'ATR {"expanding" if atr_expanding else "flat"} | vol={vol_ratio:.2f}x'
        ),
        supporting_factors=[
            f'SMA5 slope={sma5_slope:+.3f}%',
            f'SMA20 slope={sma20_slope:+.3f}%',
            f'LSTM/SMA agreement={agreement}',
            f'RSI={rsi:.1f}',
        ],
        contra_factors=['Choppy market detected'] if is_choppy else [],
        method_confidence=0.45 if is_choppy else (0.80 if lstm_model is not None else 0.55),
        regime_suitability='LOW' if is_choppy else 'HIGH',
        reliability_flags={
            'choppy_market':    is_choppy,
            'cross_too_old':    cross_age > 10,
            'no_lstm_model':    lstm_model is None,
            'low_volume_cross': vol_ratio < 0.6,
            'atr_contracting':  not atr_expanding,
        },
        measurements={
            'sma5_slope':         sma5_slope,
            'sma20_slope':        sma20_slope,
            'sma_gap_pct':        sma_gap_pct,
            'cross_age':          cross_age,
            'lstm_p_up':          p_up,
            'lstm_p_down':        p_down,
            'vol_ratio':          round(vol_ratio, 3),
            'rsi':                round(rsi, 1),
            'atr_expanding':      atr_expanding,
            'atr_val':            round(atr_val, 6),
            'atr_avg_5':          round(atr_avg_5, 6),
            'entry_price':        round(entry_p, 6),
            'target_1':           round(target_1, 6),
            'target_2':           round(target_2, 6),
            'stop_loss':          round(stop_loss, 6),
            'decision_factor':    'SMA_CROSS',
            'price_at_signal':    round(price, 6),
            'atr_at_signal':      round(atr_val, 6),
            'atr_pct_at_signal':  round(atr_val / price * 100, 3) if price > 0 else 0.0,
            'bars_used':          len(hist),
            'regime':             regime,
            'rr_t1':              rr_t1,
            'rr_sl':              rr_sl,
            'indicator_1_name':   'sma_gap_pct',
            'indicator_1_value':  round(sma_gap_pct, 4),
            'indicator_2_name':   'vol_ratio',
            'indicator_2_value':  round(vol_ratio, 3),
            'indicator_3_name':   'atr_expanding',
            'indicator_3_value':  int(atr_expanding),
        },
        rr_t1_mult=rr_t1,
        rr_t2_mult=rr_t2,
        rr_sl_mult=rr_sl,
        recent_accuracy=None,
        regime_accuracy=None,
    )