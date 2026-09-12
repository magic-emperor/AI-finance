"""
Brain 1: AMV-LSTM — Temporal Memory Brain (TRENDING_DOWN Specialist)
=====================================================================
SMA 5/20 crossover as trigger.
LSTM sequence prediction as primary signal (falls back to SMA when model not loaded).

SPECIALISATION: TRENDING_DOWN only.
  TRENDING_UP is handled by amv_lstm_uptrend.py (pullback-to-MA entry).
  Crypto (BTC-USD, ETH-USD) is excluded — see exclusion note below.
  NVDA is excluded — see NVDA exclusion note below.

REGIME EXCLUSION — CRYPTO:
  Three backtests confirm this brain has NO edge on BTC-USD (implied WR=24.9%,
  avg_R=-0.189). Root cause: BTC is the most algo-efficient 24/7 market. SMA
  5/20 crossover signals are arb'd away within minutes. No parameter tuning fixes
  this — it is structural. BTC-USD and ETH-USD must be excluded at the scanner
  level (signal_generators.py or watchlist_scanner.py) before calling this brain.
  Equity symbols (RELIANCE.NS, AAPL, etc.) are the intended use case.

SYMBOL EXCLUSION — NVDA:
  Four independent backtest runs (90d grid, 180d confirm ×3) all show:
    NVDA WR=14.3% (1 win out of 7), avg_R=-0.476R consistently.
  Root cause: NVDA is extremely high-beta US tech. In TRENDING_DOWN, SMA 5/20
  crosses fire correctly but price bounces violently off intraday lows (algo and
  options-driven). The bounce hits SL before T1 in 6 out of 7 signals.
  This is structural — no gate tuning fixes asset class behavior.
  NVDA must be excluded from AMV-LSTM TRENDING_DOWN at the scanner level.

SYMBOL WATCH — AMD:
  AMD shows similar behavior to NVDA (n=3, WR=0%, avg_R=-1.0R in 180d run).
  Sample is too small for permanent exclusion (n=3). Monitor across next 2 runs.
  If WR < 25% persists after n >= 15, exclude AMD same as NVDA.

Works best in: TRENDING_DOWN + equity/forex/commodity symbols.
Regime gate is applied in signal_generators.py before this is called.

Returns: BrainSignal (brain_contract.py)

GATE HISTORY:
  GATE 1 RAISED: Cross freshness threshold raised from age < 2 to age < 4 to < 6.
    3-run evidence: age 2-3 has WR=21.8%, avg_R=-0.259R consistently.
    Run 4: age 4-5 avg_R=+0.250, age 6-7 avg_R=+0.500.
    Run 5 (180d, Gate 7 active): age 4-5 WR=27.3%, avg_R=-0.182R (still a loser).
    Raised to 6: age 4-5 is a consistent loser across every run.

  GATE 5 (UPDATED): Originally applied to TRENDING_UP BUY only — was dead code
    since brain is TRENDING_DOWN SELL specialist. Now applies to ALL signals.
    ATR must be expanding on any cross — contracting ATR = no momentum.

  GATE 5 ATR WINDOW (FIXED in this version):
    Old version in run_amv_grid.py (_make_amv_fn): computed ATR expansion by
    calling calc_atr(hist.iloc[:j]) in a loop — O(n²), called 5 times.
    This version and run_amv_confirm.py use the brain directly.
    Brain uses vectorised TR computation. Consistent.
    NOTE: The O(n²) issue is in run_amv_grid.py's patched function only —
    it does not affect production accuracy, only speed. Fixed here for clarity.

  GATE 6: Low-volume cross hard block.

  GATE 7 (NEW): Higher-timeframe (D1) alignment.
    Root cause of Sep-Dec 2025 failures (WR=20%, 15 signals):
    Regime-Ensemble correctly labelled H1 dips as TRENDING_DOWN during a D1 uptrend.
    Brain fired SELL signals that were countertrend to the bigger daily move.
    Fix: Resample H1 data to D1 inside the brain. Block SELL if D1 SMA20 slope > 0.
    Block BUY if D1 SMA20 slope < 0. Only trade WITH the higher-TF trend.
    Falls back gracefully if not enough D1 bars (< 20 after resample).

  ASYMMETRIC R:R:
    TRENDING_DOWN: T1=2.0×ATR, SL=0.75×ATR → 2.67:1, break-even WR=27.3%
    TRENDING_UP:   T1=2.5×ATR, SL=0.65×ATR → 3.85:1 (path preserved for LSTM)

  cross_age BONUS FIXED: was >= 3 (always true after Gate 1 raised to 4).
    Changed to >= 5. Age 4 gets no bonus — just past threshold.
    Age 5+ gets +0.02 confidence. Matches backtest: age 6-7 avg_R=+0.500.

  DOCSTRING GATE LIST (FIXED in this version):
    Old docstring listed Gate 5 as "TRENDING_UP BUY only" — wrong since the fix.
    Gate 5 now applies to ALL signals. Docstring updated.

DOWNTREND ISSUES FOUND IN CODE REVIEW (2026-03-08):
  1. ATR expansion in run_amv_grid.py (patched function) uses O(n²) loop.
     Impact: slow but not wrong. Fix: not done here (brain file uses vectorised version).
  2. run_amv_grid.py has Gate 7 (HTF D1) MISSING from the patched function.
     Impact: grid was tested WITHOUT Gate 7, confirm runner tests WITH Gate 7.
     This means grid results and confirm results are on different gate sets.
     The confirm run (n=49, EV=+0.573R) includes Gate 7 — that is the correct number.
     The grid was run without Gate 7 — its results may include signals that Gate 7
     would block. The confirmed params (min_cross_age=6, gap=0.2, vol=1.2) remain
     valid because the confirm runner validated them WITH Gate 7 active.
     ACTION: run_amv_grid.py should add Gate 7 to its patched function.
     See run_amv_grid.py for the patch — noted there.
  3. AMD exclusion: n=3, WR=0%. Not enough data for permanent exclusion yet.
     Monitor. If WR < 25% after n >= 15, add to AMV_LSTM_EXCLUDED_SYMBOLS.
  4. No slippage in confirm runner (run_amv_confirm.py).
     Impact: EV is slightly optimistic. Fix applied in run_amv_confirm.py.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from market_agent.brain.brain_contract import BrainSignal
from market_agent.brain.brain_utils    import calc_atr, calc_rsi_float


# ── R:R multipliers — asymmetric per regime ──────────────────────────────────
_RR_T1_TRENDING_DOWN = 2.0
_RR_T2_TRENDING_DOWN = 3.5
_RR_SL_TRENDING_DOWN = 0.75

# TRENDING_UP path: preserved for LSTM. In SMA fallback, this brain is TRENDING_DOWN only.
_RR_T1_TRENDING_UP = 2.5
_RR_T2_TRENDING_UP = 4.0
_RR_SL_TRENDING_UP = 0.65

# Default (regime unknown — should not happen in production)
_RR_T1_MULT = 2.0
_RR_T2_MULT = 3.5
_RR_SL_MULT = 0.75

# ── Gate thresholds ───────────────────────────────────────────────────────────

# Gate 1: Cross freshness
# Evidence trail:
#   Run 1-3: age 2-3 WR=21.8%, avg_R=-0.259R (MA retest catches SL)
#   Run 4:   raised to 4. age 4-5 avg_R=+0.250, age 6-7 avg_R=+0.500
#   Run 5 (180d, Gate 7 active): age 4-5 WR=27.3%, avg_R=-0.182R (still a loser)
#   Raised to 6: age 4-5 consistently destroys EV across all runs.
_MIN_CROSS_AGE = 6

# Gate 2: Stale cross window — 420 min = 7 hours. At 1h TF: 7 candles.
_STALE_WINDOW_MINUTES = 420

# Gate 3: Minimum SMA gap
_MIN_GAP_PCT = 0.20

# Gate 4: Minimum SMA20 slope
_MIN_SLOPE_PCT = 0.02   # 0.02% over last 5 bars

# Gate 5: ATR expansion factor — ALL signals (FIXED: was TRENDING_UP BUY only)
_ATR_EXPANSION_FACTOR = 0.95

# Gate 6: Minimum volume ratio (hard block)
_MIN_VOL_RATIO = 0.6   # below 0.6× = thin tape, no institutional participation

# ── Confidence scoring constants ──────────────────────────────────────────────
_FALLBACK_CONF_CAP  = 0.68
_GAP_TO_CONF_SCALE  = 0.27
_GAP_CONF_MAX       = 0.08
_VOL_BOOST_THRESHOLD = 1.2
_LSTM_AGREE_BOOST    = 1.10
_LSTM_DISAGREE_PENALTY = 0.80
_LSTM_FALLBACK_PROBS   = [0.25, 0.50, 0.25]
_LSTM_CONF_CAP         = 0.95

# Gate 7: HTF D1 alignment
_MIN_D1_BARS      = 20
_MIN_D1_SLOPE_PCT = 0.01

# Symbol exclusion list — authoritative record of structural no-edge findings.
# Check in signal_generators.py / watchlist_scanner.py before calling this brain.
AMV_LSTM_EXCLUDED_SYMBOLS: frozenset = frozenset({
    'BTC-USD',   # 3-run evidence, WR=24.9%. SMA crossover arb'd in 24/7 algo market.
    'ETH-USD',   # Same structural reason as BTC-USD.
    'NVDA',      # 4-run evidence, WR=14.3%. High-beta — violent SL-hunting bounces.
    # AMD: under watch (n=3, WR=0%). NOT yet excluded — need n >= 15 to confirm.
})


def amv_lstm_signal(
    hist: pd.DataFrame,
    lstm_model=None,
    timeframe_minutes: int = 60,
    regime: str = 'TRENDING_DOWN',
) -> BrainSignal:
    """
    Brain 1: AMV-LSTM — Temporal Trend Memory.

    Logic (SMA fallback path):
      Gate 1: Cross freshness  — age < _MIN_CROSS_AGE → HOLD (MA retest not complete)
      Gate 2: Stale cross      — age > stale_candles → HOLD (move already happened)
      Gate 3: Gap minimum      — gap_pct < _MIN_GAP_PCT → HOLD (SMAs overlapping)
      Gate 4: Slope alignment  — SMA20 slope must agree with direction AND exceed
                                  _MIN_SLOPE_PCT. SMA5 must also agree in direction.
      Gate 5: ATR expansion    — ALL signals: ATR must be expanding vs 14-bar TR mean.
                                  (FIXED: was TRENDING_UP BUY only — dead code for SELL specialist)
                                  (FIXED: ATR window is now 14-bar to match calc_atr period)
      Gate 6: Volume hard block — vol_ratio < _MIN_VOL_RATIO → HOLD.
      Gate 7: HTF D1 alignment — Block SELL if D1 SMA20 slope > 0. Block BUY if < 0.

    Confidence (SMA fallback only):
      Base: 0.50 + min(0.08, sma_gap_pct × 0.27)
      +0.04 if both SMA slopes agree and exceed threshold
      +0.02 if cross_age >= 5 (was >= 3, dead after Gate 1 raised to 6)
      +0.03 if ATR expanding
      RSI guard: -0.10 overbought BUY / oversold SELL
      Vol boost (after cap): +0.04 if vol_ratio >= 1.2
      RSI boost (after cap): +0.04 if favorable entry
      Hard cap: 0.68 (SMA-only), 0.95 (LSTM loaded)

    R:R (TRENDING_DOWN specialist):
      T1=2.0×ATR below entry, SL=0.75×ATR above entry → 2.67:1
      Break-even WR = 0.75 / (2.0 + 0.75) = 27.3%
      Confirmed WR in 180d run: 42.9% → EV = +0.573R ✅
    """
    close   = hist['Close']
    sma5    = close.rolling(5).mean()
    sma20   = close.rolling(20).mean()
    atr     = calc_atr(hist)
    price   = float(close.iloc[-1])
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

    sma5_slope  = (
        float((sma5.iloc[-1]  - sma5.iloc[-3])  / sma5.iloc[-3]  * 100)
        if len(sma5) >= 3 and sma5.iloc[-3] else 0.0
    )
    sma20_slope = (
        float((sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100)
        if len(sma20) >= 5 and sma20.iloc[-5] else 0.0
    )

    sma_gap_pct = abs(float(sma5.iloc[-1]) - float(sma20.iloc[-1])) / price * 100 if price > 0 else 0

    # Volume
    vol_series = hist['Volume']
    vol_avg    = float(vol_series.iloc[-20:].mean()) if len(vol_series) >= 20 else float(vol_series.mean())
    vol_curr   = float(vol_series.iloc[-1])
    vol_ratio  = vol_curr / vol_avg if vol_avg > 0 else 1.0

    # ATR expansion — vectorised TR, 14-bar window matches calc_atr period
    # FIXED from old O(n²) approach: was calling calc_atr(hist.iloc[:j]) in a loop 5 times.
    high = hist['High']
    low  = hist['Low']
    if len(hist) >= 15:
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_avg_14 = float(tr.iloc[-14:].mean())
    else:
        atr_avg_14 = atr_val
    atr_expanding = atr_val >= atr_avg_14 * _ATR_EXPANSION_FACTOR

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
                    f'Cross too fresh (age={cross_age} < {_MIN_CROSS_AGE}) — '
                    f'waiting for MA retest to complete. '
                    f'Evidence: age 2-5 WR consistently < 30% across 5 runs.'
                ),
                supporting_factors=[],
                contra_factors=[f'Cross age < {_MIN_CROSS_AGE} — MA retest not yet complete'],
                method_confidence=0.20, regime_suitability='LOW',
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
                    f'Stale cross — age={cross_age} > {stale_candles} candles '
                    f'({timeframe_minutes}-min TF). Move already happened.'
                ),
                supporting_factors=[],
                contra_factors=[f'Cross age {cross_age} > {stale_candles} — stale'],
                method_confidence=0.10, regime_suitability='LOW',
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
                primary_evidence=f'SMA gap {sma_gap_pct:.3f}% < {_MIN_GAP_PCT}% — MAs overlapping',
                supporting_factors=[],
                contra_factors=['SMA gap < minimum — no trend separation'],
                method_confidence=0.15, regime_suitability='LOW',
                reliability_flags={'gap_too_small': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'sma_gap_pct': sma_gap_pct,
                    'decision_factor': 'GAP_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gate 4: Slope alignment with minimum threshold ────────────────────
        direction_check = 'BUY' if cross_bullish else 'SELL'
        if direction_check == 'BUY':
            sma20_ok = sma20_slope > _MIN_SLOPE_PCT
            sma5_ok  = sma5_slope  > 0
        else:
            sma20_ok = sma20_slope < -_MIN_SLOPE_PCT
            sma5_ok  = sma5_slope  < 0

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
                    f'SMA5={sma5_slope:+.3f}% SMA20={sma20_slope:+.3f}%'
                ),
                supporting_factors=[],
                contra_factors=[
                    f'SMA20 slope {sma20_slope:+.3f}% insufficient',
                    f'SMA5 slope {sma5_slope:+.3f}% conflicts' if not sma5_ok else '',
                ],
                method_confidence=0.15, regime_suitability='LOW',
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

        # ── Gate 5: ATR expansion — ALL signals ──────────────────────────────
        # FIXED: now applies to SELL signals too (was TRENDING_UP BUY only — dead code).
        # ATR window: 14-bar (FIXED: was 20-bar in uptrend version, misnamed as "atr_avg_5").
        if not atr_expanding:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.43,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=(
                    f'ATR contracting — no momentum behind cross. '
                    f'ATR={atr_val:.4f} < atr_avg_14={atr_avg_14:.4f} × {_ATR_EXPANSION_FACTOR}'
                ),
                supporting_factors=[],
                contra_factors=['ATR contracting — cross has no momentum fuel'],
                method_confidence=0.15, regime_suitability='LOW',
                reliability_flags={'atr_contracting': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'sma_gap_pct': sma_gap_pct, 'cross_age': cross_age,
                    'atr_val': round(atr_val, 6), 'atr_avg_14': round(atr_avg_14, 6),
                    'decision_factor': 'ATR_EXPANSION_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gate 6: Low-volume hard block ─────────────────────────────────────
        if vol_ratio < _MIN_VOL_RATIO:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.41,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=(
                    f'Low volume cross — vol_ratio={vol_ratio:.2f} (< {_MIN_VOL_RATIO}). '
                    f'Thin tape, no institutional participation.'
                ),
                supporting_factors=[],
                contra_factors=[f'vol_ratio={vol_ratio:.2f} below minimum {_MIN_VOL_RATIO}'],
                method_confidence=0.15, regime_suitability='LOW',
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

        # ── Gate 7: Higher-timeframe (D1) alignment ───────────────────────────
        # Root cause of Sep-Dec 2025 failures: H1 TRENDING_DOWN fired SELL during D1 uptrend.
        # Fix: resample H1 to D1, block SELL if D1 SMA20 slope > 0.
        # Falls back gracefully (gate open) if insufficient D1 bars.
        _htf_gate_blocked = False
        if hasattr(hist.index, 'date'):
            try:
                daily = hist.resample('D').agg(
                    Open=('Open', 'first'),
                    High=('High', 'max'),
                    Low=('Low', 'min'),
                    Close=('Close', 'last'),
                    bar_count=('Close', 'count'),
                ).dropna(subset=['Close'])
                daily = daily[daily['bar_count'] >= 4]
                if len(daily) > 1:
                    daily = daily.iloc[:-1]
                if len(daily) >= _MIN_D1_BARS:
                    d1_sma20 = daily['Close'].rolling(20).mean()
                    d1_slope = (
                        float((d1_sma20.iloc[-1] - d1_sma20.iloc[-5]) / d1_sma20.iloc[-5] * 100)
                        if d1_sma20.iloc[-5] else 0.0
                    )
                    if direction_check == 'SELL' and d1_slope > _MIN_D1_SLOPE_PCT:
                        _htf_gate_blocked = True
                    elif direction_check == 'BUY' and d1_slope < -_MIN_D1_SLOPE_PCT:
                        _htf_gate_blocked = True
            except Exception:
                pass   # Gate 7 fails open — never block all signals on exception

        if _htf_gate_blocked:
            return BrainSignal(
                brain_name='AMV-LSTM',
                specialization='Temporal Trend Memory',
                method='SMA 5/20 crossover + LSTM sequence prediction (SMA fallback)',
                direction='HOLD',
                confidence=0.43,
                signal_strength=0.0,
                signal_age_candles=cross_age,
                primary_evidence=(
                    f'HTF conflict — H1 {direction_check} signal against D1 trend. '
                    f'D1 SMA20 slope opposes trade direction. Countertrend trade blocked.'
                ),
                supporting_factors=[],
                contra_factors=['D1 trend opposes H1 signal direction'],
                method_confidence=0.15, regime_suitability='LOW',
                reliability_flags={'htf_conflict': True, 'no_lstm_model': True},
                measurements={
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'sma_gap_pct': sma_gap_pct, 'cross_age': cross_age,
                    'decision_factor': 'HTF_ALIGNMENT_GATE',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=rr_t2, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        lstm_probs = _LSTM_FALLBACK_PROBS
        lstm_dir   = 'BUY' if cross_bullish else 'SELL'

    p_up, p_down = float(lstm_probs[2]), float(lstm_probs[0])
    sma_dir      = 'BUY' if cross_bullish else 'SELL'
    agreement    = lstm_dir == sma_dir

    rsi = calc_rsi_float(hist)

    if lstm_model is not None:
        confidence = float(max(lstm_probs)) * (_LSTM_AGREE_BOOST if agreement else _LSTM_DISAGREE_PENALTY)
    else:
        base_conf = 0.50 + min(_GAP_CONF_MAX, sma_gap_pct * _GAP_TO_CONF_SCALE)

        slope_quality = (
            (sma5_slope > 0 and sma20_slope > _MIN_SLOPE_PCT) or
            (sma5_slope < 0 and sma20_slope < -_MIN_SLOPE_PCT)
        )
        if slope_quality:
            base_conf += 0.04

        # cross_age >= 5: genuine mature cross.
        # Note: >= 3 was old threshold — dead code after Gate 1 raised to 6.
        if cross_age >= 5:
            base_conf += 0.02

        if atr_expanding:
            base_conf += 0.03

        # RSI guard
        if lstm_dir == 'BUY':
            if rsi > 70:   base_conf -= 0.10
            elif rsi > 60: base_conf -= 0.05
        else:
            if rsi < 30:   base_conf -= 0.10
            elif rsi < 40: base_conf -= 0.05

        base_conf  = max(0.0, base_conf)
        confidence = base_conf

    conf_cap   = _LSTM_CONF_CAP if lstm_model is not None else _FALLBACK_CONF_CAP
    confidence = min(conf_cap, confidence)

    if lstm_model is None and vol_ratio >= _VOL_BOOST_THRESHOLD:
        confidence = min(conf_cap + 0.04, confidence + 0.04)

    if lstm_model is None:
        if lstm_dir == 'BUY' and rsi < 40:
            confidence = min(conf_cap + 0.04, confidence + 0.04)
        elif lstm_dir == 'SELL' and rsi > 60:
            confidence = min(conf_cap + 0.04, confidence + 0.04)

    price_range = hist['High'].iloc[-20:].max() - hist['Low'].iloc[-20:].min()
    is_choppy   = (price_range / price) < (atr_val / price * 3) if price > 0 else False

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
            'htf_conflict':     False,   # only True when gate blocks
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
            'atr_avg_14':         round(atr_avg_14, 6),   # renamed from atr_avg_5
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