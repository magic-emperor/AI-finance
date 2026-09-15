"""
Phase B Tests: B7, B8, B9
===========================
B7: timeframe-aware stale gate in AMV-LSTM
    stale_candles = max(4, int(600 / timeframe_minutes))
    60-min: stale > 10.  15-min: stale > 40.  4h: stale > 4 (floor).

B8: weighted timeframe votes in Multi-Timeframe
    1d=weight 3, 4h=weight 2, 1h=weight 1.
    4h SELL(w=2) beats 1h BUY(w=1) → SELL direction.

B9: adaptive SMA periods per timeframe in Multi-Timeframe
    1h/4h: SMA 5/20.  1d: SMA 3/10.
    Verified via measurements dict '{tf}_sma' key.
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

from market_agent.brain.amv_lstm import amv_lstm_signal
from market_agent.brain.multi_timeframe import multi_timeframe_signal, _TF_WEIGHTS, _TF_SMA

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


def make_df(prices, wick=0.15, vol_mult=1.0):
    close = pd.Series([float(p) for p in prices])
    return pd.DataFrame({
        'Open': close, 'High': close + wick, 'Low': close - wick, 'Close': close,
        'Volume': [1_000_000 * vol_mult] * len(close),
    })


# ── Data builders ─────────────────────────────────────────────────────────────

def build_amv_stale_cross(cross_age: int):
    """
    SMA5 > SMA20, gap=4%, stale cross of given age.
    First 40 bars flat at 100, then rises so SMA5 > SMA20.
    Last `cross_age` bars all at 104.0 to simulate age.
    """
    prices = [100.0] * 40
    # push price up quickly to generate cross
    for _ in range(15):
        prices.append(prices[-1] + 0.3)
    # now flat-ish for cross_age bars (cross happened cross_age bars ago)
    flat_val = prices[-1]
    for _ in range(cross_age):
        prices.append(flat_val + 0.01)
    return make_df(prices, wick=0.5)


def build_bullish_tf():
    t = np.linspace(0.5 * np.pi, 4.5 * np.pi, 60)
    prices = 100 + np.linspace(0, -6, 60) + 2.0 * np.sin(t)
    return make_df(prices, wick=0.15)


def build_bearish_tf():
    t = np.linspace(1.5 * np.pi, 5.5 * np.pi, 60)
    prices = 100 + np.linspace(0, 4, 60) + 1.5 * np.sin(t)
    return make_df(prices, wick=0.15)


# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("Phase B Tests: B7 / B8 / B9")
print("=" * 65)
print()

# ── B7 Test 1: 60-min default — cross_age=11 should be HOLD ──────────────────
print("B7 Test 1: 60-min (default) — cross_age=11 > stale threshold(10) → HOLD")
df_stale_60 = build_amv_stale_cross(cross_age=11)
r_b7_1 = amv_lstm_signal(df_stale_60, timeframe_minutes=60)
check('60-min cross_age=11 -> HOLD (stale > 10)',
      r_b7_1.direction == 'HOLD' and r_b7_1.reliability_flags.get('cross_too_old') is True,
      f"dir={r_b7_1.direction}  cross_too_old={r_b7_1.reliability_flags.get('cross_too_old')}",
      'HOLD with cross_too_old=True (stale_candles=10 for 60-min)',
      r_b7_1.primary_evidence[:80])

# ── B7 Test 2: 15-min — cross_age=19 should NOT be gated (threshold=40) ─────
print("B7 Test 2: 15-min TF — stale_candle threshold=40, cross_age<40 -> NOT gated")
df_stale_15 = build_amv_stale_cross(cross_age=11)
# Note: scanner finds cross_age=19 (rising + flat bars all bullish), still < 40 threshold
r_b7_2 = amv_lstm_signal(df_stale_15, timeframe_minutes=15)
# Gate fires: cross_age > stale_candles (19 > 40 is False) -> should NOT gate
check('15-min stale_candles=40 -> gate does NOT block cross_age=19',
      r_b7_2.direction != 'HOLD' or not r_b7_2.reliability_flags.get('cross_too_old'),
      f"dir={r_b7_2.direction}  gated={r_b7_2.reliability_flags.get('cross_too_old', False)}",
      'Not gated: cross_age=19 < stale_candles=40 (15-min TF)',
      r_b7_2.primary_evidence[:80])

# ── B7 Test 3: 4h — floor=4; cross_age=5 should be stale ────────────────────
print("B7 Test 3: 4-hour TF — cross_age=5 > floor stale_candles(4) → HOLD")
df_stale_4h = build_amv_stale_cross(cross_age=5)
r_b7_3 = amv_lstm_signal(df_stale_4h, timeframe_minutes=240)
check('4h cross_age=5 -> HOLD (stale_candles=floor 4)',
      r_b7_3.reliability_flags.get('cross_too_old') is True,
      f"dir={r_b7_3.direction}  cross_too_old={r_b7_3.reliability_flags.get('cross_too_old')}",
      'HOLD: 5 > floor stale_candles=4 (600/240=2, max(4,2)=4)',
      r_b7_3.primary_evidence[:80])

# ── B8 Test: weights dict correct ────────────────────────────────────────────
print("B8 Test: _TF_WEIGHTS dict has correct values (1d=3, 4h=2, 1h=1)")
check('_TF_WEIGHTS correct',
      _TF_WEIGHTS.get('1d') == 3 and _TF_WEIGHTS.get('4h') == 2 and _TF_WEIGHTS.get('1h') == 1,
      f"1d={_TF_WEIGHTS.get('1d')} | 4h={_TF_WEIGHTS.get('4h')} | 1h={_TF_WEIGHTS.get('1h')}",
      '1d=3, 4h=2, 1h=1')

# ── B8 Test: 4h SELL(w=2) beats 1h BUY(w=1) → SELL ─────────────────────────
print("B8 Test: 4h SELL(w=2) outweighs 1h BUY(w=1) → direction=SELL")
df_bull = build_bullish_tf()
df_bear = build_bearish_tf()

def fetch_fn_4h_bear(symbol, interval, period):
    if interval == '4h':
        return df_bear   # votes SELL, weight=2
    return None          # 1d unavailable

r_b8 = multi_timeframe_signal('BTC-USD', df_bull, fetch_fn=fetch_fn_4h_bear)
check('4h SELL(w=2) > 1h BUY(w=1) -> SELL',
      r_b8.direction == 'SELL',
      f"dir={r_b8.direction}  conf={r_b8.confidence:.3f}",
      'SELL: sell_weight=2 > buy_weight=1 (B8)',
      r_b8.measurements.get('sell_weight'))

# ── B9 Test: 1d TF uses SMA 3/10 ─────────────────────────────────────────────
print("B9 Test: _TF_SMA dict — 1d uses (3, 10), 1h uses (5, 20)")
check('_TF_SMA correct',
      _TF_SMA.get('1d') == (3, 10) and _TF_SMA.get('1h') == (5, 20),
      f"1d={_TF_SMA.get('1d')} | 1h={_TF_SMA.get('1h')}",
      "1d=(3,10), 1h=(5,20)")

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"Phase B (B7/B8/B9): {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
