"""
test_honesty.py — Tests that the harness CANNOT flatter itself.

These guard the exact sins that made the old system lie:
  (a) a stop hit is a FULL ~-1R loss, never partial credit
  (b) no look-ahead: entry fills at the NEXT bar's open, and a decision never
      depends on bars after the decision bar
  (c) costs are deducted on BOTH entry and exit
  (d) an intrabar bar touching both stop and target is scored as a STOP
  (e) 1% position sizing means 4 losses on $500 leave >= $480 (no wipeout)

Run directly:  python -X utf8 -m engine.tests.test_honesty
(also works under pytest)
"""
from __future__ import annotations

import pandas as pd

from engine.strategy.base import Trade
from engine.backtest.simulator import simulate
from engine.backtest.simulator_v2 import simulate_v2
from engine.risk.money import position_size, MoneyManager


# ── helpers ───────────────────────────────────────────────────────────────────

def make_df(rows):
    """rows = list of (open, high, low, close, volume). UTC hourly index."""
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="h", tz="UTC")
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)
    df.index.name = "Datetime"
    return df


class OneShot:
    """Emits one fixed Trade when the decision bar == at_index."""
    name = "oneshot"

    def __init__(self, at_index, direction, stop, target):
        self.at_index, self.direction, self.stop, self.target = at_index, direction, stop, target

    def signals(self, bars, symbol):
        if len(bars) - 1 == self.at_index:
            close = float(bars["Close"].iloc[-1])
            return Trade(symbol, bars.index[-1], self.direction, close,
                         self.stop, self.target, 1.0, "test")
        return None


NO_COST = {"fee_bps": 0.0, "slippage_bps": 0.0}
US_COST = {"fee_bps": 0.0, "slippage_bps": 3.0}


# ── tests ─────────────────────────────────────────────────────────────────────

def test_stop_is_full_loss_not_partial():
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11        # flat up to decision bar 10
    rows += [(100, 100.2, 99.9, 100, 1000)]            # bar 11 = entry bar (no stop hit)
    rows += [(100, 100.1, 98.0, 99.0, 1000)]           # bar 12 = drops through stop 99
    rows += [(99, 99, 98, 98, 1000)] * 5
    df = make_df(rows)
    trades = simulate(df, OneShot(10, "LONG", stop=99.0, target=102.0), "X", US_COST, max_hold_bars=8)
    assert len(trades) == 1, "expected exactly one trade"
    t = trades[0]
    assert t.outcome == "STOP", t.outcome
    # full loss, slightly worse than -1 after costs — and definitely NOT a partial like -0.33
    assert -1.4 < t.R < -0.95, f"stop should be ~-1R, got {t.R}"


def test_entry_fills_next_open_no_lookahead():
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11
    rows += [(101, 103.5, 100.5, 103, 1000)]           # bar 11 entry: open=101 (not the 100 close)
    rows += [(103, 104, 102.5, 103.5, 1000)] * 6
    df = make_df(rows)
    trades = simulate(df, OneShot(10, "LONG", stop=99.0, target=102.0), "X", NO_COST, max_hold_bars=8)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_time == df.index[11], "entry must be the bar AFTER the signal"
    assert abs(t.entry_fill - 101.0) < 1e-9, f"entry must fill at next open 101, got {t.entry_fill}"


def test_costs_reduce_pnl_both_sides():
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11
    rows += [(100, 100.2, 99.9, 100, 1000)]            # entry bar
    rows += [(100, 103.0, 99.9, 102.5, 1000)]          # hits target 102, never stop
    rows += [(102, 103, 101.5, 102.5, 1000)] * 5
    df = make_df(rows)
    no = simulate(df, OneShot(10, "LONG", 99.0, 102.0), "X", NO_COST, 8)[0]
    yes = simulate(df, OneShot(10, "LONG", 99.0, 102.0), "X", US_COST, 8)[0]
    assert no.outcome == "TARGET" and yes.outcome == "TARGET"
    assert abs(no.R - 2.0) < 1e-6, f"frictionless target should be exactly 2R, got {no.R}"
    assert yes.R < no.R, "costs must reduce realised R"


def test_intrabar_tie_scored_as_stop():
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11
    rows += [(100, 100.2, 99.9, 100, 1000)]            # entry bar
    rows += [(100, 103.0, 98.0, 100, 1000)]            # bar touches BOTH target 102 and stop 99
    rows += [(100, 101, 99.5, 100, 1000)] * 5
    df = make_df(rows)
    t = simulate(df, OneShot(10, "LONG", 99.0, 102.0), "X", NO_COST, 8)[0]
    assert t.outcome == "STOP", f"ambiguous bar must be scored as STOP, got {t.outcome}"


def test_gross_R_at_least_net_R():
    # costs can only HURT: cost-free gross_R must be >= net R for the same trade.
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11
    rows += [(100, 100.2, 99.9, 100, 1000)]            # entry bar
    rows += [(100, 103.0, 99.9, 102.5, 1000)]          # hits target
    rows += [(102, 103, 101.5, 102.5, 1000)] * 5
    df = make_df(rows)
    t = simulate(df, OneShot(10, "LONG", 99.0, 102.0), "X", US_COST, 8)[0]
    assert t.gross_R >= t.R - 1e-9, f"gross {t.gross_R} should be >= net {t.R}"
    assert t.mfe_r >= 0.0 and t.mae_r >= 0.0


def test_sizing_survives_four_losses():
    # 4 full losses on $500 at 1% risk, on separate days (so the daily limit
    # doesn't even kick in) — must still leave >= $480.
    mm = MoneyManager(starting_capital=500.0, risk_pct=0.01,
                      max_trades_per_day=2, daily_loss_limit_pct=0.02)
    for d in range(4):
        day = pd.Timestamp(f"2025-01-0{d+1}", tz="UTC").date()
        assert mm.can_trade(day)
        _, risk_d = mm.size(entry=100.0, stop=99.0)
        mm.apply(-1.0 * risk_d, day)                    # R = -1
    assert mm.capital >= 480.0, f"4 losses wiped too much: ${mm.capital:.2f}"

    units, risk_d = position_size(500.0, 0.01, 100.0, 99.0)
    assert abs(risk_d - 5.0) < 1e-9 and abs(units - 5.0) < 1e-9


V2CFG = {"atr_period": 3, "trail_atr_mult": 1.0, "t1_R": 2.0, "partial_fraction": 0.5,
         "pyramid_levels": [1.0, 2.0], "pyramid_sizes": [0.5, 0.25], "max_hold_bars": 20}


def test_v2_trailing_full_loss():
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11
    rows += [(100, 100.2, 99.9, 100, 1000)]            # entry bar (no stop)
    rows += [(100, 100.1, 98.0, 99.0, 1000)]           # drops through stop 99
    rows += [(99, 99, 98, 98, 1000)] * 4
    df = make_df(rows)
    t = simulate_v2(df, OneShot(10, "LONG", 99.0, 200.0), "X", US_COST, "trail", V2CFG)[0]
    assert t.R < -0.5, f"straight-to-stop trail should be a real loss, got {t.R}"
    assert t.gross_R >= t.R - 1e-9


def test_v2_trailing_captures_winner_and_no_lookahead():
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11
    rows += [(100, 100.5, 99.9, 100, 1000)]            # entry bar
    rows += [(100, 103, 100, 102, 1000)]               # rally
    rows += [(102, 106, 102, 105, 1000)]
    rows += [(105, 107, 103, 104, 1000)]               # pullback trips the ratcheted trail
    rows += [(104, 104, 100, 100, 1000)] * 3
    df = make_df(rows)
    t = simulate_v2(df, OneShot(10, "LONG", 99.0, 200.0), "X", US_COST, "trail", V2CFG)[0]
    assert t.R > 0, f"trailing should capture the up-move, got {t.R}"
    assert t.gross_R >= t.R - 1e-9
    assert t.outcome in ("TRAIL", "TIME")


def test_v2_pyramid_risk_capped_no_early_adds():
    # immediate gap to stop on the entry bar: no add can trigger (price never reached +1R),
    # so the loss must be ~ a single base unit (no amplified loss).
    rows = [(100, 100.5, 99.8, 100, 1000)] * 11
    rows += [(100, 100.1, 98.0, 99.0, 1000)]           # entry bar gaps to stop
    rows += [(99, 99, 98, 98, 1000)] * 4
    df = make_df(rows)
    t = simulate_v2(df, OneShot(10, "LONG", 99.0, 200.0), "X", US_COST, "pyramid", V2CFG)[0]
    assert -1.4 < t.R < -0.9, f"pyramid immediate stop must be ~-1R (no add amplification), got {t.R}"


ALL = [
    test_stop_is_full_loss_not_partial,
    test_entry_fills_next_open_no_lookahead,
    test_costs_reduce_pnl_both_sides,
    test_intrabar_tie_scored_as_stop,
    test_gross_R_at_least_net_R,
    test_v2_trailing_full_loss,
    test_v2_trailing_captures_winner_and_no_lookahead,
    test_v2_pyramid_risk_capped_no_early_adds,
    test_sizing_survives_four_losses,
]

if __name__ == "__main__":
    failed = 0
    for fn in ALL:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(ALL) - failed}/{len(ALL)} passed")
    raise SystemExit(1 if failed else 0)
