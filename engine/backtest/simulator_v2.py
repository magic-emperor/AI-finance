"""
simulator_v2.py — Advanced EXIT modes, built as a COPY (baseline simulator.py is
never touched, per the user's rule). Same honesty guarantees as v1:
  - no look-ahead: decide on bars[:i], fill at i+1 open; trailing stop at bar j
    uses only closes through j-1 (updated AFTER the stop check).
  - symmetric costs on every leg (entry + each exit).
  - a stop hit is a real loss in R (no partial credit).
  - intrabar tie → assume the STOP/trail was hit first (pessimistic).

Modes:
  "trail"         — pure ATR trailing stop (let winners run, no fixed target).
  "partial_trail" — take `partial_fraction` off at T1 (t1_R), move the rest to
                    breakeven and trail it (the user's T1/T2 idea).
  "pyramid"       — add units at +1R/+2R with DECREASING size, shared trailing
                    stop moved to breakeven after the first add (risk-controlled).

R is always normalised by the INITIAL risk per unit (entry_fill → initial stop),
so v2 numbers are directly comparable to the v1 baseline.
"""
from __future__ import annotations

from typing import List
import pandas as pd

from engine.strategy.base import Strategy
from engine.backtest.simulator import CompletedTrade
from engine.indicators import calc_atr


def _exit_fill(direction, price, slip):
    return price * (1 - slip) if direction == "LONG" else price * (1 + slip)


def _leg_net_R(direction, entry_fill, exit_price, risk_net, slip, fee):
    ef = _exit_fill(direction, exit_price, slip)
    gross = (ef - entry_fill) if direction == "LONG" else (entry_fill - ef)
    net = gross - fee * (entry_fill + ef)
    return net / risk_net


def _leg_gross_R(direction, raw_entry, exit_price, risk_gross):
    move = (exit_price - raw_entry) if direction == "LONG" else (raw_entry - exit_price)
    return move / risk_gross


def simulate_v2(df: pd.DataFrame, strategy: Strategy, symbol: str, costs: dict,
                mode: str, exit_cfg: dict, one_trade_per_day: bool = True) -> List[CompletedTrade]:
    fee = (costs.get("fee_bps", 0.0) + costs.get("extra_bps", 0.0)) / 10000.0
    slip = costs.get("slippage_bps", 0.0) / 10000.0
    atr_period = exit_cfg["atr_period"]
    mult = exit_cfg["trail_atr_mult"]
    max_hold = exit_cfg["max_hold_bars"]

    n = len(df)
    if n < 3:
        return []
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    index = df.index

    trades: List[CompletedTrade] = []
    i = 0
    while i < n - 1:
        sig = strategy.signals(df.iloc[: i + 1], symbol)
        if sig is None:
            i += 1
            continue

        entry_idx = i + 1
        d = sig.direction
        long = d == "LONG"
        raw_entry = float(opens[entry_idx])
        entry_fill = raw_entry * (1 + slip) if long else raw_entry * (1 - slip)
        init_stop = sig.stop
        risk_net = (entry_fill - init_stop) if long else (init_stop - entry_fill)
        risk_gross = (raw_entry - init_stop) if long else (init_stop - raw_entry)
        atr_ref = calc_atr(df.iloc[: i + 1], atr_period)
        if risk_net <= 0 or risk_gross <= 0 or atr_ref <= 0:
            i += 1
            continue

        last_j = min(entry_idx + max_hold, n - 1)
        res = _manage(mode, long, d, highs, lows, closes, entry_idx, last_j,
                      entry_fill, raw_entry, init_stop, risk_net, risk_gross,
                      atr_ref, mult, slip, fee, exit_cfg)
        exit_idx = res["exit_idx"]

        trades.append(CompletedTrade(
            symbol=symbol, signal_time=index[i], entry_time=index[entry_idx],
            exit_time=index[exit_idx], direction=d, entry_fill=entry_fill,
            exit_price=res["exit_price"], stop=init_stop, target=sig.target,
            risk_per_unit=risk_net, R=res["net_R"], gross_R=res["gross_R"],
            mfe_r=res["mfe_r"], mae_r=res["mae_r"], outcome=res["outcome"],
            conviction=sig.conviction, reason=f"{mode}:{sig.reason}",
            bars_held=exit_idx - entry_idx,
        ))

        i = exit_idx + 1
        if one_trade_per_day:
            entry_date = index[entry_idx].date()
            while i < n and index[i].date() <= entry_date:
                i += 1

    return trades


def _excursions(long, hi_max, lo_min, raw_entry, risk_gross):
    if long:
        return (hi_max - raw_entry) / risk_gross, (raw_entry - lo_min) / risk_gross
    return (raw_entry - lo_min) / risk_gross, (hi_max - raw_entry) / risk_gross


def _manage(mode, long, d, highs, lows, closes, entry_idx, last_j, entry_fill,
            raw_entry, init_stop, risk_net, risk_gross, atr_ref, mult, slip, fee, cfg):
    hi_max, lo_min = float("-inf"), float("inf")
    highest_close = float("-inf")
    lowest_close = float("inf")
    trail = init_stop

    def stop_hit(lo, hi):
        return (lo <= trail) if long else (hi >= trail)

    def update_trail(cl):
        nonlocal trail, highest_close, lowest_close
        if long:
            highest_close = max(highest_close, cl)
            nt = highest_close - mult * atr_ref
            if nt > trail:
                trail = nt
        else:
            lowest_close = min(lowest_close, cl)
            nt = lowest_close + mult * atr_ref
            if nt < trail:
                trail = nt

    # ── pure trailing ──
    if mode == "trail":
        exit_price = outcome = exit_idx = None
        for j in range(entry_idx, last_j + 1):
            hi, lo, cl = float(highs[j]), float(lows[j]), float(closes[j])
            hi_max, lo_min = max(hi_max, hi), min(lo_min, lo)
            if stop_hit(lo, hi):
                exit_price, outcome, exit_idx = trail, "TRAIL", j
                break
            update_trail(cl)
        if exit_price is None:
            exit_idx, exit_price, outcome = last_j, float(closes[last_j]), "TIME"
        net_R = _leg_net_R(d, entry_fill, exit_price, risk_net, slip, fee)
        gross_R = _leg_gross_R(d, raw_entry, exit_price, risk_gross)
        mfe, mae = _excursions(long, hi_max, lo_min, raw_entry, risk_gross)
        return dict(exit_idx=exit_idx, exit_price=exit_price, outcome=outcome,
                    net_R=net_R, gross_R=gross_R, mfe_r=mfe, mae_r=mae)

    # ── partial at T1, then trail the remainder ──
    if mode == "partial_trail":
        pf = cfg["partial_fraction"]
        t1 = entry_fill + cfg["t1_R"] * risk_net if long else entry_fill - cfg["t1_R"] * risk_net
        phase = 1
        R_t1_net = R_t1_gross = 0.0
        exit_price = outcome = exit_idx = None
        for j in range(entry_idx, last_j + 1):
            hi, lo, cl = float(highs[j]), float(lows[j]), float(closes[j])
            hi_max, lo_min = max(hi_max, hi), min(lo_min, lo)
            if phase == 1:
                if stop_hit(lo, hi):                      # whole position out as a loss
                    net = _leg_net_R(d, entry_fill, trail, risk_net, slip, fee)
                    gross = _leg_gross_R(d, raw_entry, trail, risk_gross)
                    mfe, mae = _excursions(long, hi_max, lo_min, raw_entry, risk_gross)
                    return dict(exit_idx=j, exit_price=trail, outcome="STOP",
                                net_R=net, gross_R=gross, mfe_r=mfe, mae_r=mae)
                t1_reached = (hi >= t1) if long else (lo <= t1)
                if t1_reached:
                    R_t1_net = _leg_net_R(d, entry_fill, t1, risk_net, slip, fee)
                    R_t1_gross = _leg_gross_R(d, raw_entry, t1, risk_gross)
                    phase = 2
                    trail = entry_fill                    # move remainder to breakeven
                    highest_close = lowest_close = cl
                    continue
            else:
                if stop_hit(lo, hi):
                    R_rem = _leg_net_R(d, entry_fill, trail, risk_net, slip, fee)
                    R_rem_g = _leg_gross_R(d, raw_entry, trail, risk_gross)
                    mfe, mae = _excursions(long, hi_max, lo_min, raw_entry, risk_gross)
                    return dict(exit_idx=j, exit_price=trail, outcome="PARTIAL_TRAIL",
                                net_R=pf * R_t1_net + (1 - pf) * R_rem,
                                gross_R=pf * R_t1_gross + (1 - pf) * R_rem_g,
                                mfe_r=mfe, mae_r=mae)
                update_trail(cl)
        # time stop
        close_last = float(closes[last_j])
        R_last = _leg_net_R(d, entry_fill, close_last, risk_net, slip, fee)
        R_last_g = _leg_gross_R(d, raw_entry, close_last, risk_gross)
        mfe, mae = _excursions(long, hi_max, lo_min, raw_entry, risk_gross)
        if phase == 1:
            net_R, gross_R = R_last, R_last_g
        else:
            net_R = pf * R_t1_net + (1 - pf) * R_last
            gross_R = pf * R_t1_gross + (1 - pf) * R_last_g
        return dict(exit_idx=last_j, exit_price=close_last, outcome="TIME",
                    net_R=net_R, gross_R=gross_R, mfe_r=mfe, mae_r=mae)

    # ── pyramiding: add to winners with decreasing size, shared trailing stop ──
    if mode == "pyramid":
        levels = cfg["pyramid_levels"]
        sizes = cfg["pyramid_sizes"]
        add_prices = [entry_fill + lvl * risk_net if long else entry_fill - lvl * risk_net
                      for lvl in levels]
        added = [False] * len(levels)
        units = [(entry_fill, 1.0)]                       # (entry_price, size); base = 1 unit
        exit_price = outcome = exit_idx = None
        for j in range(entry_idx, last_j + 1):
            hi, lo, cl = float(highs[j]), float(lows[j]), float(closes[j])
            hi_max, lo_min = max(hi_max, hi), min(lo_min, lo)
            if stop_hit(lo, hi):                           # stop-first: all units out
                exit_price, outcome, exit_idx = trail, "PYRAMID", j
                break
            for k, lvl_price in enumerate(add_prices):
                reached = (hi >= lvl_price) if long else (lo <= lvl_price)
                if not added[k] and reached:
                    add_fill = lvl_price * (1 + slip) if long else lvl_price * (1 - slip)
                    units.append((add_fill, sizes[k]))
                    added[k] = True
                    if long and trail < entry_fill:
                        trail = entry_fill                # breakeven after first add
                    if (not long) and trail > entry_fill:
                        trail = entry_fill
            update_trail(cl)
        if exit_price is None:
            exit_idx, exit_price, outcome = last_j, float(closes[last_j]), "TIME"
        # total P&L per unit-of-initial-risk
        total_net = total_gross = 0.0
        ef = _exit_fill(d, exit_price, slip)
        for uentry, usize in units:
            if long:
                total_net += usize * ((ef - uentry) - fee * (uentry + ef))
                total_gross += usize * (exit_price - uentry)
            else:
                total_net += usize * ((uentry - ef) - fee * (uentry + ef))
                total_gross += usize * (uentry - exit_price)
        mfe, mae = _excursions(long, hi_max, lo_min, raw_entry, risk_gross)
        return dict(exit_idx=exit_idx, exit_price=exit_price, outcome=outcome,
                    net_R=total_net / risk_net, gross_R=total_gross / risk_gross,
                    mfe_r=mfe, mae_r=mae)

    raise ValueError(f"unknown exit mode: {mode}")
