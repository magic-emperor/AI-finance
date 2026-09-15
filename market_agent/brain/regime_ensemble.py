"""
Brain 2 — Regime-Ensemble v2  (Market Condition Classifier / Meta Brain)
=========================================================================
RESEARCH FOUNDATION
-------------------
1. Hamilton (1989) — Regime-Switching Models
   Theoretical basis for hidden-state financial regime detection.

2. Two Sigma (Botte & Bao, 2021) — "A Machine Learning Approach to Regime Modeling"
   GMM on multi-factor returns. Key insight: let MARKET STATISTICS define regimes,
   not fixed calendar or arbitrary thresholds.
   Ref: https://www.twosigma.com/articles/a-machine-learning-approach-to-regime-modeling/

3. Hurst (1951) via Chan (2013), Lo & MacKinlay (1999), Kroha & Friedrich (2018)
   H < 0.42 = mean-reverting | H ~ 0.5 = random walk | H > 0.58 = trending
   Kroha & Friedrich showed Moving Hurst outperforms MACD 3-7x in controlled tests.

4. QuantStart — "Market Regime Detection using HMMs"
   Validated that regime-based gating eliminates a large class of false signals
   from applying trend systems in ranging/chaotic markets.

WHY NOT PURE HMM/GMM
---------------------
HMM/GMM require training phases, are non-deterministic, need sklearn/hmmlearn,
and crash on thin asset histories (USDJPY 80 bars wont converge GaussianHMM).
This system runs 20+ assets simultaneously in real-time. A crashed brain is a
live risk. This v2 uses a DETERMINISTIC 4-layer ensemble — no training,
fully reproducible, graceful degradation on short data.

WHAT CHANGED FROM v1
--------------------
[BF-1] ATR computed twice with DIFFERENT methods (smoothed vs raw TR). 
       Now unified: raw TR/close used consistently throughout.
[BF-2] calc_atr/calc_adx return type unvalidated. Wrapped in _scalar() 
       that handles Series, ndarray, float safely.
[BF-3] measurements dict contained None values violating Dict[str,float].
       All values are now float; sentinel -1.0 replaces None.
[BF-4] NaN from rolling().iloc[-1] silently propagated. Now _safe_float() 
       guards every rolling result.
[BF-5] signal_strength = adx/50 always. Now reflects regime confidence.
[BF-6] contra_factors always []. Now every regime states falsification conditions.
[NF-1] Hurst Exponent added as Layer 2 (variance-ratio, zero external deps).
[NF-2] MEAN_REVERTING is a new regime label distinct from RANGING.
[NF-3] signal_age_candles now computed (bars since regime began).
[NF-4] Asset class detection + per-class volatility threshold multipliers.
[NF-5] Volume context in squeeze: SQUEEZE + rising vol = breakout_imminent flag.
[NF-6] Optional htf_close for higher-timeframe bias as supporting factor.
[NF-7] regime_duration_bars in measurements for aggregator use.
[NF-8] TRENDING split into STRONG/WEAK variants. Backward-compat via REGIME_COMPAT_MAP.
[BF-7] SQUEEZE was overriding confirmed strong trends (ADX>22).
       Backtest showed ADX=45 markets labelled SQUEEZE when BB width was
       tight. Fixed: SQUEEZE now only fires when ADX<22 (FLAT regime).
[GRID] adx_strong_threshold raised 30→33 based on 90d+180d grid backtest.
       Both runs consistently show adx_s=33 outperforms adx_s=30.
       ADX 30-33 was triggering STRONG on exhausted trends (confirmation trap).

TAXONOMY v2 (10 regimes)
------------------------
  TRENDING_UP_STRONG    ADX>33 + Hurst>0.55 + price>SMA50  [grid-validated]
  TRENDING_UP_WEAK      ADX 22-30 + price>SMA50
  TRENDING_DOWN_STRONG  ADX>33 + Hurst>0.55 + price<SMA50  [grid-validated]
  TRENDING_DOWN_WEAK    ADX 22-30 + price<SMA50
  MEAN_REVERTING        ADX<22 + Hurst<0.42 (statistically confirmed MR)
  RANGING               ADX<22 + Hurst 0.42-0.58 (no structure)
  SQUEEZE               BB width < 70% of 20-bar avg
  VOLATILE              ATR% > 2.5x asset-adjusted median
  CHAOS                 ATR% > 5.0x asset-adjusted median (NO TRADES)
  TRANSITIONING         ADX and Hurst in genuine disagreement

Returns: BrainSignal(direction='HOLD') — meta brain, no directional vote.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from market_agent.brain.brain_contract import BrainSignal
from market_agent.brain.brain_utils import calc_atr, calc_adx


# =============================================================================
# CONSTANTS
# =============================================================================

REGIME_COMPAT_MAP: dict[str, str] = {
    'TRENDING_UP_STRONG':   'TRENDING_UP',
    'TRENDING_UP_WEAK':     'TRENDING_UP',
    'TRENDING_DOWN_STRONG': 'TRENDING_DOWN',
    'TRENDING_DOWN_WEAK':   'TRENDING_DOWN',
    'MEAN_REVERTING':       'RANGING',
    'RANGING':              'RANGING',
    'SQUEEZE':              'SQUEEZE',
    'VOLATILE':             'VOLATILE',
    'CHAOS':                'CHAOS',
    'TRANSITIONING':        'RANGING',
}

# v1 backward-compat trust weights — signal_generators.py reads these
UNIFIED_REGIMES: dict[str, dict[str, float]] = {
    'TRENDING_UP':   {'trust_trend_brains': 0.90, 'trust_mean_rev': 0.15, 'volatility_brains': 0.50},
    'TRENDING_DOWN': {'trust_trend_brains': 0.90, 'trust_mean_rev': 0.15, 'volatility_brains': 0.50},
    'RANGING':       {'trust_trend_brains': 0.25, 'trust_mean_rev': 0.90, 'volatility_brains': 0.60},
    'VOLATILE':      {'trust_trend_brains': 0.40, 'trust_mean_rev': 0.10, 'volatility_brains': 0.85},
    'SQUEEZE':       {'trust_trend_brains': 0.20, 'trust_mean_rev': 0.70, 'volatility_brains': 0.95},
    'CHAOS':         {'trust_trend_brains': 0.00, 'trust_mean_rev': 0.00, 'volatility_brains': 0.00},
}

# v2 extended trust weights
UNIFIED_REGIMES_V2: dict[str, dict[str, float]] = {
    'TRENDING_UP_STRONG':   {'trust_trend_brains': 0.95, 'trust_mean_rev': 0.05, 'volatility_brains': 0.40},
    'TRENDING_UP_WEAK':     {'trust_trend_brains': 0.70, 'trust_mean_rev': 0.25, 'volatility_brains': 0.50},
    'TRENDING_DOWN_STRONG': {'trust_trend_brains': 0.95, 'trust_mean_rev': 0.05, 'volatility_brains': 0.40},
    'TRENDING_DOWN_WEAK':   {'trust_trend_brains': 0.70, 'trust_mean_rev': 0.25, 'volatility_brains': 0.50},
    'MEAN_REVERTING':       {'trust_trend_brains': 0.10, 'trust_mean_rev': 0.95, 'volatility_brains': 0.55},
    'RANGING':              {'trust_trend_brains': 0.25, 'trust_mean_rev': 0.90, 'volatility_brains': 0.60},
    'SQUEEZE':              {'trust_trend_brains': 0.20, 'trust_mean_rev': 0.70, 'volatility_brains': 0.95},
    'VOLATILE':             {'trust_trend_brains': 0.40, 'trust_mean_rev': 0.10, 'volatility_brains': 0.85},
    'CHAOS':                {'trust_trend_brains': 0.00, 'trust_mean_rev': 0.00, 'volatility_brains': 0.00},
    'TRANSITIONING':        {'trust_trend_brains': 0.35, 'trust_mean_rev': 0.35, 'volatility_brains': 0.60},
}

MIN_BARS_FULL    = 60
MIN_BARS_PARTIAL = 30
MIN_BARS_MINIMUM = 15

# Asset-class ATR threshold multipliers
# >1.0 = raises bar for VOLATILE (asset is naturally wilder)
# <1.0 = lowers bar for VOLATILE (asset is normally calm)
ASSET_VOL_MULTIPLIER: dict[str, float] = {
    'CRYPTO':       1.40,
    'FOREX':        0.70,
    'COMMODITY':    1.00,
    'EQUITY_INDIA': 0.90,
    'EQUITY_US':    1.00,
    'INDEX':        0.80,
    'UNKNOWN':      1.00,
}


# =============================================================================
# HELPERS
# =============================================================================

def _scalar(val) -> float:
    """[BF-2] Safe scalar extractor for brain_utils returns."""
    if val is None:
        return 0.0
    if isinstance(val, pd.Series):
        try:
            val = float(val.iloc[-1])
        except (IndexError, TypeError):
            return 0.0
    elif isinstance(val, np.ndarray):
        try:
            val = float(val[-1])
        except (IndexError, TypeError):
            return 0.0
    try:
        v = float(val)
        return 0.0 if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return 0.0


def _safe_float(val, default: float = 0.0) -> float:
    """[BF-4] NaN-safe float coercion for rolling window results."""
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return default


def detect_asset_class(symbol: str) -> str:
    """[NF-4] Infer asset class from symbol string."""
    if not symbol:
        return 'UNKNOWN'
    s = symbol.upper().strip()
    if s.endswith('-USD') or s.endswith('USDT') or s.endswith('-USDT'):
        return 'CRYPTO'
    if any(kw in s for kw in ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'BNB')):
        return 'CRYPTO'
    if s.endswith('=X'):
        return 'FOREX'
    if s.endswith('=F'):
        return 'COMMODITY'
    if s.startswith('^'):
        return 'INDEX'
    if s.endswith('.NS') or s.endswith('.BO'):
        return 'EQUITY_INDIA'
    if s.replace('&', '').isalpha() and len(s) <= 5:
        return 'EQUITY_US'
    return 'UNKNOWN'


# =============================================================================
# LAYER 1 — VOLATILITY
# =============================================================================

def _compute_volatility_layer(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    asset_class: str,
) -> Tuple[str, float, float, float, float, float]:
    """
    [BF-1] Unified raw TR/close ATR% — no mixing with EMA-smoothed calc_atr().
    [NF-4] Asset-class multiplier calibrates thresholds per asset universe.
    """
    prev_c = close.shift(1)
    tr_s   = pd.concat(
        [high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1
    ).max(axis=1)
    atr_pct_s = tr_s / close.replace(0, np.nan)
    atr_pct   = _safe_float(atr_pct_s.iloc[-1], default=0.01)
    mult      = ASSET_VOL_MULTIPLIER.get(asset_class, 1.0)

    if len(close) >= 60:
        med_atr            = _safe_float(atr_pct_s.iloc[-60:].median(), default=0.01)
        volatile_threshold = max(0.010 * mult, med_atr * 2.5)
        chaos_threshold    = max(0.025 * mult, med_atr * 5.0)
    else:
        med_atr            = _safe_float(atr_pct_s.median(), default=0.01)
        volatile_threshold = 0.040 * mult
        chaos_threshold    = 0.080 * mult

    if atr_pct > chaos_threshold:
        return ('CHAOS',   0.92, atr_pct, volatile_threshold, chaos_threshold, med_atr)
    elif atr_pct > volatile_threshold:
        ratio = (atr_pct - volatile_threshold) / max(chaos_threshold - volatile_threshold, 1e-9)
        conf  = min(0.90, 0.65 + ratio * 0.25)
        return ('VOLATILE', conf, atr_pct, volatile_threshold, chaos_threshold, med_atr)
    else:
        return ('NORMAL',  0.0,  atr_pct, volatile_threshold, chaos_threshold, med_atr)


# =============================================================================
# LAYER 2 — HURST EXPONENT
# =============================================================================

def _hurst_variance_ratio(log_prices: np.ndarray, max_lag: int = 20) -> float:
    """
    [NF-1] Variance-ratio Hurst exponent. Zero external dependencies.

    Basis: E[Var(X(t+tau)-X(t))] ~ tau^(2H)
    => log(Var) = 2H*log(tau) + C
    => H = OLS_slope / 2

    Source: Chan (2013) Algorithmic Trading, Ch.2;
            Lo & MacKinlay (1999) A Non-Random Walk Down Wall Street.
    """
    n = len(log_prices)
    if n < 30:
        return 0.5
    eff_max = min(max_lag, n // 4)
    if eff_max < 3:
        return 0.5

    tau_list, var_list = [], []
    for lag in range(2, eff_max + 1):
        diffs = log_prices[lag:] - log_prices[:-lag]
        if len(diffs) < 4:
            continue
        var = float(np.var(diffs))
        if var > 1e-14:
            tau_list.append(float(lag))
            var_list.append(var)

    if len(tau_list) < 4:
        return 0.5

    try:
        slope, _ = np.polyfit(np.log(tau_list), np.log(var_list), 1)
        return float(np.clip(slope / 2.0, 0.05, 0.95))
    except (np.linalg.LinAlgError, ValueError):
        return 0.5


def _compute_hurst_layer(close: pd.Series) -> Tuple[str, float, float]:
    """[NF-1] Classify market memory from Hurst exponent."""
    n = len(close)
    if n < 30:
        return ('RANDOM_WALK', 0.40, 0.5)
    window     = min(100, n)
    log_prices = np.log(np.maximum(close.values[-window:].astype(float), 1e-10))
    H          = _hurst_variance_ratio(log_prices, max_lag=min(20, window // 4))

    if H > 0.58:
        return ('TRENDING',       min(0.90, 0.60 + (H - 0.58) * 2.0),  H)
    elif H < 0.42:
        return ('MEAN_REVERTING', min(0.90, 0.60 + (0.42 - H) * 2.0), H)
    elif 0.48 <= H <= 0.52:
        return ('RANDOM_WALK', 0.65, H)
    else:
        return ('RANDOM_WALK', 0.40, H)


# =============================================================================
# LAYER 3 — TREND STRUCTURE
# =============================================================================

def _compute_trend_layer(
    hist: pd.DataFrame, adx: float, price: float
) -> Tuple[str, float, str, float]:
    """ADX + SMA50 trend classification. [BF-4] NaN guarded throughout."""
    close = hist['Close']
    n     = len(close)
    sma50 = _safe_float(
        close.rolling(50).mean().iloc[-1] if n >= 50 else np.nan,
        default=price
    )
    price_vs_sma50_pct = ((price - sma50) / sma50 * 100) if sma50 > 0 else 0.0
    bullish            = price > sma50

    if adx > 33:
        # Grid-validated threshold: 33 beats 30 on both 90d and 180d backtests.
        # ADX 30-33 was calling too many exhausted trends STRONG — confirmation trap.
        return (
            'STRONG_UP' if bullish else 'STRONG_DOWN',
            min(0.92, 0.70 + (adx - 33) / 100.0),
            'HIGH',
            price_vs_sma50_pct,
        )
    elif adx > 22:
        return (
            'WEAK_UP' if bullish else 'WEAK_DOWN',
            0.55 + (adx - 22) / 80.0,
            'MEDIUM',
            price_vs_sma50_pct,
        )
    else:
        return ('FLAT', 0.70, 'HIGH', price_vs_sma50_pct)


# =============================================================================
# LAYER 4 — SQUEEZE
# =============================================================================

def _compute_squeeze_layer(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Tuple[bool, float, bool, float, float]:
    """[NF-5] BB squeeze + volume breakout pressure detection."""
    n = len(close)
    if n < 20:
        return (False, 0.0, False, 0.0, 0.0)

    sma20  = close.rolling(20).mean()
    std20  = close.rolling(20).std()
    bb_w_s = (4 * std20) / sma20.replace(0, np.nan)
    bb_w   = _safe_float(bb_w_s.iloc[-1], default=0.04)
    avg_bw = _safe_float(bb_w_s.rolling(20).mean().iloc[-1], default=bb_w)

    if avg_bw <= 0:
        return (False, 0.0, False, bb_w, avg_bw)

    ratio      = bb_w / avg_bw
    is_squeeze = ratio < 0.70
    sq_conf    = min(0.90, 0.60 + (0.70 - ratio) * 1.5) if is_squeeze else 0.0

    breakout_imminent = False
    if is_squeeze and volume is not None and len(volume) >= 10:
        vol_now = _safe_float(volume.iloc[-1], default=0.0)
        vol_avg = _safe_float(volume.rolling(10).mean().iloc[-1], default=vol_now)
        if vol_avg > 0 and vol_now > vol_avg * 1.3:
            breakout_imminent = True

    return (is_squeeze, sq_conf, breakout_imminent, bb_w, avg_bw)


# =============================================================================
# REGIME DURATION
# =============================================================================

def _detect_regime_duration(
    hist: pd.DataFrame,
    current_regime: str,
    atr_pct: float,
    volatile_threshold: float,
    chaos_threshold: float,
) -> int:
    """[NF-3] Estimate bars elapsed since current regime started."""
    close = hist['Close']
    high  = hist['High']
    low   = hist['Low']
    n     = len(hist)
    limit = min(50, n - 1)
    if limit < 2:
        return 1

    prev_c    = close.shift(1)
    tr_s      = pd.concat(
        [high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1
    ).max(axis=1)
    atr_pct_s = tr_s / close.replace(0, np.nan)

    count        = 1
    now_chaos    = current_regime == 'CHAOS'
    now_volatile = current_regime == 'VOLATILE'
    now_calm     = not now_chaos and not now_volatile

    for i in range(2, limit + 1):
        past = _safe_float(atr_pct_s.iloc[-i], default=atr_pct)
        past_chaos    = past > chaos_threshold
        past_volatile = past > volatile_threshold and not past_chaos
        past_calm     = not past_chaos and not past_volatile

        if now_chaos    and not past_chaos:    break
        if now_volatile and not past_volatile: break
        if now_calm     and not past_calm:     break
        count += 1

    return count


# =============================================================================
# ENSEMBLE COMBINATOR
# =============================================================================

def _combine_layers(
    vol_regime: str,
    hurst_regime: str,
    trend_regime: str,
    is_squeeze: bool,
    H: float,
    adx: float,
) -> Tuple[str, float]:
    """
    Combine Layer 1-4 outputs into final (regime_v2, confidence).

    Priority (hard cascade):
      1. CHAOS       — absolute override, no trades
      2. VOLATILE    — overrides everything, high ATR environment
      3. STRONG trend (ADX>33) — confirmed trend blocks SQUEEZE
      4. WEAK trend  (ADX 22-33) — developing trend, SQUEEZE does not override
      5. SQUEEZE     — only fires when ADX<22 (no confirmed trend at all)
      6. MEAN_REVERTING — Hurst<0.42 + ADX flat
      7. TRANSITIONING  — ADX/Hurst genuine disagreement
      8. RANGING     — default

    [BF-7] SQUEEZE must NOT override a confirmed trend.
    Backtest showed ADX=45 markets being labelled SQUEEZE because BB happened
    to be tight. A market with ADX>22 is trending — tight BBs in a trend are
    normal and do not indicate a squeeze. SQUEEZE now only fires when ADX<22.

    [GRID] adx_strong threshold raised 30→33. Both 90d and 180d grid backtests
    confirm adx_s=33 outperforms adx_s=30. ADX 30-33 was triggering STRONG
    labels on exhausted trends (confirmation trap).
    """
    if vol_regime == 'CHAOS':
        return ('CHAOS', 0.93)
    if vol_regime == 'VOLATILE':
        return ('VOLATILE', 0.82)

    bullish = 'UP' in trend_regime

    # Strong confirmed trend (ADX>33) — SQUEEZE cannot override
    if trend_regime in ('STRONG_UP', 'STRONG_DOWN'):
        if hurst_regime == 'TRENDING':
            label = 'TRENDING_UP_STRONG' if bullish else 'TRENDING_DOWN_STRONG'
            conf  = min(0.92, 0.78 + (adx - 33) / 100.0 + max(0.0, H - 0.55) * 0.3)
            return (label, conf)
        elif hurst_regime == 'MEAN_REVERTING':
            # ADX strong but Hurst anti-persistent: momentum burst inside MR structure
            label = 'TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK'
            return (label, 0.52)
        else:
            label = 'TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK'
            return (label, min(0.75, 0.60 + (adx - 22) / 100.0))

    # Developing trend (ADX 22-33) — SQUEEZE does not override here either
    elif trend_regime in ('WEAK_UP', 'WEAK_DOWN'):
        if hurst_regime == 'TRENDING':
            label = 'TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK'
            return (label, 0.65)
        elif hurst_regime == 'MEAN_REVERTING':
            return ('TRANSITIONING', 0.50)
        else:
            return ('TRANSITIONING', 0.48)

    # FLAT (ADX<22) — no directional structure, SQUEEZE fires here
    else:
        if is_squeeze:
            return ('SQUEEZE', 0.80)
        if hurst_regime == 'MEAN_REVERTING':
            return ('MEAN_REVERTING', min(0.88, 0.60 + (0.42 - H) * 2.5))
        elif hurst_regime == 'TRENDING':
            label = 'TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK'
            return (label, 0.50)
        else:
            return ('RANGING', 0.72)


# =============================================================================
# CONTRA FACTORS
# =============================================================================

def _build_contra_factors(
    regime: str, adx: float, H: float,
    atr_pct: float, volatile_threshold: float,
    chaos_threshold: float, bb_w: float, avg_bb_w: float,
) -> list[str]:
    """[BF-6] Falsification conditions for every regime classification."""
    c: list[str] = []
    if 'TRENDING' in regime:
        c.append(f'Invalidated if ADX drops below 22 (now {adx:.1f})')
        if 'STRONG' in regime and H < 0.60:
            c.append(f'Hurst={H:.2f} below ideal 0.60 — trend persistence moderate')
        c.append('Engulfing reversal candle would signal trend exit')
    elif regime == 'MEAN_REVERTING':
        c.append(f'Invalidated if ADX rises above 25 (now {adx:.1f})')
        c.append(f'Hurst={H:.2f}: rise above 0.50 shifts toward RANGING')
    elif regime == 'RANGING':
        c.append(f'ADX > 22 + directional breakout flips to TRENDING (now {adx:.1f})')
        c.append('Volume surge with sustained move exits RANGING')
    elif regime == 'VOLATILE':
        c.append(f'ATR% {atr_pct:.2%} only {(chaos_threshold - atr_pct):.2%} below CHAOS')
        c.append(f'Sustained ATR% < {volatile_threshold:.2%} exits VOLATILE')
    elif regime == 'CHAOS':
        c.append(f'Exits when ATR% < {chaos_threshold:.2%} (now {atr_pct:.2%})')
        c.append('No trades permitted while CHAOS active')
    elif regime == 'SQUEEZE':
        c.append(f'BB expansion above {avg_bb_w:.3f} exits squeeze (now {bb_w:.3f})')
        c.append('Volume expansion without price follow-through = false breakout')
    elif regime == 'TRANSITIONING':
        c.append(f'ADX>25 or H>0.55 resolves to TRENDING (ADX={adx:.1f}, H={H:.2f})')
        c.append(f'ADX<20 + H<0.42 resolves to MEAN_REVERTING')
    return c


# =============================================================================
# HTF BIAS
# =============================================================================

def _htf_bias_string(htf_close: Optional[pd.Series]) -> str:
    """[NF-6] Higher-timeframe SMA50 alignment as a supporting factor string."""
    if htf_close is None or len(htf_close) < 20:
        return ''
    htf_price = _safe_float(htf_close.iloc[-1], default=0.0)
    if htf_price <= 0:
        return ''
    htf_sma = _safe_float(
        htf_close.rolling(min(50, len(htf_close))).mean().iloc[-1],
        default=htf_price
    )
    if htf_sma <= 0:
        return ''
    pct = (htf_price - htf_sma) / htf_sma * 100
    if pct > 1.0:
        return f'HTF bias: BULLISH (price {pct:.1f}% above HTF SMA50)'
    elif pct < -1.0:
        return f'HTF bias: BEARISH (price {abs(pct):.1f}% below HTF SMA50)'
    return f'HTF bias: NEUTRAL ({pct:+.1f}% from HTF SMA50)'


# =============================================================================
# INSUFFICIENT DATA FALLBACK
# =============================================================================

def _insufficient_data_signal(n_bars: int) -> BrainSignal:
    return BrainSignal(
        brain_name='Regime-Ensemble',
        specialization='Market Condition Classifier -- Meta Brain (v2)',
        method='4-Layer Ensemble: ATR-Vol + Hurst(R/S) + ADX-Trend + BB-Squeeze',
        direction='HOLD',
        confidence=0.30,
        signal_strength=0.0,
        signal_age_candles=0,
        primary_evidence=(
            f'Insufficient data: {n_bars} bars (need >= {MIN_BARS_MINIMUM})'
        ),
        supporting_factors=[
            f'Full signal at >= {MIN_BARS_FULL} bars',
            f'Partial signal (no Hurst) at >= {MIN_BARS_PARTIAL} bars',
        ],
        contra_factors=['Cannot classify with this data volume'],
        method_confidence=0.30,
        regime_suitability='LOW',
        symbol='',
        reliability_flags={
            'insufficient_data':     True,
            'hurst_unavailable':     True,
            'vol_layer_unavailable': True,
        },
        measurements={
            'computed_regime':      'RANGING',
            'computed_regime_v2':   'RANGING',
            'adx': -1.0, 'hurst_exponent': -1.0, 'atr_pct': -1.0,
            'volatile_threshold': -1.0, 'chaos_threshold': -1.0,
            'median_atr_pct': -1.0, 'bb_width': -1.0, 'avg_bb_w': -1.0,
            'squeeze_ratio': -1.0, 'price_vs_sma50_pct': 0.0,
            'regime_duration_bars': 1.0, 'breakout_imminent': 0.0,
            'hurst_adx_conflict': 0.0, 'bars_used': float(n_bars),
            'price_at_signal': -1.0, 'atr_at_signal': -1.0, 'atr_pct_at_signal': -1.0,
            'vol_multiplier': 1.0, 'decision_factor': 0.0, 'hurst_trending': 0.0,
            'hurst_mean_rev': 0.0, 'trust_trend': 0.25,
            'trust_mean_rev': 0.90, 'trust_volatility': 0.60,
        },
        recent_accuracy=None,
        regime_accuracy=None,
    )


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def regime_ensemble_signal(
    hist:      pd.DataFrame,
    symbol:    str = '',
    htf_close: Optional[pd.Series] = None,
) -> BrainSignal:
    """
    Brain 2 v2: Market Condition Classifier (Meta Brain).

    META BRAIN — direction is always 'HOLD'. No directional vote.
    Consumers read measurements['computed_regime'] (v1 compat) or
    measurements['computed_regime_v2'] (full v2 label).

    Args:
        hist:      OHLCV DataFrame. Required: High, Low, Close.
                   Optional: Volume (for squeeze breakout context).
                   Min 15 bars; full accuracy needs 60+ bars.
        symbol:    Ticker string for asset-class detection.
                   'BTC-USD', 'RELIANCE.NS', 'USDJPY=X', 'GC=F', 'NVDA', '^NSEBANK'
        htf_close: Optional higher-timeframe Close series for HTF bias context.
                   Does NOT change primary regime -- appears as supporting factor only.
    """
    n = len(hist)
    if n < MIN_BARS_MINIMUM:
        return _insufficient_data_signal(n)

    close  = hist['Close']
    high   = hist['High']
    low    = hist['Low']
    volume = hist.get('Volume', None)
    price  = _safe_float(close.iloc[-1], default=1.0) or 1.0

    asset_class = detect_asset_class(symbol)
    adx         = _scalar(calc_adx(hist, 14))

    # ── 4 Layers ──────────────────────────────────────────────────────────
    (vol_regime, vol_conf, atr_pct,
     volatile_threshold, chaos_threshold,
     median_atr_pct) = _compute_volatility_layer(close, high, low, asset_class)

    if n >= MIN_BARS_PARTIAL:
        hurst_regime, hurst_conf, H = _compute_hurst_layer(close)
    else:
        hurst_regime, hurst_conf, H = 'RANDOM_WALK', 0.40, 0.50

    (trend_regime, trend_conf,
     regime_suitability,
     price_vs_sma50_pct) = _compute_trend_layer(hist, adx, price)

    (is_squeeze, squeeze_conf,
     breakout_imminent,
     bb_w, avg_bb_w) = _compute_squeeze_layer(close, high, low, volume)

    # ── Ensemble ──────────────────────────────────────────────────────────
    regime_v2, confidence = _combine_layers(
        vol_regime, hurst_regime, trend_regime, is_squeeze, H, adx
    )
    regime_v1   = REGIME_COMPAT_MAP.get(regime_v2, 'RANGING')
    weights_v2  = UNIFIED_REGIMES_V2.get(regime_v2, UNIFIED_REGIMES_V2['RANGING'])
    duration    = _detect_regime_duration(
        hist, regime_v2, atr_pct, volatile_threshold, chaos_threshold
    )

    # ── Reliability flags ─────────────────────────────────────────────────
    flags: dict[str, bool] = {}
    if n < MIN_BARS_FULL:
        flags['hurst_short_window']   = True
    if adx < 15 and 'TRENDING' in regime_v2:
        flags['low_adx_trend_warning'] = True
    if breakout_imminent:
        flags['breakout_imminent']    = True
    if hurst_regime == 'MEAN_REVERTING' and 'TRENDING' in regime_v2:
        flags['hurst_adx_conflict']   = True
    if asset_class == 'UNKNOWN':
        flags['unknown_asset_class']  = True

    # ── Narrative ─────────────────────────────────────────────────────────
    htf_str = _htf_bias_string(htf_close)

    if regime_v2 in ('CHAOS', 'VOLATILE'):
        signal_strength = round(confidence, 3)
    else:
        signal_strength = round(confidence * min(1.0, adx / 40.0 + 0.30), 3)

    supporting: list[str] = [
        f'Trend brain trust: {weights_v2["trust_trend_brains"]:.0%}',
        f'Mean-rev brain trust: {weights_v2["trust_mean_rev"]:.0%}',
        f'Vol brain trust: {weights_v2["volatility_brains"]:.0%}',
        f'Hurst={H:.2f} ({hurst_regime}, conf={hurst_conf:.0%})',
        f'Regime active ~{duration} bars',
        f'Asset class: {asset_class}',
    ]
    if htf_str:
        supporting.append(htf_str)
    if breakout_imminent:
        supporting.append('SQUEEZE + rising volume: breakout pressure detected')

    contra = _build_contra_factors(
        regime_v2, adx, H, atr_pct,
        volatile_threshold, chaos_threshold, bb_w, avg_bb_w
    )

    primary_evidence = (
        f'Regime: {regime_v2} (compat={regime_v1}) | '
        f'ADX={adx:.1f} | Hurst={H:.2f} | '
        f'ATR%={atr_pct:.2%} (VOLATILE>{volatile_threshold:.2%}, '
        f'CHAOS>{chaos_threshold:.2%}) | '
        f'BB_w={bb_w:.3f}/avg={avg_bb_w:.3f} | ~{duration}bars'
    )

    measurements: dict = {
        'computed_regime':      regime_v1,    # str — v1 backward compat
        'computed_regime_v2':   regime_v2,    # str — v2 full label
        'adx':                  round(adx, 2),
        'hurst_exponent':       round(H, 4),
        'hurst_trending':       1.0 if hurst_regime == 'TRENDING'       else 0.0,
        'hurst_mean_rev':       1.0 if hurst_regime == 'MEAN_REVERTING' else 0.0,
        'atr_pct':              round(atr_pct, 5),
        'volatile_threshold':   round(volatile_threshold, 5),
        'chaos_threshold':      round(chaos_threshold, 5),
        'median_atr_pct':       round(median_atr_pct, 5),
        'bb_width':             round(bb_w, 5),
        'avg_bb_w':             round(avg_bb_w, 5),
        'squeeze_ratio':        round(bb_w / avg_bb_w, 4) if avg_bb_w > 0 else 1.0,
        'price_vs_sma50_pct':   round(price_vs_sma50_pct, 3),
        'regime_duration_bars': float(duration),
        'breakout_imminent':    1.0 if breakout_imminent else 0.0,
        'hurst_adx_conflict':   1.0 if flags.get('hurst_adx_conflict') else 0.0,
        'bars_used':            float(n),
        'price_at_signal':      round(price, 6),
        'atr_at_signal':        round(atr_pct * price, 6),        # A12: raw ATR value
        'atr_pct_at_signal':    round(atr_pct * 100, 3),          # A12: ATR as % of price
        'vol_multiplier':       ASSET_VOL_MULTIPLIER.get(asset_class, 1.0),
        'trust_trend':          weights_v2['trust_trend_brains'],
        'trust_mean_rev':       weights_v2['trust_mean_rev'],
        'trust_volatility':     weights_v2['volatility_brains'],
        'decision_factor':      float(abs(hash(regime_v2)) % 1_000_000),
    }

    return BrainSignal(
        brain_name='Regime-Ensemble',
        specialization='Market Condition Classifier -- Meta Brain (v2)',
        method='4-Layer Ensemble: ATR-Vol + Hurst(R/S) + ADX-Trend + BB-Squeeze',
        direction='HOLD',
        confidence=round(confidence, 4),
        signal_strength=signal_strength,
        signal_age_candles=duration,
        primary_evidence=primary_evidence,
        supporting_factors=supporting,
        contra_factors=contra,
        method_confidence=0.87,
        regime_suitability=regime_suitability,
        symbol=symbol,
        reliability_flags=flags,
        measurements=measurements,
        recent_accuracy=None,
        regime_accuracy=None,
    )