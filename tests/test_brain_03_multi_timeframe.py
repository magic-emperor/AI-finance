"""
Test 3: Multi-Timeframe — Cross-Timeframe Signal Alignment Verifier
====================================================================
From multi_timeframe.py logic:
  - Per TF: BUY if SMA5 > SMA20 AND RSI < 60
             SELL if SMA5 < SMA20 AND RSI > 40
             HOLD otherwise
  - B8: alignment = max(buy_weight, sell_weight) / total_weight (1d=3, 4h=2, 1h=1)
  - Single TF (fetch_fn=None): confidence = min(alignment * 0.85, 0.62)
  - 3-TF all BUY: buy_weight=1+2+3=6, total=6, alignment=1.0, conf=0.85
  - T2: 1h=HOLD, 4h=BUY, 1d=BUY: buy_weight=5, total=6, alignment=5/6=0.833, conf=0.708
  - R:R: T1=2.0x, SL=0.75x (2.67:1)

DATA ENGINEERING (diagnostically verified):
  - BUY (RSI=56.7, gap=1.4%): sinusoidal + downward drift, phase-shifted
  - SELL (RSI=40.9, gap=-1.0%): sinusoidal (neg amplitude) + upward drift
  - RSI>60 HOLD: BUY setup with large continuous uptrend -> RSI=74.9
  - fetch_fn mock: returns pre-built DataFrames for 4h/1d
"""
import sys
import inspect
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.multi_timeframe_signal)
assert 'signal_generators' not in src_file, f"Phase 4 shadowing! {src_file}"
print(f"[OK] multi_timeframe_signal: {src_file.split(chr(92))[-1]}")
print()

from market_agent.brain.multi_timeframe import multi_timeframe_signal

PASS = 0
FAIL = 0


def check(name, condition, actual_label, expected_label, extra=None):
    global PASS, FAIL
    if condition:
        PASS += 1; status = 'PASS'
    else:
        FAIL += 1; status = 'FAIL'
    print(f"  [{status}] {name}")
    print(f"    Expected : {expected_label}")
    print(f"    Got      : {actual_label}")
    if extra:
        print(f"    Details  : {extra}")
    print()


def make_df(prices, wick=0.15):
    close = pd.Series([float(p) for p in prices])
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': [1_000_000] * len(close),
    })


# ─────────────────────────────────────────────────────────────────────────────
# VERIFIED DATA BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_bullish_tf():
    """
    BUY: RSI=56.7 < 60, SMA5 > SMA20, gap=1.4%.
    Sinusoidal + downward drift, phase-shifted t2=[0.5pi, 4.5pi].
    Diagnostically verified: vote=BUY, RSI=56.7.
    """
    t2 = np.linspace(0.5 * np.pi, 4.5 * np.pi, 60)
    drift = np.linspace(0, -6, 60)   # downward drift keeps RSI moderate
    prices = 100 + drift + 2.0 * np.sin(t2)
    return make_df(prices, wick=0.15)


def build_bearish_tf():
    """
    SELL: RSI=40.9 > 40, SMA5 < SMA20, gap=-1.0%.
    Sinusoidal phase t=[1.5pi, 5.5pi], amplitude=-1.5, upward drift.
    Diagnostically verified: vote=SELL, RSI=40.9.
    """
    t = np.linspace(1.5 * np.pi, 5.5 * np.pi, 60)
    drift = np.linspace(0, 4, 60)
    prices = 100 + drift + 1.5 * np.sin(t)
    return make_df(prices, wick=0.15)


def build_overbought_tf():
    """
    RSI > 60 with SMA5 > SMA20: large continuous uptrend.
    -> brain votes HOLD (RSI gate blocks despite bullish SMA cross).
    Diagnostically verified: vote=HOLD, RSI=74.9.
    """
    t_orig = np.linspace(0, 4 * np.pi, 60)
    drift  = np.linspace(0, 6, 60)
    prices = 100 + drift + 2.0 * np.sin(t_orig)
    return make_df(prices, wick=0.15)


# ─────────────────────────────────────────────────────────────────────────────
# PRE-RUN DIAGNOSTIC
# ─────────────────────────────────────────────────────────────────────────────
from market_agent.brain.brain_utils import calc_rsi_series

def diag(df):
    close = df['Close']
    sma5  = float(close.rolling(5).mean().iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1])
    rsi   = float(calc_rsi_series(df).iloc[-1]) if len(df) > 15 else 50.0
    d = ('BUY' if sma5 > sma20 and rsi < 60 else
         'SELL' if sma5 < sma20 and rsi > 40 else 'HOLD')
    return d, round(rsi, 1), round((sma5 - sma20) / sma20 * 100, 3) if sma20 > 0 else 0

print("Pre-run diagnostic:")
for name, df in [('bullish', build_bullish_tf()),
                 ('bearish', build_bearish_tf()),
                 ('overbought', build_overbought_tf())]:
    d, rsi, gap = diag(df)
    print(f"  {name:12s}: vote={d:4s}  RSI={rsi:.0f}  gap={gap:.3f}%")
print()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("BRAIN 3: Multi-Timeframe -- All 4 Scenarios")
print("=" * 65)
print()

# ── Test 1: 3-TF full BUY alignment → confidence = 0.85 ──────────────────────
print("Test 1: 3-TF full alignment -> BUY, confidence = 0.85")
df_1h = build_bullish_tf()
df_4h = build_bullish_tf()
df_1d = build_bullish_tf()

def fetch_fn_all_bullish(symbol, interval, period):
    return df_4h if interval == '4h' else df_1d

r1 = multi_timeframe_signal('BTC-USD', df_1h, fetch_fn=fetch_fn_all_bullish)
# build_bullish_tf() cross_age=6 -> stale penalty (6-5)*0.02=0.02 -> 0.85-0.02=0.830
check('3-TF BUY alignment',
      r1.direction == 'BUY' and abs(r1.confidence - 0.830) < 0.01,
      f"direction={r1.direction}  conf={r1.confidence:.3f}  strength={r1.signal_strength:.2f}",
      'BUY with confidence=0.830 (3/3 alignment=1.0, age=6 stale penalty -0.02)',
      r1.primary_evidence)

# BF-13: T1 reduced from 2.0 to 1.0 (breakeven WR raised to 43%)
check('R:R correct (1.33:1)',
      r1.rr_t1_mult == 1.0 and r1.rr_sl_mult == 0.75,
      f"rr_t1={r1.rr_t1_mult}  rr_sl={r1.rr_sl_mult}  ratio={r1.rr_t1_mult/r1.rr_sl_mult:.2f}:1",
      'rr_t1_mult=1.0, rr_sl_mult=0.75 (1.33:1) [BF-13]')

# ── Test 2: RSI gate — 1h RSI > 60 → HOLD on 1h, 2/3 BUY ─────────────────────
print("Test 2: RSI gate -- 1h overbought (RSI>60) -> 1h votes HOLD, 2/3 BUY")
df_1h_ob = build_overbought_tf()  # RSI=74.9 -> HOLD despite bullish SMA
df_4h_b  = build_bullish_tf()     # RSI=56.7 -> BUY
df_1d_b  = build_bullish_tf()     # RSI=56.7 -> BUY

def fetch_fn_2of3(symbol, interval, period):
    return df_4h_b if interval == '4h' else df_1d_b

r2 = multi_timeframe_signal('BTC-USD', df_1h_ob, fetch_fn=fetch_fn_2of3)
# B8: 1h=HOLD(w=1), 4h=BUY(w=2), 1d=BUY(w=3)
# buy_weight=5, total_weight=6, alignment=5/6=0.833 -> conf=0.833*0.85=0.708
expected_conf = round(5 / 6 * 0.85, 3)   # 0.708 with B8 weights
check('RSI gate blocks 1h -> 2/3 BUY alignment (B8 weighted)',
      r2.direction == 'BUY' and abs(r2.confidence - expected_conf) < 0.02,
      f"direction={r2.direction}  conf={r2.confidence:.3f}  (expected ~{expected_conf:.3f})",
      f'BUY with conf~{expected_conf:.3f} (B8: 1h HOLD w=1, 4h+1d BUY w=5/6)',
      r2.primary_evidence)

# ── Test 3: fetch_fn=None → single-TF, confidence capped at 0.62 ─────────────
print("Test 3: fetch_fn=None -> single-TF only -> confidence capped at 0.62")
df_single = build_bullish_tf()
r3 = multi_timeframe_signal('BTC-USD', df_single, fetch_fn=None)
# 1h=BUY only, alignment=1.0/1=1.0, singles cap: min(1.0*0.85, 0.62) = 0.62
check('Single-TF confidence capped at 0.62',
      r3.direction == 'BUY' and r3.confidence <= 0.62,
      f"direction={r3.direction}  conf={r3.confidence:.3f}  (max_allowed=0.62)",
      'BUY with confidence <= 0.62 (single-TF honesty cap)',
      r3.reliability_flags)

check('missing_timeframes=True when fetch_fn=None',
      r3.reliability_flags.get('missing_timeframes') is True,
      f"missing_timeframes={r3.reliability_flags.get('missing_timeframes')}",
      'missing_timeframes=True')

# ── Test 4: Mixed TFs — 1/2 BUY, 1/2 SELL → tie → HOLD ──────────────────────
print("Test 4: Mixed TF signals -> 1/2 BUY, 1/2 SELL -> HOLD")
df_bull = build_bullish_tf()
df_bear = build_bearish_tf()

# fetch_fn returns bearish for 4h, None for 1d (so only 2 TFs)
def fetch_fn_mixed(symbol, interval, period):
    if interval == '4h':
        return df_bear  # votes SELL
    return None         # 1d unavailable -> only 2 TFs used

r4 = multi_timeframe_signal('BTC-USD', df_bull, fetch_fn=fetch_fn_mixed)
# B8 weights: 1h=BUY(w=1), 4h=SELL(w=2) -> sell_weight(2) > buy_weight(1) -> SELL
# (simple count would be 1 BUY vs 1 SELL = tie = HOLD, but weighted is SELL)
check('Mixed TF with B8 weights -> SELL (4h weight > 1h)',
      r4.direction == 'SELL',
      f"direction={r4.direction}  conf={r4.confidence:.3f}",
      'SELL: 4h SELL(w=2) outweighs 1h BUY(w=1) with B8 weighting',
      r4.primary_evidence)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"Multi-Timeframe: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
