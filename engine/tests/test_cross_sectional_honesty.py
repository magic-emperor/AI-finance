"""
test_cross_sectional_honesty.py — same discipline as test_honesty.py, applied to
the cross-sectional ranking engine (engine/backtest/cross_sectional.py).

Guards against the exact class of bug the single-symbol simulator was already
tested against, in the new shape:
  (a) no look-ahead: the SCORE at a rebalance never sees bars after the decision
      bar, even when a huge move happens the very next bar
  (b) fills happen at the NEXT bar's open, never the decision bar's own close
  (c) direction sign is correct: LONG profits when price rises, SHORT profits
      when the identical price path falls (mirrored)
  (d) a universe smaller than min_universe produces zero trades for that
      rebalance -- ranking 1-2 names is not cross-sectional ranking
  (e) costs are actually subtracted from net_return relative to gross_return

Run directly: python -X utf8 -m engine.tests.test_cross_sectional_honesty
(also works under pytest)
"""
from __future__ import annotations

import pandas as pd

from engine.backtest.cross_sectional import simulate_cross_sectional, _bar_at_or_before


def make_df(closes, start="2025-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({
        "Open": closes, "High": closes, "Low": closes, "Close": closes,
        "Volume": [1000] * len(closes),
    }, index=idx)
    return df


def _flat_then_rising(n, flat_val=100.0, rise_start=None, rise_step=1.0):
    vals = [flat_val] * n
    if rise_start is not None:
        for i in range(rise_start, n):
            vals[i] = flat_val + (i - rise_start + 1) * rise_step
    return vals


# ── (a) no look-ahead in the score ─────────────────────────────────────────────

def test_score_has_no_lookahead():
    """Symbol A: flat history, then a huge spike starting AFTER a rebalance's
    decision bar. Symbol B: genuinely rose over the formation window BEFORE
    that rebalance. If the score leaked the future spike, A would rank above B
    at that rebalance -- it must not."""
    n = 40
    formation = 10
    hold = 10
    # A: flat for the whole formation window, then spikes only after bar 20
    a_vals = _flat_then_rising(n, flat_val=100.0, rise_start=21, rise_step=50.0)
    # B: real upward drift across the whole series (positive formation-window return)
    b_vals = [100.0 + 0.5 * i for i in range(n)]
    c_vals = [100.0] * n  # flat control, always scores ~0

    universe = {"A": make_df(a_vals), "B": make_df(b_vals), "C": make_df(c_vals)}
    trades = simulate_cross_sectional(universe, formation_bars=formation, hold_bars=hold,
                                      top_frac=0.34, bottom_frac=0.0, cost_bps_roundtrip=0.0)

    # At the FIRST rebalance (bar index 10), A has not spiked yet (spike starts at 21).
    first_rebalance_trades = [t for t in trades if t.rebalance_time == universe["A"].index[formation]]
    assert first_rebalance_trades, "expected a trade at the first rebalance"
    picked = {t.symbol for t in first_rebalance_trades}
    assert "A" not in picked, (
        f"look-ahead bug: A was picked at the first rebalance using a score that "
        f"could only be legitimate if it saw the future spike (picked={picked})"
    )
    assert "B" in picked, "B has genuine formation-window drift and should be picked"


# ── (b) fills are next-bar-open, not decision-bar close ────────────────────────

def test_entry_fill_is_next_bar_open_not_decision_close():
    n = 30
    formation, hold = 10, 10
    # Decision bar (index 10) closes at 100; the VERY NEXT bar opens at 500 --
    # a fill at the decision bar's close (100) vs the next bar's open (500) are
    # unmistakably different, so this pins down which one the engine actually uses.
    vals = [100.0] * 11 + [500.0] * (n - 11)
    a = make_df(vals)
    b = make_df([100.0 + i for i in range(n)])   # genuine drift, ranks as a long
    c = make_df([100.0] * n)

    universe = {"A": a, "B": b, "C": c}
    trades = simulate_cross_sectional(universe, formation_bars=formation, hold_bars=hold,
                                      top_frac=0.34, bottom_frac=0.0, cost_bps_roundtrip=0.0)
    b_trades = [t for t in trades if t.symbol == "B"]
    assert b_trades
    t0 = b_trades[0]
    # entry_time must be strictly AFTER rebalance_time (the decision bar)
    assert t0.entry_time > t0.rebalance_time
    # and the entry_fill must equal that later bar's Open, not the decision bar's Close
    entry_pos = b.index.get_loc(t0.entry_time)
    assert t0.entry_fill == float(b["Open"].iloc[entry_pos])
    decision_pos = b.index.get_loc(t0.rebalance_time)
    assert t0.entry_fill != float(b["Close"].iloc[decision_pos]), (
        "entry_fill matches the decision bar's own close -- look-ahead/same-bar fill bug"
    )


# ── (c) direction sign correctness, LONG vs SHORT on the SAME price path ───────

def test_long_and_short_sign_are_mirrored():
    n = 30
    formation, hold = 10, 10
    rising = [100.0 + i for i in range(n)]     # ranks as the strongest LONG candidate
    falling = [100.0 - i for i in range(n)]    # ranks as the strongest SHORT candidate
    flat = [100.0] * n

    universe = {"UP": make_df(rising), "DOWN": make_df(falling), "FLAT": make_df(flat)}
    trades = simulate_cross_sectional(universe, formation_bars=formation, hold_bars=hold,
                                      top_frac=0.34, bottom_frac=0.34, cost_bps_roundtrip=0.0)

    up_trades = [t for t in trades if t.symbol == "UP"]
    down_trades = [t for t in trades if t.symbol == "DOWN"]
    assert up_trades and down_trades
    assert up_trades[0].direction == "LONG"
    assert down_trades[0].direction == "SHORT"
    # UP keeps rising after entry -> LONG must show a positive gross_return
    assert up_trades[0].gross_return > 0
    # DOWN keeps falling after entry -> the position is SHORT, so gross_return
    # (sign-adjusted for direction) must ALSO be positive -- a falling price is
    # a WIN for a short, not a loss
    assert down_trades[0].gross_return > 0


# ── (d) universe too small -> no trades ─────────────────────────────────────────

def test_universe_below_minimum_produces_no_trades():
    n = 30
    formation, hold = 10, 10
    universe = {
        "A": make_df([100.0 + i for i in range(n)]),
        "B": make_df([100.0 - i for i in range(n)]),
    }
    trades = simulate_cross_sectional(universe, formation_bars=formation, hold_bars=hold,
                                      top_frac=0.5, bottom_frac=0.0, cost_bps_roundtrip=0.0,
                                      min_universe=3)
    assert trades == [], "a 2-symbol universe should never produce a trade with min_universe=3"


# ── (e) costs are actually subtracted ───────────────────────────────────────────

def test_costs_reduce_net_return_relative_to_gross():
    n = 30
    formation, hold = 10, 10
    universe = {
        "A": make_df([100.0 + i for i in range(n)]),
        "B": make_df([100.0 - i for i in range(n)]),
        "C": make_df([100.0] * n),
    }
    free = simulate_cross_sectional(universe, formation, hold, top_frac=0.34,
                                    bottom_frac=0.0, cost_bps_roundtrip=0.0)
    costly = simulate_cross_sectional(universe, formation, hold, top_frac=0.34,
                                      bottom_frac=0.0, cost_bps_roundtrip=50.0)
    assert free and costly
    f0, c0 = free[0], costly[0]
    assert f0.gross_return == c0.gross_return  # same price path, same gross
    assert c0.net_return < f0.net_return        # costs strictly hurt
    assert abs((f0.net_return - c0.net_return) - 0.0050) < 1e-9  # 50bps = 0.0050


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
