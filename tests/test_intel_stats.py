"""
The self-tests of docs/plans/stats_reference.py, as pytest cases against the port in intel.stats.
The two simulation tests are smaller than the reference's (speed); the full-size versions still
run with `python docs/plans/stats_reference.py`.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from intel import stats


def test_round_trip_cost_matches_the_plan():
    rt = {b: stats.round_trip_cost(100_000, b) for b in ("large", "mid", "small")}
    assert rt["large"] == pytest.approx(0.0035, abs=2e-4)    # plan §7.3: about 0.35 / 0.55 / 0.85%
    assert rt["mid"] == pytest.approx(0.0055, abs=2e-4)
    assert rt["small"] == pytest.approx(0.0085, abs=2e-4)
    assert 0.0022 < stats.round_trip_cost(10**9, "large") - 0.001 < 0.0024


def test_cost_config_from_dict_rejects_unknown_keys():
    cfg = stats.cost_config_from({"stt_buy": 0.002, "notional_inr": 1, "comment": "x"})
    assert cfg.stt_buy == 0.002 and cfg.stt_sell == 0.001
    with pytest.raises(ValueError):
        stats.cost_config_from({"stt": 0.001})


def test_payoff():
    assert stats.payoff("NO_CALL", 0.05, 0.01) == 0.0
    assert stats.payoff("UP", 0.05, 0.01) == pytest.approx(0.04)
    assert stats.payoff("DOWN", 0.05, 0.01) == pytest.approx(-0.06)
    with pytest.raises(ValueError):
        stats.payoff("SIDEWAYS", 0.0, 0.0)


def test_week_block_ci_covers_truth_when_calls_share_weekly_shocks():
    rng = np.random.default_rng(42)
    cover = 0
    for t in range(80):
        weeks = np.repeat(np.arange(30), 20)
        vals = rng.normal(0, 0.02, 30)[weeks] + rng.normal(0, 0.04, len(weeks))
        _, lo, hi, n_weeks = stats.week_block_bootstrap_ci(vals, weeks, n_boot=400, seed=t)
        cover += lo <= 0 <= hi
    assert n_weeks == 30 and cover / 80 > 0.85


def test_calibration_and_isotonic():
    rng = np.random.default_rng(1)
    p = np.clip(rng.normal(0.68, 0.08, 4000), 0.5, 0.95)
    y = (rng.random(4000) < 0.5 + (p - 0.5) * 0.3).astype(int)
    knots = stats.isotonic_fit(p, y)
    assert np.all(np.diff(knots[1]) >= -1e-12)
    assert stats.brier_skill_score(stats.isotonic_apply(knots, p), y, 0.5) > stats.brier_skill_score(p, y, 0.5)
    lo, hi = stats.wilson(55, 100)
    assert lo < 0.55 < hi and math.isnan(stats.wilson(0, 0)[0])
    assert sum(r["n"] for r in stats.calibration_table(p, y)) == 4000


def _weekly(rng, mu, weeks, sd=0.003):
    return [mu + rng.standard_t(3) * sd / math.sqrt(3) for _ in range(weeks)]


def _crosses(series, thr):
    g = stats.RobustEProcess()
    return any(g.update(y) >= thr for y in series)


def test_eprocess_rarely_promotes_a_useless_change_and_finds_a_big_one():
    rng = np.random.default_rng(42)
    thr = stats.elond_threshold(1, 0)
    assert thr == pytest.approx(16.45, abs=0.01)
    false = np.mean([_crosses(_weekly(rng, 0.0, 104), thr) for _ in range(300)])
    real = np.mean([_crosses(_weekly(rng, 0.004, 104), thr) for _ in range(150)])
    assert false <= 1 / thr and real > 0.9


def test_elond_bar_rises_with_k():
    assert stats.elond_threshold(2, 0) > stats.elond_threshold(1, 0)
    assert stats.elond_threshold(10, 2) == pytest.approx(548, abs=1)


def test_visible_weeks_alternate_and_hold_within_a_week():
    mondays = [date(2026, 1, 5) + timedelta(weeks=i) for i in range(104)]
    vis = [stats.is_visible_week(m) for m in mondays]
    assert all(vis[i] != vis[i + 1] for i in range(103))
    assert all(stats.is_visible_week(m) == stats.is_visible_week(m + timedelta(days=6)) for m in mondays)
