"""
stats_reference.py — reference statistics for Prediction Agent v2 (two-speed learning).

A small, numpy-only, self-tested reference that Claude Code ports into the repo
(suggested home: intel/stats/). Each piece exists because the plan needs it:

  costs            grade every decision net of realistic Indian delivery-equity costs
  payoff           one number per decision, NO_CALL included (= 0), so abstaining is not free
  week bootstrap   confidence intervals that respect clustering (same-week calls move together)
  scoring          Brier score, Brier skill score, calibration table, isotonic recalibration
  RobustEProcess   the promotion gate and harm monitor: safe to check every week, robust to
                   fat tails, negative skew (small wins + rare big losses) and mild autocorrelation
  e-LOND           keeps the share of false promotions under control across many proposals
  visible weeks    sealed holdout: alternating weeks; the agent sees one set, gates use the other

Run `python stats_reference.py` for the self-tests (about a minute). Decimals: 0.01 = 1%.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Sequence

import numpy as np


# ---------------------------------------------------------------------------
# 1. Costs (NSE cash delivery). Researched 2026-10-01; keep these in a config file, not code.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostConfig:
    stt_buy: float = 0.001            # 0.1% STT on delivery buy
    stt_sell: float = 0.001           # 0.1% STT on delivery sell
    stamp_buy: float = 0.00015        # 0.015% stamp duty on buy
    exch_txn: float = 0.0000307       # ~0.00307% NSE transaction charge, each side
    sebi_fee: float = 0.000001        # Rs 10 per crore, each side
    gst: float = 0.18                 # on brokerage + exchange + SEBI fees
    brokerage_per_side_inr: float = 0.0   # 0 at most discount brokers for delivery; set yours
    dp_charge_inr: float = 20.0       # per scrip on the sell day (+GST); broker-specific
    # one-way slippage (half-spread + impact) by liquidity bucket; re-fit later from real fills
    slippage_one_way: tuple = (("large", 0.0005), ("mid", 0.0015), ("small", 0.0030))


def round_trip_cost(notional_inr: float, liquidity_bucket: str, cfg: CostConfig = CostConfig()) -> float:
    """Total round-trip cost as a fraction of notional for a buy-then-sell delivery trade."""
    pct = cfg.stt_buy + cfg.stt_sell + cfg.stamp_buy
    pct += 2 * (cfg.exch_txn + cfg.sebi_fee) * (1 + cfg.gst)
    pct += 2 * cfg.brokerage_per_side_inr * (1 + cfg.gst) / notional_inr
    pct += cfg.dp_charge_inr * (1 + cfg.gst) / notional_inr
    return pct + 2 * dict(cfg.slippage_one_way)[liquidity_bucket]


# ---------------------------------------------------------------------------
# 2. Payoff per decision. Measured per OPPORTUNITY (NO_CALL = 0); otherwise calling less always
#    "improves" the average and the agent drifts toward never calling.
# ---------------------------------------------------------------------------
def payoff(decision: str, excess_return: float, cost: float) -> float:
    """decision in {"UP", "DOWN", "NO_CALL"}; excess_return = instrument minus benchmark."""
    if decision == "NO_CALL":
        return 0.0
    if decision == "UP":
        return excess_return - cost
    if decision == "DOWN":
        # Retail cash equity can't be shorted overnight: grade DOWN symmetrically for learning,
        # but keep a separate `tradable` flag in the grader for anything you'd actually execute.
        return -excess_return - cost
    raise ValueError(decision)


# ---------------------------------------------------------------------------
# 3. Week-block bootstrap for reporting CIs. Calls in the same week share shocks and overlapping
#    windows, so resample whole weeks, not individual calls.
# ---------------------------------------------------------------------------
def week_block_bootstrap_ci(values: Sequence[float], week_ids: Sequence, stat: Callable = np.mean,
                            n_boot: int = 4000, alpha: float = 0.05, seed: int = 0):
    values, week_ids = np.asarray(values, float), np.asarray(week_ids)
    groups = [values[week_ids == w] for w in np.unique(week_ids)]
    rng = np.random.default_rng(seed)
    boots = np.array([stat(np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]))
                      for _ in range(n_boot)])
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return float(stat(values)), float(lo), float(hi), len(groups)


# ---------------------------------------------------------------------------
# 4. Probability scoring and calibration
# ---------------------------------------------------------------------------
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
    blocks = [[v[i], 1.0, x[i], x[i]] for i in range(len(v))]   # mean, weight, xmin, xmax
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


# ---------------------------------------------------------------------------
# 5. RobustEProcess — the promotion gate (and the harm monitor).
#
#    Feed ONE number per week: y_w = mean over that week's paired opportunities of
#    (challenger payoff - champion payoff). H0: "the change does not help" (E[y_w | past] <= 0).
#    e_value >= threshold  ->  promote (or, for the harm monitor, alert).
#
#    Why this and not a t-test: a plain weekly t-test falsely promoted too many useless changes
#    when payoffs were negatively skewed (small frequent wins, rare big losses) and/or autocorrelated
#    (8-17% instead of 5% in testing).
#    Here gains are capped at +3 sd and losses floored at -6 sd, bets are sized against the floor,
#    and the scale sd is estimated only from PAST weeks, so it stays valid when checked weekly.
#
#    min_sd matters: if early weeks happen to contain no big loss, a too-small scale lets the floor
#    clip real losses and the test turns optimistic (seen in testing: 9% false promotions with
#    min_sd = 0.1%). Set min_sd from data the challenger can't influence: the weekly sd of
#    (champion - mechanical baseline) over the previous 26 weeks; 0.3% until that exists.
# ---------------------------------------------------------------------------
class RobustEProcess:
    LAMBDAS = np.array([0.1, 0.25, 0.5, 0.75, 0.95])

    def __init__(self, burn_in: int = 6, cap_sd: float = 3.0, floor_sd: float = 6.0, min_sd: float = 0.003):
        self.burn_in, self.cap_sd, self.floor_sd, self.min_sd = burn_in, cap_sd, floor_sd, min_sd
        self.history: list[float] = []
        self.log_wealth = np.zeros(len(self.LAMBDAS))

    def update(self, y_week: float) -> float:
        if len(self.history) >= self.burn_in:
            sd = max(float(np.std(self.history, ddof=1)), self.min_sd)          # predictable scale
            x = min(max(y_week, -self.floor_sd * sd), self.cap_sd * sd) / (self.floor_sd * sd)
            self.log_wealth += np.log1p(self.LAMBDAS * x)                       # x in [-1, cap/floor]
        self.history.append(float(y_week))
        return self.e_value

    @property
    def e_value(self) -> float:
        m = self.log_wealth.max()
        return float(math.exp(m) * np.mean(np.exp(self.log_wealth - m)))


# ---------------------------------------------------------------------------
# 6. e-LOND online false-discovery control (Xu & Ramdas, 2024). Proposal number k (1, 2, 3, ...;
#    never reused, rejected ones included) is promoted iff e >= 1 / alpha_k, where
#    alpha_k = q * gamma_k * (promotions_so_far + 1). Many weak proposals raise the bar.
# ---------------------------------------------------------------------------
def gamma(k: int) -> float:
    return 6.0 / (math.pi ** 2 * k * k)          # sums to 1 over k = 1, 2, ...


def elond_threshold(k: int, promotions_so_far: int, q: float = 0.10) -> float:
    return 1.0 / (q * gamma(k) * (promotions_so_far + 1))


# ---------------------------------------------------------------------------
# 7. Sealed holdout by alternating weeks (continuous counter, so it alternates cleanly across
#    year ends). Stats the agent reads come ONLY from visible weeks; gates use the other half.
# ---------------------------------------------------------------------------
_EPOCH_MONDAY = date(2000, 1, 3)


def week_index(d: date) -> int:
    return (d - _EPOCH_MONDAY).days // 7


def is_visible_week(d: date) -> bool:
    return week_index(d) % 2 == 1


# ===========================================================================
# Self-tests
# ===========================================================================
def _weekly_series(rng, mu, weeks, rho=0.0, skewed=False, sd=0.003):
    out, prev = [], 0.0
    for _ in range(weeks):
        if skewed:   # mean zero, negatively skewed: small gains, rare big losses (sd ~0.45%)
            e = (0.0015 if rng.random() < 0.9 else -0.0135) + rng.normal(0, 0.0015)
        else:        # fat-tailed (Student t, 3 df)
            e = rng.standard_t(3) * sd / math.sqrt(3)
        prev = rho * prev + math.sqrt(1 - rho * rho) * e
        out.append(mu + prev)
    return out


def _first_crossing(series, threshold):
    g = RobustEProcess()
    for w, y in enumerate(series):
        if g.update(y) >= threshold:
            return w + 1
    return None


def _tests():
    rng = np.random.default_rng(42)

    print("1) round-trip cost at Rs 1,00,000 per trade:")
    for b in ("large", "mid", "small"):
        print(f"   {b:5s} {round_trip_cost(100_000, b) * 100:.2f}%")
    assert 0.0022 < round_trip_cost(10**9, "large") - 0.001 < 0.0024     # statutory part ~0.22%

    print("2) CI coverage when 20 calls/week share a weekly shock (truth = 0):")
    cover = naive = 0
    for t in range(250):
        weeks = np.repeat(np.arange(30), 20)
        vals = rng.normal(0, 0.02, 30)[weeks] + rng.normal(0, 0.04, len(weeks))
        _, lo, hi, _ = week_block_bootstrap_ci(vals, weeks, n_boot=600, seed=t)
        cover += lo <= 0 <= hi
        naive += abs(vals.mean()) <= 1.96 * vals.std(ddof=1) / math.sqrt(len(vals))
    print(f"   week-block CI {cover / 250:.0%} (want ~95%) vs naive per-call CI {naive / 250:.0%}")
    assert cover / 250 > 0.88

    print("3) calibration (agent says ~0.70, right ~55% of the time):")
    p = np.clip(rng.normal(0.68, 0.08, 4000), 0.5, 0.95)
    y = (rng.random(4000) < 0.5 + (p - 0.5) * 0.3).astype(int)
    knots = isotonic_fit(p, y)
    assert np.all(np.diff(knots[1]) >= -1e-12)
    print(f"   Brier {brier(p, y):.3f} vs coin flip 0.250 (skill {brier_skill_score(p, y, 0.5):+.3f}); "
          f"stated 0.70 -> calibrated {float(isotonic_apply(knots, 0.70)):.2f}; "
          f"skill after recalibration {brier_skill_score(isotonic_apply(knots, p), y, 0.5):+.3f}")

    thr1 = elond_threshold(1, 0)
    print(f"4) false promotions of a USELESS change over 2 years, checked weekly (threshold {thr1:.1f}, allowed {1 / thr1:.1%}):")
    for rho in (0.0, 0.2):
        for skewed in (False, True):
            fp = np.mean([_first_crossing(_weekly_series(rng, 0.0, 104, rho, skewed), thr1) is not None
                          for _ in range(1200)])
            print(f"   autocorrelation {rho}, skewed={skewed!s:5s}: {fp:.1%}")
            assert fp <= 1 / thr1

    print("5) chance a REAL improvement gets promoted (weekly sd of the paired difference 0.30%):")
    for mu in (0.001, 0.002, 0.004):
        hits = np.array([_first_crossing(_weekly_series(rng, mu, 156), thr1) or 999 for _ in range(800)])
        print(f"   +{mu * 100:.2f}% per opportunity: by 26 weeks {np.mean(hits <= 26):.0%}, "
              f"52 weeks {np.mean(hits <= 52):.0%}, 104 weeks {np.mean(hits <= 104):.0%}")

    print("6) e-LOND bar (q = 0.10):")
    for k, d in ((1, 0), (2, 0), (3, 1), (5, 1), (10, 2)):
        print(f"   proposal #{k} after {d} promotion(s): e >= {elond_threshold(k, d):,.0f}")

    mondays = [date(2026, 1, 5) + timedelta(weeks=i) for i in range(104)]
    vis = [is_visible_week(m) for m in mondays]
    assert all(vis[i] != vis[i + 1] for i in range(len(vis) - 1))          # strictly alternating
    assert all(is_visible_week(m) == is_visible_week(m + timedelta(days=4)) for m in mondays)
    print("7) visible weeks: strictly alternating;", sum(vis[:52]), "of 52 weeks in 2026 visible")
    print("all self-tests passed")


if __name__ == "__main__":
    _tests()
