"""
Grader v2 on synthetic prices with hand-computable answers (plan v2 §7 acceptance: costs,
NO_CALL = 0, baselines, paired differences, sealing). No network.

Prices: ^NSEI is flat, so excess = the stock's own return.
  AAA.NS  100 through 2026-10-12, 110 after  -> +10% from a 10-12 entry   (turnover Rs 20 cr: mid)
  BBB.NS   50 through 2026-10-12,  45 after  -> -10% from a 10-12 entry,
                                                 0% from a 10-13 entry    (turnover Rs 0.5 cr: small)
Week of 2026-10-12 is visible; week of 2026-10-05 is holdout.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone

import numpy as np
import pandas as pd
import pytest

from intel import grader_v2 as g2
from intel import stats
from intel.ledger import append_call, append_decision

UTC = timezone.utc
NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)            # 20:30 IST Friday: that day's NSE bar is final
CFG = g2.load_config()
COST_MID = stats.round_trip_cost(100_000, "mid", CFG["_cost_cfg"])
COST_SMALL = stats.round_trip_cost(100_000, "small", CFG["_cost_cfg"])
DAYS = pd.bdate_range("2024-06-03", "2026-11-30")


def _series(before, after, volume):
    close = np.where(DAYS <= pd.Timestamp("2026-10-12"), before, after).astype(float)
    return pd.DataFrame({"Close": close, "Volume": float(volume)}, index=DAYS)


def _fx():
    rng = np.random.default_rng(7)
    return pd.DataFrame({"Close": 83 * np.exp(np.cumsum(rng.normal(0, 0.003, len(DAYS))))}, index=DAYS)


PRICES = {"AAA.NS": _series(100, 110, 2e6), "BBB.NS": _series(50, 45, 1e5),
          "^NSEI": _series(20000, 20000, 1e6), "USDINR=X": _fx()}


def fake_prices(symbols, start):
    return {s: (PRICES[s][PRICES[s].index >= pd.Timestamp(start)] if s in PRICES else None) for s in symbols}


def _flag(fid, kind, sym, observed, hint=None, desc=None):
    return {"flag_id": fid, "kind": kind, "symbol": sym, "instrument": f"{sym}.NS" if sym else None,
            "importance": 0.8, "direction_hint": hint, "summary": "s", "observed_at": observed, "seen_at": observed,
            "evidence": [{"url": "https://x", "publisher": "NSE", "published_at": observed, "claim": "c"}],
            "extra": {"desc": desc} if desc else {}}


FLAGS = [
    _flag("f1", "sast_acquisition", "AAA", "2026-10-12T05:00:00Z"),                 # 10:30 IST -> 10-12 close
    _flag("f2", "news_multi_source", "BBB", "2026-10-12T05:00:00Z"),
    _flag("f3", "volume_breakout", "BBB", "2026-10-12T10:00:00Z", hint="DOWN"),     # 15:30 IST -> 10-13 close
    _flag("f4", "sast_acquisition", "AAA", "2026-10-05T05:00:00Z"),                 # holdout week
    _flag("f5", "regulator_release", None, "2026-10-12T05:00:00Z"),
]


def _dec(**o):
    d = {"track": "agent", "role": "champion", "model": "claude-opus-5-5", "playbook_version": "v1",
         "process_version": "p0", "run_id": "r", "thesis": "t", "rules_applied": [],
         "evidence": [{"url": "https://x", "publisher": "NSE", "published_at": "2026-10-12T04:00:00Z", "claim": "c"}]}
    d.update(o)
    return d


def _call(**o):
    return _dec(signal_family="insider_buy", **o)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("world")
    data, ledger = tmp_path / "data", tmp_path / "ledger"
    (data / "archive" / "flags").mkdir(parents=True)
    (data / "archive" / "flags" / "2026-10-12.jsonl").write_text("\n".join(json.dumps(f) for f in FLAGS) + "\n")
    # like the real first scout run: a filing published 29 Sep, first seen (archived) 1 Oct, no seen_at
    backfill = {k: v for k, v in _flag("f6", "bulk_deal_buy", "BBB", "2026-09-29T07:57:00Z").items() if k != "seen_at"}
    (data / "archive" / "flags" / "2026-10-01.jsonl").write_text(json.dumps(backfill) + "\n")
    at = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    # the real excluded call: same id the live ledger holds
    append_call(str(ledger), {"track": "agent", "version": "v1", "run_id": "r", "instrument": "USDINR=X",
                              "direction": "UP", "horizon_days": 5, "probability": 0.56, "signal_family": "fx_macro",
                              "thesis": "t", "evidence": [{"url": "https://x", "publisher": "p",
                                                           "published_at": "2026-10-01T00:00:00Z", "claim": "c"}]},
                now=at("2026-10-02T03:41:24Z"))
    append_decision(str(ledger), _call(opportunity_id="opp:AAA:aaaaaaaaaaaa", flag_ids=["f1"], instrument="AAA.NS",
                                       decision="UP", horizon_days=5, probability=0.6), now=at("2026-10-12T06:00:00Z"))
    append_decision(str(ledger), _dec(opportunity_id="opp:BBB:bbbbbbbbbbbb", flag_ids=["f3"], instrument="BBB.NS",
                                      decision="NO_CALL", no_call_reason="priced_in"), now=at("2026-10-12T11:00:00Z"))
    append_decision(str(ledger), _call(opportunity_id="opp:BBB:cccccccccccc", flag_ids=["f2"], instrument="BBB.NS",
                                       decision="DOWN", horizon_days=5, probability=0.7), now=at("2026-10-12T06:00:01Z"))
    append_decision(str(ledger), _call(opportunity_id="opp:AAA:dddddddddddd", flag_ids=["f1"], instrument="AAA.NS",
                                       decision="UP", horizon_days=5, probability=0.6), now=at("2026-10-14T06:00:00Z"))
    out = tmp_path / "out"
    g2.run(str(data), str(ledger), None, str(out), get_prices=fake_prices, now=NOW, cfg=CFG)
    rows = lambda n: [json.loads(l) for l in (out / "scores" / f"{n}.jsonl").read_text().splitlines()]
    return {"out": out, "flags": {r["flag_id"]: r for r in rows("opportunities_scored")},
            "decs": rows("decisions_scored"), "views": rows("macro_views_scored")}


# ── entry rule ────────────────────────────────────────────────────────────────
DATES = [d.date() for d in pd.bdate_range("2026-09-28", "2026-10-09") if d.date() != date(2026, 10, 2)]


@pytest.mark.parametrize("decided,expected", [
    ("2026-09-30T09:29:00Z", date(2026, 9, 30)),     # 14:59 IST: that day's close
    ("2026-09-30T09:30:00Z", date(2026, 10, 1)),     # 15:00 IST: next close
    ("2026-10-02T05:00:00Z", date(2026, 10, 5)),     # holiday: next trading day
    ("2026-10-03T05:00:00Z", date(2026, 10, 5)),     # Saturday
    ("2026-10-09T20:00:00Z", None),                  # no bar after yet: pending
])
def test_entry_rule(decided, expected):
    i = g2.entry_index(DATES, datetime.fromisoformat(decided.replace("Z", "+00:00")), time(15, 0))
    assert (DATES[i] if i is not None else None) == expected


def test_nse_bar_is_final_only_after_1600_ist():
    assert g2.last_complete_bar("AAA.NS", datetime(2026, 10, 12, 10, 0, tzinfo=UTC)) == date(2026, 10, 11)
    assert g2.last_complete_bar("AAA.NS", datetime(2026, 10, 12, 10, 31, tzinfo=UTC)) == date(2026, 10, 12)
    assert g2.last_complete_bar("USDINR=X", datetime(2026, 10, 12, 23, 0, tzinfo=UTC)) == date(2026, 10, 11)


# ── every flag, B0, costs ─────────────────────────────────────────────────────
def test_flags_graded_with_b0_net_of_costs(world):
    f1 = world["flags"]["f1"]
    h5 = f1["horizons"]["5"]
    assert f1["liquidity_bucket"] == "mid" and f1["cost"] == pytest.approx(COST_MID)
    assert (h5["entry_date"], h5["exit_date"]) == ("2026-10-12", "2026-10-19")
    assert h5["excess"] == pytest.approx(0.10) and h5["b0_net"] == pytest.approx(0.10 - COST_MID)
    assert f1["horizons"]["20"]["status"] == "PENDING"


def test_volume_breakout_follows_the_breakout_day_and_news_is_graded_but_not_in_b0(world):
    f3, f2 = world["flags"]["f3"], world["flags"]["f2"]
    assert f3["b0_direction"] == "DOWN" and f3["liquidity_bucket"] == "small"
    assert f3["horizons"]["5"]["entry_date"] == "2026-10-13"                 # after the 15:00 IST cutoff
    assert f3["horizons"]["5"]["b0_net"] == pytest.approx(-COST_SMALL)      # flat after entry: pays costs only
    assert f2["status"] == "GRADED" and f2["b0_direction"] is None and "b0_net" not in f2["horizons"]["5"]


def test_typed_announcements_follow_the_approved_table():
    ann = _flag("a", "announcement", "AAA", "2026-10-12T05:00:00Z", desc="Bagging/Receiving of orders/contracts")
    assert CFG["typed_announcement_directions"]["active"] is True            # owner approval 2026-10-02
    assert g2.b0_direction(ann, CFG) == "UP"
    assert g2.b0_direction(dict(ann, extra={"desc": "Acquisition"}), CFG) is None        # ambiguous: out
    assert g2.b0_direction(dict(ann, extra={"desc": "Board meeting"}), CFG) is None      # untyped: out
    off = dict(CFG, typed_announcement_directions=dict(CFG["typed_announcement_directions"], active=False))
    assert g2.b0_direction(ann, off) is None


def test_macro_move_follows_its_hint_in_b0():
    mv = dict(_flag("m", "macro_move", None, "2026-10-12T05:00:00Z"), symbol="USDINR=X", instrument="USDINR=X")
    assert CFG["b0_default_direction"]["macro_move"] == "hint"
    assert g2.b0_direction(dict(mv, direction_hint="DOWN"), CFG) == "DOWN"
    assert g2.b0_direction(dict(mv, direction_hint=None), CFG) is None       # flags archived before the hint


def test_backfilled_flag_enters_no_earlier_than_the_scouts_saw_it(world):
    f6 = world["flags"]["f6"]
    assert f6["seen_at"] == "2026-10-01T23:59:59Z"                           # end of its archive day
    assert f6["horizons"]["1"]["entry_date"] == "2026-10-02"                 # not 29 Sep: no look-ahead


def test_symbolless_flags_are_recorded_not_dropped(world):
    assert world["flags"]["f5"]["status"] == "NO_INSTRUMENT"


# ── decisions ────────────────────────────────────────────────────────────────
def test_call_no_call_and_paired_b0(world):
    by = {(r["instrument"], r["decision"], r["status"]): r for r in world["decs"]}
    up = by[("AAA.NS", "UP", "GRADED")]
    assert up["net"] == pytest.approx(0.10 - COST_MID) and up["hit"] is True
    assert up["brier"] == pytest.approx(0.16) and up["minus_b0"] == pytest.approx(0.0) and up["tradable"] is True
    nc = by[("BBB.NS", "NO_CALL", "GRADED")]
    assert nc["net"] == 0.0 and nc["b0_direction"] == "DOWN"
    assert nc["minus_b0"] == pytest.approx(COST_SMALL)                      # abstaining saved the costs
    down = by[("BBB.NS", "DOWN", "GRADED")]
    assert down["net"] == pytest.approx(0.10 - COST_SMALL) and down["tradable"] is None
    assert "minus_b0" not in down                                            # news flag: no B0 pair


def test_restating_an_open_call_is_reaffirm(world):
    assert [r["status"] for r in world["decs"] if r["instrument"] == "AAA.NS"] == ["GRADED", "REAFFIRM"]


def test_forced_call_is_excluded_and_becomes_a_macro_view(world):
    (exc,) = [r for r in world["decs"] if r["status"] == "EXCLUDED"]
    assert exc["decision_id"] == "agent-20261002T034124Z-001" and "forced" in exc["excluded_reason"]
    (v,) = world["views"]
    assert v["source"] == "excluded_call" and v["p_up"] == 0.56 and v["status"] == "GRADED"
    assert v["base_date"] == "2026-10-02" and v["p_clim"] is not None
    assert v["brier"] == pytest.approx((0.56 - v["up"]) ** 2)


# ── sealing and the summary ───────────────────────────────────────────────────
def test_holdout_rows_are_sealed(world):
    f4 = world["flags"]["f4"]
    assert f4["sealed"] is True and f4["visible"] is False
    assert not {"horizons", "cost", "b0_direction", "turnover_cr_20d_median"} & set(f4)


def test_summary_and_stats_use_visible_weeks_only(world):
    out = world["out"]
    champ = json.loads((out / "stats" / "visible" / "champion.json").read_text())["rows"]
    (c,) = champ
    assert (c["opportunities"], c["calls"], c["no_calls"]) == (3, 2, 1)
    expect = np.mean([0.10 - COST_MID, 0.0, 0.10 - COST_SMALL])
    assert c["net_per_opportunity"]["mean"] == pytest.approx(expect)
    b0 = json.loads((out / "stats" / "visible" / "b0_by_kind.json").read_text())["rows"]
    sast5 = next(r for r in b0 if r["kind"] == "sast_acquisition" and r["horizon"] == 5)
    assert sast5["b0_n"] == 1                                                # f4 (holdout) not counted
    md = (out / "stats" / "summary.md").read_text(encoding="utf-8")
    assert "Champion vs mechanical baseline" in md and "agent-20261002T034124Z-001" in md
    assert "holdout 1 (sealed)" in md
