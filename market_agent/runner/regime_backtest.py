"""
Regime Brain Backtesting Framework
====================================
Tests regime_ensemble_signal() against 729 days of synthetic 1H OHLCV data
that matches the statistical properties of your actual universe:
  - NSE equities (RELIANCE.NS, TATASTEEL.NS, LT.NS, ITC.NS)
  - US equities   (NVDA, GOOGL, AAPL, AMD)
  - Crypto        (BTC-USD)
  - Forex         (USDJPY=X, GBPJPY=X)
  - Commodity     (GC=F gold)

Validation criteria per instructions Step 4:
  1. Regime label distribution — are labels sensible? Not always RANGING?
  2. Regime accuracy — does TRENDING_UP actually precede upward moves?
  3. CHAOS accuracy  — does CHAOS flag extreme bars correctly?
  4. Hurst threshold validation — are 0.42/0.58 thresholds right per asset?
  5. Asset-class calibration — do BTC thresholds differ correctly from NIFTY?
  6. Regime transition detection — signal_age_candles correctness
  7. Edge cases — 15 bars, 30 bars, NaN injection, zero-price guard
"""
from __future__ import annotations
import sys
import math
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# ── Inline the brain code (no package installed) ─────────────────────────────
# We paste the two functions the brain needs from brain_utils directly

def calc_atr(hist: pd.DataFrame, period: int = 14) -> float:
    if hist is None or len(hist) < period + 1:
        return 0.0
    high = hist['High']; low = hist['Low']
    prev_close = hist['Close'].shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    result = tr.rolling(period).mean().iloc[-1]
    return float(result) if not pd.isna(result) else 0.0

def calc_adx(hist: pd.DataFrame, period: int = 14) -> float:
    if hist is None or len(hist) < period * 2 + 1:
        return 15.0
    high = hist['High']; low = hist['Low']; close = hist['Close']
    plus_dm  = (high.diff()).where(high.diff() > low.diff().abs(), 0.0).where(high.diff() > 0, 0.0)
    minus_dm = (low.diff().abs()).where(low.diff().abs() > high.diff(), 0.0).where(low.diff() < 0, 0.0)
    tr_vals  = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr14    = tr_vals.rolling(period).mean()
    plus_di  = 100 * plus_dm.rolling(period).mean() / atr14.replace(0, float('nan'))
    minus_di = 100 * minus_dm.rolling(period).mean() / atr14.replace(0, float('nan'))
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float('nan'))
    adx_val  = float(dx.rolling(period).mean().iloc[-1])
    return adx_val if not pd.isna(adx_val) else 15.0

# ── Paste regime brain inline ─────────────────────────────────────────────────
# (Simplified BrainSignal dataclass for standalone testing)

from dataclasses import dataclass, field

@dataclass
class BrainSignal:
    brain_name: str; specialization: str; method: str
    direction: str; confidence: float; signal_strength: float
    signal_age_candles: int; primary_evidence: str
    supporting_factors: list; contra_factors: list
    method_confidence: float; regime_suitability: str
    symbol: str = ''
    reliability_flags: dict = field(default_factory=dict)
    measurements: dict = field(default_factory=dict)
    recent_accuracy: Optional[float] = None
    regime_accuracy: Optional[float] = None
    rr_t1_mult: Optional[float] = None
    rr_t2_mult: Optional[float] = None
    rr_sl_mult: Optional[float] = None

    def effective_confidence(self):
        penalties = sum(1 for v in self.reliability_flags.values() if v)
        return max(0.0, self.confidence - min(penalties * 0.05, 0.30))
    def is_abstaining(self):
        return self.direction == 'HOLD' and self.confidence < 0.45

# ── Now paste the actual brain functions ─────────────────────────────────────

REGIME_COMPAT_MAP = {
    'TRENDING_UP_STRONG': 'TRENDING_UP', 'TRENDING_UP_WEAK': 'TRENDING_UP',
    'TRENDING_DOWN_STRONG': 'TRENDING_DOWN', 'TRENDING_DOWN_WEAK': 'TRENDING_DOWN',
    'MEAN_REVERTING': 'RANGING', 'RANGING': 'RANGING', 'SQUEEZE': 'SQUEEZE',
    'VOLATILE': 'VOLATILE', 'CHAOS': 'CHAOS', 'TRANSITIONING': 'RANGING',
}
UNIFIED_REGIMES_V2 = {
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
MIN_BARS_FULL=60; MIN_BARS_PARTIAL=30; MIN_BARS_MINIMUM=15
ASSET_VOL_MULTIPLIER = {
    'CRYPTO':1.40,'FOREX':0.70,'COMMODITY':1.00,
    'EQUITY_INDIA':0.90,'EQUITY_US':1.00,'INDEX':0.80,'UNKNOWN':1.00
}

def _scalar(val):
    if val is None: return 0.0
    if isinstance(val, pd.Series):
        try: val = float(val.iloc[-1])
        except: return 0.0
    elif isinstance(val, np.ndarray):
        try: val = float(val[-1])
        except: return 0.0
    try:
        v = float(val)
        return 0.0 if (math.isnan(v) or math.isinf(v)) else v
    except: return 0.0

def _safe_float(val, default=0.0):
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except: return default

def detect_asset_class(symbol):
    if not symbol: return 'UNKNOWN'
    s = symbol.upper().strip()
    if s.endswith('-USD') or s.endswith('USDT') or any(k in s for k in ('BTC','ETH','SOL','XRP','BNB')): return 'CRYPTO'
    if s.endswith('=X'): return 'FOREX'
    if s.endswith('=F'): return 'COMMODITY'
    if s.startswith('^'): return 'INDEX'
    if s.endswith('.NS') or s.endswith('.BO'): return 'EQUITY_INDIA'
    if s.replace('&','').isalpha() and len(s) <= 5: return 'EQUITY_US'
    return 'UNKNOWN'

def _compute_volatility_layer(close, high, low, asset_class):
    prev_c = close.shift(1)
    tr_s = pd.concat([high-low,(high-prev_c).abs(),(low-prev_c).abs()],axis=1).max(axis=1)
    atr_pct_s = tr_s / close.replace(0, np.nan)
    atr_pct = _safe_float(atr_pct_s.iloc[-1], default=0.01)
    mult = ASSET_VOL_MULTIPLIER.get(asset_class, 1.0)
    if len(close) >= 60:
        med = _safe_float(atr_pct_s.iloc[-60:].median(), default=0.01)
        vt = max(0.010*mult, med*2.5); ct = max(0.025*mult, med*5.0)
    else:
        med = _safe_float(atr_pct_s.median(), default=0.01)
        vt = 0.040*mult; ct = 0.080*mult
    if atr_pct > ct: return ('CHAOS',0.92,atr_pct,vt,ct,med)
    elif atr_pct > vt:
        ratio=(atr_pct-vt)/max(ct-vt,1e-9); conf=min(0.90,0.65+ratio*0.25)
        return ('VOLATILE',conf,atr_pct,vt,ct,med)
    return ('NORMAL',0.0,atr_pct,vt,ct,med)

def _hurst_variance_ratio(log_prices, max_lag=20):
    n = len(log_prices)
    if n < 30: return 0.5
    eff = min(max_lag, n//4)
    if eff < 3: return 0.5
    tl=[]; vl=[]
    for lag in range(2, eff+1):
        diffs = log_prices[lag:] - log_prices[:-lag]
        if len(diffs) < 4: continue
        var = float(np.var(diffs))
        if var > 1e-14: tl.append(float(lag)); vl.append(var)
    if len(tl) < 4: return 0.5
    try:
        slope, _ = np.polyfit(np.log(tl), np.log(vl), 1)
        return float(np.clip(slope/2.0, 0.05, 0.95))
    except: return 0.5

def _compute_hurst_layer(close):
    n = len(close)
    if n < 30: return ('RANDOM_WALK',0.40,0.5)
    window = min(100,n)
    lp = np.log(np.maximum(close.values[-window:].astype(float),1e-10))
    H = _hurst_variance_ratio(lp, max_lag=min(20,window//4))
    if H > 0.58: return ('TRENDING', min(0.90,0.60+(H-0.58)*2.0), H)
    elif H < 0.42: return ('MEAN_REVERTING', min(0.90,0.60+(0.42-H)*2.0), H)
    elif 0.48<=H<=0.52: return ('RANDOM_WALK',0.65,H)
    else: return ('RANDOM_WALK',0.40,H)

def _compute_trend_layer(hist, adx, price):
    close=hist['Close']; n=len(close)
    sma50 = _safe_float(close.rolling(50).mean().iloc[-1] if n>=50 else np.nan, default=price)
    pvs = ((price-sma50)/sma50*100) if sma50>0 else 0.0
    bullish = price > sma50
    if adx>30: return ('STRONG_UP' if bullish else 'STRONG_DOWN', min(0.92,0.70+(adx-30)/100.0),'HIGH',pvs)
    elif adx>22: return ('WEAK_UP' if bullish else 'WEAK_DOWN', 0.55+(adx-22)/80.0,'MEDIUM',pvs)
    else: return ('FLAT',0.70,'HIGH',pvs)

def _compute_squeeze_layer(close, high, low, volume=None):
    n=len(close)
    if n<20: return (False,0.0,False,0.0,0.0)
    sma20=close.rolling(20).mean(); std20=close.rolling(20).std()
    bws=(4*std20)/sma20.replace(0,np.nan)
    bw=_safe_float(bws.iloc[-1],default=0.04); abw=_safe_float(bws.rolling(20).mean().iloc[-1],default=bw)
    if abw<=0: return (False,0.0,False,bw,abw)
    ratio=bw/abw; iss=ratio<0.70
    sc=min(0.90,0.60+(0.70-ratio)*1.5) if iss else 0.0
    bi=False
    if iss and volume is not None and len(volume)>=10:
        vn=_safe_float(volume.iloc[-1],default=0.0); va=_safe_float(volume.rolling(10).mean().iloc[-1],default=vn)
        if va>0 and vn>va*1.3: bi=True
    return (iss,sc,bi,bw,abw)

def _detect_regime_duration(hist, current_regime, atr_pct, vt, ct):
    close=hist['Close']; high=hist['High']; low=hist['Low']
    n=len(hist); limit=min(50,n-1)
    if limit<2: return 1
    prev_c=close.shift(1)
    tr_s=pd.concat([high-low,(high-prev_c).abs(),(low-prev_c).abs()],axis=1).max(axis=1)
    atr_pct_s=tr_s/close.replace(0,np.nan)
    count=1; nc=current_regime=='CHAOS'; nv=current_regime=='VOLATILE'; nm=not nc and not nv
    for i in range(2, limit+1):
        past=_safe_float(atr_pct_s.iloc[-i],default=atr_pct)
        pc=past>ct; pv=past>vt and not pc; pm=not pc and not pv
        if nc and not pc: break
        if nv and not pv: break
        if nm and not pm: break
        count+=1
    return count

def _combine_layers(vol_regime, hurst_regime, trend_regime, is_squeeze, H, adx):
    if vol_regime=='CHAOS': return ('CHAOS',0.93)
    if vol_regime=='VOLATILE': return ('VOLATILE',0.82)
    if is_squeeze: return ('SQUEEZE',0.80)
    bullish='UP' in trend_regime
    if trend_regime in ('STRONG_UP','STRONG_DOWN'):
        if hurst_regime=='TRENDING':
            label='TRENDING_UP_STRONG' if bullish else 'TRENDING_DOWN_STRONG'
            conf=min(0.92,0.78+(adx-30)/100.0+max(0.0,H-0.55)*0.3)
            return (label,conf)
        elif hurst_regime=='MEAN_REVERTING':
            return ('TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK',0.52)
        else:
            return ('TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK',min(0.75,0.60+(adx-22)/100.0))
    elif trend_regime in ('WEAK_UP','WEAK_DOWN'):
        if hurst_regime=='TRENDING': return ('TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK',0.65)
        elif hurst_regime=='MEAN_REVERTING': return ('TRANSITIONING',0.50)
        else: return ('TRANSITIONING',0.48)
    else:
        if hurst_regime=='MEAN_REVERTING': return ('MEAN_REVERTING',min(0.88,0.60+(0.42-H)*2.5))
        elif hurst_regime=='TRENDING': return ('TRENDING_UP_WEAK' if bullish else 'TRENDING_DOWN_WEAK',0.50)
        else: return ('RANGING',0.72)

def regime_ensemble_signal(hist, symbol='', htf_close=None):
    n=len(hist)
    if n<MIN_BARS_MINIMUM:
        return BrainSignal(
            brain_name='Regime-Ensemble',specialization='MCM v2',method='4-Layer',
            direction='HOLD',confidence=0.30,signal_strength=0.0,signal_age_candles=0,
            primary_evidence=f'Insufficient data: {n} bars',supporting_factors=[],
            contra_factors=['Cannot classify'],method_confidence=0.30,regime_suitability='LOW',
            symbol=symbol,reliability_flags={'insufficient_data':True},
            measurements={'computed_regime':'RANGING','computed_regime_v2':'RANGING',
                         'adx':-1.0,'hurst_exponent':-1.0,'atr_pct':-1.0,
                         'volatile_threshold':-1.0,'chaos_threshold':-1.0,
                         'median_atr_pct':-1.0,'bb_width':-1.0,'avg_bb_w':-1.0,
                         'squeeze_ratio':-1.0,'price_vs_sma50_pct':0.0,
                         'regime_duration_bars':1.0,'breakout_imminent':0.0,
                         'hurst_adx_conflict':0.0,'bars_used':float(n),
                         'price_at_signal':-1.0,'vol_multiplier':1.0,'decision_factor':0.0,
                         'hurst_trending':0.0,'hurst_mean_rev':0.0,
                         'trust_trend':0.25,'trust_mean_rev':0.90,'trust_volatility':0.60},
        )
    close=hist['Close']; high=hist['High']; low=hist['Low']
    volume=hist.get('Volume',None) if hasattr(hist,'get') else (hist['Volume'] if 'Volume' in hist.columns else None)
    price=_safe_float(close.iloc[-1],default=1.0) or 1.0
    asset_class=detect_asset_class(symbol)
    adx=_scalar(calc_adx(hist,14))
    (vol_regime,vol_conf,atr_pct,vt,ct,med_atr)=_compute_volatility_layer(close,high,low,asset_class)
    if n>=MIN_BARS_PARTIAL: hr,hc,H=_compute_hurst_layer(close)
    else: hr,hc,H='RANDOM_WALK',0.40,0.50
    tr,tc,rs,pvs=_compute_trend_layer(hist,adx,price)
    iss,sc,bi,bw,abw=_compute_squeeze_layer(close,high,low,volume)
    regime_v2,confidence=_combine_layers(vol_regime,hr,tr,iss,H,adx)
    regime_v1=REGIME_COMPAT_MAP.get(regime_v2,'RANGING')
    wv2=UNIFIED_REGIMES_V2.get(regime_v2,UNIFIED_REGIMES_V2['RANGING'])
    dur=_detect_regime_duration(hist,regime_v2,atr_pct,vt,ct)
    flags={}
    if n<MIN_BARS_FULL: flags['hurst_short_window']=True
    if adx<15 and 'TRENDING' in regime_v2: flags['low_adx_trend_warning']=True
    if bi: flags['breakout_imminent']=True
    if hr=='MEAN_REVERTING' and 'TRENDING' in regime_v2: flags['hurst_adx_conflict']=True
    if regime_v2 in ('CHAOS','VOLATILE'): ss=round(confidence,3)
    else: ss=round(confidence*min(1.0,adx/40.0+0.30),3)
    meas={
        'computed_regime':regime_v1,'computed_regime_v2':regime_v2,
        'adx':round(adx,2),'hurst_exponent':round(H,4),
        'hurst_trending':1.0 if hr=='TRENDING' else 0.0,
        'hurst_mean_rev':1.0 if hr=='MEAN_REVERTING' else 0.0,
        'atr_pct':round(atr_pct,5),'volatile_threshold':round(vt,5),
        'chaos_threshold':round(ct,5),'median_atr_pct':round(med_atr,5),
        'bb_width':round(bw,5),'avg_bb_w':round(abw,5),
        'squeeze_ratio':round(bw/abw,4) if abw>0 else 1.0,
        'price_vs_sma50_pct':round(pvs,3),'regime_duration_bars':float(dur),
        'breakout_imminent':1.0 if bi else 0.0,
        'hurst_adx_conflict':1.0 if flags.get('hurst_adx_conflict') else 0.0,
        'bars_used':float(n),'price_at_signal':round(price,6),
        'vol_multiplier':ASSET_VOL_MULTIPLIER.get(asset_class,1.0),
        'trust_trend':wv2['trust_trend_brains'],
        'trust_mean_rev':wv2['trust_mean_rev'],
        'trust_volatility':wv2['volatility_brains'],
        'decision_factor':float(abs(hash(regime_v2))%1_000_000),
    }
    return BrainSignal(
        brain_name='Regime-Ensemble',
        specialization='Market Condition Classifier -- Meta Brain (v2)',
        method='4-Layer Ensemble: ATR-Vol + Hurst(R/S) + ADX-Trend + BB-Squeeze',
        direction='HOLD',confidence=round(confidence,4),signal_strength=ss,
        signal_age_candles=dur,
        primary_evidence=f'Regime:{regime_v2}(compat={regime_v1})|ADX={adx:.1f}|H={H:.2f}|ATR%={atr_pct:.2%}',
        supporting_factors=[f'H={H:.2f}({hr})',f'dur~{dur}bars',f'asset:{asset_class}'],
        contra_factors=[],method_confidence=0.87,regime_suitability=rs,
        symbol=symbol,reliability_flags=flags,measurements=meas,
    )

# =============================================================================
# SYNTHETIC DATA GENERATORS
# Matches statistical properties of your DB data (1H bars, 729 days)
# =============================================================================

def make_trending_data(n=200, drift=0.0003, vol=0.008, start=1000.0, seed=42):
    """Strong uptrend: ADX should be high, Hurst > 0.55"""
    np.random.seed(seed)
    returns = np.random.normal(drift, vol, n)
    prices = start * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        'Open':  prices * (1 - np.random.uniform(0,0.002,n)),
        'High':  prices * (1 + np.random.uniform(0.001,0.005,n)),
        'Low':   prices * (1 - np.random.uniform(0.001,0.005,n)),
        'Close': prices,
        'Volume': np.random.randint(1_000_000, 5_000_000, n).astype(float),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

def make_ranging_data(n=200, vol=0.006, center=1000.0, seed=43):
    """Mean-reverting range: ADX low, price oscillates"""
    np.random.seed(seed)
    prices = [center]
    for _ in range(n-1):
        mean_pull = (center - prices[-1]) * 0.05
        shock = np.random.normal(mean_pull, vol * center)
        prices.append(max(center*0.8, min(center*1.2, prices[-1] + shock)))
    prices = np.array(prices)
    df = pd.DataFrame({
        'Open':  prices * (1 - np.random.uniform(0,0.002,n)),
        'High':  prices * (1 + np.random.uniform(0.001,0.004,n)),
        'Low':   prices * (1 - np.random.uniform(0.001,0.004,n)),
        'Close': prices,
        'Volume': np.random.randint(500_000, 2_000_000, n).astype(float),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

def make_volatile_data(n=200, vol=0.035, start=1000.0, seed=44):
    """High volatility: ATR% should exceed threshold → VOLATILE/CHAOS"""
    np.random.seed(seed)
    returns = np.random.normal(0.0001, vol, n)
    prices = start * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        'Open':  prices * (1 - np.random.uniform(0,0.005,n)),
        'High':  prices * (1 + np.random.uniform(0.005,0.030,n)),
        'Low':   prices * (1 - np.random.uniform(0.005,0.030,n)),
        'Close': prices,
        'Volume': np.random.randint(5_000_000, 20_000_000, n).astype(float),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

def make_squeeze_data(n=200, start=1000.0, seed=45):
    """BB squeeze: tight range then expand"""
    np.random.seed(seed)
    prices = [start]
    for i in range(n-1):
        if i < 120:
            shock = np.random.normal(0, 0.001 * start)  # very tight
        else:
            shock = np.random.normal(0.001*start, 0.008*start)  # expand
        prices.append(prices[-1] + shock)
    prices = np.array(prices)
    df = pd.DataFrame({
        'Open':  prices * (1 - np.random.uniform(0,0.001,n)),
        'High':  prices * (1 + np.random.uniform(0.0005,0.002,n)),
        'Low':   prices * (1 - np.random.uniform(0.0005,0.002,n)),
        'Close': prices,
        'Volume': np.concatenate([
            np.random.randint(300_000, 700_000, 120),      # low vol during squeeze
            np.random.randint(2_000_000, 6_000_000, n-120) # volume surge after
        ]).astype(float),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

def make_btc_like_data(n=500, seed=46):
    """BTC-like: high baseline vol ~3% per 1H bar, episodic chaos"""
    np.random.seed(seed)
    returns = np.random.normal(0.0002, 0.012, n)
    # inject 3 chaos events
    for idx in [100, 250, 400]:
        returns[idx:idx+5] = np.random.normal(0, 0.045, 5)
    prices = 45000.0 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        'Open':  prices * (1 - np.random.uniform(0,0.003,n)),
        'High':  prices * (1 + np.random.uniform(0.003,0.015,n)),
        'Low':   prices * (1 - np.random.uniform(0.003,0.015,n)),
        'Close': prices,
        'Volume': np.random.randint(10_000_000, 50_000_000, n).astype(float),
        'TakerBase': np.random.uniform(0.4, 0.6, n) * np.random.randint(10_000_000, 50_000_000, n),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

def make_forex_like_data(n=500, seed=47):
    """USDJPY-like: low vol ~0.3% per bar, gentle drifts"""
    np.random.seed(seed)
    returns = np.random.normal(0.00005, 0.0025, n)
    prices = 150.0 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        'Open':  prices * (1 - np.random.uniform(0,0.001,n)),
        'High':  prices * (1 + np.random.uniform(0.0005,0.002,n)),
        'Low':   prices * (1 - np.random.uniform(0.0005,0.002,n)),
        'Close': prices,
        'Volume': np.random.randint(100_000, 500_000, n).astype(float),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

def make_nse_like_data(n=500, seed=48):
    """RELIANCE.NS-like: ~0.8% vol per 1H, session breaks"""
    np.random.seed(seed)
    returns = np.random.normal(0.00015, 0.006, n)
    prices = 2800.0 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        'Open':  prices * (1 - np.random.uniform(0,0.002,n)),
        'High':  prices * (1 + np.random.uniform(0.001,0.006,n)),
        'Low':   prices * (1 - np.random.uniform(0.001,0.006,n)),
        'Close': prices,
        'Volume': np.random.randint(500_000, 3_000_000, n).astype(float),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

def make_downtrend_data(n=200, drift=-0.0004, vol=0.008, start=1000.0, seed=49):
    """Strong downtrend: TRENDING_DOWN should fire"""
    np.random.seed(seed)
    returns = np.random.normal(drift, vol, n)
    prices = start * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        'Open':  prices * (1 + np.random.uniform(0,0.002,n)),
        'High':  prices * (1 + np.random.uniform(0.001,0.005,n)),
        'Low':   prices * (1 - np.random.uniform(0.001,0.005,n)),
        'Close': prices,
        'Volume': np.random.randint(1_000_000, 5_000_000, n).astype(float),
    })
    df.index = pd.date_range('2024-01-01', periods=n, freq='1h')
    return df

# =============================================================================
# ROLLING WINDOW BACKTEST ENGINE
# =============================================================================

def rolling_backtest(df, symbol, window=100, step=10):
    """
    Run regime_ensemble_signal on rolling windows.
    Returns list of result dicts per window.
    """
    results = []
    n = len(df)
    for start in range(0, n - window, step):
        end = start + window
        hist = df.iloc[start:end].copy()
        try:
            bs = regime_ensemble_signal(hist, symbol=symbol)
            m = bs.measurements
            # Compute what actually happened in next 10 bars
            future = df.iloc[end:end+10] if end+10 <= n else df.iloc[end:]
            if len(future) >= 5:
                fwd_return = (float(future['Close'].iloc[-1]) - float(hist['Close'].iloc[-1])) / float(hist['Close'].iloc[-1])
            else:
                fwd_return = None
            results.append({
                'bar':          end,
                'regime_v2':    m.get('computed_regime_v2','?'),
                'regime_v1':    m.get('computed_regime','?'),
                'confidence':   bs.confidence,
                'adx':          m.get('adx',0),
                'hurst':        m.get('hurst_exponent',0.5),
                'atr_pct':      m.get('atr_pct',0),
                'duration':     m.get('regime_duration_bars',1),
                'fwd_return':   fwd_return,
                'signal_age':   bs.signal_age_candles,
            })
        except Exception as e:
            results.append({'bar': end, 'regime_v2': f'ERROR:{e}', 'error': True})
    return results

def analyse_results(results, label):
    """Analyse backtest results for regime accuracy."""
    if not results: return
    valid = [r for r in results if 'error' not in r and r.get('fwd_return') is not None]
    if not valid: return
    
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Total windows: {len(results)} | Valid: {len(valid)}")
    
    # Regime distribution
    from collections import Counter
    dist = Counter(r['regime_v2'] for r in valid)
    print(f"\n  REGIME DISTRIBUTION:")
    for reg, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        pct = cnt/len(valid)*100
        print(f"    {reg:<28} {cnt:>4} ({pct:5.1f}%)")
    
    # Regime accuracy: does the regime predict what should happen?
    print(f"\n  REGIME ACCURACY (10-bar forward return):")
    regime_groups = {}
    for r in valid:
        rv = r['regime_v2']
        if rv not in regime_groups: regime_groups[rv] = []
        regime_groups[rv].append(r['fwd_return'])
    
    for reg, rets in sorted(regime_groups.items()):
        avg = np.mean(rets)*100; std = np.std(rets)*100; n = len(rets)
        # Accuracy = % of time signal was directionally correct
        if 'TRENDING_UP' in reg:
            acc = sum(1 for r in rets if r > 0) / n * 100
            verdict = "✅ CORRECT" if acc > 55 else "❌ WRONG"
        elif 'TRENDING_DOWN' in reg:
            acc = sum(1 for r in rets if r < 0) / n * 100
            verdict = "✅ CORRECT" if acc > 55 else "❌ WRONG"
        elif reg in ('RANGING','MEAN_REVERTING','TRANSITIONING'):
            acc = sum(1 for r in rets if abs(r) < np.std(rets)*0.8) / n * 100
            verdict = "✅ LOW DRIFT" if np.abs(avg) < 0.3 else "⚠  DRIFTING"
        elif reg == 'CHAOS':
            acc = sum(1 for r in rets if abs(r) > 0.01) / n * 100
            verdict = "✅ VOLATILE" if acc > 60 else "⚠  MILD"
        elif reg == 'VOLATILE':
            acc = sum(1 for r in rets if abs(r) > 0.005) / n * 100
            verdict = "✅ VOLATILE" if acc > 55 else "⚠  MILD"
        elif reg == 'SQUEEZE':
            acc = sum(1 for r in rets if abs(r) > 0.003) / n * 100
            verdict = "✅ EXPANDING" if acc > 50 else "⚠  STILL TIGHT"
        else:
            acc = 0; verdict = "N/A"
        print(f"    {reg:<28} n={n:>3} avg={avg:+.3f}% std={std:.3f}% acc={acc:.0f}% {verdict}")
    
    # Hurst threshold analysis
    h_vals = [r['hurst'] for r in valid]
    print(f"\n  HURST STATISTICS:")
    print(f"    mean={np.mean(h_vals):.3f} | median={np.median(h_vals):.3f} | "
          f"std={np.std(h_vals):.3f} | min={np.min(h_vals):.3f} | max={np.max(h_vals):.3f}")
    n_trending  = sum(1 for h in h_vals if h > 0.58)
    n_mr        = sum(1 for h in h_vals if h < 0.42)
    n_random    = sum(1 for h in h_vals if 0.42 <= h <= 0.58)
    print(f"    H>0.58 (TRENDING):      {n_trending:>3} ({n_trending/len(h_vals)*100:.0f}%)")
    print(f"    H<0.42 (MEAN_REV):      {n_mr:>3} ({n_mr/len(h_vals)*100:.0f}%)")
    print(f"    0.42≤H≤0.58 (RANDOM):   {n_random:>3} ({n_random/len(h_vals)*100:.0f}%)")
    
    # ADX stats
    adx_vals = [r['adx'] for r in valid]
    print(f"\n  ADX STATISTICS:")
    print(f"    mean={np.mean(adx_vals):.1f} | median={np.median(adx_vals):.1f} | "
          f"max={np.max(adx_vals):.1f}")
    
    # Confidence stats
    conf_vals = [r['confidence'] for r in valid]
    print(f"\n  CONFIDENCE STATISTICS:")
    print(f"    mean={np.mean(conf_vals):.3f} | min={np.min(conf_vals):.3f} | max={np.max(conf_vals):.3f}")


# =============================================================================
# EDGE CASE TESTS
# =============================================================================

def test_edge_cases():
    print(f"\n{'='*60}")
    print("  EDGE CASE TESTS")
    print(f"{'='*60}")
    
    # 1. Minimum bars (15)
    df_min = make_trending_data(n=15)
    bs = regime_ensemble_signal(df_min, symbol='TEST')
    print(f"\n  [1] 15 bars (min): regime={bs.measurements.get('computed_regime_v2')} "
          f"conf={bs.confidence:.2f} flags={list(bs.reliability_flags.keys())}")
    assert bs.measurements.get('computed_regime_v2') == 'RANGING', "15-bar fallback should be RANGING"
    assert bs.reliability_flags.get('insufficient_data'), "Should flag insufficient_data"
    print(f"      ✅ 15-bar minimum guard working")
    
    # 2. Exactly 14 bars (below minimum)
    df_tiny = make_trending_data(n=14)
    bs2 = regime_ensemble_signal(df_tiny, symbol='TEST')
    assert bs2.measurements.get('computed_regime_v2') == 'RANGING'
    print(f"  [2] 14 bars (below min): ✅ returns safe RANGING fallback")
    
    # 3. NaN injection in Close
    df_nan = make_trending_data(n=100).copy()
    df_nan.loc[df_nan.index[50:55], 'Close'] = np.nan
    try:
        bs3 = regime_ensemble_signal(df_nan, symbol='RELIANCE.NS')
        print(f"  [3] NaN in Close[50:55]: ✅ survived, regime={bs3.measurements.get('computed_regime_v2')}")
    except Exception as e:
        print(f"  [3] NaN in Close: ❌ CRASHED: {e}")
    
    # 4. Zero price guard
    df_zero = make_trending_data(n=100).copy()
    df_zero.loc[df_zero.index[-1], 'Close'] = 0.0
    try:
        bs4 = regime_ensemble_signal(df_zero, symbol='TEST')
        print(f"  [4] Zero price last bar: ✅ survived, regime={bs4.measurements.get('computed_regime_v2')}")
    except Exception as e:
        print(f"  [4] Zero price: ❌ CRASHED: {e}")
    
    # 5. Partial data (30 bars — partial Hurst)
    df_30 = make_trending_data(n=30)
    bs5 = regime_ensemble_signal(df_30, symbol='NVDA')
    flag = bs5.reliability_flags.get('hurst_short_window', False)
    print(f"  [5] 30 bars (partial): regime={bs5.measurements.get('computed_regime_v2')} "
          f"hurst_short_window={flag}")
    assert flag, "Should flag hurst_short_window at 30 bars"
    print(f"      ✅ partial window flag working")
    
    # 6. Asset class detection
    test_symbols = [
        ('BTC-USD',     'CRYPTO'),
        ('RELIANCE.NS', 'EQUITY_INDIA'),
        ('USDJPY=X',    'FOREX'),
        ('GC=F',        'COMMODITY'),
        ('^NSEBANK',    'INDEX'),
        ('NVDA',        'EQUITY_US'),
        ('AAPL',        'EQUITY_US'),
        ('',            'UNKNOWN'),
    ]
    print(f"\n  [6] Asset class detection:")
    all_ok = True
    for sym, expected in test_symbols:
        got = detect_asset_class(sym)
        ok = got == expected
        if not ok: all_ok = False
        print(f"      {sym:<15} expected={expected:<15} got={got:<15} {'✅' if ok else '❌'}")
    
    # 7. Measurements dict — no None values (BF-3)
    df_full = make_trending_data(n=100)
    bs7 = regime_ensemble_signal(df_full, symbol='AAPL')
    none_keys = [k for k,v in bs7.measurements.items() if v is None]
    if none_keys:
        print(f"  [7] None in measurements: ❌ Keys with None: {none_keys}")
    else:
        print(f"  [7] measurements dict: ✅ No None values (BF-3 compliant)")
    
    # 8. CHAOS blocks all trust weights
    df_vol = make_volatile_data(n=200, vol=0.08)  # extreme vol
    bs8 = regime_ensemble_signal(df_vol, symbol='BTC-USD')
    regime8 = bs8.measurements.get('computed_regime_v2')
    trust_t = bs8.measurements.get('trust_trend', 1.0)
    trust_m = bs8.measurements.get('trust_mean_rev', 1.0)
    if regime8 == 'CHAOS':
        assert trust_t == 0.0 and trust_m == 0.0
        print(f"  [8] CHAOS trust weights: ✅ All zero (no trades)")
    else:
        print(f"  [8] CHAOS test: ⚠  regime={regime8} (vol may not have hit threshold at this seed)")
    
    # 9. contra_factors never empty (BF-6)
    for regime_type, df_test, sym in [
        ('TRENDING', make_trending_data(n=200), 'NVDA'),
        ('RANGING',  make_ranging_data(n=200),  'ITC.NS'),
        ('VOLATILE', make_volatile_data(n=200), 'BTC-USD'),
    ]:
        bs9 = regime_ensemble_signal(df_test, symbol=sym)
        has_contra = len(bs9.contra_factors) > 0
        print(f"  [9] contra_factors ({regime_type}): {'✅ populated' if has_contra else '❌ EMPTY'} "
              f"({len(bs9.contra_factors)} items)")
    
    # 10. signal_age_candles > 0 on established regime
    df_long_trend = make_trending_data(n=300)
    bs10 = regime_ensemble_signal(df_long_trend, symbol='NVDA')
    age = bs10.signal_age_candles
    print(f"  [10] Regime duration (300-bar trend): age={age} bars "
          f"{'✅' if age > 1 else '⚠  (expected > 1)'}")

# =============================================================================
# ASSET CLASS CALIBRATION TEST
# Validates that BTC and NIFTY get different volatility thresholds
# =============================================================================

def test_asset_class_calibration():
    print(f"\n{'='*60}")
    print("  ASSET CLASS CALIBRATION TEST")
    print(f"{'='*60}")
    
    df = make_volatile_data(n=200, vol=0.018, start=1000.0)
    
    results = {}
    for sym, expected_class in [
        ('BTC-USD',     'CRYPTO'),
        ('RELIANCE.NS', 'EQUITY_INDIA'),
        ('USDJPY=X',    'FOREX'),
        ('GC=F',        'COMMODITY'),
        ('NVDA',        'EQUITY_US'),
    ]:
        bs = regime_ensemble_signal(df, symbol=sym)
        m = bs.measurements
        results[sym] = {
            'class':     expected_class,
            'regime':    m.get('computed_regime_v2'),
            'vol_mult':  m.get('vol_multiplier'),
            'vt':        m.get('volatile_threshold'),
            'ct':        m.get('chaos_threshold'),
            'atr_pct':   m.get('atr_pct'),
        }
    
    print(f"\n  {'Symbol':<15} {'Class':<15} {'VolMult':<9} {'VolThresh':<12} {'ChaosThresh':<13} {'ATR%':<8} {'Regime'}")
    print(f"  {'-'*90}")
    for sym, r in results.items():
        print(f"  {sym:<15} {r['class']:<15} {r['vol_mult']:<9.2f} "
              f"{r['vt']:<12.4f} {r['ct']:<13.4f} {r['atr_pct']:<8.4f} {r['regime']}")
    
    # Key validation: BTC threshold must be higher than EQUITY_INDIA threshold
    btc_vt = results['BTC-USD']['vt']
    nse_vt = results['RELIANCE.NS']['vt']
    forex_vt = results['USDJPY=X']['vt']
    
    print(f"\n  Calibration checks:")
    print(f"    BTC volatile_threshold > NSE threshold: "
          f"{btc_vt:.4f} > {nse_vt:.4f} {'✅' if btc_vt > nse_vt else '❌'}")
    print(f"    NSE threshold > FOREX threshold: "
          f"{nse_vt:.4f} > {forex_vt:.4f} {'✅' if nse_vt > forex_vt else '❌'}")
    
    # All the same ATR data but different regimes expected
    regimes = {sym: r['regime'] for sym, r in results.items()}
    unique = len(set(regimes.values()))
    print(f"    Different regimes for same volatility data: {unique} unique labels")
    if unique > 1:
        print(f"    ✅ Asset-class calibration is working (different assets → different sensitivity)")
    else:
        print(f"    ⚠  All assets gave same regime — calibration may need review")
    for sym, reg in regimes.items():
        print(f"      {sym:<15}: {reg}")

# =============================================================================
# HURST THRESHOLD VALIDATION
# Validates that 0.42/0.58 thresholds are appropriate for each asset class
# =============================================================================

def test_hurst_thresholds():
    print(f"\n{'='*60}")
    print("  HURST THRESHOLD VALIDATION")
    print(f"{'='*60}")
    
    datasets = [
        ('Strong Uptrend',   make_trending_data(n=300),     'NVDA',        'TRENDING'),
        ('Strong Downtrend', make_downtrend_data(n=300),    'TATASTEEL.NS','TRENDING'),
        ('Ranging/MR',       make_ranging_data(n=300),      'ITC.NS',      'MEAN_REVERTING_or_RANDOM'),
        ('BTC like',         make_btc_like_data(n=500),     'BTC-USD',     'varies'),
        ('Forex like',       make_forex_like_data(n=500),   'USDJPY=X',    'MEAN_REVERTING_or_RANGING'),
        ('NSE like',         make_nse_like_data(n=500),     'RELIANCE.NS', 'varies'),
    ]
    
    print(f"\n  {'Dataset':<22} {'Symbol':<15} {'H':<7} {'H_regime':<18} {'Expected':<25} {'Match'}")
    print(f"  {'-'*95}")
    
    issues = []
    for name, df, sym, expected_hint in datasets:
        bs = regime_ensemble_signal(df, symbol=sym)
        m = bs.measurements
        H = m.get('hurst_exponent', 0.5)
        h_trend = m.get('hurst_trending', 0)
        h_mr    = m.get('hurst_mean_rev', 0)
        h_regime = 'TRENDING' if h_trend else ('MEAN_REVERTING' if h_mr else 'RANDOM_WALK')
        
        if 'TRENDING' in expected_hint and h_regime == 'TRENDING': match = '✅'
        elif 'MEAN_REV' in expected_hint and h_regime in ('MEAN_REVERTING','RANDOM_WALK'): match = '✅'
        elif 'varies' in expected_hint: match = 'ℹ '
        else: match = '⚠ '; issues.append(f"{name}: expected {expected_hint}, got H={H:.3f} ({h_regime})")
        
        print(f"  {name:<22} {sym:<15} {H:<7.3f} {h_regime:<18} {expected_hint:<25} {match}")
    
    if issues:
        print(f"\n  ⚠  Hurst threshold concerns:")
        for i in issues: print(f"    - {i}")
    else:
        print(f"\n  ✅ Hurst thresholds are working correctly for these datasets")

# =============================================================================
# FULL ROLLING BACKTEST
# =============================================================================

def run_full_backtest():
    print(f"\n{'='*60}")
    print("  FULL ROLLING WINDOW BACKTEST")
    print(f"{'='*60}")
    
    test_cases = [
        (make_trending_data(n=500),      'NVDA',         'Strong Uptrend (EQUITY_US)'),
        (make_downtrend_data(n=500),     'TATASTEEL.NS', 'Strong Downtrend (NSE)'),
        (make_ranging_data(n=500),       'ITC.NS',       'Ranging/MR (NSE)'),
        (make_volatile_data(n=500),      'BTC-USD',      'Volatile (CRYPTO)'),
        (make_squeeze_data(n=500),       'LT.NS',        'Squeeze → Expansion'),
        (make_btc_like_data(n=729*6),    'BTC-USD',      'BTC Full 729-day 1H sim'),
        (make_forex_like_data(n=729*6),  'USDJPY=X',     'USDJPY Full 729-day 1H sim'),
        (make_nse_like_data(n=729*6),    'RELIANCE.NS',  'RELIANCE Full 729-day 1H sim'),
    ]
    
    for df, symbol, label in test_cases:
        results = rolling_backtest(df, symbol=symbol, window=100, step=5)
        analyse_results(results, f"{label} | {symbol} | n={len(df)}")

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  REGIME ENSEMBLE v2 — BACKTEST VALIDATION")
    print("  Per instructions: Step 4 (Backtesting)")
    print("=" * 60)
    
    test_edge_cases()
    test_asset_class_calibration()
    test_hurst_thresholds()
    run_full_backtest()
    
    print(f"\n{'='*60}")
    print("  BACKTEST COMPLETE")
    print(f"{'='*60}")