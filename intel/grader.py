"""
grader.py — decides, with deterministic code and no LLM, whether predictions were right.

The prediction agent can change HOW it predicts. It can never change how it is
judged: this file, the schema and the ledger sit outside anything the agent may edit.

Rules (all fixed in advance, mirrored in the pre-registered hypothesis):
  * Entry  = OPEN of the first session strictly AFTER the call's UTC date
             (no intraday fills, no look-ahead; conservative).
  * Exit   = CLOSE of the horizon_days-th session counted from the entry session.
  * Metric = signed excess return: direction * (instrument return - benchmark return).
             Equities are measured against Nifty 50; FX/commodities/indices have none.
  * A call is PENDING until its exit bar is complete, UNGRADEABLE if price data is missing.
  * REAFFIRM calls (restating an open call) are excluded from N so repeating a view
    cannot inflate the sample.
  * REACTIVE calls (made right after a >2-sigma one-day move) are reported separately
    from anticipatory ones: chasing a move that already happened is not foresight.
  * Scores are reported per (track, version). Versions are never pooled.
  * Calls marked test=true are ignored.

Primary endpoint (pre-registered): over independent, non-reactive, graded calls at the
5-day horizon -- permutation p < 0.05 (one-sided), mean signed excess above the momentum
baseline, and n >= 50. No verdict is stated before the T+90 checkpoint regardless.

Not implemented yet (stated, not hidden): commit-time validation of call timestamps via
git blame, and URL-liveness sampling of cited evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from intel.ledger import read_records
from intel.schema import parse_ts
from intel.universe import benchmark_for

PriceProvider = Callable[[str], Optional[pd.DataFrame]]

PRIMARY_HORIZON = 5
PRIMARY_MIN_N = 50
PERMUTATIONS = 10_000


def yfinance_provider(symbol: str) -> Optional[pd.DataFrame]:
    import yfinance as yf
    df = yf.Ticker(symbol).history(period="2y", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return None
    df = df[["Open", "Close"]].copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    return df[~df.index.duplicated(keep="last")].sort_index()


def _entry_index(index: pd.DatetimeIndex, created: date) -> Optional[int]:
    pos = int(np.searchsorted(index.values, np.datetime64(created) + np.timedelta64(1, "D"), side="left"))
    return pos if pos < len(index) else None


def _grade_one(call: dict, prices: Optional[pd.DataFrame], bench: Optional[pd.DataFrame],
               needs_bench: bool, today: date) -> dict:
    out = {"call_id": call["call_id"], "status": "UNGRADEABLE"}
    if prices is None or prices.empty or (needs_bench and (bench is None or bench.empty)):
        return out
    created = parse_ts(call["created_at"]).astimezone(timezone.utc).date()
    ei = _entry_index(prices.index, created)
    h = call["horizon_days"]
    if ei is None or ei + h - 1 >= len(prices) or prices.index[ei + h - 1].date() >= today:
        out["status"] = "PENDING"
        return out

    xi = ei + h - 1
    entry_open = float(prices["Open"].iloc[ei])
    exit_close = float(prices["Close"].iloc[xi])
    r_i = exit_close / entry_open - 1.0
    r_b = 0.0
    if needs_bench:
        b = bench.reindex([prices.index[ei], prices.index[xi]])
        if b["Open"].isna().iloc[0] or b["Close"].isna().iloc[1]:
            return out
        r_b = float(b["Close"].iloc[1]) / float(b["Open"].iloc[0]) - 1.0
    excess = r_i - r_b
    sign = 1.0 if call["direction"] == "UP" else -1.0

    # REACTIVE = the last move before entry was a >2-sigma surprise relative to the
    # preceding 20 days' own mean and spread. (Measured from the mean, not from zero:
    # a steadily trending stock is not "surprised" by yet another up day.)
    reactive = False
    closes = prices["Close"].values
    if ei >= 23:
        window = closes[ei - 22:ei - 1]
        rets = np.diff(window) / window[:-1]
        sigma = float(np.std(rets, ddof=1))
        prior = float(closes[ei - 1] / closes[ei - 2] - 1.0)
        if sigma > 1e-9:
            reactive = bool(abs(prior - float(rets.mean())) > 2 * sigma)

    momentum_sign = None
    if ei >= 6:
        momentum_sign = 1.0 if closes[ei - 1] / closes[ei - 6] - 1.0 > 0 else -1.0

    out.update({
        "status": "GRADED",
        "entry_date": str(prices.index[ei].date()),
        "exit_date": str(prices.index[xi].date()),
        "excess": excess,
        "signed_excess": sign * excess,
        "hit": sign * excess > 0,
        "reactive": reactive,
        "momentum_signed_excess": (momentum_sign * excess) if momentum_sign is not None else None,
    })
    return out


def grade_calls(calls: List[dict], get_prices: PriceProvider, today: Optional[date] = None) -> List[dict]:
    today = today or datetime.now(timezone.utc).date()
    cache: Dict[str, Optional[pd.DataFrame]] = {}

    def prices_for(sym: str):
        if sym not in cache:
            cache[sym] = get_prices(sym)
        return cache[sym]

    scored: List[dict] = []
    open_until: Dict[tuple, Optional[date]] = {}
    for call in sorted(calls, key=lambda c: c["created_at"]):
        if call.get("test"):
            continue
        bsym = benchmark_for(call["instrument"])
        g = _grade_one(call, prices_for(call["instrument"]),
                       prices_for(bsym) if bsym else None, bsym is not None, today)
        g.update({k: call[k] for k in ("track", "version", "instrument", "direction",
                                      "horizon_days", "probability", "signal_family")})
        created = parse_ts(call["created_at"]).astimezone(timezone.utc).date()

        key = (call["track"], call["version"], call["instrument"], call["direction"])
        prior_exists = key in open_until
        still_open = prior_exists and (open_until[key] is None or created <= open_until[key])
        if call.get("reaffirms") or still_open:
            g["status"] = "REAFFIRM"
        else:
            if g["status"] == "GRADED":
                open_until[key] = date.fromisoformat(g["exit_date"])
            else:
                open_until[key] = None
        scored.append(g)
    return scored


def summarize(scored: List[dict], seed: int = 0) -> List[dict]:
    groups: Dict[tuple, List[dict]] = {}
    for s in scored:
        groups.setdefault((s["track"], s["version"]), []).append(s)

    rng = np.random.RandomState(seed)
    rows = []
    for (track, version), items in sorted(groups.items()):
        graded = [s for s in items if s["status"] == "GRADED"]
        primary = [s for s in graded if s["horizon_days"] == PRIMARY_HORIZON and not s["reactive"]]
        row = {
            "track": track, "version": version,
            "calls": len(items),
            "pending": sum(s["status"] == "PENDING" for s in items),
            "ungradeable": sum(s["status"] == "UNGRADEABLE" for s in items),
            "reaffirm": sum(s["status"] == "REAFFIRM" for s in items),
            "graded": len(graded),
            "graded_reactive": sum(s["reactive"] for s in graded),
            "primary_n": len(primary),
        }
        if primary:
            se = np.array([s["signed_excess"] for s in primary])
            raw = np.array([s["excess"] for s in primary])
            observed = float(se.mean())
            signs = rng.choice([-1.0, 1.0], size=(PERMUTATIONS, len(raw)))
            perm_means = (signs * raw).mean(axis=1)
            mom = [s["momentum_signed_excess"] for s in primary if s["momentum_signed_excess"] is not None]
            probs = np.array([s["probability"] for s in primary])
            hits = np.array([1.0 if s["hit"] else 0.0 for s in primary])
            row.update({
                "hit_rate": float(hits.mean()),
                "mean_signed_excess": observed,
                "perm_p": float((perm_means >= observed).mean()),
                "momentum_mean": float(np.mean(mom)) if mom else None,
                "brier": float(((probs - hits) ** 2).mean()),
            })
            row["meets_primary_endpoint"] = bool(
                row["primary_n"] >= PRIMARY_MIN_N and row["perm_p"] < 0.05
                and row["momentum_mean"] is not None and observed > row["momentum_mean"])
        else:
            row["meets_primary_endpoint"] = False
        rows.append(row)
    return rows


def write_report(scored: List[dict], summary: List[dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "calls_scored.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for s in scored:
            f.write(json.dumps(s, sort_keys=True) + "\n")
    lines = [
        "# Prediction agent scoreboard", "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%MZ')}.", "",
        "> **No verdict is valid before the T+90 checkpoint.** At n~60 a one-sided 5% test can only",
        "> detect roughly a 61%+ hit rate: a month can show a track is broken, never that it works.",
        "> Scores are per version; versions are never pooled.", "",
        "| track | version | calls | graded | pending | reaffirm | primary n (5d, non-reactive) | hit rate | mean excess | perm p | momentum mean | brier | primary endpoint |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def f(v, pct=False):
        if v is None:
            return "-"
        return f"{v * 100:.2f}%" if pct else f"{v:.3f}"

    for r in summary:
        lines.append(
            f"| {r['track']} | {r['version']} | {r['calls']} | {r['graded']} | {r['pending']} | "
            f"{r['reaffirm']} | {r['primary_n']} | {f(r.get('hit_rate'), True)} | "
            f"{f(r.get('mean_signed_excess'), True)} | {f(r.get('perm_p'))} | "
            f"{f(r.get('momentum_mean'), True)} | {f(r.get('brier'))} | "
            f"{'MET (pending T+90)' if r['meets_primary_endpoint'] else 'not met'} |")
    if not summary:
        lines.append("| (no calls yet) |  |  |  |  |  |  |  |  |  |  |  |  |")
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8", newline="\n") as f_:
        f_.write("\n".join(lines) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="intel.grader")
    ap.add_argument("--ledger", action="append", required=True, help="ledger dir (repeatable)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    calls: List[dict] = []
    for d in args.ledger:
        calls += read_records(d, "calls")
    scored = grade_calls(calls, yfinance_provider)
    summary = summarize(scored)
    write_report(scored, summary, args.out)
    print(f"graded {len(calls)} call(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
