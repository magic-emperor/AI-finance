"""
grader_v2.py — honest measurement (plan v2 §7). Deterministic, no LLM. Runs in SHADOW next to
intel/grader.py (v1) until the owner switches the agent over (§7.10).

What it grades, and why each piece exists:
  * EVERY scout flag is an opportunity, graded at 1/5/20 trading days whether or not the agent
    looked at it -- the denominator the agent's choices are judged against.
  * EVERY decision (CALL or NO_CALL, from ledger/decisions; legacy ledger/calls are mapped in).
    NO_CALL pays 0, so abstaining is not free and calling less can't flatter the average.
  * B0, the mechanical baseline: each flag taken in its kind's default direction
    (intel/config/grader_v2.json). The agent's value is champion minus B0, paired on the same
    opportunity, same entry, same horizon, same costs.
  * Net of costs for NSE equities (STT, stamp, fees, GST, DP, slippage by liquidity bucket).

Rules (config holds the numbers):
  * Entry = close of the decision day if decided before 15:00 IST on a trading day, else the
    next trading day's close. Exit = close h trading days after entry. For an uninvestigated flag
    the decision time is the LATER of its publication (observed_at) and the scouts' first
    sighting (seen_at): grading a backfilled filing from its publication time is look-ahead.
  * Excess = instrument return - ^NSEI return for .NS; indices/FX/commodities: raw return.
  * Prices: Yahoo, adjusted (owner decision 2026-10-02; bhavcopy + sector benchmarks: Phase 3).
  * Weeks alternate visible/holdout (intel.stats.is_visible_week). Holdout rows are written with
    their outcomes SEALED, and every statistic is computed from visible weeks only -- nothing the
    agent can read carries holdout results (invariant 6). Gates (Phase 4) recompute from prices.
  * Calls listed under excluded_calls are shown but never counted.

Not yet (stated, not hidden): B1 random-matched baseline, isotonic calibration map (needs >= 100
graded calls), harm monitor, dashboard.json, F&O list for DOWN tradability, process flags (Phase 2).

    python -m intel.grader_v2 --data _agent_data --ledger _ledger/ledger \
        --views _ledger/agent/macro_views.jsonl --out _agent_data
"""
from __future__ import annotations

import argparse
import bisect
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from intel import stats
from intel.ledger import read_records
from intel.schema import parse_ts
from intel.universe import benchmark_for, resolve_instrument

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "grader_v2.json")
IST = timedelta(hours=5, minutes=30)
NSE_BAR_COMPLETE_IST = time(16, 0)
CLIMATOLOGY_BARS = 500                 # ~2 years of daily bars
CLIMATOLOGY_MIN_WINDOWS = 100

Prices = Dict[str, Optional[pd.DataFrame]]
PricesProvider = Callable[[List[str], date], Prices]


# ── config and inputs ─────────────────────────────────────────────────────────
def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_cost_cfg"] = stats.cost_config_from(cfg["costs"])
    cfg["_cutoff"] = time.fromisoformat(cfg["entry_cutoff_ist"])
    return cfg


def read_flags(data_dir: str) -> List[dict]:
    """All archived flags, first sighting wins (flag_id is deterministic). Flags archived before
    the scouts stamped `seen_at` get the END of their archive day: conservative, never early."""
    out, seen = [], set()
    for path in sorted(glob.glob(os.path.join(data_dir, "archive", "flags", "*.jsonl"))):
        day_end = os.path.basename(path)[:10] + "T23:59:59Z"
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    fl = json.loads(line)
                    if fl["flag_id"] not in seen:
                        seen.add(fl["flag_id"])
                        fl.setdefault("seen_at", day_end)
                        out.append(fl)
    return out


def flag_time(flag: dict) -> datetime:
    """When our system could first have acted: the later of publication and first sighting."""
    return max(parse_ts(flag["observed_at"]), parse_ts(flag["seen_at"]))


def legacy_call_as_decision(c: dict) -> dict:
    return {"decision_id": c["call_id"], "legacy": True, "opportunity_id": None, "flag_ids": [],
            "track": c["track"], "role": "champion", "model": "unknown (legacy call)",
            "playbook_version": c["version"], "process_version": "p0", "created_at": c["created_at"],
            "instrument": c["instrument"], "decision": c["direction"], "horizon_days": c["horizon_days"],
            "probability": c["probability"], "signal_family": c["signal_family"],
            "no_call_reason": None, "reaffirms": c.get("reaffirms"), "test": c.get("test", False)}


def read_decisions(ledger_dir: str) -> List[dict]:
    decs = [dict(d, legacy=False) for d in read_records(ledger_dir, "decisions")]
    decs += [legacy_call_as_decision(c) for c in read_records(ledger_dir, "calls")]
    return sorted((d for d in decs if not d.get("test")), key=lambda d: d["created_at"])


def read_views(path: Optional[str]) -> List[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# ── prices ────────────────────────────────────────────────────────────────────
def _normalize(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or df.empty or "Close" not in df:
        return None
    df = df[[c for c in ("Close", "Volume") if c in df]].dropna(subset=["Close"]).copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df if not df.empty else None


def yahoo_adjusted(symbols: List[str], start: date) -> Prices:
    import yfinance as yf
    syms = sorted(set(symbols))
    if not syms:
        return {}
    raw = yf.download(syms, start=start.isoformat(), interval="1d", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    out: Prices = {}
    for s in syms:
        try:
            df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            df = None
        out[s] = _normalize(df)
    return out


def is_nse_listed(inst: str) -> bool:
    return inst.endswith(".NS") or inst.startswith("^NSE")


def last_complete_bar(inst: str, now: datetime) -> date:
    """Date bound: every bar dated on or before it is final. NSE bars are final after 16:00 IST;
    FX/commodity daily bars only once the UTC day is over."""
    if is_nse_listed(inst):
        ist = now + IST
        return ist.date() if ist.time() >= NSE_BAR_COMPLETE_IST else ist.date() - timedelta(days=1)
    return now.date() - timedelta(days=1)


def _dates(px: pd.DataFrame) -> List[date]:
    return [ts.date() for ts in px.index]


def entry_index(dates: List[date], decided_at: datetime, cutoff: time) -> Optional[int]:
    """Close of the decision day if decided before `cutoff` IST and that day has a bar, else the
    next bar. None while the entry bar doesn't exist yet."""
    ist = decided_at.astimezone(timezone.utc) + IST
    d = ist.date()
    i = bisect.bisect_left(dates, d)
    if i < len(dates) and dates[i] == d and ist.time() < cutoff:
        return i
    j = bisect.bisect_right(dates, d)
    return j if j < len(dates) else None


def forward(px: pd.DataFrame, bench: Optional[pd.DataFrame], needs_bench: bool, ei: int, h: int,
            last_complete: date) -> Dict[str, Any]:
    dates = _dates(px)
    xi = ei + h
    if xi >= len(px) or dates[xi] > last_complete:
        return {"status": "PENDING"}
    c = px["Close"].values
    ret = float(c[xi] / c[ei] - 1.0)
    bret = 0.0
    if needs_bench:
        if bench is None:
            return {"status": "UNGRADEABLE", "why": "no benchmark prices"}
        b = bench["Close"].reindex(pd.DatetimeIndex([px.index[ei], px.index[xi]]))
        if b.isna().any():
            return {"status": "UNGRADEABLE", "why": "benchmark missing on entry/exit date"}
        bret = float(b.iloc[1] / b.iloc[0] - 1.0)
    return {"status": "GRADED", "entry_date": dates[ei].isoformat(), "exit_date": dates[xi].isoformat(),
            "ret": ret, "bench_ret": bret, "excess": ret - bret}


def turnover_cr(px: pd.DataFrame, ei: int) -> Optional[float]:
    """Median daily turnover over the 20 sessions BEFORE entry, in Rs crore."""
    if "Volume" not in px:
        return None
    w = px.iloc[max(0, ei - 20):ei]
    if len(w) < 10:
        return None
    return float((w["Close"] * w["Volume"]).median() / 1e7)


def liquidity_bucket(t_cr: Optional[float], cfg: dict) -> Optional[str]:
    if t_cr is None:
        return None
    b = cfg["liquidity_buckets_cr"]
    if t_cr >= b["large_min"]:
        return "large"
    return "mid" if t_cr >= b["mid_min"] else "small"


def round_trip(inst: str, bucket: Optional[str], cfg: dict) -> float:
    """Only NSE cash equities carry a cost model; unknown liquidity is costed as small."""
    if not inst.endswith(".NS"):
        return 0.0
    return stats.round_trip_cost(cfg["costs"]["notional_inr"], bucket or "small", cfg["_cost_cfg"])


# ── B0 direction ──────────────────────────────────────────────────────────────
def b0_direction(flag: dict, cfg: dict) -> Optional[str]:
    rule = cfg["b0_default_direction"].get(flag["kind"])
    if rule in ("UP", "DOWN"):
        return rule
    if rule == "hint":
        return flag.get("direction_hint") if flag.get("direction_hint") in ("UP", "DOWN") else None
    if rule == "typed_announcement":
        t = cfg["typed_announcement_directions"]
        return t["table"].get((flag.get("extra") or {}).get("desc")) if t["active"] else None
    return None


def majority(dirs: List[Optional[str]]) -> Optional[str]:
    c = Counter(d for d in dirs if d)
    if not c:
        return None
    (top, n), *rest = c.most_common()
    return None if rest and rest[0][1] == n else top


# ── grading ───────────────────────────────────────────────────────────────────
def _week(ts: datetime) -> date:
    return (ts.astimezone(timezone.utc) + IST).date()


def _position(inst: Optional[str], decided_at: datetime, prices: Prices, cfg: dict, now: datetime):
    """Shared setup: (px, bench, needs_bench, entry index, last complete bar) or a status string."""
    if not inst or not resolve_instrument(inst):
        return "NO_INSTRUMENT"
    px = prices.get(inst)
    if px is None:
        return "UNGRADEABLE"
    bsym = benchmark_for(inst)
    last = last_complete_bar(inst, now)
    ei = entry_index(_dates(px), decided_at, cfg["_cutoff"])
    if ei is None or _dates(px)[ei] > last:
        return "PENDING"
    return px, (prices.get(bsym) if bsym else None), bsym is not None, ei, last


def grade_flag(flag: dict, prices: Prices, cfg: dict, now: datetime) -> dict:
    seen = flag_time(flag)
    row = {"flag_id": flag["flag_id"], "kind": flag["kind"], "symbol": flag.get("symbol"),
           "instrument": flag.get("instrument"), "observed_at": flag["observed_at"], "seen_at": flag["seen_at"],
           "importance": flag.get("importance"), "desc": (flag.get("extra") or {}).get("desc"),
           "week": stats.week_index(_week(seen)), "visible": stats.is_visible_week(_week(seen)),
           "b0_direction": b0_direction(flag, cfg)}
    pos = _position(flag.get("instrument"), seen, prices, cfg, now)
    if isinstance(pos, str):
        return dict(row, status=pos)
    px, bench, needs_bench, ei, last = pos
    t = turnover_cr(px, ei)
    bucket = liquidity_bucket(t, cfg) if row["instrument"].endswith(".NS") else None
    cost = round_trip(row["instrument"], bucket, cfg)
    row.update(turnover_cr_20d_median=t, liquidity_bucket=bucket, cost=cost, horizons={})
    for h in cfg["horizons"]:
        r = forward(px, bench, needs_bench, ei, h, last)
        if r["status"] == "GRADED" and row["b0_direction"]:
            r["b0_gross"] = stats.payoff(row["b0_direction"], r["excess"], 0.0)
            r["b0_net"] = stats.payoff(row["b0_direction"], r["excess"], cost)
        row["horizons"][str(h)] = r
    row["status"] = row["horizons"][str(cfg["primary_horizon"])]["status"]
    return row


_DECISION_FIELDS = ("decision_id", "opportunity_id", "flag_ids", "track", "role", "model", "playbook_version",
                    "process_version", "instrument", "decision", "horizon_days", "probability",
                    "no_call_reason", "created_at", "legacy", "signal_family")


def tradable(decision: str, inst: str) -> Optional[bool]:
    """UP on an NSE stock is a delivery buy. DOWN needs an F&O name (list not built yet: unknown)."""
    if not inst.endswith(".NS"):
        return False
    return True if decision == "UP" else None


def score_decision(d: dict, b0: Optional[str], prices: Prices, cfg: dict, now: datetime) -> dict:
    """Payoff of one decision at its horizon (NO_CALL: the primary horizon, payoff 0), plus the
    paired B0 payoff on the same entry, horizon and costs."""
    is_call = d["decision"] in ("UP", "DOWN")
    h = d["horizon_days"] if is_call else cfg["primary_horizon"]
    out: Dict[str, Any] = {"graded_horizon": h, "b0_direction": b0}
    if is_call:
        out["tradable"] = tradable(d["decision"], d["instrument"])
    pos = _position(d["instrument"], parse_ts(d["created_at"]), prices, cfg, now)
    if isinstance(pos, str):
        return dict(out, status=pos)
    px, bench, needs_bench, ei, last = pos
    bucket = liquidity_bucket(turnover_cr(px, ei), cfg) if d["instrument"].endswith(".NS") else None
    cost = round_trip(d["instrument"], bucket, cfg)
    r = forward(px, bench, needs_bench, ei, h, last)
    out.update(r, liquidity_bucket=bucket, cost=cost)
    if r["status"] != "GRADED":
        return out
    out["net"] = stats.payoff(d["decision"], r["excess"], cost)
    out["gross"] = stats.payoff(d["decision"], r["excess"], 0.0)
    if b0:
        out["b0_net"] = stats.payoff(b0, r["excess"], cost)
        out["minus_b0"] = out["net"] - out["b0_net"]
    if is_call:
        out["hit"] = out["gross"] > 0
        out["brier"] = (d["probability"] - float(out["hit"])) ** 2
    return out


def grade_decisions(decisions: List[dict], flags_by_id: Dict[str, dict], prices: Prices,
                    cfg: dict, now: datetime) -> List[dict]:
    """Excluded calls are shown, never scored. A CALL restating a still-open CALL (same role,
    instrument, direction) is REAFFIRM and left out of N, so repeating a view can't inflate it."""
    excluded = {e["call_id"]: e["reason"] for e in cfg["excluded_calls"]}
    open_until: Dict[tuple, Optional[str]] = {}
    out = []
    for d in decisions:
        made = _week(parse_ts(d["created_at"]))
        row = {k: d.get(k) for k in _DECISION_FIELDS}
        row.update(week=stats.week_index(made), visible=stats.is_visible_week(made))
        if d["decision_id"] in excluded:
            out.append(dict(row, status="EXCLUDED", excluded_reason=excluded[d["decision_id"]]))
            continue
        is_call = d["decision"] in ("UP", "DOWN")
        key = (d["role"], d["instrument"], d["decision"])
        still_open = key in open_until and (open_until[key] is None or d["created_at"][:10] <= open_until[key])
        if is_call and (d.get("reaffirms") or still_open):
            out.append(dict(row, status="REAFFIRM"))
            continue
        b0 = majority([b0_direction(flags_by_id[f], cfg) for f in d.get("flag_ids") or [] if f in flags_by_id])
        row.update(score_decision(d, b0, prices, cfg, now))
        if is_call:
            open_until[key] = row.get("exit_date")
        out.append(row)
    return out


def views_with_excluded(views: List[dict], cfg: dict, decisions: List[dict]) -> List[dict]:
    """Owner decision Q4: an excluded forced macro call counts as one macro-view data point."""
    by_id = {d["decision_id"]: d for d in decisions}
    out = list(views)
    for e in cfg["excluded_calls"]:
        mv, d = e.get("as_macro_view"), by_id.get(e["call_id"])
        if mv and d:
            out.append({"view_id": f"{e['call_id']}-as-view", "created_at": d["created_at"],
                        "instrument": mv["instrument"], "p_up": mv["p_up"], "horizon_days": 5,
                        "source": "excluded_call"})
    return out


def grade_views(views: List[dict], prices: Prices, now: datetime) -> List[dict]:
    """P(close 5 sessions after the view day's close > that close). Base = the view day's bar if it
    has one, else the next bar. Climatology = share of up 5-day windows in the prior ~2 years."""
    out = []
    for v in views:
        made = parse_ts(v["created_at"])
        row = {"view_id": v["view_id"], "instrument": v["instrument"], "p_up": v["p_up"],
               "created_at": v["created_at"], "source": v.get("source", "agent"),
               "week": stats.week_index(_week(made)), "visible": stats.is_visible_week(_week(made))}
        px = prices.get(v["instrument"])
        if px is None:
            out.append(dict(row, status="UNGRADEABLE"))
            continue
        dates = _dates(px)
        bi = entry_index(dates, made, time(23, 59, 59))
        if bi is None or bi + 5 >= len(px) or dates[bi + 5] > last_complete_bar(v["instrument"], now):
            out.append(dict(row, status="PENDING"))
            continue
        c = px["Close"].values
        up = bool(c[bi + 5] > c[bi])
        hist = c[max(0, bi - CLIMATOLOGY_BARS):bi + 1]
        wins = hist[5:] > hist[:-5]
        clim = float(wins.mean()) if len(wins) >= CLIMATOLOGY_MIN_WINDOWS else None
        out.append(dict(row, status="GRADED", base_date=dates[bi].isoformat(),
                        exit_date=dates[bi + 5].isoformat(), up=up, brier=(v["p_up"] - up) ** 2,
                        p_clim=clim, brier_clim=None if clim is None else (clim - up) ** 2))
    return out


# ── sealing and statistics (visible weeks only) ──────────────────────────────
_SEALED_KEEP = {"flag_id", "decision_id", "view_id", "opportunity_id", "kind", "instrument", "symbol",
                "observed_at", "created_at", "role", "decision", "status", "week", "visible", "excluded_reason"}


def sealed(row: dict) -> dict:
    """Holdout-week rows keep identity and status; every outcome field is withheld."""
    if row.get("visible", True):
        return row
    return dict({k: v for k, v in row.items() if k in _SEALED_KEEP}, sealed=True)


def _ci(values: List[float], weeks: List[int]) -> Optional[dict]:
    if not values:
        return None
    m, lo, hi, nw = stats.week_block_bootstrap_ci(values, weeks)
    return {"mean": m, "lo": lo, "hi": hi, "n": len(values), "weeks": nw}


def b0_table(flag_rows: List[dict], cfg: dict) -> List[dict]:
    rows = []
    vis = [r for r in flag_rows if r["visible"]]
    for kind in sorted({r["kind"] for r in vis}):
        for h in cfg["horizons"]:
            g = [r for r in vis if r["kind"] == kind and r.get("horizons", {}).get(str(h), {}).get("status") == "GRADED"]
            row = {"kind": kind, "horizon": h, "graded": len(g),
                   "b0_direction_rule": cfg["b0_default_direction"].get(kind)}
            in_b0 = [r for r in g if r["b0_direction"]]
            if in_b0:
                net = [r["horizons"][str(h)]["b0_net"] for r in in_b0]
                wk = [r["week"] for r in in_b0]
                k = sum(r["horizons"][str(h)]["b0_gross"] > 0 for r in in_b0)
                row.update(b0_n=len(in_b0), b0_net=_ci(net, wk), b0_hit=k / len(in_b0),
                           b0_hit_ci=stats.wilson(k, len(in_b0)),
                           mean_cost=float(np.mean([r["cost"] for r in in_b0])))
            elif g:
                row["mean_abs_excess"] = float(np.mean([abs(r["horizons"][str(h)]["excess"]) for r in g]))
            rows.append(row)
    return rows


def b0_by_bucket(flag_rows: List[dict], cfg: dict) -> List[dict]:
    h = str(cfg["primary_horizon"])
    g = [r for r in flag_rows if r["visible"] and r["b0_direction"]
         and r.get("horizons", {}).get(h, {}).get("status") == "GRADED"]
    out = []
    for b in ("large", "mid", "small", None):
        s = [r for r in g if r.get("liquidity_bucket") == b and r["instrument"].endswith(".NS")]
        if s:
            out.append({"bucket": b or "unknown", "n": len(s),
                        "b0_net": _ci([r["horizons"][h]["b0_net"] for r in s], [r["week"] for r in s])})
    return out


def champion_table(dec_rows: List[dict]) -> List[dict]:
    vis = [r for r in dec_rows if r["visible"] and r["status"] not in ("EXCLUDED", "REAFFIRM")]
    out = []
    for (role, ver) in sorted({(r["role"], r["playbook_version"]) for r in vis}):
        rs = [r for r in vis if (r["role"], r["playbook_version"]) == (role, ver)]
        graded = [r for r in rs if r["status"] == "GRADED"]
        calls = [r for r in graded if r["decision"] in ("UP", "DOWN")]
        paired = [r for r in graded if "minus_b0" in r]
        k = sum(r["hit"] for r in calls)
        row = {"role": role, "playbook_version": ver, "opportunities": len(rs), "graded": len(graded),
               "calls": sum(r["decision"] in ("UP", "DOWN") for r in rs),
               "no_calls": sum(r["decision"] == "NO_CALL" for r in rs),
               "net_per_opportunity": _ci([r["net"] for r in graded], [r["week"] for r in graded]),
               "net_per_call": _ci([r["net"] for r in calls], [r["week"] for r in calls]),
               "hit_rate": k / len(calls) if calls else None, "hit_ci": stats.wilson(k, len(calls)),
               "minus_b0": _ci([r["minus_b0"] for r in paired], [r["week"] for r in paired]),
               "no_call_reasons": dict(Counter(r["no_call_reason"] for r in rs if r["decision"] == "NO_CALL"))}
        row["call_rate"] = row["calls"] / row["opportunities"] if row["opportunities"] else None
        if calls:
            p = [r["probability"] for r in calls]
            y = [float(r["hit"]) for r in calls]
            row["brier"] = stats.brier(p, y)
            row["brier_skill_vs_half"] = stats.brier_skill_score(p, y, 0.5)
        out.append(row)
    return out


def views_table(view_rows: List[dict]) -> List[dict]:
    g = [r for r in view_rows if r["visible"] and r["status"] == "GRADED"]
    out = []
    for inst in sorted({r["instrument"] for r in g}) + ["ALL"]:
        s = [r for r in g if inst in ("ALL", r["instrument"])]
        if not s:
            continue
        clim = [r for r in s if r["brier_clim"] is not None]
        row = {"instrument": inst, "n": len(s), "brier": float(np.mean([r["brier"] for r in s]))}
        if clim:
            bc = float(np.mean([r["brier_clim"] for r in clim]))
            bs = float(np.mean([r["brier"] for r in clim]))
            row.update(brier_clim=bc, skill_vs_clim=(1 - bs / bc) if bc > 0 else None)
        out.append(row)
    return out


# ── report ────────────────────────────────────────────────────────────────────
def _pct(x, signed=True) -> str:
    return "-" if x is None else (f"{x * 100:+.2f}%" if signed else f"{x * 100:.1f}%")


def _ci_txt(c: Optional[dict]) -> str:
    return "-" if not c else f"{_pct(c['mean'])} [{_pct(c['lo'])}, {_pct(c['hi'])}] n={c['n']}, {c['weeks']} wk"


def _num(x: Optional[float], fmt: str = ".3f") -> str:
    return "-" if x is None else format(x, fmt)


def _rate_ci(rate: Optional[float], ci) -> str:
    return "-" if rate is None else f"{_pct(rate, False)} [{_pct(ci[0], False)}, {_pct(ci[1], False)}]"


def render_summary(flag_rows, dec_rows, view_rows, b0, buckets, champ, vt, cfg, now) -> str:
    vis_f = [r for r in flag_rows if r["visible"]]
    hold_f = [r for r in flag_rows if not r["visible"]]
    st = Counter(r["status"] for r in vis_f)
    L = [f"# Scoreboard v2 (shadow) — {now:%Y-%m-%d %H:%MZ}", "",
         f"Grader {cfg['version']}. Visible weeks only. Holdout weeks are sealed; only their counts are shown.",
         "Net of costs for NSE equities. Excess vs ^NSEI for .NS; raw return otherwise. Entry = decision-day close "
         "if before 15:00 IST, else next close. **Small samples: read the confidence intervals, not the means.**", "",
         "## Coverage", "",
         f"- Flags (opportunities): {len(flag_rows)} total; visible {len(vis_f)} "
         f"(graded at {cfg['primary_horizon']}d {st.get('GRADED', 0)}, pending {st.get('PENDING', 0)}, "
         f"ungradeable {st.get('UNGRADEABLE', 0)}, no instrument {st.get('NO_INSTRUMENT', 0)}); holdout {len(hold_f)} (sealed)",
         f"- Decisions: {len(dec_rows)} total; visible {sum(r['visible'] for r in dec_rows)}; "
         f"excluded {sum(r['status'] == 'EXCLUDED' for r in dec_rows)}; "
         f"reaffirm {sum(r['status'] == 'REAFFIRM' for r in dec_rows)}",
         f"- Macro views: {len(view_rows)} total; visible graded "
         f"{sum(r['visible'] and r['status'] == 'GRADED' for r in view_rows)}", "",
         "## Champion vs mechanical baseline (B0)", "",
         "| role | playbook | opportunities | calls | call rate | net / opportunity (95% week-block CI) | "
         "net / call | hit rate [Wilson] | Brier | champion − B0 (paired) | NO_CALL reasons |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in champ:
        reasons = ", ".join(f"{k} {v}" for k, v in r["no_call_reasons"].items()) or "-"
        L.append(f"| {r['role']} | {r['playbook_version']} | {r['opportunities']} | {r['calls']} | "
                 f"{_pct(r['call_rate'], False)} | {_ci_txt(r['net_per_opportunity'])} | {_ci_txt(r['net_per_call'])} | "
                 f"{_rate_ci(r['hit_rate'], r['hit_ci'])} | {_num(r.get('brier'))} | {_ci_txt(r['minus_b0'])} | "
                 f"{reasons} |")
    if not champ:
        L.append("| (no graded visible decisions yet) | | | | | | | | | | |")
    L += ["", "## Every flag, by kind: B0 = each flag in its kind's default direction", "",
          "| kind | horizon | graded | B0 rule | B0 n | B0 net (95% week-block CI) | B0 hit [Wilson] | mean cost | mean abs excess (no B0) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in b0:
        L.append(f"| {r['kind']} | {r['horizon']}d | {r['graded']} | {r['b0_direction_rule'] or 'not in B0'} | "
                 f"{r.get('b0_n', 0)} | {_ci_txt(r.get('b0_net'))} | {_rate_ci(r.get('b0_hit'), r.get('b0_hit_ci'))} | "
                 f"{_pct(r.get('mean_cost'), False)} | {_pct(r.get('mean_abs_excess'), False)} |")
    L += ["", f"## B0 by liquidity bucket ({cfg['primary_horizon']}d, NSE equities)", "",
          "| bucket | n | B0 net (95% week-block CI) |", "|---|---|---|"]
    L += [f"| {r['bucket']} | {r['n']} | {_ci_txt(r['b0_net'])} |" for r in buckets] or ["| (none graded yet) | | |"]
    L += ["", "## Macro views (calibration only, never playbook evidence)", "",
          "| market | n | Brier | climatology Brier | skill vs climatology |", "|---|---|---|---|---|"]
    L += [f"| {r['instrument']} | {r['n']} | {_num(r['brier'])} | {_num(r.get('brier_clim'))} | "
          f"{_num(r.get('skill_vs_clim'), '+.3f')} |" for r in vt] or ["| (none graded yet) | | | | |"]
    L += ["", "## Excluded (shown, never counted)", ""]
    exc = [r for r in dec_rows if r["status"] == "EXCLUDED"]
    L += [f"- `{r['decision_id']}` {r['instrument']} {r['decision']} p={r['probability']}: {r['excluded_reason']}"
          for r in exc] or ["- none"]
    L += ["", "## Not in this version", "",
          "B1 random-matched baseline, calibration map (needs >= 100 graded calls), harm monitor, process flags, "
          "F&O list (DOWN calls show tradable = unknown), sector benchmarks and bhavcopy prices (Phase 3)."]
    return "\n".join(L) + "\n"


def write_outputs(out_dir: str, flag_rows, dec_rows, view_rows, cfg, now) -> None:
    b0, buckets = b0_table(flag_rows, cfg), b0_by_bucket(flag_rows, cfg)
    champ, vt = champion_table(dec_rows), views_table(view_rows)
    for sub in ("scores", os.path.join("stats", "visible")):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    for name, rows in (("opportunities_scored", flag_rows), ("decisions_scored", dec_rows),
                       ("macro_views_scored", view_rows)):
        with open(os.path.join(out_dir, "scores", f"{name}.jsonl"), "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(sealed(r), sort_keys=True, default=str) + "\n")
    for name, obj in (("b0_by_kind", b0), ("b0_by_bucket", buckets), ("champion", champ), ("macro_views", vt)):
        with open(os.path.join(out_dir, "stats", "visible", f"{name}.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "grader": cfg["version"], "rows": obj},
                      f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(out_dir, "stats", "summary.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_summary(flag_rows, dec_rows, view_rows, b0, buckets, champ, vt, cfg, now))


def run(data_dir: str, ledger_dir: str, views_path: Optional[str], out_dir: str,
        get_prices: PricesProvider = yahoo_adjusted, now: Optional[datetime] = None,
        cfg: Optional[dict] = None) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    cfg = cfg or load_config()
    flags, decisions = read_flags(data_dir), read_decisions(ledger_dir)
    views = views_with_excluded(read_views(views_path), cfg, decisions)

    times = [parse_ts(f["observed_at"]) for f in flags] + [parse_ts(d["created_at"]) for d in decisions]
    # observed_at can precede seen_at by days; fetching from it keeps the 20-day turnover window
    insts = {f["instrument"] for f in flags if f.get("instrument")} | {d["instrument"] for d in decisions}
    insts = {i for i in insts if resolve_instrument(i)}
    insts |= {b for b in (benchmark_for(i) for i in insts) if b}
    prices: Prices = {}
    if times and insts:
        prices.update(get_prices(sorted(insts), (min(times) - timedelta(days=60)).date()))
    if views:
        vstart = (min(parse_ts(v["created_at"]) for v in views) - timedelta(days=760)).date()
        prices.update(get_prices(sorted({v["instrument"] for v in views}), vstart))

    flag_rows = [grade_flag(f, prices, cfg, now) for f in flags]
    dec_rows = grade_decisions(decisions, {f["flag_id"]: f for f in flags}, prices, cfg, now)
    view_rows = grade_views(views, prices, now)
    write_outputs(out_dir, flag_rows, dec_rows, view_rows, cfg, now)
    return {"flags": len(flag_rows), "decisions": len(dec_rows), "views": len(view_rows),
            "instruments": len(insts), "priced": sum(v is not None for v in prices.values())}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="intel.grader_v2")
    ap.add_argument("--data", required=True, help="agent-data checkout (reads archive/flags)")
    ap.add_argument("--ledger", required=True, help="agent ledger dir (calls/, decisions/)")
    ap.add_argument("--views", help="agent/macro_views.jsonl on the ledger branch")
    ap.add_argument("--out", required=True, help="where scores/ and stats/ are written")
    ap.add_argument("--config", default=CONFIG_PATH)
    args = ap.parse_args(argv)
    n = run(args.data, args.ledger, args.views, args.out, cfg=load_config(args.config))
    print(f"grader v2: {n['flags']} flags, {n['decisions']} decisions, {n['views']} views; "
          f"prices for {n['priced']} of {n['instruments']} instruments -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
