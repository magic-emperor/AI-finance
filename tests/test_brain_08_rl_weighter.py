"""
Test 8: RL-Weighter -- Adaptive Capital Allocation (Risk Sizing Brain)
======================================================================
From rl_weighter.py logic:
  - NOT a directional brain -- a SIZER only. NOT added to signals[].
  - performance_history: list of {'outcome': 'TARGET'|'SL'|'EXPIRED', 'regime': str}
  - No history   -> risk_multiplier = 1.0
  - With history -> half_kelly = Kelly / 2, clipped [0.2, 1.5]
    Kelly = (b * win_rate - (1 - win_rate)) / b
    b = BASE_ATR_T1_MULT / BASE_ATR_SL_MULT  (read from signal_params at runtime)
  - Regime boost: if regime_win_rate > overall_win_rate -> x 1.1, else x 0.9
  - direction mirrors base_signal['direction'] (council context only)

NOTE ON DB DEPENDENCY:
  Unit test uses synthetic performance_history -- no DB needed.
  In production, performance_history comes from real trade outcomes (TARGET/SL/EXPIRED)
  stored in DB. The DB dependency only applies to production use, not this unit test.

IMPORTANT: All expected values are computed from the LIVE signal_params import,
not hardcoded. If BASE_ATR_T1_MULT or BASE_ATR_SL_MULT change, the expected
values recalculate automatically and the test remains an honest contract.
"""
import sys
import inspect
import math

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.rl_weighter_signal)
assert 'signal_generators' not in src_file, f"Phase 4 shadowing! {src_file}"
print(f"[OK] rl_weighter_signal: {src_file.split(chr(92))[-1]}")

# ── Import the SAME params the brain uses at runtime ─────────────────────────
from market_agent.signal_params import BASE_ATR_T1_MULT, BASE_ATR_SL_MULT
B = BASE_ATR_T1_MULT / BASE_ATR_SL_MULT   # same formula as rl_weighter.py line 59
KELLY_FLOOR = 0.20
KELLY_CAP   = 1.50
print(f"[OK] signal_params: T1_MULT={BASE_ATR_T1_MULT}  SL_MULT={BASE_ATR_SL_MULT}  b={B:.4f}")

# ── Pre-compute expected values from live params ──────────────────────────────
WR_WIN  = 14 / 20   # 0.70
WR_LOSE = 6  / 20   # 0.30


def expected_risk_mult(win_rate, regime_rate):
    """Mirror the exact formula in rl_weighter.py lines 60-68."""
    kelly      = (B * win_rate - (1 - win_rate)) / B
    half_kelly = max(KELLY_FLOOR, min(KELLY_CAP, kelly / 2))
    boost      = 1.1 if regime_rate > win_rate else 0.9
    return round(half_kelly * boost, 3)


EXP_WIN  = expected_risk_mult(WR_WIN,  WR_WIN)    # same regime -> 0.9 boost (equal, not >)
EXP_LOSE = expected_risk_mult(WR_LOSE, WR_LOSE)   # same regime -> 0.9

print(f"[OK] Expected win:  half_kelly * regime_boost = {EXP_WIN:.3f}")
print(f"[OK] Expected lose: half_kelly * regime_boost = {EXP_LOSE:.3f}  (floor={KELLY_FLOOR} * 0.9)")
print()

from market_agent.brain.rl_weighter import rl_weighter_signal

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


# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("BRAIN 8: RL-Weighter -- All 4 Scenarios")
print("=" * 65)
print()

# ── Test 1: No history -> risk_multiplier=1.0, mirrors base direction ─────────
print("Test 1: No history -> risk_multiplier=1.0, direction mirrors base")
r1 = rl_weighter_signal({'direction': 'BUY', 'confidence': 0.72},
                         performance_history=[], current_regime='RANGING')
m1 = r1.measurements
check('No history -> default 1x sizing',
      r1.direction == 'BUY' and abs(m1.get('risk_multiplier', 0) - 1.0) < 0.01,
      f"dir={r1.direction}  risk_mult={m1.get('risk_multiplier')}  conf={r1.confidence:.3f}",
      'direction=BUY (mirrors base), risk_multiplier=1.0',
      r1.primary_evidence)

check('insufficient_history flag when no history',
      r1.reliability_flags.get('insufficient_history') is True,
      f"insufficient_history={r1.reliability_flags.get('insufficient_history')}",
      'insufficient_history=True (len < 10)')

# ── Test 2: Winning streak (WR=70%) -> exact Kelly from live params ───────────
print(f"Test 2: Winning streak (WR=70%) -> expected risk_mult={EXP_WIN:.3f} from live b={B:.2f}")
history_win = (
    [{'outcome': 'TARGET', 'regime': 'VOLATILE'}] * 14 +
    [{'outcome': 'SL',     'regime': 'VOLATILE'}] * 6
)
r2 = rl_weighter_signal({'direction': 'BUY', 'confidence': 0.80}, history_win, 'VOLATILE')
m2 = r2.measurements
check(f'Winning streak -> risk_mult={EXP_WIN:.3f} (from live signal_params b={B:.2f})',
      r2.direction == 'BUY' and abs(m2.get('risk_multiplier', 0) - EXP_WIN) < 0.01,
      f"dir={r2.direction}  risk_mult={m2.get('risk_multiplier'):.3f}  (expected {EXP_WIN:.3f})",
      f'BUY risk_mult={EXP_WIN:.3f} (half-Kelly={expected_risk_mult(WR_WIN, WR_WIN):.3f})',
      r2.primary_evidence)

# ── Test 3: Losing streak (WR=30%) -> Kelly floor, exact from live params ─────
print(f"Test 3: Losing streak (WR=30%) -> expected risk_mult={EXP_LOSE:.3f} (floor*regime)")
history_loss = (
    [{'outcome': 'TARGET', 'regime': 'RANGING'}] * 6 +
    [{'outcome': 'SL',     'regime': 'RANGING'}] * 14
)
r3 = rl_weighter_signal({'direction': 'SELL', 'confidence': 0.70}, history_loss, 'RANGING')
m3 = r3.measurements
check(f'Losing streak -> risk_mult={EXP_LOSE:.3f} (floor={KELLY_FLOOR} * 0.9 regime)',
      abs(m3.get('risk_multiplier', 1.0) - EXP_LOSE) < 0.01,
      f"risk_mult={m3.get('risk_multiplier')}  (expected {EXP_LOSE:.3f})  win_rate={m3.get('win_rate'):.2f}",
      f'risk_mult={EXP_LOSE:.3f} (Kelly floor {KELLY_FLOOR} * 0.9)',
      r3.primary_evidence)

check('Losing streak triggers reduce-size contra_factor',
      len(r3.contra_factors) > 0 and 'Reduce size' in r3.contra_factors[0],
      f"contra_factors={r3.contra_factors}",
      'contra_factors contains "Reduce size" warning')

# ── Test 4: HOLD base signal -> mirrors HOLD (sizer, not voter) ───────────────
print("Test 4: base direction=HOLD -> RL-Weighter mirrors HOLD (not a voter)")
r4 = rl_weighter_signal({'direction': 'HOLD', 'confidence': 0.40},
                         performance_history=[], current_regime='RANGING')
check('HOLD base -> direction stays HOLD',
      r4.direction == 'HOLD',
      f"dir={r4.direction}  conf={r4.confidence:.3f}",
      'direction=HOLD (mirrors base, RL-Weighter is a sizer not a voter)')

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"RL-Weighter: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
