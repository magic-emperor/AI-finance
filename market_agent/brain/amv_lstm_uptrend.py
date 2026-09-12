"""
Brain 1b: AMV-LSTM-Uptrend — Pullback-to-SMA20 Entry Brain (v4 — D1 timeframe)
================================================================================
TRENDING_UP specialist. Companion to AMV-LSTM (amv_lstm.py) which handles
TRENDING_DOWN on H1.

WHY D1:
  H1 version (v1-v3) produced n=4-9 signals on 270d across 8 symbols.
  Root cause: RSI 45-55 coinciding with SMA20 proximity in TRENDING_UP on H1
  is an extremely rare alignment — ~1 event per symbol per 3 months.
  H1 SMA20 = 20-hour average (~3 trading days). Not a level institutions watch.
  H1 regime signals are noisy — TRENDING_UP labels flip frequently.
  Result: near-zero n, cannot achieve statistical validation.

  D1 SMA20 = 20-day average (~1 calendar month). This IS a level every
  fund manager, swing trader, and institutional algo watches. When price
  pulls back to D1 SMA20 in a confirmed uptrend it is a genuine event.
  RSI 45-55 on D1 = a real multi-day correction happened (3-10 trading days
  of selling). Volume on the D1 bounce candle = real capital re-entering.
  D1 TRENDING_UP regime is more stable — regime labels do not flip intraday.

STRATEGY (unchanged from H1 concept, now applied correctly at D1):
  1. Trend confirmed up (Regime-Ensemble: TRENDING_UP, computed on D1 bars)
  2. Price retraces toward D1 SMA20 over 3-7 trading days
  3. Price closes back above D1 SMA20 on the bounce day
  4. RSI in pullback zone 45-60 (multi-day correction confirmed)
     v4.1 evidence: RSI 55-60 EV=+0.400R on D1 (H1 evidence did not transfer)
     RSI 60-65 EV=-0.125R — boundary confirmed at 60, not lower
  5. Bounce day close >= 40% of daily range (buyers won the day)
     v4.2 evidence: raw 5yr investigation across 7 symbols shows:
       LT.NS:  ALL zones positive including <0.40 and >=0.70
       GOOGL:  0.40-0.50 EV=+0.475R — was being blocked at 0.50 floor (WRONG)
       AAPL:   0.50-0.70 EV=+1.000R — 0.50 floor correct for AAPL
       RELIANCE: 0.50-0.70 EV=+0.562R, >=0.70 EV=+0.118R (upper cap too tight)
       AMD:    0.50-0.70 EV=+0.400R, >=0.70 EV=+0.116R (upper cap too tight)
     Floor lowered 0.50→0.40: LT.NS+GOOGL edge in 0.40-0.50 confirmed
     Upper cap raised 0.70→0.85: 5/7 symbols positive above 0.70
       0.85 keeps extreme blow-off candles out, unlocks moderate strong closes
     v4.1 upper cap 0.70 was set on 2.5yr n=23. Now overridden by 5yr evidence.
  6. Bounce day volume above 20-day average (real buyers returning)
  7. ATR expanding on bounce day (momentum behind the move)
  8. D1 SMA20 rising >= 0.05%/5d (MA genuinely trending up, not drifting)
     v4.1 evidence: current gate 0.10% was filtering without adding quality
  → BUY the bounce. SL = 0.65×D1 ATR below entry. T1 = 2.5×D1 ATR above.

R:R: T1=2.5×ATR, SL=0.65×ATR (entry-anchored)
  R:R = 3.85:1
  Break-even WR = 0.65 / (2.5 + 0.65) = 20.6%
  At 35% WR: EV = 0.35×2.5 − 0.65×0.65 = +0.452R
  At 42% WR: EV = 0.42×2.5 − 0.58×0.65 = +0.672R

═══════════════════════════════════════════════════════════════════
D1-SPECIFIC DESIGN DECISIONS (vs H1 v3)
═══════════════════════════════════════════════════════════════════

1. Gate 8 — W1 alignment (replaces D1 resample):
   H1 brain resampled H1→D1 bars for Gate 8. On D1, hist IS D1 already.
   Resampling D1→D1 is meaningless.
   D1 brain Gate 8 resamples D1 bars to WEEKLY (W1) and checks W1 SMA10 slope.
   W1 SMA10 = 10-week moving average ≈ 2.5 months. This is the correct
   higher-timeframe alignment check for a D1 brain.

2. SMA20 slope threshold raised: 0.015% → 0.10% over 5 bars (5 trading days)
   H1: 0.015% over 5 hours was a meaningful threshold.
   D1: 0.015% over 5 days is nearly flat — almost every stock passes this.
   0.10% over 5 days = 0.10% weekly slope. A genuinely trending stock shows
   at least 0.10% SMA20 growth per week. Below this = sideways drift, not trend.

3. ATR expansion factor loosened: 0.90 → 0.85
   D1 candles have higher natural variance than H1.
   A valid D1 bounce candle may have ATR slightly below the 14-day mean
   while still showing real buyer intent (via close_pct and volume).
   0.85 avoids over-filtering legitimate bounce days.

4. timeframe_minutes set to 1440 (D1 = 24×60 min)
   Used by downstream position sizing and holding period calculations.

5. Pullback window: Method A checks last 5 D1 bars (was 3 on H1)
   A D1 pullback to SMA20 can span 3-7 trading days.
   Checking only 3 bars on H1 (= 3 hours) was correct for H1.
   Checking 5 bars on D1 (= 1 trading week) is correct for D1.

6. MIN_HIST_BARS: runners must use at least 60 D1 bars for warmup
   (20 for SMA20 + 14 for RSI + 26 extra warmup)

═══════════════════════════════════════════════════════════════════
BACKTEST EVIDENCE TRAIL
═══════════════════════════════════════════════════════════════════

v1 H1 (180d): WR=45.3%, EV=+0.017R, n=64 — near-zero. 8 structural issues found.
v2 H1 (180d): WR=23.7%, EV=-0.076R, n=76 — negative. RSI/symbol breakdown done.
v3 H1 (270d): Best combo n=4-9 — signal starvation. H1 SMA20 not respected support.
v4 D1 (2026-03-09): D1 timeframe reframe. Full 2.5-year dataset. 7 symbols.
  n=1 on grid — regime filter confirmed healthy (30.4% TRENDING_UP).
  Regime diagnostic showed brain gates killing 873/874 TRENDING_UP bars.
  RSI diagnostic run to identify exact fixes needed.
v4.1 D1 (this version): Three evidence-driven gate adjustments.
  RSI ceiling: 55 → 60  (D1 diagnostic: RSI 55-60 EV=+0.400R n=9 ✅)
                         (RSI 60-65 EV=-0.125R confirms 60 is the right boundary)
                         (H1 evidence of RSI 55-65 being bad does NOT transfer to D1)
  close_pct upper cap added: now gate is 0.50 <= close_pct <= 0.70
                         (diagnostic: >0.70 EV=-0.102R n=23 — exhausted moves)
                         (0.40-0.50 EV=-0.020R n=5 — lower bound confirmed at 0.50)
  SMA slope: 0.10% → 0.05% (current gate 0.10% EV=+0.025R — filtering without quality)
  Expected n after fixes: ~25-40 signals across 7 symbols over 2.5 years.
  Confirm target: n >= 20, EV > 0, 95% CI lower bound > 0.

═══════════════════════════════════════════════════════════════════
CHANGE LOG
═══════════════════════════════════════════════════════════════════
v1 2026-03-08 H1: Initial build — 8 structural fixes
v2 2026-03-08 H1: Grid all-negative. Data breakdown revealed RSI/symbol issues.
v3 2026-03-08 H1: RSI 65→55, close_pct 0.30→0.50, exclusions, n=4-9 (starvation).
v4 2026-03-09 D1: Full timeframe reframe.
                  Gate 8: D1 resample → W1 resample + W1 SMA10 slope check.
                  SMA20 slope: 0.015% → 0.10% over 5 D1 bars.
                  ATR expansion factor: 0.90 → 0.85.
                  Pullback window Method A: 3 bars → 5 bars.
                  timeframe_minutes: 60 → 1440.
                  UPTREND_EXCLUDED_SYMBOLS cleared.
                  n=1 on grid — gates too tight.
v4.1 2026-03-11 D1: Three surgical gate adjustments from forward-outcome diagnostic.
                  RSI ceiling: 55 → 60 (D1 evidence: +0.400R)
                  close_pct: >= 0.50 → range [0.50, 0.70] (upper cap added)
                  SMA slope: 0.10% → 0.05% (quality unchanged, volume unlocked)
v4.2 2026-03-11 D1: Gate 8 W1 bug fix — two bugs identified from funnel diagnostic.
                  BUG 1: `hasattr(hist.index, 'to_period')` condition caused Gate 8
                         to fire incorrectly depending on index type. Removed entirely.
                         Gate 8 now always attempts to run and only fails open on exception.
                  BUG 2: W1 slope threshold -0.05% over 5 weeks was too sensitive.
                         During D1 pullbacks, W1 bars also dip → W1 SMA10 compresses.
                         Gate was firing on normal pullback behaviour, not real bear trends.
                         Three confirmed valid signals blocked: LT.NS 2025-09-23,
                         RELIANCE.NS 2025-03-18, ITC.NS 2025-04-09.
                  FIX:   W1 slope threshold changed to -0.20% over 5 weeks.
                         -0.20% = weekly trend falling ~10% annualised (genuine bear).
                         -0.05% = normal pullback compression (should NOT block).
                  FIX:   Null/NaN guard added to w1_sma10.iloc[-5] before division.
"""
from __future__ import annotations
import pandas as pd
from market_agent.brain.brain_contract import BrainSignal
from market_agent.brain.brain_utils    import calc_atr, calc_rsi_float

# ── R:R ──────────────────────────────────────────────────────────────────────
_RR_T1_MULT = 2.5    # T1 = 2.5×D1 ATR above entry
_RR_T2_MULT = 4.0    # T2 = 4.0×D1 ATR above entry (partial exit)
_RR_SL_MULT = 0.65   # SL = 0.65×D1 ATR below entry (entry-anchored)
                      # R:R = 3.85:1, break-even WR = 20.6%

# ── Confidence cap — SMA-only mode ───────────────────────────────────────────
_FALLBACK_CONF_CAP = 0.70

# ── Pullback detection ────────────────────────────────────────────────────────
# Method A: candle LOW within _PULLBACK_ATR_DISTANCE×ATR of SMA20, last 5 D1 bars.
# D1 CHANGE: window extended 3 → 5 bars. A D1 pullback to SMA20 spans 3-7 days.
# Checking only 3 bars missed valid setups where the low touched SMA20 on day -4 or -5.
_PULLBACK_ATR_DISTANCE  = 1.0
_PULLBACK_LOOKBACK_BARS = 5   # D1: look back 5 bars (= 1 trading week)

# Method B: sequential close-to-close drop.
# >= 2 of last 3 bar-to-bar closes DOWN AND total close drop >= threshold × ATR.
# Unchanged from H1 — directional logic is correct on any timeframe.
_MIN_PULLBACK_DROP_ATR = 0.4

# ── Bounce candle quality ─────────────────────────────────────────────────────
# v4.2 CHANGE: Floor 0.50→0.40, Upper cap 0.70→0.85
#
# v4.1 set gate at [0.50, 0.70] based on 2.5yr data (n=23 above 0.70, n=5 below 0.50).
# That evidence was insufficient. 5yr raw investigation across 7 symbols overrides it.
#
# New floor 0.40 — evidence:
#   LT.NS:  0.40-0.50 EV=+1.046R n=13 ✅ (was being blocked — WRONG)
#   GOOGL:  0.40-0.50 EV=+0.475R n=14 ✅ (edge IS in this zone for GOOGL)
#           0.50-0.70 EV=-0.059R ❌ — we had GOOGL's gate inverted
#   AAPL:   0.40-0.50 EV=+0.295R n=10 ✅ (lower floor loses nothing for AAPL)
#   AMD:    0.40-0.50 EV=-0.020R n=5  ⚠ (marginal, but RSI+vol gates filter these)
#   Structural logic: 0.40-0.50 = buyers recovered price to middle of range.
#   Sellers tried, buyers pushed back. Not a weak close — a contested recovery.
#
# New upper cap 0.85 — evidence:
#   LT.NS:     >=0.70 EV=+0.744R n=61 ✅
#   RELIANCE:  >=0.70 EV=+0.118R n=41 ✅
#   AMD:       >=0.70 EV=+0.116R n=37 ✅
#   ITC.NS:    >=0.70 EV=+0.201R n=37 ✅
#   AAPL:      >=0.70 EV=-0.050R n=42 ❌ (only exception — but marginal negative)
#   5/7 symbols positive above 0.70. The 0.70 cap was wrong.
#   0.85 chosen over removing cap entirely: >=0.85 = extreme blow-off (top 15% of range)
#   Structural logic: close in top 15% all day = sellers completely absent.
#   Either a gap-continuation (different setup) or genuine exhaustion with no test.
#   Both are reasons not to enter a pullback-bounce trade.
_MIN_CLOSE_ABOVE_LOW_PCT = 0.40   # v4.2: lowered from 0.50 (LT.NS+GOOGL 5yr evidence)
_MAX_CLOSE_ABOVE_LOW_PCT = 0.85   # v4.2: raised from 0.70 (5/7 symbols positive >0.70)

# ── RSI gates ─────────────────────────────────────────────────────────────────
# v4.1 CHANGE: RSI ceiling raised from 55 → 60.
#
# Original ceiling of 55 was based on H1 backtest evidence:
#   H1 v2: RSI 55-65 = EV=-0.152R on n=64 — clearly negative on H1.
# But H1 SMA20 = 3-day average. Not a level institutions respect.
# D1 SMA20 = 1-month average. Institutions ACTIVELY manage around this level.
#
# D1 forward-outcome diagnostic (2026-03-11):
#   RSI 50-55 [D1]: EV=+1.712R on n=4  ✅ (small n, directionally strong)
#   RSI 55-60 [D1]: EV=+0.400R on n=9  ✅ — H1 evidence does NOT transfer to D1
#   RSI 60-65 [D1]: EV=-0.125R on n=12 ❌ — edge drops off sharply above 60
#
# Conclusion: On D1, SMA20 is real support. RSI 55-60 = moderate pullback in
# a genuine uptrend — buyer exhaustion has started but trend still intact.
# RSI 60-65 = barely any pullback at all. Setting boundary at 60 is data-driven.
#
# RSI < 45 on D1 = momentum breaking down — too deep, trend may be reversing.
_RSI_PULLBACK_MAX = 60   # v4.1: raised from 55 → 60 (D1 diagnostic evidence)
_RSI_RECOVERY_MIN = 45   # unchanged — below this = potential trend break

# ── ATR expansion ─────────────────────────────────────────────────────────────
# Bounce day ATR >= 14-bar simple TR mean × factor.
# D1 CHANGE: 0.90 → 0.85. D1 candles have higher natural daily variance than H1.
# A valid bounce day can have ATR slightly below the 14-day mean while still
# showing real intent via close_pct and volume. 0.85 avoids over-filtering.
_ATR_EXPANSION_FACTOR = 0.85

# ── SMA20 slope ───────────────────────────────────────────────────────────────
# v4.1 CHANGE: 0.10% → 0.05% over 5 D1 bars.
#
# v4 set this at 0.10% reasoning that 0.015% (H1 value) was too loose for D1.
# That was correct — 0.015% on D1 is nearly flat.
# But 0.10% turned out to be too tight:
#   Regime diagnostic: killed 136 bars (15.6% of TRENDING_UP bars).
#   Forward-outcome diagnostic: current gate (>= 0.10%) EV=+0.025R — barely above zero.
#   The gate at 0.10% was reducing signal count without improving signal quality.
#
# 0.05% over 5 D1 bars = SMA20 is meaningfully rising (~2.5% annualised growth).
# This filters true flat/declining MAs while keeping genuinely trending setups.
# The RSI and close_pct gates now do the quality filtering.
_MIN_SMA20_SLOPE_PCT = 0.05   # v4.1: lowered from 0.10% → 0.05% over 5 D1 bars

# ── Volume ────────────────────────────────────────────────────────────────────
# Bounce day volume must be above 20-day average.
# On D1 this is highly meaningful — above-average daily volume on a bounce day
# = institutional money stepping back in. Let grid search this.
_MIN_BOUNCE_VOL_RATIO = 1.2

# ── Gate 8: W1 alignment (D1 CHANGE — replaces D1 resample) ──────────────────
# H1 brain resampled H1→D1 and checked D1 SMA20 slope.
# D1 brain resamples D1→W1 and checks W1 SMA10 slope.
# W1 SMA10 = 10-week MA ≈ 2.5 months. Correct HTF for a D1 entry brain.
# Require at least 15 complete weekly bars before computing W1 SMA10.
# Gate fails open — if insufficient weekly history, trade is allowed through.
#
# v4.1 BUG FIX: _MIN_W1_SLOPE_PCT removed from the gate threshold.
# The gate previously blocked when W1 SMA10 slope < -_MIN_W1_SLOPE_PCT (-0.05%).
# This was too sensitive — during any D1 pullback, W1 bars also dip slightly,
# compressing W1 SMA10. The gate was firing on normal pullback behaviour.
# Three valid signals killed: LT.NS 2025-09-23, RELIANCE.NS 2025-03-18, ITC.NS 2025-04-09.
# New gate: block only when W1 SMA10 slope < -0.20% over 5 weeks.
# -0.20% = weekly trend falling ~10% annualised. That is a real bear structure.
# _MIN_W1_SLOPE_PCT kept for reference but gate uses hardcoded -0.20 threshold.
_MIN_W1_BARS      = 15    # minimum complete W1 bars for W1 SMA10 slope
_MIN_W1_SLOPE_PCT = 0.05  # kept for reference — gate uses -0.20 threshold (see Gate 8)

# ── Symbol exclusions ─────────────────────────────────────────────────────────
# v4.2: TATASTEEL.NS removed from basket (not from exclusions — never added).
# TATASTEEL structural finding (5yr raw investigation):
#   SMA20 touch zones: 0.50-0.70 EV=-0.335R ❌, 0.40-0.50 EV=-0.125R ❌
#   Only >=0.70 positive: EV=+0.309R n=46
#   Edge is in STRONG MOMENTUM candles, not pullback-to-support bounces.
#   This is a BREAKOUT pattern — wrong strategy for this brain.
#   No gate change can fix a fundamental strategy mismatch.
#   Action: remove from ALL_SYMBOLS in runner files. Not in UPTREND_EXCLUDED_SYMBOLS
#   because that list is for post-live monitoring exclusions, not structural removal.
UPTREND_EXCLUDED_SYMBOLS: frozenset = frozenset()   # post-live monitoring exclusions only


def amv_lstm_uptrend_signal(
    hist: pd.DataFrame,
    lstm_model=None,
    timeframe_minutes: int = 1440,   # D1 = 24 × 60 minutes
    regime: str = 'TRENDING_UP',
) -> BrainSignal:
    """
    Brain 1b: AMV-LSTM-Uptrend (D1) — Pullback-to-SMA20 entry in TRENDING_UP.

    hist: D1 OHLCV bars (daily timeframe). NOT H1 bars.

    Gates (ALL must pass):
      Gate 1: SMA20 slope >= 0.05% over 5 D1 bars (rising MA, not flat/declining)
              v4.1: lowered from 0.10% — was filtering without improving quality
      Gate 2: Pullback confirmed — Method A OR Method B:
               Method A: candle LOW within 1.0×ATR of SMA20, last 5 D1 bars.
               Method B: >= 2 of last 3 D1 closes down AND total drop >= 0.4×ATR.
      Gate 3: Current close > SMA20 (bounce confirmed, not still below support)
      Gate 4: close_pct >= 0.40 AND <= 0.85 — buyers won the day, not extreme blow-off
               v4.2: floor 0.50→0.40 (LT.NS 0.40-0.50 EV=+1.046R, GOOGL 0.40-0.50 EV=+0.475R)
               v4.2: upper cap 0.70→0.85 (5/7 symbols positive above 0.70 on 5yr data)
               v4.1: lower bound 0.50 kept — 0.40-0.50 EV=-0.020R (sellers still active)
      Gate 5a: RSI <= 70 (hard overbought block)
      Gate 5b: RSI >= 45 (not trend-break territory)
      Gate 5c: RSI <= 60 (genuine pullback zone — v4.1: raised from 55→60)
               D1 diagnostic: RSI 55-60 EV=+0.400R ✅  RSI 60-65 EV=-0.125R ❌
      Gate 6: Volume >= 1.2×20-day average (institutional buyer confirmation)
      Gate 7: ATR >= 14-day TR mean × 0.85 (momentum on bounce day)
      Gate 8: W1 SMA10 not clearly falling (weekly trend alignment)
    """
    close   = hist['Close']
    high    = hist['High']
    low     = hist['Low']
    sma20   = close.rolling(20).mean()
    sma5    = close.rolling(5).mean()
    atr     = calc_atr(hist)
    price   = float(close.iloc[-1])
    atr_val = float(atr)
    rsi     = calc_rsi_float(hist)

    sma20_curr = float(sma20.iloc[-1])
    sma5_curr  = float(sma5.iloc[-1])

    # SMA20 slope over last 5 D1 bars (% change) — threshold 0.10% (D1-calibrated)
    sma20_slope = (
        float((sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100)
        if len(sma20) >= 5 and sma20.iloc[-5] else 0.0
    )
    sma5_slope = (
        float((sma5.iloc[-1] - sma5.iloc[-3]) / sma5.iloc[-3] * 100)
        if len(sma5) >= 3 and sma5.iloc[-3] else 0.0
    )

    # Volume — 20-day average
    vol_series = hist['Volume']
    vol_avg    = (float(vol_series.iloc[-20:].mean())
                  if len(vol_series) >= 20 else float(vol_series.mean()))
    vol_ratio  = float(vol_series.iloc[-1]) / vol_avg if vol_avg > 0 else 1.0

    # ATR expansion — 14-bar simple TR mean vs current D1 ATR
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

    # ── Pullback detection ─────────────────────────────────────────────────────
    pullback_method  = 'NONE'
    pullback_touched = False

    # Method A: D1 candle LOW within _PULLBACK_ATR_DISTANCE×ATR of SMA20
    # D1 CHANGE: look back 5 bars (was 3 on H1).
    # A D1 pullback to SMA20 can take 3-7 trading days. Checking only 3 bars
    # misses valid setups where the SMA20 touch was 4-5 days ago.
    for j in range(1, min(_PULLBACK_LOOKBACK_BARS + 1, len(hist))):
        dist_to_sma = abs(float(low.iloc[-j]) - sma20_curr)
        if dist_to_sma <= _PULLBACK_ATR_DISTANCE * atr_val:
            pullback_touched = True
            pullback_method  = 'METHOD_A'
            break

    # Method B: sequential close-to-close directional drop
    # >= 2 of last 3 bar-to-bar D1 closes are DOWN
    # AND total close drop from window peak >= _MIN_PULLBACK_DROP_ATR × ATR
    if not pullback_touched and len(hist) >= 5:
        recent_closes = close.iloc[-4:]
        bar_changes   = recent_closes.diff().dropna()
        down_bars     = int((bar_changes < 0).sum())
        total_drop    = float(recent_closes.max() - recent_closes.iloc[-1])
        if down_bars >= 2 and total_drop >= _MIN_PULLBACK_DROP_ATR * atr_val:
            pullback_touched = True
            pullback_method  = 'METHOD_B'

    # Bounce candle quality — close position within daily range
    candle_low   = float(low.iloc[-1])
    candle_high  = float(high.iloc[-1])
    candle_range = candle_high - candle_low if candle_high > candle_low else atr_val * 0.1
    close_pct    = (price - candle_low) / candle_range

    # ── _hold helper ──────────────────────────────────────────────────────────
    def _hold(reason: str, conf: float = 0.42, factor: str = 'GATE_HOLD') -> BrainSignal:
        return BrainSignal(
            brain_name='AMV-LSTM-Uptrend-D1',
            specialization='Pullback-to-SMA20 D1 Entry',
            method='D1 SMA20 pullback bounce (v4)',
            direction='HOLD', confidence=conf,
            signal_strength=0.0, signal_age_candles=0,
            primary_evidence=reason,
            supporting_factors=[], contra_factors=[reason],
            method_confidence=0.15, regime_suitability='LOW',
            reliability_flags={factor: True},
            measurements={
                'decision_factor':  factor,
                'price_at_signal':  round(price, 6),
                'atr_at_signal':    round(atr_val, 6),
                'sma20':            round(sma20_curr, 6),
                'sma20_slope':      round(sma20_slope, 4),
                'rsi':              round(rsi, 1),
                'vol_ratio':        round(vol_ratio, 3),
                'atr_expanding':    int(atr_expanding),
                'atr_avg_14':       round(atr_avg_14, 6),
                'pullback_touched': int(pullback_touched),
                'pullback_method':  pullback_method,
                'close_pct':        round(close_pct, 3),
                'bars_used':        len(hist),
                'timeframe':        'D1',
            },
            rr_t1_mult=_RR_T1_MULT, rr_t2_mult=_RR_T2_MULT, rr_sl_mult=_RR_SL_MULT,
            recent_accuracy=None, regime_accuracy=None,
        )

    # ── LSTM path ─────────────────────────────────────────────────────────────
    lstm_probs_used = [0.25, 0.50, 0.25]
    lstm_dir        = None

    if lstm_model is not None:
        try:
            lstm_probs   = lstm_model.predict(hist)
            p_up, p_down = float(lstm_probs[2]), float(lstm_probs[0])
            if p_up <= 0.55:
                return _hold(
                    f'LSTM P_up={p_up:.3f} <= 0.55 — not confirming D1 BUY.',
                    conf=0.44, factor='LSTM_WEAK_SIGNAL',
                )
            lstm_dir        = 'BUY'
            lstm_probs_used = lstm_probs
        except Exception:
            lstm_probs_used = [0.25, 0.50, 0.25]
            lstm_dir        = None

    # ── SMA fallback gates ────────────────────────────────────────────────────
    if lstm_model is None or lstm_dir is None:

        # Gate 1: D1 SMA20 genuinely rising
        # v4.1: threshold lowered 0.10% → 0.05% over 5 D1 bars.
        # 0.10% was filtering 136 bars (15.6% of TRENDING_UP) without improving EV.
        # 0.05% still blocks flat/declining MAs. RSI + close_pct do quality filtering.
        if sma20_slope < _MIN_SMA20_SLOPE_PCT:
            return _hold(
                f'D1 SMA20 slope {sma20_slope:+.4f}% < {_MIN_SMA20_SLOPE_PCT}% over 5 days. '
                f'MA is flat or declining — not a valid D1 uptrend structure.',
                conf=0.42, factor='SMA20_SLOPE_GATE',
            )

        # Gate 2: Pullback confirmed (Method A or B)
        if not pullback_touched:
            dist = (price - sma20_curr) / atr_val if atr_val > 0 else 999
            return _hold(
                f'No pullback to D1 SMA20 detected in last {_PULLBACK_LOOKBACK_BARS} days. '
                f'Method A: price is {dist:.2f}×ATR from SMA20 (need <= {_PULLBACK_ATR_DISTANCE}). '
                f'Method B: need >= 2 down closes AND total drop >= {_MIN_PULLBACK_DROP_ATR}×ATR. '
                f'Wait for retracement before entering.',
                conf=0.40, factor='NO_PULLBACK_GATE',
            )

        # Gate 3: D1 close above SMA20 — bounce confirmed
        if price <= sma20_curr:
            return _hold(
                f'D1 close {price:.4f} at or below SMA20 {sma20_curr:.4f}. '
                f'Price has not reclaimed above D1 SMA20. Bounce not confirmed.',
                conf=0.40, factor='BELOW_SMA20_GATE',
            )

        # Gate 4: Bounce day quality — close in [40%, 85%] of daily range
        # v4.2: floor 0.50→0.40, upper cap 0.70→0.85 (5yr raw investigation)
        # Floor 0.40: LT.NS 0.40-0.50 EV=+1.046R, GOOGL 0.40-0.50 EV=+0.475R.
        #             GOOGL edge is IN this zone — 0.50 floor had it backwards.
        # Upper cap 0.85: 5/7 symbols positive above 0.70 on 5yr data.
        #             Only extreme blow-off candles (top 15% of range) excluded.
        if close_pct < _MIN_CLOSE_ABOVE_LOW_PCT:
            return _hold(
                f'Weak bounce day — D1 close at {close_pct:.0%} of daily range '
                f'(need >= {_MIN_CLOSE_ABOVE_LOW_PCT:.0%}). '
                f'Sellers still dominated this day. Below 40% = buyers failed to recover.',
                conf=0.41, factor='WEAK_BOUNCE_GATE',
            )
        if close_pct > _MAX_CLOSE_ABOVE_LOW_PCT:
            return _hold(
                f'Extreme blow-off day — D1 close at {close_pct:.0%} of daily range '
                f'(need <= {_MAX_CLOSE_ABOVE_LOW_PCT:.0%}). '
                f'Close in top 15% of range all day — sellers completely absent. '
                f'Gap-continuation or exhaustion. Not a pullback-bounce entry.',
                conf=0.41, factor='EXHAUSTED_BOUNCE_GATE',
            )

        # Gate 5a: RSI not overbought on D1
        if rsi > 70:
            return _hold(
                f'D1 RSI={rsi:.1f} > 70. Still overbought — no real pullback occurred yet.',
                conf=0.41, factor='RSI_OVERBOUGHT_GATE',
            )

        # Gate 5b: RSI not in trend-break territory
        if rsi < _RSI_RECOVERY_MIN:
            return _hold(
                f'D1 RSI={rsi:.1f} < {_RSI_RECOVERY_MIN}. '
                f'RSI this low in TRENDING_UP context signals trend break, not a bounce. '
                f'Wait for RSI to stabilise above {_RSI_RECOVERY_MIN}.',
                conf=0.41, factor='RSI_TOO_LOW_GATE',
            )

        # Gate 5c: RSI in genuine pullback zone [45, 60]
        # v4.1: ceiling raised 55 → 60 based on D1 forward-outcome diagnostic.
        # D1 RSI 55-60: EV=+0.400R on n=9 — edge confirmed on D1.
        # D1 RSI 60-65: EV=-0.125R on n=12 — no edge above 60. Boundary confirmed.
        # H1 evidence (RSI 55-65 = EV=-0.152R) does NOT transfer to D1 because
        # H1 SMA20 is not real institutional support. D1 SMA20 is.
        if rsi > _RSI_PULLBACK_MAX:
            return _hold(
                f'D1 RSI={rsi:.1f} > {_RSI_PULLBACK_MAX}. '
                f'Has not pulled back enough for a genuine D1 SMA20 retest. '
                f'D1 diagnostic: RSI 60-65 = EV=-0.125R (no edge). '
                f'RSI 55-60 = EV=+0.400R (edge confirmed — below {_RSI_PULLBACK_MAX} is the zone). '
                f'Wait for RSI to retract to [{_RSI_RECOVERY_MIN}, {_RSI_PULLBACK_MAX}].',
                conf=0.42, factor='RSI_NOT_PULLED_BACK_GATE',
            )

        # Gate 6: Volume confirmation on bounce day
        if vol_ratio < _MIN_BOUNCE_VOL_RATIO:
            return _hold(
                f'Bounce day volume {vol_ratio:.2f}×avg < {_MIN_BOUNCE_VOL_RATIO}×. '
                f'No institutional buyer confirmation on this D1 candle.',
                conf=0.41, factor='LOW_BOUNCE_VOLUME_GATE',
            )

        # Gate 7: ATR expanding — momentum on bounce day
        if not atr_expanding:
            return _hold(
                f'D1 ATR contracting (ATR={atr_val:.4f} < '
                f'atr_avg_14={atr_avg_14:.4f} × {_ATR_EXPANSION_FACTOR}). '
                f'Low-energy bounce day. Wait for a candle with real momentum.',
                conf=0.42, factor='ATR_CONTRACTION_GATE',
            )

        # Gate 8: W1 (weekly) alignment — D1 CHANGE from D1 resample
        # D1 brain cannot use D1 resample (hist IS D1 already).
        # Instead we resample D1 bars to weekly and check W1 SMA10 slope.
        # W1 SMA10 = 10-week MA ≈ 2.5 months — correct HTF for D1 entry.
        #
        # v4.1 BUG FIX: Removed faulty `hasattr(hist.index, 'to_period')` condition.
        # That check caused Gate 8 to silently skip OR incorrectly fire depending on
        # index type. Gate 8 must always attempt to run and fail open on exception.
        #
        # v4.1 THRESHOLD FIX: Gate now blocks only when W1 SMA10 is CLEARLY falling.
        # Previous threshold -0.05% over 5 weeks was too sensitive — during a D1
        # pullback (which is exactly our setup), W1 bars are also in a dip phase.
        # W1 SMA10 naturally compresses slightly. This was killing 3 valid signals
        # (LT.NS 2025-09-23, RELIANCE.NS 2025-03-18, ITC.NS 2025-04-09) — all
        # excellent setups that were blocked by a W1 slope of ~ -0.05% to -0.15%.
        # New threshold: block only when W1 SMA10 slope < -0.20% over 5 weeks.
        # -0.20% over 5 weeks = weekly trend falling at ~10% annualised. That IS
        # a genuinely declining weekly trend. -0.05% is just a pullback pause.
        #
        # Gate fails open — never block signals on exception or insufficient data.
        try:
            weekly = hist.resample('W').agg(
                Open=('Open',   'first'),
                High=('High',   'max'),
                Low=('Low',     'min'),
                Close=('Close', 'last'),
                bar_count=('Close', 'count'),
            ).dropna(subset=['Close'])
            # Filter partial weeks (< 3 D1 bars — holiday-shortened weeks)
            weekly = weekly[weekly['bar_count'] >= 3]
            # Drop current incomplete week
            if len(weekly) > 1:
                weekly = weekly.iloc[:-1]
            if len(weekly) >= _MIN_W1_BARS:
                w1_sma10 = weekly['Close'].rolling(10).mean()
                if (w1_sma10.iloc[-5] is not None
                        and float(w1_sma10.iloc[-5]) != 0
                        and not pd.isna(w1_sma10.iloc[-5])):
                    w1_slope = float(
                        (w1_sma10.iloc[-1] - w1_sma10.iloc[-5])
                        / w1_sma10.iloc[-5] * 100
                    )
                    # Block only clearly declining weekly trend (< -0.20% over 5 weeks)
                    # -0.05% was too sensitive — fires during normal D1 pullback dips
                    if w1_slope < -0.20:
                        return _hold(
                            f'W1 conflict — weekly SMA10 slope={w1_slope:+.3f}% '
                            f'(< -0.20%). Clearly declining weekly trend. '
                            f'D1 BUY signal against a falling weekly structure. '
                            f'Wait for W1 SMA10 to stabilise (slope > -0.20%).',
                            conf=0.43, factor='HTF_W1_ALIGNMENT_GATE',
                        )
        except Exception:
            pass   # Gate 8 fails open — never block all signals on exception

        lstm_probs_used = [0.25, 0.50, 0.25]
        lstm_dir        = 'BUY'

    # ── All gates passed — build D1 BUY signal ────────────────────────────────
    p_up, p_down = float(lstm_probs_used[2]), float(lstm_probs_used[0])

    if lstm_model is not None:
        confidence = p_up * (1.10 if p_up > 0.65 else 1.0)
        confidence = min(0.95, confidence)
    else:
        base_conf = 0.52

        # SMA20 slope quality
        if sma20_slope > _MIN_SMA20_SLOPE_PCT * 2:   # >= 0.10% per week = solid trend
            base_conf += 0.03
        # Close quality — best zone is 0.55-0.70 (buyers clearly won, not exhausted)
        if 0.55 <= close_pct <= _MAX_CLOSE_ABOVE_LOW_PCT:
            base_conf += 0.03
        # Volume
        if vol_ratio >= 1.5:
            base_conf += 0.03
        # ATR genuinely expanding above mean
        if atr_val > atr_avg_14:
            base_conf += 0.02
        # RSI sub-zone — deeper pullback = more selling exhausted = better bounce quality
        if _RSI_RECOVERY_MIN <= rsi < 50:
            base_conf += 0.04   # deep pullback (45-50): high-quality exhaustion setup
        elif rsi < 55:
            base_conf += 0.03   # moderate pullback (50-55): solid
        elif rsi < 60:
            base_conf += 0.01   # light pullback (55-60): valid but less exhausted
        # Method A = D1 low physically touched SMA20 = cleanest setup
        if pullback_method == 'METHOD_A':
            base_conf += 0.02

        confidence = min(_FALLBACK_CONF_CAP, base_conf)

        # Post-cap: exceptional volume
        if vol_ratio >= 2.0:
            confidence = min(_FALLBACK_CONF_CAP + 0.04, confidence + 0.04)

    # SL: entry-anchored — always 0.65×D1 ATR below entry
    entry     = price
    target_1  = price + _RR_T1_MULT * atr_val
    target_2  = price + _RR_T2_MULT * atr_val
    stop_loss = price - _RR_SL_MULT * atr_val

    return BrainSignal(
        brain_name='AMV-LSTM-Uptrend-D1',
        specialization='Pullback-to-SMA20 D1 Entry',
        method='D1 SMA20 pullback bounce (v4)',
        direction='BUY',
        confidence=confidence,
        signal_strength=float(max(lstm_probs_used)),
        signal_age_candles=0,
        primary_evidence=(
            f'D1 pullback bounce [{pullback_method}] off SMA20={sma20_curr:.4f}. '
            f'RSI={rsi:.1f} (zone {_RSI_RECOVERY_MIN}-{_RSI_PULLBACK_MAX}) '
            f'close_pct={close_pct:.0%} vol={vol_ratio:.2f}× '
            f'ATR={"expanding" if atr_expanding else "flat"}'
        ),
        supporting_factors=[
            f'D1 SMA20 slope={sma20_slope:+.4f}% per 5 days (>= {_MIN_SMA20_SLOPE_PCT}%)',
            f'Pullback method={pullback_method} (lookback={_PULLBACK_LOOKBACK_BARS} D1 bars)',
            f'Bounce day close={close_pct:.0%} of daily range [{_MIN_CLOSE_ABOVE_LOW_PCT:.0%}-{_MAX_CLOSE_ABOVE_LOW_PCT:.0%}]',
            f'Vol={vol_ratio:.2f}×20-day avg',
            f'D1 RSI={rsi:.1f} in pullback zone [{_RSI_RECOVERY_MIN},{_RSI_PULLBACK_MAX}]',
        ],
        contra_factors=[],
        method_confidence=0.80 if lstm_model is not None else 0.60,
        regime_suitability='HIGH',
        reliability_flags={
            'no_lstm_model':   lstm_model is None,
            'atr_expanding':   atr_expanding,
            'high_volume':     vol_ratio >= 1.5,
            'htf_conflict':    False,
            'method_a_signal': pullback_method == 'METHOD_A',
            'deep_rsi':        rsi < 50,
            'timeframe_d1':    True,
        },
        measurements={
            'timeframe':          'D1',
            'sma20':              round(sma20_curr, 6),
            'sma5':               round(sma5_curr, 6),
            'sma20_slope':        round(sma20_slope, 4),
            'sma5_slope':         round(sma5_slope, 4),
            'close_pct':          round(close_pct, 3),
            'pullback_touched':   int(pullback_touched),
            'pullback_method':    pullback_method,
            'rsi':                round(rsi, 1),
            'vol_ratio':          round(vol_ratio, 3),
            'atr_expanding':      int(atr_expanding),
            'atr_val':            round(atr_val, 6),
            'atr_avg_14':         round(atr_avg_14, 6),
            'sma20_dist_atr':     round(abs(price - sma20_curr) / atr_val, 3)
                                  if atr_val > 0 else 0,
            'lstm_p_up':          p_up,
            'lstm_p_down':        p_down,
            'entry_price':        round(entry, 6),
            'target_1':           round(target_1, 6),
            'target_2':           round(target_2, 6),
            'stop_loss':          round(stop_loss, 6),
            'decision_factor':    'D1_PULLBACK_BOUNCE',
            'price_at_signal':    round(price, 6),
            'atr_at_signal':      round(atr_val, 6),
            'atr_pct_at_signal':  round(atr_val / price * 100, 3) if price > 0 else 0.0,
            'bars_used':          len(hist),
            'indicator_1_name':   'rsi',
            'indicator_1_value':  round(rsi, 1),
            'indicator_2_name':   'close_pct',
            'indicator_2_value':  round(close_pct, 3),
            'indicator_3_name':   'vol_ratio',
            'indicator_3_value':  round(vol_ratio, 3),
        },
        rr_t1_mult=_RR_T1_MULT,
        rr_t2_mult=_RR_T2_MULT,
        rr_sl_mult=_RR_SL_MULT,
        recent_accuracy=None,
        regime_accuracy=None,
    )