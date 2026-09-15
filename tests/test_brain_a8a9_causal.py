"""
Test A8/A9: Causal-Ensemble Improvements
==========================================
A8: Squeeze breakout needs 5 consecutive candles (raised from 3).
    - 4-candle upward sequence in squeeze -> NEUTRAL (no longer triggers BUY)
    - 5-candle upward sequence in squeeze -> BUY
A9: WEAK tier BUY requires RSI rising (RSI[-1] > RSI[-3]).
    - pct_b<0.20 + RSI rising  -> BUY (WEAK)
    - pct_b<0.20 + RSI falling -> HOLD (blocked)
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')
import market_agent.brain.causal_ensemble as ce_module
from market_agent.brain.causal_ensemble import (
    causal_ensemble_signal, _squeeze_breakout_direction
)

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


def build_squeeze_base(n=80, base=100.0):
    """Flat price + tiny wicks -> narrow BB -> is_squeeze=True."""
    closes = [base + 0.01 * (i % 4 - 1.5) for i in range(n)]
    highs  = [c + 0.05 for c in closes]
    lows   = [c - 0.05 for c in closes]
    return pd.DataFrame({
        'Open': closes, 'High': highs, 'Low': lows, 'Close': closes,
        'Volume': [1_000_000] * n,
    })


def build_4candle_squeeze_up(n=80, base=100.0):
    """4 strictly up candles + bar -5 == bar -4 -> 5-bar chain broken -> NEUTRAL."""
    df = build_squeeze_base(n, base)
    close = list(df['Close'])
    close[-1] = base + 0.4
    close[-2] = base + 0.3
    close[-3] = base + 0.2
    close[-4] = base + 0.1
    close[-5] = base + 0.1    # tied with -4 -> not strictly monotone over 5
    df['Close'] = close; df['Open'] = close
    df['High']  = [c + 0.05 for c in close]
    df['Low']   = [c - 0.05 for c in close]
    return df


def build_5candle_squeeze_up(n=80, base=100.0):
    """5 strictly rising candles -> triggers A8 BUY."""
    df = build_squeeze_base(n, base)
    close = list(df['Close'])
    close[-1] = base + 0.5
    close[-2] = base + 0.4
    close[-3] = base + 0.3
    close[-4] = base + 0.2
    close[-5] = base + 0.1
    df['Close'] = close; df['Open'] = close
    df['High']  = [c + 0.05 for c in close]
    df['Low']   = [c - 0.05 for c in close]
    return df


def build_weak_buy_hist(n=80, base=100.0):
    """Gentle downtrend -> price near lower BB (pct_b ~0.10-0.20). RSI controlled via patch."""
    closes = [base - i * 0.2 for i in range(n)]
    highs  = [c + 2.0 for c in closes]
    lows   = [c - 2.0 for c in closes]
    return pd.DataFrame({
        'Open': closes, 'High': highs, 'Low': lows, 'Close': closes,
        'Volume': [1_000_000] * n,
    })


def make_rsi_patch(last_val, prev3_val):
    """Returns a function that gives a Series with rsi[-1]=last_val, rsi[-3]=prev3_val."""
    def _rsi(hist, period=14):
        n = len(hist)
        vals = [50.0] * n
        if n >= 1: vals[-1]  = last_val
        if n >= 3: vals[-3]  = prev3_val
        return pd.Series(vals)
    return _rsi


orig_rsi_series = ce_module.calc_rsi_series

print("=" * 65)
print("A8/A9: Causal-Ensemble Improvements")
print("=" * 65)
print()

# ── A8: 5-candle squeeze requirement ─────────────────────────────────────────
print("--- A8: 5-candle Squeeze Breakout ---")

print("T1: 4 rising candles (bar-5 == bar-4) -> NEUTRAL")
df_4c = build_4candle_squeeze_up()
sq4 = _squeeze_breakout_direction(df_4c)
check('4-candle up -> NEUTRAL',
      sq4 == 'NEUTRAL',
      f"_squeeze_breakout_direction = {sq4!r}",
      "NEUTRAL (need 5 strictly monotone candles)")

print("T2: 5 strictly rising candles + above midband -> BUY")
df_5c = build_5candle_squeeze_up()
sq5 = _squeeze_breakout_direction(df_5c)
check('5-candle up -> BUY',
      sq5 == 'BUY',
      f"_squeeze_breakout_direction = {sq5!r}",
      "BUY (5 consecutive closes strictly rising, above midband)")

# ── A9: WEAK tier RSI direction gate ─────────────────────────────────────────
print("--- A9: WEAK Tier RSI Direction Gate ---")

hist_weak = build_weak_buy_hist()

print("T3: pct_b<0.20 + RSI RISING (41->44) -> WEAK BUY fires")
ce_module.calc_rsi_series = make_rsi_patch(last_val=44.0, prev3_val=41.0)
r_up = causal_ensemble_signal(hist_weak)
check('WEAK BUY + RSI rising -> BUY with conf=0.58',
      r_up.direction == 'BUY' and r_up.confidence == 0.58,
      f"direction={r_up.direction}  conf={r_up.confidence}  rsi={r_up.measurements.get('rsi')}",
      "direction=BUY, confidence=0.58")

print("T4: pct_b<0.20 + RSI FALLING (44->41) -> WEAK BUY blocked -> HOLD")
ce_module.calc_rsi_series = make_rsi_patch(last_val=41.0, prev3_val=44.0)
r_dn = causal_ensemble_signal(hist_weak)
check('WEAK BUY + RSI falling -> HOLD',
      r_dn.direction == 'HOLD',
      f"direction={r_dn.direction}  conf={r_dn.confidence}  rsi={r_dn.measurements.get('rsi')}",
      "direction=HOLD (RSI direction blocks WEAK BUY)")

ce_module.calc_rsi_series = orig_rsi_series   # always restore

print("=" * 65)
print(f"A8/A9 Tests: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
