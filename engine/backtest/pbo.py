"""
pbo.py — Probability of Backtest Overfitting, via Combinatorially Symmetric
Cross-Validation (CSCV), per Bailey, Borwein, Lopez de Prado & Zhu (2015).

The question this answers, concretely: when we picked the BEST of several
candidate configurations based on their historical performance (e.g.
run_improve.py picking "pyramid" as the best of 4 Donchian exit variants),
how often would that same picking process have chosen an OOS LOSER instead,
if we'd only gotten to see a different slice of history? If the in-sample
winner is *usually* also the out-of-sample winner across many resampled
splits, the pick is real skill. If the in-sample winner is a below-median
performer out-of-sample about half the time, the selection was noise, not
signal -- PICKING among many backtested variants is itself a place
overfitting hides, separate from whether any single variant's own OOS test
looked honest.

Method (S time blocks, N candidate variants):
  1. Build an N x S matrix of each variant's mean return in each time block.
  2. For every way of splitting the S blocks into two disjoint equal halves
     (a "combination"), the first half is IS, the second is OOS (and vice
     versa -- both directions are tested, which is what makes this
     "symmetric").
  3. Pick the variant with the best IS mean. Find its OOS rank among all N
     variants (convention: rank 1 = worst OOS, N = best OOS).
  4. r = rank / (N+1) (its OOS percentile). lambda = logit(r).
  5. PBO = fraction of splits where lambda <= 0 -- the IS-winner performed
     BELOW the cross-sectional OOS median.

Honest limitations, stated rather than hidden: block-level performance uses
an unweighted mean of per-trade R within each block (not trade-count-
weighted), and S is chosen for a reasonable number of combinations, not
the paper's specific recommendation. This is a faithful, working
simplification -- not the full apparatus of the original paper's software
package.
"""
from __future__ import annotations

import math
from itertools import combinations
from typing import Dict, List, Callable
import numpy as np
import pandas as pd


def _time_blocks(all_times: pd.DatetimeIndex, n_blocks: int) -> List[pd.Timestamp]:
    """Quantile boundaries over the pooled timestamps of ALL variants, so every
    variant is binned against the SAME shared blocks."""
    q = np.linspace(0, 1, n_blocks + 1)
    return sorted(all_times.quantile(q).tolist()) if hasattr(all_times, "quantile") else \
           list(np.quantile(all_times.values.astype("int64"), q).astype("datetime64[ns]"))


def build_block_matrix(variant_trades: Dict[str, List], n_blocks: int = 10) -> pd.DataFrame:
    """variant_trades: {variant_name: [trades with .signal_time and .R]}.
    Returns an (n_blocks x n_variants) DataFrame of each variant's mean R
    per shared time block."""
    all_times = pd.Series(
        [t.signal_time for trades in variant_trades.values() for t in trades]
    )
    edges = pd.Series(sorted(all_times)).quantile(np.linspace(0, 1, n_blocks + 1)).tolist()
    edges[0] -= pd.Timedelta(seconds=1)
    edges[-1] += pd.Timedelta(seconds=1)

    cols = {}
    for name, trades in variant_trades.items():
        df = pd.DataFrame({"t": [tr.signal_time for tr in trades], "R": [tr.R for tr in trades]})
        df["block"] = pd.cut(df["t"], bins=edges, labels=False, include_lowest=True)
        block_means = df.groupby("block")["R"].mean().reindex(range(n_blocks))
        cols[name] = block_means
    return pd.DataFrame(cols)


def compute_pbo(block_matrix: pd.DataFrame) -> dict:
    """block_matrix: rows = time blocks, columns = variant names, values = mean R.
    Returns PBO and the full list of per-split lambda values for inspection."""
    n_blocks = len(block_matrix)
    variants = list(block_matrix.columns)
    n_variants = len(variants)
    half = n_blocks // 2

    lambdas = []
    below_median_count = 0
    total = 0
    for is_blocks in combinations(range(n_blocks), half):
        is_blocks = set(is_blocks)
        oos_blocks = set(range(n_blocks)) - is_blocks
        for is_set, oos_set in [(is_blocks, oos_blocks), (oos_blocks, is_blocks)]:
            is_means = block_matrix.iloc[sorted(is_set)].mean()
            oos_means = block_matrix.iloc[sorted(oos_set)].mean()
            if is_means.isna().any() or oos_means.isna().any():
                continue
            winner = is_means.idxmax()
            # rank convention: 1 = worst OOS, N = best OOS
            oos_rank_ascending = oos_means.rank(method="average", ascending=True)
            rank = oos_rank_ascending[winner]
            r = rank / (n_variants + 1)
            r = min(max(r, 1e-6), 1 - 1e-6)
            lam = math.log(r / (1 - r))
            lambdas.append(lam)
            total += 1
            if lam <= 0:
                below_median_count += 1

    pbo = below_median_count / total if total else None
    return {
        "pbo": pbo,
        "n_splits_tested": total,
        "n_blocks": n_blocks,
        "n_variants": n_variants,
        "variants": variants,
        "lambdas": lambdas,
    }
