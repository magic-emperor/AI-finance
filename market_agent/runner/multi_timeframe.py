"""
Brain 4: Multi-Timeframe — Cross-Timeframe Signal Alignment Verifier
=====================================================================
RESEARCH FOUNDATION
-------------------
1. Elder, A. (1993). Trading for a Living. Wiley.
   Original Triple Screen System — trade in direction of higher TF trend.
   Higher TF (1d) weighted more than lower TF (1h). Weights 3:2:1 directly
   derived from Elder's relative timeframe importance hierarchy.

2. Trade With The Pros (2025): Multi-Timeframe Analysis.
   "Data shows trades executed with aligned signals across two timeframes
   achieve a 58% win rate versus 39% for non-aligned trades."
   Source: https://tradewiththepros.com/multi-timeframe-analysis/

3. LuxAlgo (2025): Moving Average Crossovers for Entry and Exit.
   "Combining MA crossovers with RSI improved annual returns in a 12-year
   S&P 500 study. Adding RSI filters reduced false signals by 62%."
   Validates RSI 60/40 gate on SMA crossover.

4. QuantPedia (2025): Multi-Timeframe Bitcoin Strategy.
   "D1H1 filter based on Elder principle reduced false signals, improved
   Sharpe and Calmar ratios." Validates 1d as filter for 1h signals.

DOCUMENTED WIN RATE RANGE (from literature):
   - Raw SMA crossover (no filter):    45–55%
   - SMA + RSI filter:                 55–62%
   - SMA + RSI + regime gate:          58–65%
   - 3-TF alignment (full system):     60–68% (target: ≥60%)

KNOWN FAILURE CONDITIONS:
   - Ranging/choppy markets: SMA crossovers produce whipsaws (handled by
     regime gate — brain excluded from RANGING/SQUEEZE/VOLATILE)
   - Lag at trend exhaustion: RSI 60/40 gate mitigates but does not
     eliminate late entries on fast trend reversals
   - Single-TF backtest: backtester runs fetch_fn=None → 1H only.
     Backtest results understate full 3-TF capability.

WHAT CHANGED FROM v4 (Phase-4 doc comment preserved below)
-----------------------------------------------------------
KEY CHANGES from Phase-4 (original, preserved):
  - RSI thresholds tightened from 65/35 → 60/40.
  - Imports RSI from brain_utils (Wilder's EMA — consistent with all others)
  - Added rr_t1_mult=2.0, rr_sl_mult=0.75 (2.67:1 R:R, tight SL)

BUG FIXES applied in this version (v5)
---------------------------------------
[BF-1]  measurements dict had STRING values (tf_sma keys like "5/20").
        BrainSignal.measurements is Dict[str, float]. to_debate_context()
        formats with f"{v:.3f}" → TypeError crash in production.
        Fix: store sma_fast_p and sma_slow_p as separate float keys.

[BF-2]  'decision_factor' key stored a string 'SMA_TF_ALIGN' in measurements.
        Same Dict[str, float] type violation as BF-1.
        Fix: removed — it added no numeric value; use brain_name for identity.

[BF-3]  RSI NaN not guarded. calc_rsi_series uses ewm() which can return NaN
        during warmup. float(NaN) does not raise — it silently propagates.
        nan < 60 evaluates False in Python → BUY condition silently becomes HOLD.
        Fix: added _safe_float() with explicit math.isnan() check, fallback 50.0.

[BF-4]  signal_age_candles hardcoded to 1 — always wrong per system contract.
        Fix: compute_signal_age() counts bars since fast SMA last crossed slow SMA.

[BF-5]  contra_factors was empty list [] when all 3 TFs agreed (direction != HOLD).
        System contract: contra_factors MUST be populated for BUY/SELL.
        Fix: always populate with falsification conditions for the current signal.

[BF-6]  sma_fast and sma_slow not guarded for NaN. rolling().mean() propagates
        NaN from bad OHLCV rows. nan > nan is False → silent wrong direction.
        Fix: _safe_float() applied to all rolling window results.

[BF-7]  calc_atr called twice for same computation (atr_at_signal + atr_pct).
        Fix: compute once, reuse.

[BF-8]  price_at_signal division unguarded for NaN close.
        Fix: _safe_float() + explicit guard before division.

[BF-9]  minimum bar guard was < 20 for all TFs regardless of sma_slow_p.
        For 1h (sma_slow=20), exactly 20 bars gives only 1 valid SMA value
        and any NaN in close breaks it.
        Fix: guard is now len(df) < sma_slow_p + 5 (TF-adaptive, buffer of 5).

[BF-10] symbol field never set in BrainSignal — always "" in production.
        Fix: pass symbol= from function parameter.

[BF-11] supporting_factors for HOLD direction incorrectly said SELL string.
        Fix: proper three-way conditional for BUY / SELL / HOLD.

[BF-12] regime_suitability could be 'HIGH' on a HOLD signal.
        Fix: HOLD → 'LOW', BUY/SELL use alignment threshold as before.

Returns: BrainSignal (brain_contract.py)
"""
from __future__ import annotations

import math
import pandas as pd
from typing import Callable, Optional

from market_agent.brain.brain_contract import BrainSignal
from market_agent.brain.brain_utils import calc_rsi_series, calc_atr

# ── R:R for trend-following: tight SL = clear invalidation point ─────────────
_RR_T1_MULT = 2.0    # T1 = 2.0 × ATR  →  2.67:1 R:R
_RR_T2_MULT = 3.5    # T2 = 3.5 × ATR
_RR_SL_MULT = 0.75   # SL = 0.75 × ATR

# B8: Higher timeframes carry more weight — 1d bar > 4h bar > 1h bar.
# Derived from Elder's Triple Screen relative weighting hierarchy.
_TF_WEIGHTS: dict = {'1d': 3, '4h': 2, '1h': 1}

# B9: Adaptive SMA periods per timeframe.
# Daily bars span a full day each — SMA20 on daily = 1-month lookback, too slow.
# Shorter periods on 1d prevent over-smoothing.
_TF_SMA: dict = {
    '1h': (5, 20),   # standard intraday
    '4h': (5, 20),   # intraday swing
    '1d': (3, 10),   # daily: 3-day fast, 10-day slow (2 trading weeks)
}


# ── Helper: safe float with explicit NaN guard ────────────────────────────────

def _safe_float(value, fallback: float = float('nan')) -> float:
    """
    [BF-3, BF-6, BF-8] Convert any scalar (float, np.float64, Series element)
    to Python float. Returns fallback if result is NaN or conversion fails.
    Uses explicit math.isnan() — float(NaN) does NOT raise, it propagates silently.
    """
    try:
        result = float(value)
        return fallback if math.isnan(result) else result
    except (TypeError, ValueError):
        return fallback


# ── Helper: compute signal age (bars since last crossover) ───────────────────

def _compute_signal_age(df: pd.DataFrame, fast_p: int, slow_p: int) -> int:
    """
    [BF-4] Count how many bars ago the fast SMA last crossed the slow SMA.

    A crossover occurs at bar i when sign(fast[i] - slow[i]) !=
    sign(fast[i-1] - slow[i-1]). We scan backwards from the last bar.

    Returns:
        int: bars since crossover (0 = happened this bar, 1 = last bar, etc.)
             Returns len(df)-1 (max lookback) if no crossover found.
    """
    if df is None or len(df) < slow_p + 2:
        return 1  # insufficient data — conservative default

    close     = df['Close']
    sma_fast  = close.rolling(fast_p).mean()
    sma_slow  = close.rolling(slow_p).mean()
    diff      = sma_fast - sma_slow

    n = len(diff)
    # Walk backwards from second-to-last bar looking for a sign change
    for bars_ago in range(1, min(n, 50)):  # cap lookback at 50 bars
        idx_curr = n - 1 - (bars_ago - 1)
        idx_prev = n - 1 - bars_ago
        if idx_prev < 0:
            break
        curr_val = _safe_float(diff.iloc[idx_curr], float('nan'))
        prev_val = _safe_float(diff.iloc[idx_prev], float('nan'))
        if math.isnan(curr_val) or math.isnan(prev_val):
            continue
        # Sign change detected = crossover at bars_ago bars ago
        if (curr_val > 0) != (prev_val > 0):
            return bars_ago

    return min(n - 1, 50)  # no crossover found — signal is stale


# ── Main brain function ───────────────────────────────────────────────────────

def multi_timeframe_signal(
    symbol: str,
    primary_hist: pd.DataFrame,
    fetch_fn: Optional[Callable] = None,
) -> BrainSignal:
    """
    Brain 4: Cross-Timeframe Signal Alignment Verifier.

    Rates alignment across 1h + 4h + 1d. Strong agreement = high conviction.
    fetch_fn: callable(symbol, interval, period) → DataFrame  (may be None).
    In backtest, fetch_fn=None → runs on 1H only (single-TF, capped confidence).

    RSI thresholds: 60/40 (not 65/35).
    BUY only if RSI < 60 — entering at RSI 60-65 means momentum already ran.
    SELL only if RSI > 40 — don't short into something that's already oversold.

    R:R: 2.67:1 (T1=2.0×ATR, SL=0.75×ATR) — tight SL for clear trend invalidation.
    """

    # ── Collect timeframes ────────────────────────────────────────────────────
    timeframes = {'1h': primary_hist}
    if fetch_fn is not None:
        for tf, period in [('4h', '10d'), ('1d', '1mo')]:
            try:
                tf_df = fetch_fn(symbol, interval=tf, period=period)
                if tf_df is not None and len(tf_df) >= 20:
                    timeframes[tf] = tf_df
            except Exception:
                pass

    # ── Per-timeframe signal computation ─────────────────────────────────────
    tf_signals: dict = {}
    for tf, df in timeframes.items():
        if df is None:
            continue

        sma_fast_p, sma_slow_p = _TF_SMA.get(tf, (5, 20))

        # [BF-9] Adaptive minimum bar guard based on actual sma_slow_p + buffer
        if len(df) < sma_slow_p + 5:
            continue

        # [BF-6] Guard SMA values for NaN before comparison
        sma_fast = _safe_float(df['Close'].rolling(sma_fast_p).mean().iloc[-1])
        sma_slow = _safe_float(df['Close'].rolling(sma_slow_p).mean().iloc[-1])

        # [BF-3] Guard RSI for NaN — ewm warmup can produce NaN even with sufficient bars
        rsi_raw = calc_rsi_series(df).iloc[-1] if len(df) > 15 else 50.0
        rsi = _safe_float(rsi_raw, 50.0)  # NaN RSI → neutral 50.0

        # All three values must be valid to emit a directional signal
        sma_valid = not (math.isnan(sma_fast) or math.isnan(sma_slow))
        rsi_valid = not math.isnan(rsi)

        if sma_valid and sma_slow > 0 and rsi_valid:
            direction = (
                # RSI gate: 65→60 for BUY, 35→40 for SELL (tightened in v4)
                'BUY'  if sma_fast > sma_slow and rsi < 60 else
                'SELL' if sma_fast < sma_slow and rsi > 40 else
                'HOLD'
            )
            ma_diff_pct = (sma_fast - sma_slow) / sma_slow * 100
        else:
            # Cannot compute direction — treat as HOLD with neutral ma_diff
            direction   = 'HOLD'
            ma_diff_pct = 0.0

        tf_signals[tf] = {
            'direction':    direction,
            'rsi':          rsi,
            'ma_diff_pct':  ma_diff_pct,
            'sma_fast_p':   float(sma_fast_p),  # [BF-1] float, not in measurements
            'sma_slow_p':   float(sma_slow_p),  # [BF-1] float, not in measurements
        }

    # ── Weighted alignment score (B8) ─────────────────────────────────────────
    total_weight = sum(_TF_WEIGHTS.get(tf, 1) for tf in tf_signals)
    buy_weight   = sum(_TF_WEIGHTS.get(tf, 1) for tf, v in tf_signals.items() if v['direction'] == 'BUY')
    sell_weight  = sum(_TF_WEIGHTS.get(tf, 1) for tf, v in tf_signals.items() if v['direction'] == 'SELL')

    directions  = [v['direction'] for v in tf_signals.values()]
    buy_count   = directions.count('BUY')
    sell_count  = directions.count('SELL')
    total_tf    = len(directions)
    alignment   = max(buy_weight, sell_weight) / total_weight if total_weight else 0.0
    direction   = 'BUY' if buy_weight > sell_weight else 'SELL' if sell_weight > buy_weight else 'HOLD'

    # ── Confidence ────────────────────────────────────────────────────────────
    if len(tf_signals) == 1:
        # Only primary timeframe available — cap at honest single-TF confidence
        confidence = min(alignment * 0.85, 0.62)
    else:
        confidence = alignment * 0.85

    # ── Signal age: bars since last SMA crossover on primary (1H) TF ─────────
    # [BF-4] Compute actual age — do not hardcode to 1
    primary_fast_p, primary_slow_p = _TF_SMA.get('1h', (5, 20))
    signal_age = _compute_signal_age(primary_hist, primary_fast_p, primary_slow_p)

    # ── Penalty: stale signals lose confidence ────────────────────────────────
    # A crossover that happened 10+ bars ago in a trend-following brain is less
    # actionable — the trend may be mature. Confidence decays after 5 bars.
    if signal_age > 5:
        staleness_penalty = min((signal_age - 5) * 0.02, 0.15)  # max 15% penalty
        confidence = max(0.0, confidence - staleness_penalty)

    # ── ATR computation (once, reused) ────────────────────────────────────────
    # [BF-7] Compute once — was called twice redundantly
    atr_val         = calc_atr(primary_hist, 14)   # returns 0.0 on failure — already safe
    price_last_raw  = _safe_float(primary_hist['Close'].iloc[-1])
    # [BF-8] Guard price against NaN/zero before division
    price_at_signal = price_last_raw if not math.isnan(price_last_raw) else 0.0
    atr_pct = (
        round(atr_val / price_at_signal * 100, 3)
        if price_at_signal > 0 and not math.isnan(atr_val) and atr_val > 0
        else 0.0
    )

    # ── Summary string for primary_evidence ──────────────────────────────────
    tf_summary = ' | '.join(
        f'{tf}:{v["direction"]}(RSI {v["rsi"]:.0f})' for tf, v in tf_signals.items()
    )

    # ── Supporting factors ────────────────────────────────────────────────────
    # [BF-11] Three-way conditional — was incorrectly SELL string for HOLD
    if direction == 'BUY':
        supporting = [
            f'{buy_count}/{total_tf} TFs bullish (weighted {buy_weight}/{total_weight})',
            f'SMA crossover age: {signal_age} bars',
        ]
    elif direction == 'SELL':
        supporting = [
            f'{sell_count}/{total_tf} TFs bearish (weighted {sell_weight}/{total_weight})',
            f'SMA crossover age: {signal_age} bars',
        ]
    else:
        hold_count = directions.count('HOLD')
        supporting = [
            f'Mixed/neutral TF signals: {buy_count}B/{sell_count}S/{hold_count}H',
        ]

    # ── Contra factors ────────────────────────────────────────────────────────
    # [BF-5] MUST always be populated for BUY/SELL signals.
    # These are FALSIFICATION conditions — what would invalidate the signal.
    contra: list = []
    if total_tf < 3:
        contra.append(f'Only {total_tf} TF available (prefer 3) — confidence capped')
    if direction == 'BUY':
        contra.append('Bearish invalidation: fast SMA crosses back below slow SMA on 1H')
        contra.append('RSI crossing above 60 would indicate momentum exhaustion')
        if total_tf > 1:
            # How many TFs are NOT aligned with the BUY direction?
            non_buy = total_tf - buy_count
            if non_buy > 0:
                contra.append(f'{non_buy} TF(s) NOT confirming BUY — partial alignment')
        if signal_age > 5:
            contra.append(f'Signal is {signal_age} bars old — trend may be mature')
    elif direction == 'SELL':
        contra.append('Bullish invalidation: fast SMA crosses back above slow SMA on 1H')
        contra.append('RSI dropping below 40 would signal oversold — SELL risk rises')
        if total_tf > 1:
            non_sell = total_tf - sell_count
            if non_sell > 0:
                contra.append(f'{non_sell} TF(s) NOT confirming SELL — partial alignment')
        if signal_age > 5:
            contra.append(f'Signal is {signal_age} bars old — trend may be mature')
    else:
        # HOLD — still populate with what would change the picture
        contra.append('No directional signal: SMAs not aligned or RSI in neutral zone')

    # Ensure contra is never empty for actionable signals
    if direction in ('BUY', 'SELL') and not contra:
        contra.append('No specific contra factor identified — exercise caution')

    # ── Regime suitability ────────────────────────────────────────────────────
    # [BF-12] HOLD signals should always be LOW — cannot be HIGH suitability
    if direction == 'HOLD':
        regime_suit = 'LOW'
    elif alignment > 0.7:
        regime_suit = 'HIGH'
    else:
        regime_suit = 'MEDIUM'

    # ── measurements dict — ALL values must be float ─────────────────────────
    # [BF-1] Removed string tf_sma entries ("5/20" etc.) — stored as separate floats
    # [BF-2] Removed string 'decision_factor' = 'SMA_TF_ALIGN' — type violation
    measurements: dict = {}

    # Per-TF ma_diff_pct (floats)
    for tf, v in tf_signals.items():
        measurements[f'{tf}_ma_diff_pct'] = round(float(v['ma_diff_pct']), 4)

    # Per-TF RSI values (floats)
    for tf, v in tf_signals.items():
        measurements[f'{tf}_rsi'] = round(float(v['rsi']), 2)

    # Per-TF SMA periods as individual float values (not a "5/20" string)
    for tf, v in tf_signals.items():
        measurements[f'{tf}_sma_fast_p'] = float(v['sma_fast_p'])
        measurements[f'{tf}_sma_slow_p'] = float(v['sma_slow_p'])

    # Global numeric fields
    measurements.update({
        'price_at_signal':   round(price_at_signal, 6),
        'atr_at_signal':     round(float(atr_val), 6),
        'atr_pct_at_signal': atr_pct,
        'bars_used':         float(len(primary_hist)),
        'buy_weight':        float(buy_weight),    # B8
        'sell_weight':       float(sell_weight),   # B8
        'total_weight':      float(total_weight),  # B8
        'alignment':         round(alignment, 4),
        'signal_age_bars':   float(signal_age),    # [BF-4] actual age stored here too
        'total_tf':          float(total_tf),
    })

    # ── Return BrainSignal ───────────────────────────────────────────────────
    return BrainSignal(
        brain_name='Multi-Timeframe',
        specialization='Cross-Timeframe Signal Alignment Verifier',
        method='SMA (B9 adaptive) + RSI 60/40 gate on 1h/4h/1d | B8 weighted votes',
        direction=direction,
        confidence=confidence,
        signal_strength=alignment,
        signal_age_candles=signal_age,           # [BF-4] computed, not hardcoded
        primary_evidence=f'Weighted alignment={alignment:.0%} across {total_tf} TFs: {tf_summary}',
        supporting_factors=supporting,           # [BF-11] proper three-way
        contra_factors=contra,                   # [BF-5] always populated
        method_confidence=alignment,
        regime_suitability=regime_suit,          # [BF-12] HOLD → LOW
        symbol=symbol,                           # [BF-10] set from parameter
        reliability_flags={
            'low_alignment':      alignment < 0.5,
            'missing_timeframes': total_tf < 3,
            'stale_signal':       signal_age > 10,
        },
        measurements=measurements,               # [BF-1, BF-2] all float values
        rr_t1_mult=_RR_T1_MULT,
        rr_t2_mult=_RR_T2_MULT,
        rr_sl_mult=_RR_SL_MULT,
        recent_accuracy=None,
        regime_accuracy=None,
    )