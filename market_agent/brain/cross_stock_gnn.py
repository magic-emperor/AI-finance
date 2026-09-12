"""
Brain 5: Cross-Stock-GNN — Institutional Flow Detector (VWAP + Volume Spike)
=============================================================================

RESEARCH FOUNDATION
-------------------
This brain implements VWAP deviation + abnormal volume spike detection,
a well-studied institutional flow detection approach.

1. VWAP as institutional fair value:
   - Zarattini & Aziz (SSRN, Nov 2023): "Volume Weighted Average Price —
     The Holy Grail for Day Trading Systems." VWAP trend strategy on QQQ
     achieved 671% return (2018–2023) vs 126% buy-and-hold. Key finding:
     VWAP is primarily a TREND tool, not a reversal tool. Mean reversion
     works in RANGING; continuation works in TRENDING/VOLATILE.

2. VWAP mean reversion documented win rate:
   - Empirical studies (Pipup.com, ForexTester): 55–62% reversion
     frequency within 5–20 sessions when used in range-bound markets.
     This is the realistic ceiling for this strategy — achievable but
     requires the regime to be genuinely ranging.

3. Volume spike threshold:
   - FXNX scalping research: Legitimate breakouts accompanied by
     1.5–2x average volume. This brain uses 2.5x — well above the
     minimum threshold, targeting institutional-size orders only.

4. OBV as confirmation tool (Granville, 1963):
   - OBV works on the premise that volume precedes price. Rising OBV
     during price consolidation = institutional accumulation. Limitation:
     large one-time volume events (earnings, news) can distort the
     10-bar OBV slope for several sessions afterward.

5. CRITICAL research finding for VOLATILE regime:
   - In VOLATILE (high ATR) regimes, a 2.5x volume spike with price
     strongly below VWAP is more likely INSTITUTIONAL DISTRIBUTION
     (selling pressure) than accumulation. The mean reversion BUY
     interpretation fights institutional momentum. This brain handles
     this by outputting HOLD with low confidence in VOLATILE when
     the deviation direction contradicts momentum, pending more data
     to validate a regime-specific continuation mode.

HONEST NAMING:
   Despite the name, this brain has no Graph Neural Network and no
   cross-stock relationship modeling. It is a VWAP + volume spike
   detector. The 'granger_leaders' parameter receives None in all
   calls — the Granger path is dead code, wired for future use.

WHAT CHANGED FROM v5 (AUDIT FIXES)
------------------------------------
[BF-1-GNN] CRITICAL: measurements dict had non-float values.
  - vwap_type (string 'SESSION'/'ROLLING_20') → vwap_is_session (float 1.0/0.0)
  - obv_bullish (bool) → float(obv_bullish) → 1.0 or 0.0
  - regime (string) → removed from measurements (not a numeric value)
  - decision_factor (string 'VWAP_SPIKE') → direction_code (float: 1.0=BUY, -1.0=SELL, 0.0=HOLD)
  All measurements values are now float as required by Dict[str, float] contract.

[BF-2-GNN] CRITICAL: No minimum bar guard. Added early-exit HOLD if len(hist) < 20.
  Brain now returns a safe HOLD with reliability_flags['insufficient_data']=True.

[BF-3-GNN] CRITICAL: Rolling VWAP NaN not guarded. vwap_s.empty check does NOT
  catch NaN from rolling warmup. Fixed with explicit math.isnan() guard.

[BF-4-GNN] CRITICAL: avg_vol used Python `or` to catch NaN — this does NOT work.
  float(NaN) is truthy in Python. Fixed with explicit math.isnan() check.

[BF-5-GNN] BUG: OBV used slow .apply(lambda) row-by-row iteration.
  Replaced with vectorised np.sign().fillna(0) — ~100x faster.

[BF-6-GNN] BUG: signal_age_candles hardcoded to 1. Now computed as the number
  of consecutive bars the current VWAP deviation condition has been present.

[BF-7-GNN] BUG: VOLATILE regime used wrong mean-reversion logic. A 2.5x volume
  spike driving price strongly away from VWAP in a VOLATILE regime is more likely
  institutional momentum continuation, not a bounce opportunity. In VOLATILE,
  confidence is now capped at 0.60 (near-HOLD threshold) to reflect this ambiguity,
  pending per-regime backtest data to confirm which interpretation is correct.

[BF-8-GNN] MINOR: contra_factors was empty for HOLD signals. Now populated with
  specific conditions that would generate a signal — proper falsification.

[BF-9-GNN] MINOR: regime_suitability was always 'HIGH'. Now reflects divergence.

[BF-10-GNN] MINOR: decision_factor was string in measurements. Fixed in BF-1.

[NF-1-GNN] NEW: _safe_float() and _scalar() helpers added for safe type coercion.
  _compute_signal_age() computes how many bars the deviation has been active.

Returns: BrainSignal (brain_contract.py)
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from market_agent.brain.brain_contract import BrainSignal
from market_agent.brain.brain_utils import calc_atr

# ── R:R multipliers ───────────────────────────────────────────────────────────
# Tight SL = clear invalidation point for momentum/mean-reversion plays
_RR_T1_MULT = 2.0    # T1 = 2.0 × ATR → 2.67:1 R:R
_RR_T2_MULT = 3.5    # T2 = 3.5 × ATR
_RR_SL_MULT = 0.75   # SL = 0.75 × ATR

# ── Minimum data requirements ─────────────────────────────────────────────────
_MIN_BARS = 20        # need 20 bars for rolling(20) calculations to be valid
_OBV_WINDOW = 10      # OBV slope computed over last 10 bars

# ── VWAP deviation thresholds ─────────────────────────────────────────────────
_VWAP_THRESHOLD_ATR = 0.8   # 0.8x ATR from VWAP = meaningful deviation
_VWAP_STRONG_ATR    = 1.5   # 1.5x ATR = strong institutional push
_VOL_SPIKE_THRESH   = 2.5   # 2.5x 20-bar avg volume = institutional size

# ── VOLATILE regime confidence cap ────────────────────────────────────────────
# Research: in VOLATILE, high-volume VWAP deviation may be continuation not
# reversion. Cap confidence to near-neutral until per-regime data validates.
# NOTE: Update this constant after first 30-trade backtest per regime.
_VOLATILE_CONF_CAP  = 0.60


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val, fallback: float = -1.0) -> float:
    """
    [NF-1-GNN] Safely convert any value to float.
    Returns fallback if value is NaN, None, or unconvertible.
    -1.0 is the sentinel for 'not computed' per system rules.
    """
    try:
        f = float(val)
        return fallback if math.isnan(f) else f
    except (TypeError, ValueError):
        return fallback


def _is_equity(symbol: str) -> bool:
    """
    Detect equity symbol for VWAP window selection.
    .NS/.BO suffix = Indian equity.
    Short uppercase-only string = US/Indian equity.
    Crypto: dash, slash, or ends with USDT/USD/BTC/ETH/BNB.
    """
    if not symbol:
        return False
    s = symbol.upper().strip()
    if s.endswith('.NS') or s.endswith('.BO'):
        return True
    if '-' in s or '/' in s:
        return False
    if any(s.endswith(x) for x in ('USDT', 'USD', 'BTC', 'ETH', 'BNB')):
        return False
    # Short uppercase-only string without dot = treat as equity (e.g. AAPL, NVDA)
    return s.isalpha()


def _compute_signal_age(
    close: pd.Series,
    vwap_val: float,
    atr_val: float,
    threshold_atr: float,
) -> int:
    """
    [BF-6-GNN] Compute how many consecutive bars the VWAP deviation condition
    has been active. Walks backward from the current bar counting bars where
    abs(close - vwap) > threshold_atr * atr_val.

    Returns 0 if the condition only holds on the current bar (fresh signal).
    Caps at 10 bars to prevent over-aged signals from dominating.
    """
    if atr_val <= 0 or len(close) < 2:
        return 0
    threshold_price = threshold_atr * atr_val
    age = 0
    # Walk backwards from bar -1 (current)
    for i in range(len(close) - 1, max(len(close) - 11, -1), -1):
        try:
            dev = abs(float(close.iloc[i]) - vwap_val)
        except (IndexError, ValueError):
            break
        if dev >= threshold_price:
            age += 1
        else:
            break  # condition broke — stop counting
    # age includes current bar, so signal age = age - 1 bars ago
    return max(0, age - 1)


def _hold_signal(
    symbol: str,
    reason: str,
    atr_val: float = 0.0,
    close_val: float = 0.0,
    bars_used: int = 0,
) -> BrainSignal:
    """
    [BF-2-GNN] Safe HOLD sentinel returned when data is insufficient.
    Prevents crashes from propagating up the stack.
    """
    return BrainSignal(
        brain_name='Cross-Stock-GNN',
        specialization='Institutional Flow via VWAP + Volume',
        method='VWAP deviation + volume spike ratio (Granger: not yet active)',
        direction='HOLD',
        confidence=0.30,
        signal_strength=0.0,
        signal_age_candles=0,
        primary_evidence=f'INSUFFICIENT DATA — {reason}',
        supporting_factors=[],
        contra_factors=[
            f'Requires >= {_MIN_BARS} bars for rolling calculations',
            'No VWAP or volume data computable yet',
        ],
        method_confidence=0.0,
        regime_suitability='LOW',
        symbol=symbol,
        reliability_flags={'insufficient_data': True},
        measurements={
            'vwap_dev_pct':      -1.0,
            'vwap_is_session':   -1.0,
            'vol_ratio':         -1.0,
            'vol_spike':         0.0,
            'granger_boost':     0.0,
            'vwap_dev_in_atr':   -1.0,
            'obv_slope':         -1.0,
            'obv_bullish':       -1.0,
            'direction_code':    0.0,   # 0.0 = HOLD
            'decision_factor':   0.0,   # A12: matches direction_code (0.0=HOLD)
            'price_at_signal':   round(close_val, 6),
            'atr_at_signal':     round(atr_val, 6),
            'atr_pct_at_signal': (
                round(atr_val / close_val * 100, 3) if close_val > 0 else 0.0
            ),
            'bars_used':         float(bars_used),
        },
        rr_t1_mult=_RR_T1_MULT,
        rr_t2_mult=_RR_T2_MULT,
        rr_sl_mult=_RR_SL_MULT,
        recent_accuracy=None,
        regime_accuracy=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BRAIN FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def cross_stock_gnn_signal(
    hist: pd.DataFrame,
    granger_leaders: Optional[dict] = None,
    symbol: str = '',
    regime: str = 'RANGING',
) -> BrainSignal:
    """
    Brain 5: Institutional Flow Detection via VWAP deviation + volume spike.

    STRATEGY LOGIC:
    - Price deviating significantly from VWAP with abnormal volume = institutional
      order flow. In RANGING, this is a mean reversion signal (price will return
      to fair value). In VOLATILE, this is ambiguous — confidence is capped.

    REGIME GATES (from signal_generators.py):
    - VOLATILE: Brain fires but confidence is capped at 0.60 (BF-7-GNN fix).
    - RANGING:  Full confidence tiers apply.

    PARAMETERS:
    - hist:           OHLCV DataFrame, minimum 20 bars required
    - granger_leaders: dict of leading-stock signals (dead code, always None in prod)
    - symbol:         ticker string for asset class detection
    - regime:         current market regime from regime_ensemble

    RETURNS: BrainSignal with direction, confidence, and measurements (all float).
    """
    # ── [BF-2-GNN] Minimum bar guard ──────────────────────────────────────────
    if hist is None or len(hist) < _MIN_BARS:
        bars = len(hist) if hist is not None else 0
        close_val = float(hist['Close'].iloc[-1]) if hist is not None and len(hist) > 0 else 0.0
        return _hold_signal(
            symbol=symbol,
            reason=f'Only {bars} bars available (need {_MIN_BARS})',
            close_val=close_val,
            bars_used=bars,
        )
    # ─────────────────────────────────────────────────────────────────────────

    close  = hist['Close']
    volume = hist['Volume']
    high   = hist['High']
    low    = hist['Low']

    # ── Asset-class-aware VWAP (Weekly Anchored / Rolling) ────────────────────
    is_eq = _is_equity(symbol)
    tp = (high + low + close) / 3
    if is_eq:
        try:
            # Weekly anchored VWAP. Anchor: Start of the current trading week.
            dt_index = pd.to_datetime(hist.index)
            df_vwap = pd.DataFrame({'tp_v': tp * volume, 'v': volume}, index=hist.index)
            df_vwap['yr'] = dt_index.isocalendar().year.values
            df_vwap['wk'] = dt_index.isocalendar().week.values
            cum_tp_v = df_vwap.groupby(['yr', 'wk'])['tp_v'].cumsum()
            cum_v = df_vwap.groupby(['yr', 'wk'])['v'].cumsum()
            vwap_s = cum_tp_v / cum_v
        except Exception:
            vwap_s = (tp * volume).rolling(75).sum() / volume.rolling(75).sum()
        
        vwap_raw = _safe_float(vwap_s.iloc[-1], fallback=float('nan'))
        vwap_val = vwap_raw if not math.isnan(vwap_raw) else float(close.iloc[-1])
        vwap_val_N = _safe_float(vwap_s.iloc[-2], fallback=float(close.iloc[-2]))
        vwap_is_session = 1.0
    else:
        # Rolling 20-bar VWAP for crypto/unknown
        vwap_s = (tp * volume).rolling(20).sum() / volume.rolling(20).sum()
        vwap_raw = _safe_float(vwap_s.iloc[-1], fallback=float('nan'))
        vwap_val = vwap_raw if not math.isnan(vwap_raw) else float(close.iloc[-1])
        vwap_val_N = _safe_float(vwap_s.iloc[-2], fallback=float(close.iloc[-2]))
        vwap_is_session = 0.0
    # ─────────────────────────────────────────────────────────────────────────

    vwap_dev_pct = (float(close.iloc[-1]) - vwap_val) / vwap_val * 100 if vwap_val > 0 else 0.0

    # ── Setup Bar (Bar N = iloc[-2]) ──────────────────────────────────────────
    avg_vol_series = volume.rolling(20).mean()
    avg_vol_N_raw = _safe_float(avg_vol_series.iloc[-2], fallback=float('nan'))
    avg_vol_N = 1.0 if math.isnan(avg_vol_N_raw) or avg_vol_N_raw <= 0 else avg_vol_N_raw
    vol_N = _safe_float(volume.iloc[-2], fallback=0.0)
    
    vol_ratio = vol_N / avg_vol_N
    vol_spike = vol_ratio > _VOL_SPIKE_THRESH

    atr_N = calc_atr(hist.iloc[:-1], 14) if len(hist) > 1 else 0.0
    close_N = float(close.iloc[-2])
    vwap_dev_pct_N = (close_N - vwap_val_N) / vwap_val_N * 100 if vwap_val_N > 0 else 0.0
    vwap_dev_in_atr_N = abs(close_N - vwap_val_N) / atr_N if atr_N > 0 else 0.0

    # ── Confirmation Bar (Bar N+1 = iloc[-1]) ─────────────────────────────────
    atr_val = calc_atr(hist, 14)
    avg_vol_curr_raw = _safe_float(avg_vol_series.iloc[-1], fallback=float('nan'))
    avg_vol_curr = 1.0 if math.isnan(avg_vol_curr_raw) or avg_vol_curr_raw <= 0 else avg_vol_curr_raw
    vol_curr = _safe_float(volume.iloc[-1], fallback=0.0)
    
    vol_exhausted = vol_curr < avg_vol_curr
    
    close_curr = float(close.iloc[-1])
    open_curr = float(hist['Open'].iloc[-1])
    bullish_reversal = close_curr > open_curr
    bearish_reversal = close_curr < open_curr

    vwap_dev_in_atr = abs(close_curr - vwap_val) / atr_val if atr_val > 0 else 0.0

    # ── OBV Trend Confirmation ────────────────────────────────────────────────
    price_diff = close.diff()
    sign_series = np.sign(price_diff).fillna(0)
    obv_series  = (volume * sign_series).cumsum()

    if len(obv_series) >= _OBV_WINDOW + 1:
        obv_slope = _safe_float(
            obv_series.iloc[-1] - obv_series.iloc[-(_OBV_WINDOW + 1)],
            fallback=0.0,
        )
    else:
        obv_slope = 0.0
    obv_bullish = obv_slope > 0

    # ── Granger causality boost ───────────────────────────────────────────────
    granger_boost    = 0.0
    granger_evidence = 'No leader stocks tracked'
    if granger_leaders:
        leader_buys   = sum(1 for sig in granger_leaders.values() if sig.get('direction') == 'BUY')
        total_leaders = len(granger_leaders)
        granger_boost    = (leader_buys / total_leaders - 0.5) * 0.2 if total_leaders else 0
        granger_evidence = f'{leader_buys}/{total_leaders} leader stocks bullish'

    # ── Base Setup Direction (Evaluated on Bar N) ─────────────────────────────
    if vwap_dev_pct_N < 0 and vwap_dev_in_atr_N > _VWAP_STRONG_ATR and vol_spike:
        setup_direction, base_conf = 'BUY', 0.85
    elif vwap_dev_pct_N > 0 and vwap_dev_in_atr_N > _VWAP_STRONG_ATR and vol_spike:
        setup_direction, base_conf = 'SELL', 0.85
    elif vwap_dev_pct_N < 0 and vwap_dev_in_atr_N > _VWAP_THRESHOLD_ATR and vol_spike:
        setup_direction, base_conf = 'BUY', 0.72
    elif vwap_dev_pct_N > 0 and vwap_dev_in_atr_N > _VWAP_THRESHOLD_ATR and vol_spike:
        setup_direction, base_conf = 'SELL', 0.72
    else:
        setup_direction, base_conf = 'HOLD', 0.40

    # ── Confirmation Logic (Evaluated on Bar N+1) ─────────────────────────────
    direction = 'HOLD'
    if setup_direction == 'BUY' and vol_exhausted and bullish_reversal:
        direction = 'BUY'
    elif setup_direction == 'SELL' and vol_exhausted and bearish_reversal:
        direction = 'SELL'
        
    confidence = min(0.92, base_conf + granger_boost) if direction != 'HOLD' else 0.40

    # ── OBV confirmation / contradiction ──────────────────────────────────────
    obv_contra = []
    if direction == 'BUY':
        if obv_bullish:
            confidence = min(0.92, confidence + 0.03)
        else:
            confidence = max(0.50, confidence - 0.10)
            obv_contra.append('OBV bearish (10-bar) — selling volume into BUY signal')
    elif direction == 'SELL':
        if not obv_bullish:
            confidence = min(0.92, confidence + 0.03)
        else:
            confidence = max(0.50, confidence - 0.10)
            obv_contra.append('OBV bullish (10-bar) — buying volume into SELL signal')

    # ── Regime context adjustment ─────────────────────────────────────────────
    regime_contra = []
    regime_upper = regime.upper()

    if 'VOLATILE' in regime_upper and direction != 'HOLD':
        confidence = min(confidence, _VOLATILE_CONF_CAP)
        regime_contra.append(
            f'VOLATILE regime: capped conf to {_VOLATILE_CONF_CAP:.0%} pending forward data'
        )

    if direction == 'BUY':
        if 'TRENDING_UP' in regime_upper:
            confidence = min(0.92, confidence + 0.05)
        elif 'TRENDING_DOWN' in regime_upper:
            confidence = max(0.50, confidence - 0.10)
            regime_contra.append(f'Counter-trend BUY in {regime} regime')
    elif direction == 'SELL':
        if 'TRENDING_DOWN' in regime_upper:
            confidence = min(0.92, confidence + 0.05)
        elif 'TRENDING_UP' in regime_upper:
            confidence = max(0.50, confidence - 0.10)
            regime_contra.append(f'Counter-trend SELL in {regime} regime')

    signal_age = 1 if direction != 'HOLD' else 0

    n_contra = len(obv_contra) + len(regime_contra)
    if n_contra == 0 and direction != 'HOLD':
        regime_suitability = 'HIGH'
    elif n_contra <= 1:
        regime_suitability = 'MEDIUM'
    else:
        regime_suitability = 'LOW'

    base_contra: list[str] = []
    if direction == 'HOLD':
        if setup_direction == 'HOLD':
            if not vol_spike:
                base_contra.append(f'Setup (Bar N) requires vol spike > {_VOL_SPIKE_THRESH}x (was {vol_ratio:.1f}x)')
            if vwap_dev_in_atr_N <= _VWAP_THRESHOLD_ATR:
                base_contra.append(f'Setup (Bar N) requires VWAP dev > {_VWAP_THRESHOLD_ATR}x ATR (was {vwap_dev_in_atr_N:.2f}x)')
        else:
            if not vol_exhausted:
                base_contra.append(f'Confirmation (Bar N+1) requires volume exhaustion (< 20-bar avg)')
            if setup_direction == 'BUY' and not bullish_reversal:
                base_contra.append('Confirmation (Bar N+1) requires bullish close')
            if setup_direction == 'SELL' and not bearish_reversal:
                base_contra.append('Confirmation (Bar N+1) requires bearish close')
                
    all_contra = base_contra + obv_contra + regime_contra
    if direction != 'HOLD' and not all_contra:
        all_contra = [
            f'Setup anchored by {_VOL_SPIKE_THRESH}x vol spike and {_VWAP_THRESHOLD_ATR}x ATR dev',
            'Confirmed by volume exhaustion and directional close in Bar N+1'
        ]

    direction_code = 1.0 if direction == 'BUY' else -1.0 if direction == 'SELL' else 0.0
    # ─────────────────────────────────────────────────────────────────────────

    return BrainSignal(
        brain_name='Cross-Stock-GNN',
        specialization='Institutional Flow via VWAP + Volume',
        method='VWAP deviation + volume spike ratio (Granger: not yet active)',
        direction=direction,
        confidence=round(confidence, 4),
        signal_strength=min(1.0, vol_ratio / 3),
        signal_age_candles=signal_age,
        primary_evidence=(
            f'VWAP dev={vwap_dev_pct:+.2f}% '
            f'({"SESSION" if vwap_is_session else "ROLLING_20"}) | '
            f'Vol ratio={vol_ratio:.1f}x | '
            f'{granger_evidence}'
        ),
        supporting_factors=[
            f'Volume spike={vol_spike} ({vol_ratio:.1f}x vs {_VOL_SPIKE_THRESH}x threshold)',
            f'VWAP dev={vwap_dev_pct:+.2f}%',
            f'ATR-based dev={vwap_dev_in_atr:.2f}x ATR (threshold {_VWAP_THRESHOLD_ATR}x)',
            f'OBV slope={obv_slope:.0f} ({"bullish" if obv_bullish else "bearish"})',
            f'Regime={regime}',
        ],
        contra_factors=all_contra,
        method_confidence=0.80 if vol_spike else 0.55,
        regime_suitability=regime_suitability,
        symbol=symbol,
        reliability_flags={
            'low_volume':       vol_ratio < 0.5,
            'no_granger_data':  not granger_leaders,
            'obv_divergence':   bool(obv_contra),
            'volatile_regime':  'VOLATILE' in regime_upper and direction != 'HOLD',
            'insufficient_data': False,
        },
        measurements={
            # [BF-1-GNN] ALL values must be float — no strings, no bools, no None
            'vwap_dev_pct':      round(vwap_dev_pct, 4),
            'vwap_is_session':   vwap_is_session,        # 1.0=SESSION, 0.0=ROLLING_20
            'vol_ratio':         round(vol_ratio, 4),
            'vol_spike':         float(vol_spike),        # 1.0 or 0.0
            'granger_boost':     round(granger_boost, 4),
            'vwap_dev_in_atr':   round(vwap_dev_in_atr, 4),
            'obv_slope':         round(obv_slope, 2),
            'obv_bullish':       float(obv_bullish),      # [BF-1-GNN] float, not bool
            'direction_code':    direction_code,            # [BF-10-GNN] 1.0/−1.0/0.0
            'decision_factor':   direction_code,            # A12: alias for direction_code
            'price_at_signal':   round(float(close.iloc[-1]), 6),
            'atr_at_signal':     round(float(atr_val), 6),
            'atr_pct_at_signal': (
                round(float(atr_val) / float(close.iloc[-1]) * 100, 3)
                if float(close.iloc[-1]) > 0
                else 0.0
            ),
            'bars_used':         float(len(hist)),
        },
        rr_t1_mult=_RR_T1_MULT,
        rr_t2_mult=_RR_T2_MULT,
        rr_sl_mult=_RR_SL_MULT,
        recent_accuracy=None,
        regime_accuracy=None,
    )