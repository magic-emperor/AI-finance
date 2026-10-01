"""
Tests for intel.grader -- synthetic price fixtures only, no network.

The grader decides whether the prediction agent is real, so these pin the rules
that stop it from flattering the agent: next-session entry, benchmark-relative
return, pending/ungradeable handling, reaffirm exclusion, reactive tagging.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from intel.grader import grade_calls, summarize

IDX = pd.bdate_range("2026-01-01", periods=120)
TODAY = date(2026, 12, 31)


def rising(offset=100.0, step=1.0):
    n = len(IDX)
    opens = offset + step * np.arange(n)
    return pd.DataFrame({"Open": opens, "Close": opens + 0.5}, index=IDX)


def flat():
    return pd.DataFrame({"Open": 100.0, "Close": 100.0}, index=IDX)


def provider(table):
    return lambda sym: table.get(sym)


def call(created, direction="UP", h=5, inst="ABC.NS", cid="c1", **over):
    c = {"call_id": cid, "track": "agent", "version": "v1", "created_at": created,
         "instrument": inst, "direction": direction, "horizon_days": h,
         "probability": 0.6, "signal_family": "insider_buy", "reaffirms": None}
    c.update(over)
    return c


def test_entry_is_open_of_next_session_and_exit_is_close_of_hth_session():
    px = rising()
    (g,) = grade_calls([call("2026-02-02T10:00:00Z")],
                       provider({"ABC.NS": px, "^NSEI": flat()}), TODAY)
    ei = px.index.get_loc(pd.Timestamp("2026-02-03"))
    assert g["status"] == "GRADED"
    assert g["entry_date"] == "2026-02-03"
    assert g["exit_date"] == str(px.index[ei + 4].date())
    expected = px["Close"].iloc[ei + 4] / px["Open"].iloc[ei] - 1
    assert abs(g["excess"] - expected) < 1e-12
    assert g["hit"] is True


def test_a_call_made_on_a_session_day_never_fills_at_that_days_prices():
    px = rising()
    (g,) = grade_calls([call("2026-02-03T00:30:00Z")],
                       provider({"ABC.NS": px, "^NSEI": flat()}), TODAY)
    assert g["entry_date"] == "2026-02-04"


def test_down_call_on_rising_stock_is_a_miss_with_negative_signed_excess():
    (g,) = grade_calls([call("2026-02-02T10:00:00Z", direction="DOWN")],
                       provider({"ABC.NS": rising(), "^NSEI": flat()}), TODAY)
    assert g["hit"] is False
    assert g["signed_excess"] < 0


def test_equity_return_is_measured_relative_to_nifty():
    (g,) = grade_calls([call("2026-02-02T10:00:00Z")],
                       provider({"ABC.NS": rising(), "^NSEI": rising(step=1.0)}), TODAY)
    assert abs(g["excess"]) < 0.02          # stock merely kept pace with the benchmark


def test_fx_has_no_benchmark():
    (g,) = grade_calls([call("2026-02-02T10:00:00Z", inst="USDINR=X")],
                       provider({"USDINR=X": rising()}), TODAY)
    assert g["status"] == "GRADED"


def test_pending_until_exit_bar_exists_and_is_complete():
    px = rising()
    last = str(px.index[-1].date())
    (g,) = grade_calls([call(f"{last}T10:00:00Z")],
                       provider({"ABC.NS": px, "^NSEI": flat()}), TODAY)
    assert g["status"] == "PENDING"
    (g2,) = grade_calls([call("2026-02-02T10:00:00Z")],
                        provider({"ABC.NS": px, "^NSEI": flat()}), date(2026, 2, 5))
    assert g2["status"] == "PENDING"        # exit bar is today or later: not complete


def test_missing_price_data_is_ungradeable_not_silently_dropped():
    (g,) = grade_calls([call("2026-02-02T10:00:00Z")], provider({"^NSEI": flat()}), TODAY)
    assert g["status"] == "UNGRADEABLE"
    (g2,) = grade_calls([call("2026-02-02T10:00:00Z")], provider({"ABC.NS": rising()}), TODAY)
    assert g2["status"] == "UNGRADEABLE"    # equity needs its benchmark too


def test_restating_an_open_call_is_a_reaffirm_and_excluded_from_n():
    table = {"ABC.NS": rising(), "^NSEI": flat()}
    out = grade_calls([call("2026-02-02T10:00:00Z", cid="a"),
                       call("2026-02-04T10:00:00Z", cid="b"),
                       call("2026-03-20T10:00:00Z", cid="c")], provider(table), TODAY)
    st = {g["call_id"]: g["status"] for g in out}
    assert st == {"a": "GRADED", "b": "REAFFIRM", "c": "GRADED"}
    (row,) = summarize(out)
    assert row["graded"] == 2 and row["reaffirm"] == 1


def test_explicit_reaffirms_field_is_excluded():
    out = grade_calls([call("2026-02-02T10:00:00Z", cid="a"),
                       call("2026-04-01T10:00:00Z", cid="b", reaffirms="a")],
                      provider({"ABC.NS": rising(), "^NSEI": flat()}), TODAY)
    assert {g["call_id"]: g["status"] for g in out}["b"] == "REAFFIRM"


def test_test_calls_are_ignored():
    out = grade_calls([call("2026-02-02T10:00:00Z", test=True)],
                      provider({"ABC.NS": rising(), "^NSEI": flat()}), TODAY)
    assert out == []


def test_chasing_a_move_that_already_happened_is_tagged_reactive():
    n = len(IDX)
    close = 100 + 0.2 * np.sin(np.arange(n))
    close = pd.Series(close, index=IDX)
    ei = IDX.get_loc(pd.Timestamp("2026-03-03"))
    close.iloc[ei - 1] = close.iloc[ei - 2] * 1.12
    px = pd.DataFrame({"Open": close.values, "Close": close.values}, index=IDX)
    (g,) = grade_calls([call("2026-03-02T10:00:00Z")], provider({"ABC.NS": px, "^NSEI": flat()}), TODAY)
    assert g["entry_date"] == "2026-03-03"
    assert g["reactive"] is True


def test_quiet_market_call_is_not_reactive():
    (g,) = grade_calls([call("2026-03-02T10:00:00Z")],
                       provider({"ABC.NS": rising(), "^NSEI": flat()}), TODAY)
    assert g["reactive"] is False


def test_versions_are_never_pooled():
    table = {"ABC.NS": rising(), "^NSEI": flat()}
    out = grade_calls([call("2026-02-02T10:00:00Z", cid="a", version="v1"),
                       call("2026-02-02T10:00:00Z", cid="b", version="v2")], provider(table), TODAY)
    rows = summarize(out)
    assert sorted(r["version"] for r in rows) == ["v1", "v2"]
    assert all(r["graded"] == 1 for r in rows)


def test_consistent_winner_has_small_p_but_cannot_meet_endpoint_below_min_n():
    table = {"ABC.NS": rising(), "^NSEI": flat()}
    dates = [str(IDX[i].date()) for i in range(21, 100, 8)]      # spaced past the horizon
    out = grade_calls([call(f"{d}T10:00:00Z", cid=f"c{i}") for i, d in enumerate(dates)],
                      provider(table), TODAY)
    (row,) = summarize(out)
    assert row["primary_n"] >= 8
    assert row["hit_rate"] == 1.0
    assert row["perm_p"] < 0.01
    assert row["meets_primary_endpoint"] is False        # n well under 50: no verdict
