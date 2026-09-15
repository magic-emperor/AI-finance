"""
Test A1: AMV-LSTM -- Volume Confirmation (vol_ratio)
====================================================
Builder: Identical to test_brain_02 build_valid_buy()
  50 bars down (-0.4/bar) + 10 bars up (+1.0/bar)
  -> cross_age=8, direction=BUY, gap=4.833% (all verified in test_brain_02)

A1 spec:
  T1: vol_ratio >= 1.5 -> base_conf += 0.04 (high-vol confirmation)
  T2: vol_ratio < 0.6  -> reliability_flags['low_volume_cross'] = True
  T3: vol_ratio = 1.0  -> no boost, no flag
  T4: vol_ratio in measurements dict
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')
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


def build_valid_buy_vol(vol_multiplier=1.0):
    """
    Exact copy of test_brain_02 build_valid_buy() (cross_age=8, BUY verified).
    Volume column: avg = 1_000_000. Last bar = 1_000_000 * vol_multiplier.
    vol_avg from brain = mean of last 20 bars.
    The 20-bar avg includes the spike bar itself, so:
      vol_avg  = (19 * 1_000_000 + 1_000_000 * vol_mult) / 20
      vol_curr = 1_000_000 * vol_mult
      vol_ratio = vol_curr / vol_avg = 20*vol_mult / (19 + vol_mult)
    For vol_mult=2.0: vol_ratio = 40/21 = 1.905  (>= 1.5 -> BOOST)
    For vol_mult=0.3: vol_ratio = 6/19.3 = 0.311  (< 0.6 -> FLAG)
    For vol_mult=1.0: vol_ratio = 20/20  = 1.000  (neutral)
    """
    prices = []
    p = 100.0
    for _ in range(50): p -= 0.4; prices.append(p)    # 50 down
    for _ in range(10): p += 1.0; prices.append(p)    # 10 up
    n = len(prices)
    avg_vol = 1_000_000.0
    volumes = [avg_vol] * n
    volumes[-1] = avg_vol * vol_multiplier   # vary the last bar
    close = pd.Series(prices)
    return pd.DataFrame({
        'Open': close, 'High': close + 0.3,
        'Low':  close - 0.3, 'Close': close,
        'Volume': volumes,
    })


print("Pre-flight: verifying builder produces valid BUY...")
r_pf = amv_lstm_signal(build_valid_buy_vol(1.0))
m_pf = r_pf.measurements
print(f"  direction={r_pf.direction}  cross_age={m_pf.get('cross_age')}  "
      f"gap={m_pf.get('sma_gap_pct',0):.3f}%  vol_ratio={m_pf.get('vol_ratio')}")
if r_pf.direction == 'HOLD':
    print("  HOLD produced -- builder verification failed!")
    sys.exit(1)
print()

print("=" * 65)
print("A1: AMV-LSTM -- Volume Confirmation (vol_ratio)")
print("=" * 65)
print()

conf_neutral = r_pf.confidence   # baseline with vol_multiplier=1.0

# T1: high volume (2.0x) -> +0.04 boost
print("T1: vol=2.0x (>= 1.5x avg) -> confidence += 0.04")
r_hi  = amv_lstm_signal(build_valid_buy_vol(2.0))
m_hi  = r_hi.measurements
vr_hi = m_hi.get('vol_ratio', 0)
boost = round(r_hi.confidence - conf_neutral, 4)
check('High volume (2.0x) boosts confidence +0.04',
      vr_hi >= 1.5 and abs(boost - 0.04) < 0.001,
      f"vol_ratio={vr_hi:.3f}  boost={boost:+.4f} (neutral={conf_neutral:.4f})",
      "vol_ratio>=1.5 -> confidence += 0.04 exactly")

# T2: low volume (0.3x) -> low_volume_cross flag
print("T2: vol=0.3x (< 0.6x avg) -> low_volume_cross=True")
r_lo  = amv_lstm_signal(build_valid_buy_vol(0.3))
m_lo  = r_lo.measurements
vr_lo = m_lo.get('vol_ratio', 1.0)
flag  = r_lo.reliability_flags.get('low_volume_cross', False)
check('Low volume (0.3x) sets low_volume_cross=True',
      vr_lo < 0.6 and flag is True,
      f"vol_ratio={vr_lo:.3f}  low_volume_cross={flag}",
      "vol_ratio<0.6 -> reliability_flags['low_volume_cross']=True")

# T3: neutral (1.0x) -> no boost, no flag
print("T3: vol=1.0x (neutral) -> no boost, no flag")
neutral_flag = r_pf.reliability_flags.get('low_volume_cross', 'MISSING')
check('Neutral volume: no boost, no flag',
      neutral_flag is False and m_pf.get('vol_ratio') == 1.0,
      f"vol_ratio={m_pf.get('vol_ratio')}  low_volume_cross={neutral_flag}",
      "vol_ratio=1.0 -> no boost, low_volume_cross=False")

# T4: vol_ratio present in measurements
print("T4: vol_ratio in measurements")
check("vol_ratio in measurements dict",
      'vol_ratio' in m_hi,
      f"vol_ratio present: {'vol_ratio' in m_hi}  value={m_hi.get('vol_ratio')}",
      "'vol_ratio' key in measurements")

print("=" * 65)
print(f"A1 Tests: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
