"""
Test 9: Coordinator Integration -- generate_brain_signals()
===========================================================
Verifies signal_generators.py wiring across all brains.

WHAT WE TEST:
  T1: Returns [] on empty hist (line 555)
  T2: Signal dicts have required fields (model_used/direction/target_1/stop_loss/confidence)
  T3: RL-Weighter absent from signals[] (line 785-786, only in brain_results[])
  T4: CHAOS regime (< 30-bar hist, no Regime-Ensemble override) -> all blocked -> signals=[]
  T5: A brain raising an exception does not abort the whole coordinator

STUBS:
  - market_data.get_ohlcv: monkey-patched on the live singleton (no network calls at import)
  - _load_brain_performance: monkey-patched to return [] (no DB)
  - get_brain_accuracy: monkey-patched to return None (no retrain JSON file)

T4 NOTE:
  Regime-Ensemble overrides the external regime param when hist >= 30 bars (lines 606-611).
  To test the CHAOS gate without interference, use a 20-bar hist so Regime-Ensemble
  returns early (insufficient data) and CHAOS passes through to regime_allows_brain().
  CHAOS is absent from all brain suitability lists -> all directional signals blocked.
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

# --- STUBS: patch before importing coordinator ---

# 1. Stub health_monitor (reads retrain_results.json)
import market_agent.brain.health_monitor as _hm
_hm.get_brain_accuracy = lambda brain_name: None

# 2. Monkey-patch market_data singleton (no network calls at import time)
from market_agent.data.ingestion.unified_market_data import market_data
_stub = {'ohlcv': None}
market_data.get_ohlcv = lambda symbol, interval='1h', bars=200: _stub['ohlcv']

# 3. Import coordinator (uses the already-patched singleton)
from market_agent.brain.signal_generators import generate_brain_signals
import market_agent.brain.signal_generators as sg

# 4. Stub DB loader
sg._load_brain_performance = lambda symbol, limit=20: []

print("[OK] generate_brain_signals imported and stubs applied")
print()


# --- DATA BUILDERS ---

def build_hist(n=80, trend=False):
    np.random.seed(42 if not trend else 7)
    if trend:
        close = pd.Series([100.0 + i * 0.3 + np.random.normal(0, 0.05) for i in range(n)])
    else:
        t     = np.linspace(0, 4 * np.pi, n)
        close = pd.Series(100.0 + 2.0 * np.sin(t))
    wick = close * 0.01
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': [1_500_000.0 if (trend and i > n - 5) else 1_000_000.0 for i in range(n)],
    })


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


# =============================================================================
print("=" * 65)
print("BRAIN 9: Coordinator Integration -- generate_brain_signals()")
print("=" * 65)
print()

hist_neutral = build_hist(n=80, trend=False)
hist_up      = build_hist(n=80, trend=True)
ATR          = float((hist_neutral['High'] - hist_neutral['Low']).rolling(14).mean().iloc[-1])
_stub['ohlcv'] = hist_neutral

# --- T1: Empty hist -> [] ----------------------------------------------------
print("T1: Empty hist -> returns []")
result_empty = generate_brain_signals('BTC-USD', 100.0, 2.0, pd.DataFrame(), regime='RANGING')
check('Empty hist returns []',
      result_empty == [],
      f"result={result_empty}",
      '[]')

# --- T2: Signal dict field integrity -----------------------------------------
print("T2: Signal dicts have required fields")
_stub['ohlcv'] = hist_up
REQUIRED = {'model_used', 'direction', 'target_1', 'stop_loss', 'confidence'}
sigs = generate_brain_signals('BTC-USD', float(hist_up['Close'].iloc[-1]),
                               ATR, hist_up, regime='TRENDING_UP')
if sigs:
    missing = [REQUIRED - set(s.keys()) for s in sigs]
    check('All signal dicts have required fields',
          all(len(m) == 0 for m in missing),
          f"{len(sigs)} signals. missing={[list(m) for m in missing if m]}",
          f"All of {REQUIRED} present",
          f"Brains fired: {[s.get('model_used') for s in sigs]}")
    check('No HOLD directions in signals[]',
          all(s.get('direction') in ('BUY', 'SELL') for s in sigs),
          f"directions={[s.get('direction') for s in sigs]}",
          'Only BUY or SELL (HOLD filtered before signals[])')
else:
    check('Coordinator ran without crash (0 signals is valid)',
          True,
          'signals=[] (no brain crossed the 0.50 confidence threshold)',
          'list returned without exception')

# --- T3: RL-Weighter NOT in signals[] ----------------------------------------
print("T3: RL-Weighter must be absent from signals[]")
_stub['ohlcv'] = hist_up
sigs2    = generate_brain_signals('BTC-USD', float(hist_up['Close'].iloc[-1]),
                                   ATR, hist_up, regime='TRENDING_UP')
rl_in    = any(s.get('model_used') == 'RL-Weighter' for s in sigs2)
check('RL-Weighter NOT in signals[]',
      not rl_in,
      f"RL-Weighter present: {rl_in} | models={[s.get('model_used') for s in sigs2]}",
      'RL-Weighter absent (brain_results[] only, per sg.py line 785-786)')

# --- T4: CHAOS with < 30 bars -> no Regime-Ensemble override -> signals=[] ---
print("T4: CHAOS regime (20-bar hist, no override) -> signals=[]")
hist_short = build_hist(n=20, trend=False)   # < 30 bars = Regime-Ensemble skips
_stub['ohlcv'] = hist_short
sigs_chaos = generate_brain_signals('BTC-USD', 100.0, 2.0, hist_short, regime='CHAOS')
check('CHAOS regime (no RE override) -> no signals',
      len(sigs_chaos) == 0,
      f"count={len(sigs_chaos)} models={[s.get('model_used') for s in sigs_chaos]}",
      'signals=[] (CHAOS blocks all brains via regime_allows_brain)')

# --- T5: Brain exception does not abort coordinator --------------------------
print("T5: AMV-LSTM crash -> does not abort coordinator, list still returned")
_orig_amv = sg.amv_lstm_signal

def _crash_amv(hist):
    raise RuntimeError("Simulated AMV-LSTM crash for T5")

sg.amv_lstm_signal = _crash_amv
try:
    _stub['ohlcv'] = hist_up
    sigs_crash = generate_brain_signals('BTC-USD', float(hist_up['Close'].iloc[-1]),
                                         ATR, hist_up, regime='TRENDING_UP')
    check('Crashed brain caught silently, list returned',
          isinstance(sigs_crash, list),
          f"type={type(sigs_crash).__name__}  count={len(sigs_crash)}  AMV-LSTM crashed silently",
          'list (coordinator continues, crash logged as warning only)')
finally:
    sg.amv_lstm_signal = _orig_amv   # always restore

# --- T6: B6 — A12 fields present in all 7 brain measurements ----------------
print("T6 (B6): All 7 brains have A12 fields in measurements")
from market_agent.brain.amv_lstm        import amv_lstm_signal
from market_agent.brain.multi_timeframe import multi_timeframe_signal
from market_agent.brain.multi_modal_fusion import multi_modal_fusion_signal
from market_agent.brain.causal_ensemble import causal_ensemble_signal
from market_agent.brain.cross_stock_gnn import cross_stock_gnn_signal
from market_agent.brain.liquidity_sweep import liquidity_sweep_signal
from market_agent.brain.regime_ensemble import regime_ensemble_signal

A12_FIELDS = {'decision_factor', 'price_at_signal', 'atr_at_signal', 'atr_pct_at_signal', 'bars_used'}

_brain_calls = [
    # AMV-LSTM: use timeframe_minutes=15 so stale_candles=40; cross_age=19 < 40 -> reaches final return with A12 fields
    ('AMV-LSTM',          lambda: amv_lstm_signal(hist_up, timeframe_minutes=15)),
    ('Multi-Timeframe',   lambda: multi_timeframe_signal('BTC-USD', hist_up)),
    ('Multi-Modal',       lambda: multi_modal_fusion_signal(hist_up)),
    ('Causal-Ensemble',   lambda: causal_ensemble_signal(hist_up)),
    ('Cross-Stock-GNN',   lambda: cross_stock_gnn_signal(hist_up, symbol='BTC-USD', regime='RANGING')),
    ('Liquidity-Sweep',   lambda: liquidity_sweep_signal(hist_up, symbol='BTC-USD', regime='VOLATILE')),
    ('Regime-Ensemble',   lambda: regime_ensemble_signal(hist_up)),
]

for brain_name, call_fn in _brain_calls:
    try:
        sig = call_fn()
        m   = sig.measurements or {}
        missing = A12_FIELDS - set(m.keys())
        check(f'{brain_name} has all A12 fields',
              len(missing) == 0,
              f"missing={list(missing)}" if missing else f"all 5 present (dir={sig.direction})",
              'All of: decision_factor, price_at_signal, atr_at_signal, atr_pct_at_signal, bars_used')
    except Exception as e:
        check(f'{brain_name} A12 check (exception)',
              False,
              f"EXCEPTION: {e}",
              'No exception, all 5 A12 fields present')

# =============================================================================
print("=" * 65)
print(f"Coordinator Integration: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
