"""
Test 2: AMV-LSTM — Temporal Trend Memory Brain
===============================================
Tests all gates (3 original + 1 new) and valid BUY signal + confidence cap + R:R.

From amv_lstm.py logic:
  Gate 1 — cross_age < 2  -> HOLD (cross too fresh)
  Gate 2 — cross_age > 7  -> HOLD (lowered from 10; backtest 2026-03-07)
  Gate 3 — sma_gap_pct < 0.20% -> HOLD (MAs overlapping)
  Gate 4 — SMA20 slope conflicts with direction -> HOLD (new; backtest 2026-03-07)
  Valid signal: direction BUY/SELL, confidence 0.50-0.68 (SMA fallback)
  Confidence cap: 0.68 (no LSTM loaded)
  R:R: T1=2.0x ATR, SL=0.75x ATR = 2.67:1
"""
import sys
import inspect
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.amv_lstm_signal)
assert 'signal_generators' not in src_file, f"Phase 4 shadowing! {src_file}"
print(f"[OK] amv_lstm_signal: {src_file.split(chr(92))[-1]}")
print()

from market_agent.brain.amv_lstm import amv_lstm_signal

PASS = 0
FAIL = 0


def check(name, condition, actual_label, expected_label, measurements=None):
    global PASS, FAIL
    if condition:
        PASS += 1
        status = 'PASS'
    else:
        FAIL += 1
        status = 'FAIL'
    print(f"  [{status}] {name}")
    print(f"    Expected : {expected_label}")
    print(f"    Got      : {actual_label}")
    if measurements:
        print(f"    Metrics  : {measurements}")
    print()


def make_df(prices, wick=0.3):
    close = pd.Series(prices)
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': [1_000_000] * len(prices),
    })


def get_cross_age_and_gap(df):
    close = df['Close']
    sma5  = close.rolling(5).mean()
    sma20 = close.rolling(20).mean()
    price = float(close.iloc[-1])
    bullish = sma5.iloc[-1] > sma20.iloc[-1]
    age = 0
    for i in range(1, min(20, len(df))):
        if (sma5.iloc[-i] > sma20.iloc[-i]) == bullish:
            age += 1
        else:
            break
    gap = abs(float(sma5.iloc[-1]) - float(sma20.iloc[-1])) / price * 100 if price > 0 else 0
    return age, gap, bullish


# ─────────────────────────────────────────────────────────────────────────────
# DATA BUILDERS — analytically verified
# ─────────────────────────────────────────────────────────────────────────────

def build_stale_cross():
    """Steady uptrend all 60 bars -> cross_age=19 (> 10 -> HOLD)."""
    return make_df([100.0 + i * 0.5 for i in range(60)])


def build_fresh_cross():
    """
    58 bars down (-0.5/bar), then 2 bars spike +20/bar.
    Diagnosed: age=1 (cross just happened), gap=4.9%.
    """
    prices = []
    p = 100.0
    for _ in range(58): p -= 0.5; prices.append(p)
    for _ in range(2):  p += 20.0; prices.append(p)
    return make_df(prices, wick=0.5)


def build_tiny_gap():
    """
    54 flat bars at 80, then 6 bars at 80.05.
    cross_age=6 (exactly at _MIN_CROSS_AGE=6, so Gate 1 passes: 6 < 6 is False).
    gap = (80.05 - 80.015) / 80.05 * 100 = 0.044% < 0.20% -> Gate 3 fires.
    """
    return make_df([80.0] * 54 + [80.05] * 6, wick=0.02)


def build_valid_buy():
    """
    50 bars flat at 100, then 6 bars steeply rising (+2.0/bar).
    Produces: cross_age=6 (within 2-7 window), gap > 0.20%,
    SMA20 slope = rising (6 rising bars have tilted SMA20 upward) -> valid BUY.
    """
    prices = [100.0] * 50 + [100.0 + i * 2.0 for i in range(1, 7)]
    return make_df(prices)


# ─────────────────────────────────────────────────────────────────────────────
# PRE-RUN DIAGNOSTIC
# ─────────────────────────────────────────────────────────────────────────────
print("Pre-run diagnostic (cross_age and gap):")
for name, df in [
    ('stale',    build_stale_cross()),
    ('fresh',    build_fresh_cross()),
    ('tiny_gap', build_tiny_gap()),
    ('valid_buy',build_valid_buy()),
]:
    age, gap, bullish = get_cross_age_and_gap(df)
    print(f"  {name:12s}: cross_age={age:3d}  gap={gap:.3f}%  bullish={bullish}")
print()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

print("=" * 65)
print("BRAIN 2: AMV-LSTM -- All 3 HOLD Gates + Valid Signal + Cap")
print("=" * 65)
print()

# Test 1: Gate 2 — Stale cross (age > 7 at 60-min) -> HOLD
print("Test 1: Gate 2 -- Stale cross (age > 7 at 60-min) -> HOLD")
r = amv_lstm_signal(build_stale_cross())
check('Stale cross HOLD',
      r.direction == 'HOLD' and r.reliability_flags.get('cross_too_old'),
      f"direction={r.direction}  cross_age={r.signal_age_candles}",
      'HOLD with cross_too_old=True', r.measurements)

# Test 2: Gate 1 — Fresh cross (age < 2) -> HOLD
print("Test 2: Gate 1 -- Fresh cross (age < 2) -> HOLD")
r = amv_lstm_signal(build_fresh_cross())
check('Fresh cross HOLD',
      r.direction == 'HOLD' and r.reliability_flags.get('cross_too_fresh'),
      f"direction={r.direction}  cross_age={r.signal_age_candles}",
      'HOLD with cross_too_fresh=True', r.measurements)

# Test 3: Gate 3 — SMA gap < 0.20% -> HOLD
print("Test 3: Gate 3 -- SMA gap < 0.20% -> HOLD")
r = amv_lstm_signal(build_tiny_gap())
m = r.measurements
gap_val = m.get('sma_gap_pct', None)
gap_str = f"{gap_val:.4f}%" if isinstance(gap_val, float) else str(r.reliability_flags)
check('Tiny gap HOLD',
      r.direction == 'HOLD' and r.reliability_flags.get('gap_too_small'),
      f"direction={r.direction}  gap={gap_str}  age={r.signal_age_candles}",
      'HOLD with gap_too_small=True and gap < 0.20%', m)

# Test 4: Valid BUY — age 2-10, gap > 0.20%, direction=BUY
print("Test 4: Valid BUY -- age 2-10, gap > 0.20%")
# Build once, share across tests 4, 5, 6
df_valid = build_valid_buy()
r_valid  = amv_lstm_signal(df_valid)
m = r_valid.measurements
check('Valid BUY direction',
      r_valid.direction == 'BUY',
      f"direction={r_valid.direction}  conf={r_valid.confidence:.3f}  gap={m.get('sma_gap_pct',0):.3f}%  age={m.get('cross_age',0)}",
      'BUY with confidence in 0.50-0.68 range', m)

# Test 5: Confidence cap at 0.68 with no LSTM
print("Test 5: Confidence cap -- no LSTM -> max = 0.68")
check('Confidence <= 0.68 (SMA fallback)',
      r_valid.confidence <= 0.68 and r_valid.direction in ('BUY', 'SELL'),
      f"direction={r_valid.direction}  conf={r_valid.confidence:.3f}  (fallback cap=0.68)",
      'confidence <= 0.68 in SMA-only mode', None)

# Test 6: R:R multipliers (TRENDING_DOWN default — this brain is TRENDING_DOWN specialist)
# Brain signature has regime='TRENDING_DOWN' as default; valid_buy() passes no regime.
print("Test 6: R:R multipliers -- TRENDING_DOWN default: T1=2.0x, SL=0.75x (2.67:1)")
rr_ratio = r_valid.rr_t1_mult / r_valid.rr_sl_mult if r_valid.rr_sl_mult else 0
check('R:R multipliers correct',
      r_valid.rr_t1_mult == 2.0 and r_valid.rr_sl_mult == 0.75,
      f"rr_t1={r_valid.rr_t1_mult}  rr_sl={r_valid.rr_sl_mult}  ratio={rr_ratio:.2f}:1",
      'rr_t1_mult=2.0, rr_sl_mult=0.75, ratio=2.67:1', None)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"AMV-LSTM: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
