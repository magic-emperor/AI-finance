"""
views.py — the daily macro view, a calibration-only track (plan v2 §8.5, owner decision 2026-10-02).

The agent gives P(close 5 trading days from now > today's close) for six fixed markets. These are
scored for calibration (Brier vs climatology) and are NEVER calls and never playbook evidence.
Like the call ledger, the agent supplies only its judgment; this module stamps view ids,
timestamps and the hash chain, so a view can't be backdated or quietly edited.

    python -m intel.views append --file ../ledger/agent/macro_views.jsonl --json \
      '{"run_id": "...", "version": "v1", "views": [{"instrument": "^NSEI", "p_up": 0.48,
        "reason": "..."}, ... all six ...]}'
    python -m intel.views verify --file ../ledger/agent/macro_views.jsonl

One line per instrument; the six lines of one submission share a view_set_id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from intel.ledger import canonical_json, compute_hash
from intel.schema import GENESIS_HASH

VIEW_SCHEMA = "view.v1"
VIEW_INSTRUMENTS = ("^NSEI", "^NSEBANK", "USDINR=X", "EURUSD=X", "GC=F", "CL=F")
HORIZON_DAYS = 5
P_MIN, P_MAX = 0.05, 0.95
REASON_MAX = 200


def read_views(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def verify(path: str) -> List[str]:
    errs, prev = [], GENESIS_HASH
    for i, rec in enumerate(read_views(path)):
        if rec.get("prev_hash") != prev:
            errs.append(f"views[{i}] prev_hash does not match the previous line")
        if rec.get("hash") != compute_hash(rec):
            errs.append(f"views[{i}] hash does not match its content (line was edited)")
        prev = rec.get("hash", "")
    return errs


def validate_submission(sub: dict) -> List[str]:
    errs: List[str] = []
    for f in ("run_id", "version", "views"):
        if not sub.get(f):
            errs.append(f"missing field: {f}")
    views = sub.get("views")
    if not isinstance(views, list):
        return errs + ["views must be a list"]
    got = [v.get("instrument") for v in views if isinstance(v, dict)]
    if sorted(got) != sorted(VIEW_INSTRUMENTS):
        errs.append(f"views must cover exactly {list(VIEW_INSTRUMENTS)} once each (0.50 = no view); got {got}")
    for v in views:
        if not isinstance(v, dict):
            errs.append("each view must be an object")
            continue
        p = v.get("p_up")
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not (P_MIN <= p <= P_MAX):
            errs.append(f"{v.get('instrument')}: p_up must be a number in [{P_MIN}, {P_MAX}]")
        r = v.get("reason")
        if not isinstance(r, str) or not r.strip() or len(r) > REASON_MAX:
            errs.append(f"{v.get('instrument')}: reason must be 1-{REASON_MAX} chars")
    return errs


def append_views(path: str, sub: dict, now: Optional[datetime] = None) -> List[dict]:
    """Validate one six-market submission, then stamp and append it. Raises ValueError if invalid."""
    errs = validate_submission(sub)
    if errs:
        raise ValueError("invalid views: " + "; ".join(errs))
    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_id = "view-" + created.replace("-", "").replace(":", "")
    existing = read_views(path)
    if any(r["view_set_id"] == set_id for r in existing):
        raise ValueError(f"a view set was already recorded at {created}")
    prev = existing[-1]["hash"] if existing else GENESIS_HASH
    by_inst = {v["instrument"]: v for v in sub["views"]}
    out = []
    for inst in VIEW_INSTRUMENTS:                       # fixed order, so files diff cleanly
        rec = {"schema": VIEW_SCHEMA, "view_set_id": set_id, "view_id": f"{set_id}-{inst}",
               "created_at": created, "run_id": sub["run_id"], "version": sub["version"],
               "instrument": inst, "horizon_days": HORIZON_DAYS,
               "p_up": float(by_inst[inst]["p_up"]), "reason": by_inst[inst]["reason"].strip(),
               "prev_hash": prev}
        rec["hash"] = compute_hash(rec)
        prev = rec["hash"]
        out.append(rec)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for rec in out:
            f.write(canonical_json(rec) + "\n")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="intel.views")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("--file", required=True)
    a.add_argument("--json", required=True, dest="payload")
    v = sub.add_parser("verify")
    v.add_argument("--file", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "verify":
        errs = verify(args.file)
        for e in errs:
            print("CHAIN ERROR:", e)
        print("views chain OK" if not errs else f"{len(errs)} chain error(s)")
        return 1 if errs else 0
    try:
        recs = append_views(args.file, json.loads(args.payload))
    except Exception as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "view_set_id": recs[0]["view_set_id"], "n": len(recs)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
