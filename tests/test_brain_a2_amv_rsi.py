"""
Test A2: AMV-LSTM -- RSI State Filter
======================================
A2 spec: RSI adjustments applied to base_conf BEFORE cap:
  BUY: RSI > 70 -> -0.10 | RSI > 60 -> -0.05 | RSI < 40 -> +0.04
  SELL: RSI < 30 -> -0.10 | RSI < 40 -> -0.05 | RSI > 60 -> +0.04

APPROACH: Use brain_utils.calc_rsi_float monkeypatching in amv_lstm module
so we can inject exact RSI values without needing to engineer specific price
sequences that reliably produce a given RSI.
"""
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.amv_lstm as amv_module
from market_agent.brain.amv_lstm import amv_lstm_signal

PASS = 0
FAIL = 0


def check(name, condition, actual, expected):
    global PASS, FAIL
    if condition:
        PASS += 1; status = 'PASS'
    else:
        FAIL += 1; status = 'FAIL'
    print(f"  [{status}] {name}")
    print(f"    Expected : {expected}")
    print(f"    Got      : {actual}")
    print()


# --- Verified builder from test_brain_02: 50 down + 10 up = cross_age=6, BUY ---
def build_valid_buy():
    prices = []
    p = 100.0
    for _ in range(50): p -= 0.4; prices.append(p)
    for _ in range(10): p += 1.0; prices.append(p)
    close = pd.Series(prices)
    return pd.DataFrame({
        'Open': close, 'High': close + 0.3,
        'Low':  close - 0.3, 'Close': close,
        'Volume': [1_000_000] * len(prices),
    })


# Builder that produces SELL direction: 50 up + 10 down bars
def build_valid_sell():
    prices = []
    p = 100.0
    for _ in range(50): p += 0.4; prices.append(p)
    for _ in range(10): p -= 1.0; prices.append(p)
    close = pd.Series(prices)
    return pd.DataFrame({
        'Open': close, 'High': close + 0.3,
        'Low':  close - 0.3, 'Close': close,
        'Volume': [1_000_000] * len(prices),
    })


hist_buy  = build_valid_buy()
hist_sell = build_valid_sell()

# Pre-flight
r_pf = amv_lstm_signal(hist_buy)
if r_pf.direction == 'HOLD':
    print("Pre-flight FAIL: builder returned HOLD")
    sys.exit(1)
# Baseline confidence with whatever RSI is natural
conf_natural = r_pf.confidence

orig_rsi = amv_module.calc_rsi_float   # save original

print("=" * 65)
print("A2: AMV-LSTM -- RSI State Filter")
print("=" * 65)
print()

# T1: BUY with RSI=72 -> -0.10 reduction vs RSI=50 (neutral)
print("T1: BUY + RSI=72 (>70) -> conf reduced by 0.10 vs RSI=50")
amv_module.calc_rsi_float = lambda hist, period=14: 50.0   # neutral baseline
r_rsi50  = amv_lstm_signal(hist_buy)
conf_50  = r_rsi50.confidence

amv_module.calc_rsi_float = lambda hist, period=14: 72.0   # overbought
r_rsi72  = amv_lstm_signal(hist_buy)
conf_72  = r_rsi72.confidence
reduction = round(conf_50 - conf_72, 4)

check('BUY + RSI=72 reduces conf by 0.10',
      abs(reduction - 0.10) < 0.001,
      f"RSI=50 conf={conf_50:.4f}  RSI=72 conf={conf_72:.4f}  reduction={reduction:.4f}",
      "conf(RSI=72) = conf(RSI=50) - 0.10")

# T2: BUY with RSI=65 -> -0.05 reduction
print("T2: BUY + RSI=65 (60<RSI<=70) -> conf reduced by 0.05 vs RSI=50")
amv_module.calc_rsi_float = lambda hist, period=14: 65.0
r_rsi65  = amv_lstm_signal(hist_buy)
conf_65  = r_rsi65.confidence
reduction_65 = round(conf_50 - conf_65, 4)

check('BUY + RSI=65 reduces conf by 0.05',
      abs(reduction_65 - 0.05) < 0.001,
      f"RSI=50 conf={conf_50:.4f}  RSI=65 conf={conf_65:.4f}  reduction={reduction_65:.4f}",
      "conf(RSI=65) = conf(RSI=50) - 0.05")

# T3: BUY with RSI=35 (<40) -> +0.04 boost
print("T3: BUY + RSI=35 (<40) -> conf boosted by 0.04 vs RSI=50")
amv_module.calc_rsi_float = lambda hist, period=14: 35.0
r_rsi35  = amv_lstm_signal(hist_buy)
conf_35  = r_rsi35.confidence
boost_35 = round(conf_35 - conf_50, 4)

check('BUY + RSI=35 boosts conf by 0.04',
      abs(boost_35 - 0.04) < 0.001,
      f"RSI=50 conf={conf_50:.4f}  RSI=35 conf={conf_35:.4f}  boost={boost_35:.4f}",
      "conf(RSI=35) = conf(RSI=50) + 0.04")

# T4: SELL with RSI=28 (<30) -> -0.10 reduction
print("T4: SELL + RSI=28 (<30) -> conf reduced by 0.10 vs RSI=50")
amv_module.calc_rsi_float = lambda hist, period=14: 50.0
r_sell50  = amv_lstm_signal(hist_sell)
conf_sell50 = r_sell50.confidence

amv_module.calc_rsi_float = lambda hist, period=14: 28.0
r_sell28  = amv_lstm_signal(hist_sell)
conf_sell28 = r_sell28.confidence
reduction_sell = round(conf_sell50 - conf_sell28, 4)

check('SELL + RSI=28 reduces conf by 0.10',
      abs(reduction_sell - 0.10) < 0.001 and r_sell28.direction == 'SELL',
      f"SELL RSI=50 conf={conf_sell50:.4f}  RSI=28 conf={conf_sell28:.4f}  reduction={reduction_sell:.4f}",
      "SELL: conf(RSI=28) = conf(RSI=50) - 0.10")

# T5: RSI in measurements
print("T5: rsi appears in measurements")
amv_module.calc_rsi_float = lambda hist, period=14: 55.0
r_meas = amv_lstm_signal(hist_buy)
check("rsi in measurements",
      'rsi' in r_meas.measurements and r_meas.measurements['rsi'] == 55.0,
      f"measurements['rsi'] = {r_meas.measurements.get('rsi')}",
      "rsi=55.0 in measurements dict")

# Always restore
amv_module.calc_rsi_float = orig_rsi

print("=" * 65)
print(f"A2 Tests: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
