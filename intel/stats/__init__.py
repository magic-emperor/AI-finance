"""
intel.stats — statistics for grader v2, ported from docs/plans/stats_reference.py (plan v2 §16).

Same functions and behaviour as the reference; two changes on porting:
  * cost constants live in intel/config/grader_v2.json, not in code (`cost_config_from`);
  * the self-tests are pytest cases in tests/test_intel_stats.py.

Decimals throughout: 0.01 = 1%.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, Sequence

import numpy as np


# ── 1. Costs (NSE cash delivery) ─────────────────────────────────────────────
@dataclass(frozen=True)
class CostConfig:
    stt_buy: float = 0.001
    stt_sell: float = 0.001
    stamp_buy: float = 0.00015
    exch_txn: float = 0.0000307
    sebi_fee: float = 0.000001
    gst: float = 0.18
    brokerage_per_side_inr: float = 0.0
    dp_charge_inr: float = 20.0
    slippage_one_way: Dict[str, float] = field(
        default_factory=lambda: {"large": 0.0005, "mid": 0.0015, "small": 0.0030})


def cost_config_from(d: dict) -> CostConfig:
    keys = set(CostConfig.__dataclass_fields__)
    unknown = set(d) - keys - {"notional_inr", "comment"}
    if unknown:
        raise ValueError(f"unknown cost keys: {sorted(unknown)}")
    return CostConfig(**{k: v for k, v in d.items() if k in keys})


def round_trip_cost(notional_inr: float, liquidity_bucket: str, cfg: CostConfig = CostConfig()) -> float:
    """Total round-trip cost as a fraction of notional for a buy-then-sell delivery trade."""
    pct = cfg.stt_buy + cfg.stt_sell + cfg.stamp_buy
    pct += 2 * (cfg.exch_txn + cfg.sebi_fee) * (1 + cfg.gst)
    pct += 2 * cfg.brokerage_per_side_inr * (1 + cfg.gst) / notional_inr
    pct += cfg.dp_charge_inr * (1 + cfg.gst) / notional_inr
    return pct + 2 * cfg.slippage_one_way[liquidity_bucket]


# ── 2. Payoff per decision (NO_CALL = 0, so abstaining is not free) ──────────
def payoff(decision: str, excess_return: float, cost: float) -> float:
    if decision == "NO_CALL":
        return 0.0
    if decision == "UP":
        return excess_return - cost
    if decision == "DOWN":
        return -excess_return - cost
    raise ValueError(decision)


# ── 3. Week-block bootstrap ──────────────────────────────────────────────────
def week_block_bootstrap_ci(values: Sequence[float], week_ids: Sequence, stat: Callable = np.mean,
                            n_boot: int = 4000, alpha: float = 0.05, seed: int = 0):
    """(estimate, lo, hi, n_weeks). Resamples whole weeks: same-week outcomes share shocks."""
    values, week_ids = np.asarray(values, float), np.asarray(week_ids)
    groups = [values[week_ids == w] for w in np.unique(week_ids)]
    rng = np.random.default_rng(seed)
    boots = np.array([stat(np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]))
                      for _ in range(n_boot)])
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return float(stat(values)), float(lo), float(hi), len(groups)


# ── 4. Probability scoring and calibration ───────────────────────────────────
def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def brier_skill_score(p, y, p_ref) -> float:
    """> 0 means better than the reference forecast (0.5, or the historical base rate)."""
    ref = np.full(len(y), p_ref) if np.isscalar(p_ref) else np.asarray(p_ref, float)
    return 1.0 - brier(p, y) / brier(ref, y)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    ph, den = k / n, 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def calibration_table(p, y, edges=(0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9500001)):
    p, y = np.asarray(p, float), np.asarray(y, int)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        n, k = int(m.sum()), int(y[m].sum())
        rows.append(dict(bin=f"[{lo:.2f},{min(hi, 0.95):.2f}]", n=n,
                         stated=float(p[m].mean()) if n else float("nan"),
                         realized=k / n if n else float("nan"), ci=wilson(k, n)))
    return rows


def isotonic_fit(p, y):
    """Pool-adjacent-violators: monotone map stated probability -> realized frequency."""
    order = np.argsort(p)
    x, v = np.asarray(p, float)[order], np.asarray(y, float)[order]
    blocks = [[v[i], 1.0, x[i], x[i]] for i in range(len(v))]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            m0, w0, a0, _ = blocks[i]
            m1, w1, _, b1 = blocks[i + 1]
            blocks[i] = [(m0 * w0 + m1 * w1) / (w0 + w1), w0 + w1, a0, b1]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    return (np.array([(b[2] + b[3]) / 2 for b in blocks]), np.array([b[0] for b in blocks]))


def isotonic_apply(knots, p):
    return np.interp(np.asarray(p, float), knots[0], knots[1])


# ── 5. RobustEProcess (gate and harm monitor; used from Phase 4) ─────────────
class RobustEProcess:
    LAMBDAS = np.array([0.1, 0.25, 0.5, 0.75, 0.95])

    def __init__(self, burn_in: int = 6, cap_sd: float = 3.0, floor_sd: float = 6.0, min_sd: float = 0.003):
        self.burn_in, self.cap_sd, self.floor_sd, self.min_sd = burn_in, cap_sd, floor_sd, min_sd
        self.history: list = []
        self.log_wealth = np.zeros(len(self.LAMBDAS))

    def update(self, y_week: float) -> float:
        if len(self.history) >= self.burn_in:
            sd = max(float(np.std(self.history, ddof=1)), self.min_sd)
            x = min(max(y_week, -self.floor_sd * sd), self.cap_sd * sd) / (self.floor_sd * sd)
            self.log_wealth += np.log1p(self.LAMBDAS * x)
        self.history.append(float(y_week))
        return self.e_value

    @property
    def e_value(self) -> float:
        m = self.log_wealth.max()
        return float(math.exp(m) * np.mean(np.exp(self.log_wealth - m)))


# ── 6. e-LOND online false-discovery control ─────────────────────────────────
def gamma(k: int) -> float:
    return 6.0 / (math.pi ** 2 * k * k)


def elond_threshold(k: int, promotions_so_far: int, q: float = 0.10) -> float:
    return 1.0 / (q * gamma(k) * (promotions_so_far + 1))


# ── 7. Sealed holdout by alternating weeks ───────────────────────────────────
_EPOCH_MONDAY = date(2000, 1, 3)


def week_index(d: date) -> int:
    return (d - _EPOCH_MONDAY).days // 7


def is_visible_week(d: date) -> bool:
    return week_index(d) % 2 == 1
