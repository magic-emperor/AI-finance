"""
schema.py — what a valid call / run record looks like.

Plain-Python validation (no jsonschema dependency) so the CI jobs and the
cloud routine can run it with nothing installed beyond the standard library.
"""
from __future__ import annotations

import re
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
    return errs + _validate_evidence(rec["evidence"], created, required=True)


def _validate_evidence(ev, created, required: bool) -> List[str]:
    """Every item needs url/publisher/published_at/claim, published BEFORE the record (P6)."""
    errs: List[str] = []
    if not isinstance(ev, list) or (required and not ev):
        return ["evidence must be a non-empty list" if required else "evidence must be a list"]
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


# ── decisions (plan v2 §7.1): one per investigated opportunity, CALL or NO_CALL ──
DECISION_SCHEMA = "decision.v1"
DECISIONS = {"UP", "DOWN", "NO_CALL"}
NO_CALL_REASONS = {"priced_in", "stale", "low_liquidity", "surveillance", "weak_evidence",
                   "no_edge", "conflicting_signals", "event_risk", "other"}
_ROLE_RE = re.compile(r"^(champion|challenger:[A-Za-z0-9_\-]{1,40})$")
_OPP_RE = re.compile(r"^opp:[^:\s]{1,40}:[0-9a-f]{12}$")


def validate_decision(rec: dict) -> List[str]:
    """Return a list of problems; empty means valid."""
    if not isinstance(rec, dict):
        return ["decision is not an object"]
    errs: List[str] = []
    if rec.get("schema") != DECISION_SCHEMA:
        errs.append(f"schema must be {DECISION_SCHEMA}")
    for f in ("decision_id", "track", "role", "model", "playbook_version", "process_version", "run_id",
              "created_at", "opportunity_id", "flag_ids", "instrument", "decision", "thesis",
              "rules_applied", "evidence", "prev_hash", "hash"):
        if f not in rec:
            errs.append(f"missing field: {f}")
    if errs:
        return errs

    if not isinstance(rec["role"], str) or not _ROLE_RE.match(rec["role"]):
        errs.append("role must be 'champion' or 'challenger:<id>'")
    for f in ("track", "model", "playbook_version", "process_version", "run_id"):
        if not isinstance(rec[f], str) or not rec[f]:
            errs.append(f"{f} must be a non-empty string")
    if not isinstance(rec["opportunity_id"], str) or not _OPP_RE.match(rec["opportunity_id"]):
        errs.append("opportunity_id must be the 'opp:...' id printed by the queue")
    fids = rec["flag_ids"]
    if not isinstance(fids, list) or not fids or not all(isinstance(x, str) and x for x in fids):
        errs.append("flag_ids must be a non-empty list of the queue's flag ids")
    if not isinstance(rec["rules_applied"], list) or not all(isinstance(x, str) for x in rec["rules_applied"]):
        errs.append("rules_applied must be a list of rule ids (may be empty)")
    if not resolve_instrument(rec["instrument"]):
        errs.append(f"instrument not in universe: {rec['instrument']!r}")
    if not isinstance(rec["thesis"], str) or not rec["thesis"] or len(rec["thesis"]) > 400:
        errs.append("thesis must be a non-empty string of at most 400 chars")
    if rec.get("reaffirms") is not None and not isinstance(rec["reaffirms"], str):
        errs.append("reaffirms must be null or a decision/call id string")
    try:
        created = parse_ts(rec["created_at"])
    except Exception:
        errs.append("created_at is not an ISO timestamp")
        created = None

    d = rec["decision"]
    if d not in DECISIONS:
        return errs + [f"decision must be one of {sorted(DECISIONS)}"]
    if d == "NO_CALL":
        if rec.get("no_call_reason") not in NO_CALL_REASONS:
            errs.append(f"NO_CALL needs no_call_reason, one of {sorted(NO_CALL_REASONS)}")
        for f in ("horizon_days", "probability", "signal_family"):
            if rec.get(f) is not None:
                errs.append(f"NO_CALL must leave {f} null")
        return errs + _validate_evidence(rec["evidence"], created, required=False)

    if rec.get("no_call_reason") is not None:
        errs.append("a CALL must leave no_call_reason null")
    if rec.get("horizon_days") not in HORIZONS:
        errs.append("horizon_days must be 1, 5 or 20")
    p = rec.get("probability")
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not (0.50 <= p <= 0.95):
        errs.append("probability must be a number in [0.50, 0.95]")
    if rec.get("signal_family") not in SIGNAL_FAMILIES:
        errs.append(f"signal_family must be one of {sorted(SIGNAL_FAMILIES)}")
    return errs + _validate_evidence(rec["evidence"], created, required=True)


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
