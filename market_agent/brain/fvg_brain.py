"""
═══════════════════════════════════════════════════════════════════════
Brain: Fair-Value-Gap (FVG) v1 — Institutional Imbalance Detector
═══════════════════════════════════════════════════════════════════════

PATTERN:
  BULLISH FVG: candle[−2].high < candle[0].low  → gap left below current price
               Price pulls back INTO gap → BUY (institutions fill the gap)

  BEARISH FVG: candle[−2].low  > candle[0].high → gap left above current price
               Price pulls back INTO gap → SELL (institutions fill the gap)

WHY IT WORKS:
  When institutions execute large orders they leave an imbalance zone —
  a price range no one traded through (the gap). Smart-money theory holds
  that price almost always returns to fill these zones before continuing.
  A pullback INTO an unfilled FVG is a high-probability entry.

SUITABLE ASSETS:    All (equities, crypto, FX, commodities)
SUITABLE TFs:       1H (primary), 4H, D1
CYCLE TIME:         Once per candle close (same as Liquidity Sweep)
DATA REQUIRED:      Minimum 50 bars

STATUS:             v1 — paper trade only, parameters NOT locked
                    Lock parameters after n≥50 live trades per regime.

═══════════════════════════════════════════════════════════════════════
DESIGN PHILOSOPHY
═══════════════════════════════════════════════════════════════════════

This brain has TWO modes of operation:

  MODE A — STANDALONE BRAIN:
    fvg_signal(hist, regime, symbol, timeframe)
    Returns a BrainSignal. Fires when price pulls back into a valid FVG.
    Can be registered as an independent brain alongside Liquidity Sweep.

  MODE B — CONFLUENCE BOOST for Liquidity Sweep:
    fvg_confluence_boost(hist, swept_level, direction)
    Returns a float confidence delta (+0.05 to +0.10).
    Call this INSIDE liquidity_sweep_signal() confidence scoring block.
    If the swept level coincides with an FVG zone → signal is stronger.

Both modes share the same detection logic (_find_fvgs).
The separation is intentional: do NOT couple Mode B to Mode A's gates.
Mode B runs even when Mode A would HOLD (the FVG may be valid as
confluence even if standalone conditions are not met).

═══════════════════════════════════════════════════════════════════════
FVG TAXONOMY (v1)
═══════════════════════════════════════════════════════════════════════

  STRONG FVG:  Gap ≥ 1.0× ATR. High institutional conviction.
  MEDIUM FVG:  Gap ≥ 0.5× ATR.
  WEAK FVG:    Gap ≥ _MIN_GAP_ATR (default 0.25× ATR). Min valid gap.
  STALE FVG:   Age > _MAX_FVG_AGE_BARS. Price had many chances to fill
               it and didn't — institutions may have abandoned the zone.
  PARTIAL:     Price has entered the FVG but not filled it completely.
               Still valid — remaining gap is the active zone.
  FILLED:      Price has fully closed through the FVG. Zone is exhausted.

═══════════════════════════════════════════════════════════════════════
V1 PARAMETER RATIONALE
═══════════════════════════════════════════════════════════════════════

_MIN_GAP_ATR = 0.25:
  Below 0.25× ATR the gap is within normal spread noise on most assets.
  At 0.25× we require the gap to be a genuine imbalance, not tick noise.
  Evidence: not yet locked (n < 50). Treat as starting hypothesis.

_MAX_FVG_AGE_BARS = 50:
  On H1, 50 bars = ~6.25 trading days. Beyond that, the gap has been
  "visible" to market participants for over a week — if institutions
  wanted to fill it they would have. Older gaps have weaker magnetism.
  On D1, 50 bars = ~50 trading days. Appropriate — daily FVGs persist.
  Adjust by timeframe if live data shows different fill rates.

_PULLBACK_TOLERANCE_PCT = 0.003:
  Price must be WITHIN 0.3% of the FVG zone to count as a pullback.
  Too tight = miss valid entries. Too loose = false entries.
  Matches _LEVEL_TOLERANCE_PCT in liquidity_sweep.py (0.003) for
  consistency across the system.

_PARTIAL_FILL_THRESHOLD = 0.50:
  An FVG is "partially filled" if price has closed through >50% of
  the gap range. Below this threshold the gap is still mostly open.
  Above it, remaining magnetism is weaker — confidence penalty applied.

═══════════════════════════════════════════════════════════════════════
INTEGRATION WITH LIQUIDITY SWEEP
═══════════════════════════════════════════════════════════════════════

In liquidity_sweep.py confidence scoring block, after the touch count
section, add:

    # ── FVG confluence boost ──────────────────────────────────────
    from market_agent.brain.fvg_brain import fvg_confluence_boost
    fvg_boost = fvg_confluence_boost(hist, sweep['swept_level'], direction, atr)
    if fvg_boost > 0:
        cs += fvg_boost
        confirmations.append(f'FVG confluence (+{fvg_boost:.0%})')
    elif fvg_boost < 0:
        contra.append('Sweep outside any FVG zone')

This is ADDITIVE — it does not replace any existing gate.
═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Tuple
import structlog

from market_agent.brain.brain_contract import BrainSignal
from market_agent.brain.brain_utils import calc_atr, calc_rsi_series

logger = structlog.get_logger("fvg_brain")


# ═══════════════════════════════════════════════════════════════
# TUNEABLE CONSTANTS — v1 starting values (NOT yet locked)
# Lock after n≥50 live trades per regime with grid validation.
# ═══════════════════════════════════════════════════════════════

# Minimum gap size to qualify as a valid FVG (in ATR multiples)
# Below this = spread noise, not institutional imbalance
_MIN_GAP_ATR              = 0.25

# FVG tier thresholds (ATR multiples)
_STRONG_GAP_ATR           = 1.00   # Strong: ≥ 1.0× ATR
_MEDIUM_GAP_ATR           = 0.50   # Medium: ≥ 0.5× ATR

# Maximum age of a valid FVG in bars
# Older FVGs have weaker fill magnetism — partially stale
_MAX_FVG_AGE_BARS         = 50
_STALE_FVG_AGE_BARS       = 30     # Age where confidence starts decaying

# How close price must be to FVG zone to count as "pulling back into it"
# 0.003 = 0.3%, matching liquidity_sweep.py tolerance
_PULLBACK_TOLERANCE_PCT   = 0.003

# Partial fill: if price has already closed through this fraction of
# the gap, remaining magnetism is weaker
_PARTIAL_FILL_THRESHOLD   = 0.50

# How many bars back to scan for FVGs
_FVG_LOOKBACK_BARS        = 100

# Minimum data requirement
_MIN_HIST_BARS            = 50

# Confidence scoring
_BASE_CONFIDENCE          = 0.62   # v1 starting base (lower than LS — unproven)
_MIN_CONFIDENCE_GATE      = 0.65   # Must clear this to fire a standalone signal

# R:R defaults (conservative until live data validates)
_RR_T1_STRONG             = 2.5
_RR_T1_MEDIUM             = 2.0
_RR_T1_WEAK               = 1.8
_RR_T2_MULT               = 1.5    # T2 = T1 × this multiplier
_RR_SL_MULT               = 1.0

# RSI gate: same philosophy as Liquidity Sweep
_RSI_BLOCK_LOW            = 30.0
_RSI_BLOCK_HIGH           = 70.0

# Regime gates: FVGs work in mean-reverting conditions
# CHAOS = no clean structure. Block.
# TRENDING: FVGs in trend direction = continuation (allowed)
#           FVGs counter-trend = fade (blocked in v1, needs evidence)
_BLOCKED_REGIMES          = {'CHAOS'}

# Confluence boost values (Mode B — used by Liquidity Sweep)
_CONFLUENCE_STRONG_BOOST  = 0.10   # Swept level inside a STRONG unfilled FVG
_CONFLUENCE_MEDIUM_BOOST  = 0.07   # Swept level inside a MEDIUM unfilled FVG
_CONFLUENCE_WEAK_BOOST    = 0.05   # Swept level inside a WEAK unfilled FVG
_CONFLUENCE_PARTIAL_BOOST = 0.03   # Swept level inside a PARTIALLY filled FVG


# ═══════════════════════════════════════════════════════════════
# FVG DATA STRUCTURE
# ═══════════════════════════════════════════════════════════════

class FVG:
    """
    A single Fair Value Gap instance.

    Attributes:
        kind        : 'BULLISH' or 'BEARISH'
        top         : upper edge of the gap
        bottom      : lower edge of the gap
        midpoint    : (top + bottom) / 2
        gap_size    : top - bottom (always positive)
        gap_atr     : gap_size / atr at detection
        age_bars    : bars since the FVG formed (0 = current bar)
        tier        : 'STRONG' | 'MEDIUM' | 'WEAK'
        partially_filled : True if price has partially closed into gap
        fill_pct    : fraction of gap already filled (0.0 = untouched)
        bar_index   : index in hist where middle candle of pattern occurred
    """
    __slots__ = (
        'kind', 'top', 'bottom', 'midpoint', 'gap_size', 'gap_atr',
        'age_bars', 'tier', 'partially_filled', 'fill_pct', 'bar_index',
    )

    def __init__(
        self,
        kind: str,
        top: float,
        bottom: float,
        gap_atr: float,
        age_bars: int,
        bar_index: int,
        fill_pct: float = 0.0,
    ):
        self.kind             = kind
        self.top              = round(top, 8)
        self.bottom           = round(bottom, 8)
        self.midpoint         = round((top + bottom) / 2, 8)
        self.gap_size         = round(top - bottom, 8)
        self.gap_atr          = round(gap_atr, 3)
        self.age_bars         = age_bars
        self.bar_index        = bar_index
        self.fill_pct         = round(fill_pct, 3)
        self.partially_filled = fill_pct > _PARTIAL_FILL_THRESHOLD

        if gap_atr >= _STRONG_GAP_ATR:
            self.tier = 'STRONG'
        elif gap_atr >= _MEDIUM_GAP_ATR:
            self.tier = 'MEDIUM'
        else:
            self.tier = 'WEAK'

    def __repr__(self) -> str:
        return (
            f"FVG({self.kind} {self.tier} "
            f"[{self.bottom:.4g}–{self.top:.4g}] "
            f"gap={self.gap_atr:.2f}×ATR age={self.age_bars}b "
            f"fill={self.fill_pct:.0%})"
        )

    def to_dict(self) -> Dict:
        return {
            'fvg_kind':          self.kind,
            'fvg_top':           self.top,
            'fvg_bottom':        self.bottom,
            'fvg_midpoint':      self.midpoint,
            'fvg_gap_size':      self.gap_size,
            'fvg_gap_atr':       self.gap_atr,
            'fvg_age_bars':      self.age_bars,
            'fvg_tier':          self.tier,
            'fvg_fill_pct':      self.fill_pct,
            'fvg_partial':       self.partially_filled,
        }


# ═══════════════════════════════════════════════════════════════
# CORE DETECTION ENGINE
# ═══════════════════════════════════════════════════════════════

def _find_fvgs(
    hist:         pd.DataFrame,
    atr:          float,
    lookback:     int = _FVG_LOOKBACK_BARS,
    max_age:      int = _MAX_FVG_AGE_BARS,
    min_gap_atr:  float = _MIN_GAP_ATR,
) -> List[FVG]:
    """
    Scan hist for unfilled (or partially filled) Fair Value Gaps.

    A 3-candle FVG pattern:
      BULLISH: candle[i-1].high < candle[i+1].low
               → gap = (candle[i-1].high, candle[i+1].low)
               → price has left an unfilled zone BELOW current price

      BEARISH: candle[i-1].low  > candle[i+1].high
               → gap = (candle[i+1].high, candle[i-1].low)
               → price has left an unfilled zone ABOVE current price

    Then for each detected gap, measure how much of it has been filled
    by subsequent candle wicks (price action after the pattern).

    Returns: List[FVG] sorted by age (most recent first), unfilled only.
    """
    if atr <= 0 or len(hist) < 5:
        return []

    n    = len(hist)
    df   = hist.tail(lookback + 3).reset_index(drop=True)
    ndf  = len(df)
    fvgs: List[FVG] = []

    for i in range(1, ndf - 1):
        c_prev = df.iloc[i - 1]   # candle before the impulse
        c_mid  = df.iloc[i]       # impulse candle (large move)
        c_next = df.iloc[i + 1]   # candle after impulse

        prev_high = float(c_prev['High'])
        prev_low  = float(c_prev['Low'])
        next_high = float(c_next['High'])
        next_low  = float(c_next['Low'])

        age = ndf - 1 - (i + 1)   # bars since c_next closed
        if age > max_age:
            continue

        # ── BULLISH FVG: gap between prev candle top and next candle bottom ──
        if prev_high < next_low:
            gap_bottom = prev_high
            gap_top    = next_low
            gap_size   = gap_top - gap_bottom
            gap_atr    = gap_size / atr

            if gap_atr < min_gap_atr:
                continue

            # Measure how much of this gap has been filled by subsequent candles
            fill_pct = _measure_fill(df, i + 1, gap_bottom, gap_top, direction='BULLISH')
            if fill_pct >= 1.0:
                continue   # Fully filled — no longer a valid FVG

            fvgs.append(FVG(
                kind='BULLISH',
                top=gap_top,
                bottom=gap_bottom,
                gap_atr=gap_atr,
                age_bars=age,
                bar_index=i,
                fill_pct=fill_pct,
            ))

        # ── BEARISH FVG: gap between prev candle bottom and next candle top ──
        elif prev_low > next_high:
            gap_top    = prev_low
            gap_bottom = next_high
            gap_size   = gap_top - gap_bottom
            gap_atr    = gap_size / atr

            if gap_atr < min_gap_atr:
                continue

            # Measure fill
            fill_pct = _measure_fill(df, i + 1, gap_bottom, gap_top, direction='BEARISH')
            if fill_pct >= 1.0:
                continue   # Fully filled

            fvgs.append(FVG(
                kind='BEARISH',
                top=gap_top,
                bottom=gap_bottom,
                gap_atr=gap_atr,
                age_bars=age,
                bar_index=i,
                fill_pct=fill_pct,
            ))

    # Sort: most recent first, then strongest gap
    fvgs.sort(key=lambda f: (f.age_bars, -f.gap_atr))
    return fvgs


def _measure_fill(
    df:        pd.DataFrame,
    from_bar:  int,
    gap_bot:   float,
    gap_top:   float,
    direction: str,
) -> float:
    """
    Measure what fraction of the FVG has been filled by candles after it formed.
    Uses wick penetration (Low/High) — if a wick enters the gap, it partially fills.
    Returns 0.0 (untouched) → 1.0 (fully filled).
    """
    gap_size = gap_top - gap_bot
    if gap_size <= 0:
        return 1.0

    subsequent = df.iloc[from_bar + 1:]
    if subsequent.empty:
        return 0.0

    if direction == 'BULLISH':
        # Price pulls back DOWN into the gap (lows enter from above)
        deepest_low = float(subsequent['Low'].min())
        if deepest_low >= gap_top:
            return 0.0   # Never touched
        if deepest_low <= gap_bot:
            return 1.0   # Fully penetrated
        penetration = gap_top - deepest_low
        return min(1.0, penetration / gap_size)

    else:  # BEARISH
        # Price pulls back UP into the gap (highs enter from below)
        highest_high = float(subsequent['High'].max())
        if highest_high <= gap_bot:
            return 0.0
        if highest_high >= gap_top:
            return 1.0
        penetration = highest_high - gap_bot
        return min(1.0, penetration / gap_size)


def _price_in_fvg(
    price:     float,
    fvg:       FVG,
    tolerance: float = 0.0,
) -> bool:
    """True if price is inside (or within tolerance of) the FVG zone."""
    return (fvg.bottom - tolerance) <= price <= (fvg.top + tolerance)


def _find_fvg_for_price(
    price:    float,
    fvgs:     List[FVG],
    kind:     str,
    atr:      float,
) -> Optional[FVG]:
    """
    Find the best FVG of the given kind that contains the given price.
    'Best' = most recent + strongest tier.
    """
    tol = price * _PULLBACK_TOLERANCE_PCT
    matching = [
        f for f in fvgs
        if f.kind == kind and _price_in_fvg(price, f, tolerance=tol)
    ]
    if not matching:
        return None
    # Prefer most recent, then strongest gap
    return min(matching, key=lambda f: (f.age_bars, -f.gap_atr))


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE SCORING HELPERS
# ═══════════════════════════════════════════════════════════════

def _fvg_confidence_score(fvg: FVG) -> Tuple[float, List[str], List[str]]:
    """
    Score a matched FVG. Returns (delta_conf, confirmations, contras).
    Called by both standalone brain and confluence boost.
    """
    cs = 0.0
    confirmations: List[str] = []
    contra:        List[str] = []

    # Tier bonus
    if fvg.tier == 'STRONG':
        cs += 0.10; confirmations.append(f'Strong FVG ({fvg.gap_atr:.2f}×ATR)')
    elif fvg.tier == 'MEDIUM':
        cs += 0.06; confirmations.append(f'Medium FVG ({fvg.gap_atr:.2f}×ATR)')
    else:
        cs += 0.03; confirmations.append(f'Weak FVG ({fvg.gap_atr:.2f}×ATR)')

    # Freshness bonus (most recent FVGs have strongest magnetism)
    if fvg.age_bars <= 5:
        cs += 0.05; confirmations.append(f'Fresh FVG ({fvg.age_bars}b old)')
    elif fvg.age_bars <= 15:
        cs += 0.02; confirmations.append(f'Recent FVG ({fvg.age_bars}b old)')
    elif fvg.age_bars > _STALE_FVG_AGE_BARS:
        cs -= 0.04; contra.append(f'Stale FVG ({fvg.age_bars}b old)')

    # Partial fill penalty
    if fvg.partially_filled:
        cs -= 0.03; contra.append(f'Partial fill ({fvg.fill_pct:.0%} used)')
    elif fvg.fill_pct > 0:
        contra.append(f'Slight fill ({fvg.fill_pct:.0%})')

    return cs, confirmations, contra


# ═══════════════════════════════════════════════════════════════
# MODE B — CONFLUENCE BOOST (called from Liquidity Sweep)
# ═══════════════════════════════════════════════════════════════

def fvg_confluence_boost(
    hist:          pd.DataFrame,
    swept_level:   float,
    direction:     str,
    atr:           float,
) -> float:
    """
    MODE B: Check if a liquidity sweep's swept_level coincides with an FVG.
    Returns a confidence delta to ADD to the Liquidity Sweep confidence score.

    Usage in liquidity_sweep_signal() confidence block:
        from market_agent.brain.fvg_brain import fvg_confluence_boost
        fvg_boost = fvg_confluence_boost(hist, sweep['swept_level'], direction, atr)
        if fvg_boost > 0:
            cs += fvg_boost
            confirmations.append(f'FVG confluence (+{fvg_boost:.0%})')

    Args:
        hist:        same OHLCV DataFrame passed to liquidity_sweep_signal
        swept_level: the swing level that was swept (from sweep dict)
        direction:   'BUY' or 'SELL' (from liquidity sweep detection)
        atr:         ATR value already computed by the sweep brain

    Returns:
        float: confidence boost (positive) or 0.0 (no FVG found)
               Negative values not returned — absence of FVG is neutral,
               not a penalty. Only its PRESENCE is a boost.
    """
    if atr <= 0 or len(hist) < _MIN_HIST_BARS:
        return 0.0

    try:
        fvgs = _find_fvgs(hist, atr)
        if not fvgs:
            return 0.0

        # BUY sweep: swept a low → look for BULLISH FVG at or near that level
        # SELL sweep: swept a high → look for BEARISH FVG at or near that level
        fvg_kind = 'BULLISH' if direction == 'BUY' else 'BEARISH'
        matched  = _find_fvg_for_price(swept_level, fvgs, fvg_kind, atr)

        if matched is None:
            return 0.0

        # Map tier → boost value
        if matched.tier == 'STRONG' and not matched.partially_filled:
            boost = _CONFLUENCE_STRONG_BOOST
        elif matched.tier == 'MEDIUM' and not matched.partially_filled:
            boost = _CONFLUENCE_MEDIUM_BOOST
        elif matched.partially_filled:
            boost = _CONFLUENCE_PARTIAL_BOOST
        else:
            boost = _CONFLUENCE_WEAK_BOOST

        logger.debug(
            "fvg_confluence_found",
            direction=direction,
            swept_level=swept_level,
            fvg=str(matched),
            boost=boost,
        )
        return boost

    except Exception as e:
        logger.warning("fvg_confluence_boost_error", error=str(e)[:80])
        return 0.0


# ═══════════════════════════════════════════════════════════════
# MODE A — STANDALONE BRAIN SIGNAL
# ═══════════════════════════════════════════════════════════════

def fvg_signal(
    hist:      pd.DataFrame,
    regime:    str  = 'VOLATILE',
    symbol:    str  = '',
    timeframe: str  = '1h',
) -> BrainSignal:
    """
    MODE A: Fair Value Gap standalone brain.

    Fires when:
      1. A valid unfilled FVG exists in the lookback window.
      2. Current price has pulled back INTO the FVG zone.
      3. RSI is not at an extreme (same gate as Liquidity Sweep).
      4. Regime is not CHAOS.
      5. Final confidence >= _MIN_CONFIDENCE_GATE (0.65).

    The signal direction matches the FVG kind:
      BULLISH FVG + price in gap → BUY (expect price to continue up after fill)
      BEARISH FVG + price in gap → SELL (expect price to continue down after fill)

    Returns: BrainSignal (direction=BUY|SELL|HOLD)
    """
    _base = dict(
        brain_name='FVG',
        specialization='Fair Value Gap Imbalance Detector',
        method='3-candle FVG detection | pullback entry | unfilled gap fill',
        rr_t1_mult=_RR_T1_MEDIUM,
        rr_t2_mult=_RR_T1_MEDIUM * _RR_T2_MULT,
        rr_sl_mult=_RR_SL_MULT,
    )

    price = float(hist['Close'].iloc[-1]) if len(hist) > 0 else 0.0

    def _hold(reason: str, conf: float = 0.30, df_key: str = 'GATE_HOLD',
              meas: dict = None) -> BrainSignal:
        m = {
            'decision_factor':  df_key,
            'price_at_signal':  round(price, 6),
            'bars_used':        len(hist),
        }
        if meas:
            m.update(meas)
        return BrainSignal(
            **_base,
            direction='HOLD', confidence=conf, signal_strength=0.0,
            signal_age_candles=0, primary_evidence=reason,
            supporting_factors=[], contra_factors=[],
            method_confidence=0.0, regime_suitability='LOW',
            measurements=m,
        )

    # ── Gate 1: Data sufficiency ──────────────────────────────────────────────
    if len(hist) < _MIN_HIST_BARS:
        return _hold(f'Insufficient data ({len(hist)}<{_MIN_HIST_BARS})', 0.25, 'GATE_DATA')

    # ── Gate 2: Regime ────────────────────────────────────────────────────────
    if regime in _BLOCKED_REGIMES:
        return _hold(f'{regime} blocked — no clean structure for FVGs', 0.25, f'GATE_{regime}')

    # ── Compute indicators ────────────────────────────────────────────────────
    atr = calc_atr(hist, period=14)
    if atr <= 0:
        return _hold('ATR=0', 0.25, 'GATE_ZERO_ATR')

    atr_pct    = atr / price if price > 0 else 0.0
    rsi_series = calc_rsi_series(hist, period=14)
    rsi_val    = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0
    if pd.isna(rsi_val):
        rsi_val = 50.0

    # ── Gate 3: RSI extreme ───────────────────────────────────────────────────
    if rsi_val < _RSI_BLOCK_LOW or rsi_val > _RSI_BLOCK_HIGH:
        return _hold(
            f'RSI extreme ({rsi_val:.1f}) — exhaustion, not pullback',
            0.35, 'GATE_RSI_EXTREME',
            {'rsi': round(rsi_val, 1)},
        )

    # ── FVG detection ─────────────────────────────────────────────────────────
    fvgs = _find_fvgs(hist, atr)
    if not fvgs:
        return _hold(
            'No valid unfilled FVGs in lookback',
            0.35, 'GATE_NO_FVG',
            {'rsi': round(rsi_val, 1), 'atr_at_signal': round(atr, 6)},
        )

    # ── Check if price is pulling back into a BULLISH FVG ─────────────────────
    bull_fvg = _find_fvg_for_price(price, fvgs, 'BULLISH', atr)

    # ── Check if price is pulling back into a BEARISH FVG ─────────────────────
    bear_fvg = _find_fvg_for_price(price, fvgs, 'BEARISH', atr)

    # If both, prefer the fresher / stronger one
    if bull_fvg and bear_fvg:
        if bull_fvg.age_bars <= bear_fvg.age_bars and bull_fvg.gap_atr >= bear_fvg.gap_atr:
            bear_fvg = None
        else:
            bull_fvg = None

    if not bull_fvg and not bear_fvg:
        return _hold(
            f'Price ({price:.4g}) not inside any valid FVG (checked {len(fvgs)} gaps)',
            0.40, 'GATE_PRICE_NOT_IN_FVG',
            {'rsi': round(rsi_val, 1), 'n_fvgs': len(fvgs),
             'nearest_fvg': _nearest_fvg_summary(price, fvgs)},
        )

    # ── Determine direction and matched FVG ───────────────────────────────────
    if bull_fvg:
        fvg       = bull_fvg
        direction = 'BUY'
    else:
        fvg       = bear_fvg
        direction = 'SELL'

    # ── Regime direction gate ─────────────────────────────────────────────────
    # In a strong downtrend, BUY FVG signals are counter-trend fades.
    # Block until we have live evidence they work (v1 conservative).
    if direction == 'BUY' and regime == 'TRENDING_DOWN':
        return _hold(
            f'BUY FVG blocked in TRENDING_DOWN — counter-trend fade (v1 conservative)',
            0.38, 'GATE_REGIME_COUNTER',
            {'regime': regime, 'fvg': str(fvg)},
        )
    if direction == 'SELL' and regime == 'TRENDING_UP':
        return _hold(
            f'SELL FVG blocked in TRENDING_UP — counter-trend fade (v1 conservative)',
            0.38, 'GATE_REGIME_COUNTER',
            {'regime': regime, 'fvg': str(fvg)},
        )

    # ── Confidence scoring ────────────────────────────────────────────────────
    cs, confirmations, contra = _fvg_confidence_score(fvg)

    # RSI alignment bonus
    if direction == 'BUY' and 40 <= rsi_val <= 60:
        cs += 0.04; confirmations.append(f'RSI neutral BUY ({rsi_val:.1f})')
    elif direction == 'SELL' and 40 <= rsi_val <= 60:
        cs += 0.04; confirmations.append(f'RSI neutral SELL ({rsi_val:.1f})')

    # Regime alignment bonus
    if regime in ('RANGING', 'VOLATILE'):
        cs += 0.04; confirmations.append(f'Regime {regime} suits FVG reversion')
    elif regime == 'SQUEEZE':
        cs += 0.02; confirmations.append('SQUEEZE: FVG may act as magnet for breakout')

    confidence = min(0.90, _BASE_CONFIDENCE + cs)

    # ── Minimum confidence gate ───────────────────────────────────────────────
    if confidence < _MIN_CONFIDENCE_GATE:
        return _hold(
            f'Confidence {confidence:.0%} < {_MIN_CONFIDENCE_GATE:.0%} gate',
            confidence, 'GATE_LOW_CONFIDENCE',
            {'rsi': round(rsi_val, 1), 'fvg_tier': fvg.tier, 'fvg_age': fvg.age_bars},
        )

    # ── Trade levels ──────────────────────────────────────────────────────────
    # Entry:  current price (pullback into FVG)
    # Stop:   opposite edge of FVG + ATR buffer (if price goes through the gap = invalidated)
    # Target: FVG midpoint first, then far edge (partial fill → full fill)
    sl_buffer = atr * 0.30

    if direction == 'BUY':
        stop_loss = fvg.bottom - sl_buffer    # Below the FVG bottom
        risk      = price - stop_loss
        if risk <= 0:
            stop_loss = price - atr; risk = atr
        t1_mult   = _RR_T1_STRONG if fvg.tier == 'STRONG' else _RR_T1_MEDIUM if fvg.tier == 'MEDIUM' else _RR_T1_WEAK
        target_1  = price + risk * t1_mult
    else:  # SELL
        stop_loss = fvg.top + sl_buffer       # Above the FVG top
        risk      = stop_loss - price
        if risk <= 0:
            stop_loss = price + atr; risk = atr
        t1_mult   = _RR_T1_STRONG if fvg.tier == 'STRONG' else _RR_T1_MEDIUM if fvg.tier == 'MEDIUM' else _RR_T1_WEAK
        target_1  = price - risk * t1_mult

    actual_rr = abs(target_1 - price) / risk if risk > 0 else t1_mult

    # ── R:R multipliers ───────────────────────────────────────────────────────
    risk_in_atr = abs(price - stop_loss) / atr if atr > 0 else 1.0
    t1_in_atr   = abs(target_1 - price)  / atr if atr > 0 else t1_mult

    return BrainSignal(
        **_base,
        direction           = direction,
        confidence          = round(confidence, 3),
        signal_strength     = round(cs, 3),
        signal_age_candles  = fvg.age_bars,
        primary_evidence    = (
            f'{fvg.kind} FVG [{fvg.bottom:.4g}–{fvg.top:.4g}] '
            f'({fvg.tier}, {fvg.gap_atr:.2f}×ATR, {fvg.age_bars}b old, '
            f'fill={fvg.fill_pct:.0%}) | '
            + ' | '.join(confirmations)
        ),
        supporting_factors  = confirmations,
        contra_factors      = contra,
        method_confidence   = 0.70,   # v1 — lower than LS (0.85) until validated
        regime_suitability  = (
            'HIGH' if regime in ('RANGING', 'VOLATILE') else 'MEDIUM'
        ),
        reliability_flags   = {
            'fvg_tier':          fvg.tier,
            'partially_filled':  fvg.partially_filled,
            'stale_fvg':         fvg.age_bars > _STALE_FVG_AGE_BARS,
            'counter_trend':     False,  # Already gated above
        },
        measurements        = {
            'entry_price':        round(price, 6),
            'target_1':           round(target_1, 6),
            'stop_loss':          round(stop_loss, 6),
            'rr_achieved':        round(actual_rr, 2),
            'rsi':                round(rsi_val, 1),
            'atr_at_signal':      round(atr, 6),
            'atr_pct_at_signal':  round(atr_pct * 100, 3),
            'bars_used':          len(hist),
            'n_fvgs_found':       len(fvgs),
            'decision_factor':    f'FVG_{fvg.kind}_{fvg.tier}',
            'price_at_signal':    round(price, 6),
            **fvg.to_dict(),
            'indicator_1_name':   'fvg_gap_atr',    'indicator_1_value': round(fvg.gap_atr, 3),
            'indicator_2_name':   'rsi',             'indicator_2_value': round(rsi_val, 1),
            'indicator_3_name':   'fvg_fill_pct',    'indicator_3_value': round(fvg.fill_pct, 3),
        },
        rr_t1_mult = round(t1_in_atr, 2),
        rr_t2_mult = round(t1_in_atr * _RR_T2_MULT, 2),
        rr_sl_mult = round(risk_in_atr, 2),
    )


# ═══════════════════════════════════════════════════════════════
# DIAGNOSTIC HELPERS
# ═══════════════════════════════════════════════════════════════

def _nearest_fvg_summary(price: float, fvgs: List[FVG]) -> dict:
    """Return summary of nearest FVG to price (for HOLD diagnostics)."""
    if not fvgs:
        return {}
    nearest = min(fvgs, key=lambda f: min(abs(price - f.top), abs(price - f.bottom)))
    dist    = min(abs(price - nearest.top), abs(price - nearest.bottom))
    return {
        'nearest_fvg_kind':   nearest.kind,
        'nearest_fvg_tier':   nearest.tier,
        'nearest_fvg_top':    nearest.top,
        'nearest_fvg_bottom': nearest.bottom,
        'nearest_fvg_dist':   round(dist, 6),
        'nearest_fvg_age':    nearest.age_bars,
    }


def get_all_fvgs(hist: pd.DataFrame, atr: float = None) -> List[FVG]:
    """
    Public utility: return all currently valid FVGs.
    Useful for dashboard display, grid runners, or AEP diagnostics.
    """
    if atr is None:
        atr = calc_atr(hist, period=14)
    return _find_fvgs(hist, atr)


# ═══════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 70)
    print("FVG Brain v1 — Self-Test")
    print("=" * 70)

    np.random.seed(42)
    n = 120

    # Build synthetic OHLCV with a deliberate FVG
    prices = [100.0]
    for i in range(n - 1):
        prices.append(max(10.0, prices[-1] + np.random.normal(0.1, 0.8)))

    highs  = [p + abs(np.random.normal(0, 0.3)) for p in prices]
    lows   = [p - abs(np.random.normal(0, 0.3)) for p in prices]
    closes = prices[:]
    vols   = [1_000_000 + np.random.randint(-100_000, 100_000) for _ in prices]

    # Inject a manual BULLISH FVG at bar 80:
    # candle[79].high < candle[81].low → gap between them
    highs[79]  = 95.0
    lows[81]   = 97.0   # gap: 95.0 → 97.0
    closes[80] = 98.0   # large impulse candle

    df = pd.DataFrame({'Open': prices, 'High': highs, 'Low': lows,
                       'Close': closes, 'Volume': vols})

    atr_val = calc_atr(df, 14)
    fvgs    = _find_fvgs(df, atr_val)

    print(f"\nTest 1: FVG detection on synthetic data")
    print(f"  ATR = {atr_val:.3f}")
    print(f"  FVGs found: {len(fvgs)}")
    for f in fvgs[:5]:
        print(f"    {f}")

    print(f"\nTest 2: CHAOS regime → HOLD")
    r = fvg_signal(df, regime='CHAOS')
    print(f"  Direction: {r.direction} {'OK' if r.direction == 'HOLD' else 'FAIL'}")

    print(f"\nTest 3: Insufficient data → HOLD")
    r = fvg_signal(df.head(20), regime='VOLATILE')
    print(f"  Direction: {r.direction} {'OK' if r.direction == 'HOLD' else 'FAIL'}")

    print(f"\nTest 4: Confluence boost — swept level inside FVG")
    if fvgs:
        f      = fvgs[0]
        midpt  = f.midpoint
        boost  = fvg_confluence_boost(df, midpt, 'BUY', atr_val)
        print(f"  Swept level: {midpt:.3f} | FVG: {f}")
        print(f"  Boost returned: {boost:.2f} {'OK' if boost > 0 else 'FAIL — expected > 0'}")

    print(f"\nTest 5: Confluence boost — swept level outside all FVGs")
    boost_miss = fvg_confluence_boost(df, 999.0, 'BUY', atr_val)
    print(f"  Boost for price=999 (no FVG): {boost_miss:.2f} {'OK' if boost_miss == 0 else 'FAIL'}")

    print(f"\nTest 6: Full signal on RANGING regime")
    r = fvg_signal(df, regime='RANGING', symbol='TEST', timeframe='1h')
    print(f"  Direction: {r.direction} | Confidence: {r.confidence:.0%}")
    print(f"  Evidence: {r.primary_evidence[:80]}")
    if r.direction != 'HOLD':
        m = r.measurements
        print(f"  entry_price in measurements: {'OK' if 'entry_price' in m else 'FAIL'}")
        print(f"  stop_loss in measurements:   {'OK' if 'stop_loss' in m else 'FAIL'}")
        print(f"  fvg_kind in measurements:    {'OK' if 'fvg_kind' in m else 'FAIL'}")

    print(f"\nTest 7: get_all_fvgs utility")
    all_fvgs = get_all_fvgs(df)
    print(f"  Total valid FVGs: {len(all_fvgs)}")
    for f in all_fvgs[:3]:
        print(f"    {f}")

    print("\n" + "=" * 70)
    print("Self-test complete.")
    print(f"Key gates: data≥{_MIN_HIST_BARS}b | regime≠CHAOS | RSI {_RSI_BLOCK_LOW}-{_RSI_BLOCK_HIGH}")
    print(f"FVG gates: gap≥{_MIN_GAP_ATR}×ATR | age≤{_MAX_FVG_AGE_BARS}b | not fully filled")
    print(f"Boost values: STRONG={_CONFLUENCE_STRONG_BOOST} MEDIUM={_CONFLUENCE_MEDIUM_BOOST} WEAK={_CONFLUENCE_WEAK_BOOST}")
    print("=" * 70)