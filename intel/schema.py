"""
schema.py — what a valid call / run record looks like.

Plain-Python validation (no jsonschema dependency) so the CI jobs and the
cloud routine can run it with nothing installed beyond the standard library.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from intel.universe import resolve_instrument

CALL_SCHEMA = "call.v1"
RUN_SCHEMA = "run.v1"
GENESIS_HASH = "0" * 64

DIRECTIONS = {"UP", "DOWN"}
HORIZONS = {1, 5, 20}                 # trading days
RUN_STATUSES = {"OK", "DEGRADED", "FAILED", "NO_NEW_INFORMATION"}
SIGNAL_FAMILIES = {
    "insider_buy", "bulk_deal", "volume_breakout", "news_event",
    "policy_macro", "fx_macro", "earnings_corporate", "other",
}


def parse_ts(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_call(rec: dict) -> List[str]:
    """Return a list of problems; empty means valid."""
    errs: List[str] = []
    if not isinstance(rec, dict):
        return ["call is not an object"]
    if rec.get("schema") != CALL_SCHEMA:
        errs.append(f"schema must be {CALL_SCHEMA}")
    for f in ("call_id", "track", "version", "run_id", "created_at", "instrument",
              "direction", "horizon_days", "probability", "signal_family",
              "thesis", "evidence", "prev_hash", "hash"):
        if f not in rec:
            errs.append(f"missing field: {f}")
    if errs:
        return errs

    if not resolve_instrument(rec["instrument"]):
        errs.append(f"instrument not in universe: {rec['instrument']!r}")
    if rec["direction"] not in DIRECTIONS:
        errs.append("direction must be UP or DOWN")
    if rec["horizon_days"] not in HORIZONS:
        errs.append("horizon_days must be 1, 5 or 20")
    p = rec["probability"]
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not (0.50 <= p <= 0.95):
        errs.append("probability must be a number in [0.50, 0.95]")
    if rec["signal_family"] not in SIGNAL_FAMILIES:
        errs.append(f"signal_family must be one of {sorted(SIGNAL_FAMILIES)}")
    if not isinstance(rec["thesis"], str) or not rec["thesis"] or len(rec["thesis"]) > 400:
        errs.append("thesis must be a non-empty string of at most 400 chars")
    if rec.get("reaffirms") is not None and not isinstance(rec["reaffirms"], str):
        errs.append("reaffirms must be null or a call_id string")

    try:
        created = parse_ts(rec["created_at"])
    except Exception:
        errs.append("created_at is not an ISO timestamp")
        created = None

    ev = rec["evidence"]
    if not isinstance(ev, list) or not ev:
        errs.append("evidence must be a non-empty list")
    else:
        for i, item in enumerate(ev):
            if not isinstance(item, dict):
                errs.append(f"evidence[{i}] is not an object")
                continue
            for f in ("url", "publisher", "published_at", "claim"):
                if not item.get(f):
                    errs.append(f"evidence[{i}] missing {f}")
            if isinstance(item.get("claim"), str) and len(item["claim"]) > 200:
                errs.append(f"evidence[{i}].claim over 200 chars")
            if item.get("published_at") and created is not None:
                try:
                    if parse_ts(item["published_at"]) > created:
                        errs.append(f"evidence[{i}] published after the call was made")
                except Exception:
                    errs.append(f"evidence[{i}].published_at is not an ISO timestamp")
    return errs


def validate_run(rec: dict) -> List[str]:
    errs: List[str] = []
    if not isinstance(rec, dict):
        return ["run is not an object"]
    if rec.get("schema") != RUN_SCHEMA:
        errs.append(f"schema must be {RUN_SCHEMA}")
    for f in ("run_id", "track", "version", "started_at", "finished_at", "status",
              "sources", "n_calls", "prev_hash", "hash"):
        if f not in rec:
            errs.append(f"missing field: {f}")
    if errs:
        return errs
    if rec["status"] not in RUN_STATUSES:
        errs.append(f"status must be one of {sorted(RUN_STATUSES)}")
    if not isinstance(rec["sources"], list):
        errs.append("sources must be a list")
    if not isinstance(rec["n_calls"], int) or rec["n_calls"] < 0:
        errs.append("n_calls must be a non-negative integer")
    for f in ("started_at", "finished_at"):
        try:
            parse_ts(rec[f])
        except Exception:
            errs.append(f"{f} is not an ISO timestamp")
    if isinstance(rec.get("notes"), str) and len(rec["notes"]) > 500:
        errs.append("notes over 500 chars")
    return errs
