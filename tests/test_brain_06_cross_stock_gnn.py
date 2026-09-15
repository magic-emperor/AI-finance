"""
Test 6: Cross-Stock-GNN -- Institutional Flow Detector
======================================================
From cross_stock_gnn.py logic:
  vwap = rolling(20) VWAP from (H+L+C)/3 * vol
  vol_spike = vol_ratio > 2.5x 20-bar avg
  vwap_dev_in_atr = |close - vwap| / ATR

  Tiers (in order):
  - STRONG BUY : vwap_dev_pct < 0 AND vwap_dev_in_atr > 1.5 AND vol_spike -> 0.85
  - STRONG SELL: vwap_dev_pct > 0 AND vwap_dev_in_atr > 1.5 AND vol_spike -> 0.85
  - MOD BUY    : vwap_dev_pct < 0 AND vwap_dev_in_atr > 0.8 AND vol_spike -> 0.72
  - MOD SELL   : vwap_dev_pct > 0 AND vwap_dev_in_atr > 0.8 AND vol_spike -> 0.72
  - else       : HOLD 0.40

  R:R: T1=2.0x, SL=0.75x (2.67:1)

DATA ENGINEERING:
  VWAP = (sum of typical_price * vol over 20 bars) / (sum of vol over 20 bars)
  To get price below VWAP: make last bar price much lower than rolling VWAP
  vol_spike: last bar volume = 3x the 20-bar average
  ATR for vwap_dev_in_atr: controlled by wick_pct
"""
import sys
import inspect
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.cross_stock_gnn_signal)
assert 'signal_generators' not in src_file, f"Phase 4 shadowing! {src_file}"
print(f"[OK] cross_stock_gnn_signal: {src_file.split(chr(92))[-1]}")
print()

from market_agent.brain.cross_stock_gnn import cross_stock_gnn_signal

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


def make_vwap_df(n=40, base_vol=1_000_000, wick_pct=0.01):
    """
    Build a baseline DataFrame where close ~ VWAP.
    Prices oscillate around 100/bar to keep VWAP near 100.
    Last bar is set to whatever signal we need via override.
    """
    np.random.seed(42)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] + np.random.choice([-0.1, 0.0, 0.1]))
    close  = pd.Series([float(p) for p in prices])
    wick   = close * wick_pct
    vols   = [float(base_vol)] * n
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': vols,
    })


def build_strong_buy(base_vol=1_000_000):
    """
    STRONG BUY: brain reads iloc[-2] as "Bar N" (setup bar).
    Set price drop and vol spike on iloc[-2]; iloc[-1] is a normal confirmation bar.
    Vol: 3.0M on 1M base -> rolling avg ~1.1M -> ratio ~2.73x > 2.5x.
    Price at Bar N = 96 (4 below VWAP ~100) -> dev_in_atr > 1.5x.
    """
    df = make_vwap_df(n=41, base_vol=base_vol, wick_pct=0.002)
    df.loc[df.index[-2], 'Close']  = 96.0
    df.loc[df.index[-2], 'Open']   = 96.0
    df.loc[df.index[-2], 'High']   = 96.2
    df.loc[df.index[-2], 'Low']    = 95.8
    df.loc[df.index[-2], 'Volume'] = 3_000_000.0
    # iloc[-1]: normal bar slightly above Bar N (confirmation)
    df.loc[df.index[-1], 'Close']  = 96.2
    df.loc[df.index[-1], 'Open']   = 96.0
    df.loc[df.index[-1], 'High']   = 96.4
    df.loc[df.index[-1], 'Low']    = 96.0
    df.loc[df.index[-1], 'Volume'] = float(base_vol)
    return df


def build_no_vol_spike(base_vol=1_000_000):
    """
    HOLD: price below VWAP at Bar N but vol_ratio=1.2x < 2.5x -> no spike.
    Bar N = iloc[-2].
    """
    df = make_vwap_df(n=41, base_vol=base_vol, wick_pct=0.002)
    df.loc[df.index[-2], 'Close']  = 96.0
    df.loc[df.index[-2], 'Open']   = 96.0
    df.loc[df.index[-2], 'High']   = 96.2
    df.loc[df.index[-2], 'Low']    = 95.8
    df.loc[df.index[-2], 'Volume'] = 1_200_000.0   # 1.2x -- below 2.5x threshold
    df.loc[df.index[-1], 'Close']  = 96.2
    df.loc[df.index[-1], 'Open']   = 96.0
    df.loc[df.index[-1], 'High']   = 96.4
    df.loc[df.index[-1], 'Low']    = 96.0
    df.loc[df.index[-1], 'Volume'] = float(base_vol)
    return df


def build_boundary_vol(base_vol=1_000_000):
    """
    HOLD: vol_ratio=2.40x at Bar N (below > 2.5x threshold).
    vol_N / avg_vol_N ~ 2.4x -> vol_spike=False -> HOLD.
    Bar N = iloc[-2].
    """
    df = make_vwap_df(n=41, base_vol=base_vol, wick_pct=0.002)
    df.loc[df.index[-2], 'Close']  = 96.0
    df.loc[df.index[-2], 'Open']   = 96.0
    df.loc[df.index[-2], 'High']   = 96.2
    df.loc[df.index[-2], 'Low']    = 95.8
    df.loc[df.index[-2], 'Volume'] = 2_591_000.0   # -> ratio ~2.40x (below 2.5x)
    df.loc[df.index[-1], 'Close']  = 96.2
    df.loc[df.index[-1], 'Open']   = 96.0
    df.loc[df.index[-1], 'High']   = 96.4
    df.loc[df.index[-1], 'Low']    = 96.0
    df.loc[df.index[-1], 'Volume'] = float(base_vol)
    return df


def build_moderate_buy(base_vol=1_000_000):
    """
    MODERATE BUY: vwap_dev_in_atr in 0.8-1.5x, vol spike > 2.5x at Bar N.
    Price drop = 0.4 below VWAP on tiny wicks -> dev_in_atr ~1.2x.
    Bar N = iloc[-2].
    """
    df = make_vwap_df(n=41, base_vol=base_vol, wick_pct=0.002)
    df.loc[df.index[-2], 'Close']  = 99.6
    df.loc[df.index[-2], 'Open']   = 99.6
    df.loc[df.index[-2], 'High']   = 99.8
    df.loc[df.index[-2], 'Low']    = 99.4
    df.loc[df.index[-2], 'Volume'] = 3_000_000.0
    df.loc[df.index[-1], 'Close']  = 99.7
    df.loc[df.index[-1], 'Open']   = 99.6
    df.loc[df.index[-1], 'High']   = 99.9
    df.loc[df.index[-1], 'Low']    = 99.5
    df.loc[df.index[-1], 'Volume'] = float(base_vol)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# PRE-RUN DIAGNOSTIC
# ─────────────────────────────────────────────────────────────────────────────
print("Pre-run diagnostic:")
for lbl, df_p in [
    ('strong_buy',    build_strong_buy()),
    ('no_vol_spike',  build_no_vol_spike()),
    ('boundary_vol',  build_boundary_vol()),
    ('moderate_buy',  build_moderate_buy()),
]:
    r_p = cross_stock_gnn_signal(df_p)
    m_p = r_p.measurements
    print(f"  {lbl:14s}: dir={r_p.direction:4s}  conf={r_p.confidence:.2f}  vol_ratio={m_p.get('vol_ratio', 0):.1f}x  vwap_dev={m_p.get('vwap_dev_pct', 0):.2f}%  dev_atr={m_p.get('vwap_dev_in_atr', 0):.2f}x")
print()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("BRAIN 6: Cross-Stock-GNN -- All 4 Scenarios")
print("=" * 65)
print()

# ── Test 1: STRONG BUY (dep > 1.5 ATR below VWAP, vol 3x) -> 0.85 ────────────
print("Test 1: STRONG BUY (price > 1.5 ATR below VWAP, vol=3x) -> conf=0.85")
df1 = build_strong_buy()
r1  = cross_stock_gnn_signal(df1)
m1  = r1.measurements
check('STRONG BUY fires',
      r1.direction == 'BUY' and r1.confidence >= 0.85,
      f"dir={r1.direction}  conf={r1.confidence:.2f}  vol={m1.get('vol_ratio', 0):.1f}x  dev_atr={m1.get('vwap_dev_in_atr', 0):.2f}x",
      'BUY conf>=0.85 (A6 OBV boost may add +0.03 on trending data)',
      r1.primary_evidence)

check('R:R: T1=2.0x SL=0.75x (2.67:1)',
      r1.rr_t1_mult == 2.0 and r1.rr_sl_mult == 0.75,
      f"rr_t1={r1.rr_t1_mult}  rr_sl={r1.rr_sl_mult}  ratio={r1.rr_t1_mult/r1.rr_sl_mult:.2f}:1",
      'rr_t1=2.0, rr_sl=0.75 (2.67:1)')

# ── Test 2: No vol spike -> HOLD ───────────────────────────────────────────────
print("Test 2: No vol spike (vol=1.2x, below 2.5x threshold) -> HOLD")
df2 = build_no_vol_spike()
r2  = cross_stock_gnn_signal(df2)
m2  = r2.measurements
check('No vol spike -> HOLD',
      r2.direction == 'HOLD',
      f"dir={r2.direction}  vol_ratio={m2.get('vol_ratio', 0):.1f}x  vol_spike={m2.get('vol_spike')}",
      'HOLD (vol_ratio=1.2x < 2.5x threshold, no spike)',
      r2.primary_evidence)

# ── Test 3: Boundary vol (2.4x) -> HOLD ───────────────────────────────────────
print("Test 3: Boundary vol (2.4x) -> HOLD (threshold is > 2.5x, exclusive)")
df3 = build_boundary_vol()
r3  = cross_stock_gnn_signal(df3)
m3  = r3.measurements
check('Vol=2.4x boundary -> still HOLD',
      r3.direction == 'HOLD' and m3.get('vol_spike') == 0.0,
      f"dir={r3.direction}  vol_ratio={m3.get('vol_ratio', 0):.1f}x  vol_spike={m3.get('vol_spike')}",
      'HOLD (vol=2.4x is below > 2.5x threshold)',
      r3.primary_evidence)

# ── Test 4: Moderate BUY (dev 0.8-1.5 ATR, vol 3x) -> 0.72 ───────────────────
print("Test 4: Moderate BUY (price 0.8-1.5 ATR below VWAP, vol=3x) -> conf=0.72")
df4 = build_moderate_buy()
r4  = cross_stock_gnn_signal(df4)
m4  = r4.measurements
check('Moderate BUY fires',
      r4.direction == 'BUY' and abs(r4.confidence - 0.72) < 0.05,
      f"dir={r4.direction}  conf={r4.confidence:.2f}  dev_atr={m4.get('vwap_dev_in_atr', 0):.2f}x",
      'BUY conf~0.72 (dev 0.8-1.5x ATR, vol > 2.5x)',
      r4.primary_evidence)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"Cross-Stock-GNN: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
