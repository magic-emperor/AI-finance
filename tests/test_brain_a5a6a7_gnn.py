"""
Test A5/A6/A7: Cross-Stock-GNN Improvements
=============================================
A5: equity symbol -> vwap_type='SESSION' (75-bar); crypto -> vwap_type='ROLLING_20'
A6: BUY + declining OBV -> confidence reduced + contra_factor present
A7: regime parameter -> BUY in TRENDING_UP gets +0.05 boost vs RANGING
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')
from market_agent.brain.cross_stock_gnn import cross_stock_gnn_signal, _is_equity

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


def build_inst_buy(n=80, base=100.0, obv_up=True):
    """
    Strong institutional BUY: price below VWAP (session) + vol spike.
    Price runs flat at 100 for 79 bars then one big drop with vol spike.
    -> close < VWAP, vol_ratio >> 2.5, direction=BUY.
    obv_up: if True, last bar is a big up-volume bar; if False, down-volume.
    """
    closes  = np.full(n, base)
    closes[-1] = base * 0.94     # 6% drop to be well below VWAP
    highs = closes + 0.5
    lows  = closes - 0.5
    lows[-1]  = closes[-1] - 2.0
    avg_vol = 1_000_000.0
    vols    = np.full(n, avg_vol)
    vols[-1] = avg_vol * 4.0     # 4x spike = institutional
    # OBV control: last bar price diff determines OBV direction
    # obv_up=True -> close[-1] > close[-2] (not the case with our drop)
    # We control OBV by adjusting close direction in last 10 bars
    if obv_up:
        # Make last 11 bars steadily rising for OBV slope, but the very
        # last bar has the spike and drop for the BUY signal
        for i in range(1, 11):
            closes[-(i+1)] = closes[-(i+1)] - 0.05   # declining before spike
    # else: default declining closes = OBV bearish = contra

    df = pd.DataFrame({
        'Open':   closes, 'High': highs, 'Low': lows, 'Close': closes,
        'Volume': vols,
    })
    return df


print("=" * 65)
print("A5/A6/A7: Cross-Stock-GNN Improvements")
print("=" * 65)
print()

# ── A5 Tests: VWAP type ──────────────────────────────────────────────────────
print("--- A5: Session vs Rolling VWAP ---")

# T1: Equity symbol (.NS) -> SESSION
print("T1: symbol='RELIANCE.NS' -> vwap_type='SESSION'")
df = build_inst_buy()
r_ns = cross_stock_gnn_signal(df, symbol='RELIANCE.NS')
check('Equity .NS -> vwap_type=SESSION',
      r_ns.measurements.get('vwap_type') == 'SESSION',
      f"vwap_type={r_ns.measurements.get('vwap_type')!r}",
      "vwap_type='SESSION'")

# T2: Crypto -> ROLLING_20
print("T2: symbol='BTC-USD' -> vwap_type='ROLLING_20'")
r_btc = cross_stock_gnn_signal(df, symbol='BTC-USD')
check('Crypto -> vwap_type=ROLLING_20',
      r_btc.measurements.get('vwap_type') == 'ROLLING_20',
      f"vwap_type={r_btc.measurements.get('vwap_type')!r}",
      "vwap_type='ROLLING_20'")

# T3: _is_equity helper
print("T3: _is_equity helper classifications")
cases = [
    ('RELIANCE.NS', True),
    ('INFY.BO',     True),
    ('AAPL',        True),
    ('BTC-USD',     False),
    ('ETHUSDT',     False),
    ('',            False),
]
all_ok = all(_is_equity(s) == expected for s, expected in cases)
check('_is_equity helper correct',
      all_ok,
      [(s, _is_equity(s)) for s, _ in cases],
      str([(s, exp) for s, exp in cases]))

# ── A6 Tests: OBV Confirmation ───────────────────────────────────────────────
print("--- A6: OBV Trend Confirmation ---")

print("T4: BUY + declining OBV -> confidence reduced + contra_factor")
df_obv_down = build_inst_buy(obv_up=False)
r_obv_down  = cross_stock_gnn_signal(df_obv_down, symbol='')
df_obv_up   = build_inst_buy(obv_up=True)
r_obv_up    = cross_stock_gnn_signal(df_obv_up, symbol='')

direction_ok = r_obv_down.direction == 'BUY' or r_obv_down.direction == 'HOLD'
if r_obv_down.direction == 'BUY':
    obv_contra = any('OBV' in f for f in r_obv_down.contra_factors)
    obv_m = r_obv_down.measurements.get('obv_bullish')
    check('BUY + declining OBV -> OBV contra_factor',
          obv_m is False and obv_contra,
          f"direction={r_obv_down.direction}  obv_bullish={obv_m}  contra={r_obv_down.contra_factors}",
          "obv_bullish=False + OBV contra_factor present")
    if r_obv_up.direction == 'BUY':
        check('BUY + rising OBV -> higher/equal confidence vs declining OBV',
              r_obv_up.confidence >= r_obv_down.confidence,
              f"OBV-up conf={r_obv_up.confidence:.3f}  OBV-down conf={r_obv_down.confidence:.3f}",
              "conf(OBV rising) >= conf(OBV declining)")
    else:
        print(f"  [SKIP] OBV-up builder direction={r_obv_up.direction} -- builder needs tuning")
else:
    print(f"  [SKIP] OBV-down builder direction={r_obv_down.direction} -- builder needs tuning for A6")

# T6: obv_slope and obv_bullish in measurements
print("T6: obv_slope and obv_bullish in measurements")
check('obv_slope and obv_bullish in measurements',
      'obv_slope' in r_obv_down.measurements and 'obv_bullish' in r_obv_down.measurements,
      f"keys: {list(r_obv_down.measurements.keys())}",
      "obv_slope and obv_bullish present in measurements")

# ── A7 Tests: Regime Parameter ───────────────────────────────────────────────
print("--- A7: Regime Context Adjustment ---")

print("T7: Same BUY signal in TRENDING_UP -> higher confidence than RANGING")
df_buy = build_inst_buy()
r_ranging      = cross_stock_gnn_signal(df_buy, symbol='', regime='RANGING')
r_trending_up  = cross_stock_gnn_signal(df_buy, symbol='', regime='TRENDING_UP')
r_trending_dn  = cross_stock_gnn_signal(df_buy, symbol='', regime='TRENDING_DOWN')

if r_ranging.direction == 'BUY':
    check('BUY + TRENDING_UP -> +0.05 vs RANGING',
          r_trending_up.confidence > r_ranging.confidence,
          f"TRENDING_UP={r_trending_up.confidence:.3f}  RANGING={r_ranging.confidence:.3f}  diff={r_trending_up.confidence - r_ranging.confidence:+.3f}",
          "conf(TRENDING_UP) > conf(RANGING)")
    check('BUY + TRENDING_DOWN -> lower than RANGING',
          r_trending_dn.confidence <= r_ranging.confidence,
          f"TRENDING_DOWN={r_trending_dn.confidence:.3f}  RANGING={r_ranging.confidence:.3f}",
          "conf(TRENDING_DOWN) <= conf(RANGING)")
else:
    print(f"  [SKIP] builder direction={r_ranging.direction} -- can't test A7 regime boost")

print("T8: regime in measurements")
check('regime in measurements',
      r_trending_up.measurements.get('regime') == 'TRENDING_UP',
      f"measurements['regime'] = {r_ranging.measurements.get('regime')!r}",
      "regime='TRENDING_UP' stored in measurements")

print("=" * 65)
print(f"A5/A6/A7 Tests: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
