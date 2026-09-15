"""
cross_sectional.py — the cross-sectional ranking engine.

Genuinely different shape from the single-symbol Strategy/simulate() path
(donchian, meanrev, breakout, ...): there is no per-trade stop/target, and
there is no `signals(bars, symbol)` call per bar. Instead, at each rebalance
date the WHOLE universe is ranked by trailing momentum, the top slice is
bought (and optionally the bottom slice sold short), held for a fixed
period, then rebalanced. P&L is measured in raw return%, not R-multiples,
because the risk unit here is "exposure to a ranked slice", not a stop-
defined loss. This is what the India/crypto/improved-US research calls for
(rank by 6-12mo return, hold 1-3mo) and an absolute breakout channel cannot
express.

No-lookahead discipline matches simulator.py exactly:
  - The SCORE at a rebalance uses only bars up to and including the decision
    bar (the bar at-or-before the rebalance timestamp on that symbol's OWN
    calendar -- no cross-symbol date alignment is assumed, since crypto
    trades weekends and equities don't).
  - The FILL (both entry and exit) happens at the OPEN of the next bar
    strictly after the decision bar, never at the decision bar's own close.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import pandas as pd


@dataclass(frozen=True)
class CrossSectionalTrade:
    symbol: str
    rebalance_time: pd.Timestamp     # the decision bar (score computed on bars up to here)
    entry_time: pd.Timestamp         # next bar strictly after rebalance_time
    exit_time: pd.Timestamp          # next bar strictly after the following rebalance
    direction: str                   # "LONG" | "SHORT"
    entry_fill: float
    exit_price: float
    gross_return: float              # (exit/entry - 1), sign-adjusted for direction
    net_return: float                # after round-trip costs
    score: float                     # trailing-return momentum score at rebalance
    rank: int                        # 1 = strongest long candidate (or strongest short)
    universe_size: int                # symbols with a valid score at this rebalance
    bars_held: int


def _bar_at_or_before(df: pd.DataFrame, t: pd.Timestamp) -> Optional[int]:
    pos = df.index.searchsorted(t, side="right") - 1
    return int(pos) if pos >= 0 else None


def _next_bar_after(df: pd.DataFrame, pos: int) -> Optional[int]:
    nxt = pos + 1
    return nxt if nxt < len(df) else None


def simulate_cross_sectional(
    universe: Dict[str, pd.DataFrame],
    formation_bars: int,
    hold_bars: int,
    top_frac: float,
    bottom_frac: float = 0.0,
    cost_bps_roundtrip: float = 10.0,
    min_universe: int = 3,
) -> List[CrossSectionalTrade]:
    """
    universe: symbol -> OHLCV DataFrame (needs Open, Close), each on its OWN
        calendar -- no cross-symbol alignment assumed.
    formation_bars: trailing lookback, in EACH symbol's own bars, for the score.
    hold_bars: spacing between rebalances, counted on the reference symbol's
        (the longest series in the universe) own bars.
    top_frac / bottom_frac: fraction of the ranked, scoreable universe to go
        long / short at each rebalance.
    cost_bps_roundtrip: total round-trip cost (bps) subtracted from net_return.
    min_universe: skip a rebalance if fewer than this many symbols have a
        valid score -- ranking 1 or 2 names is not cross-sectional ranking.
    """
    if not universe:
        return []
    ref_sym = max(universe, key=lambda s: len(universe[s]))
    ref_df = universe[ref_sym]

    rebalance_positions = list(range(formation_bars, len(ref_df) - 1, hold_bars))
    trades: List[CrossSectionalTrade] = []

    for i in range(len(rebalance_positions) - 1):
        t = ref_df.index[rebalance_positions[i]]
        t_next = ref_df.index[rebalance_positions[i + 1]]

        scores: Dict[str, Tuple[float, int]] = {}
        for sym, df in universe.items():
            b = _bar_at_or_before(df, t)
            if b is None or b < formation_bars:
                continue
            close_now = float(df["Close"].iloc[b])
            close_then = float(df["Close"].iloc[b - formation_bars])
            if close_then <= 0:
                continue
            scores[sym] = (close_now / close_then - 1.0, b)

        if len(scores) < min_universe:
            continue

        ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
        n = len(ranked)
        n_long = max(1, int(round(n * top_frac)))
        n_short = max(0, int(round(n * bottom_frac))) if bottom_frac > 0 else 0

        longs = ranked[:n_long]
        shorts = ranked[-n_short:] if n_short > 0 else []

        for rank_i, (sym, (score, b)) in enumerate(longs, start=1):
            tr = _build_trade(universe[sym], sym, t_next, b, "LONG", score,
                              rank_i, n, cost_bps_roundtrip)
            if tr:
                trades.append(tr)
        for rank_i, (sym, (score, b)) in enumerate(shorts, start=1):
            tr = _build_trade(universe[sym], sym, t_next, b, "SHORT", score,
                              n - n_short + rank_i, n, cost_bps_roundtrip)
            if tr:
                trades.append(tr)

    return trades


def _build_trade(df, sym, t_next, decision_bar, direction, score, rank,
                 universe_size, cost_bps) -> Optional[CrossSectionalTrade]:
    entry_pos = _next_bar_after(df, decision_bar)
    if entry_pos is None:
        return None
    entry_time = df.index[entry_pos]
    entry_fill = float(df["Open"].iloc[entry_pos])

    exit_decision = _bar_at_or_before(df, t_next)
    if exit_decision is None or exit_decision <= entry_pos:
        return None
    exit_pos = _next_bar_after(df, exit_decision)
    if exit_pos is None:
        return None
    exit_time = df.index[exit_pos]
    exit_price = float(df["Open"].iloc[exit_pos])

    if entry_fill <= 0:
        return None
    raw_ret = exit_price / entry_fill - 1.0
    gross = raw_ret if direction == "LONG" else -raw_ret
    net = gross - (cost_bps / 10000.0)

    return CrossSectionalTrade(
        symbol=sym, rebalance_time=df.index[decision_bar], entry_time=entry_time,
        exit_time=exit_time, direction=direction, entry_fill=entry_fill,
        exit_price=exit_price, gross_return=gross, net_return=net, score=score,
        rank=rank, universe_size=universe_size, bars_held=exit_pos - entry_pos,
    )


def compute_cs_metrics(trades: List[CrossSectionalTrade]) -> dict:
    if not trades:
        return {"n_trades": 0, "win_rate": 0.0, "expectancy_pct": 0.0,
                "profit_factor": None, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
                "total_return_pct": 0.0}
    wins = [t.net_return for t in trades if t.net_return > 0]
    losses = [t.net_return for t in trades if t.net_return <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    if gross_loss > 0:
        pf = gross_win / gross_loss
    elif gross_win > 0:
        pf = float("inf")
    else:
        pf = None
    return {
        "n_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "expectancy_pct": sum(t.net_return for t in trades) / len(trades) * 100,
        "profit_factor": pf,
        "avg_win_pct": (gross_win / len(wins) * 100) if wins else 0.0,
        "avg_loss_pct": (-gross_loss / len(losses) * 100) if losses else 0.0,
        "total_return_pct": sum(t.net_return for t in trades) * 100,
    }


def portfolio_equity_curve(trades: List[CrossSectionalTrade],
                           starting_capital: float = 100.0) -> List[Tuple[pd.Timestamp, float]]:
    """Equal-weight across each rebalance period's selected positions, compounded
    sequentially -- the portfolio-level view, not just per-position stats."""
    by_period: Dict[pd.Timestamp, List[float]] = defaultdict(list)
    for t in trades:
        by_period[t.rebalance_time].append(t.net_return)
    capital = starting_capital
    curve = [(None, capital)]
    for p in sorted(by_period):
        period_ret = sum(by_period[p]) / len(by_period[p])
        capital *= (1 + period_ret)
        curve.append((p, capital))
    return curve


def max_drawdown_pct(curve: List[Tuple[pd.Timestamp, float]]) -> float:
    peak = curve[0][1]
    worst = 0.0
    for _, cap in curve:
        peak = max(peak, cap)
        dd = (cap / peak - 1.0) * 100
        worst = min(worst, dd)
    return worst
