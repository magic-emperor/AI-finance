"""
ledger.py — append-only, hash-chained record of every prediction and every run.

Why this exists: the agent's predictions are only worth grading if nobody
(including the agent) can quietly edit or delete one afterwards. Each line
carries the sha256 of the line before it, so any edit breaks the chain and
verify_chain() reports it.

The agent never writes timestamps or hashes itself -- it hands this module
the fields it is responsible for (instrument, direction, thesis, evidence...)
and the ledger stamps call_id / created_at / prev_hash / hash from the system
clock. That removes the easiest way to backdate a "prediction".

Layout:  <ledger_dir>/calls/YYYY-MM.jsonl   and   <ledger_dir>/runs/YYYY-MM.jsonl
Calls and runs are two independent chains. Plain text JSONL (not SQLite) so
git can rebase concurrent appends instead of hitting a binary merge conflict.

CLI (what the cloud routine calls):
    python -m intel.ledger append-call --dir ledger --json '{...}'
    python -m intel.ledger append-run  --dir ledger --json '{...}'
    python -m intel.ledger verify      --dir ledger
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from intel.schema import (CALL_SCHEMA, RUN_SCHEMA, GENESIS_HASH,
                          validate_call, validate_run)


def canonical_json(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _now_iso(now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kind_dir(ledger_dir: str, kind: str) -> str:
    return os.path.join(ledger_dir, kind)


def read_records(ledger_dir: str, kind: str) -> List[dict]:
    """All records of one kind ('calls' or 'runs'), in chain order."""
    d = _kind_dir(ledger_dir, kind)
    if not os.path.isdir(d):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(d, name), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def verify_chain(ledger_dir: str, kind: str) -> List[str]:
    """Return a list of problems with the hash chain; empty means intact."""
    errs: List[str] = []
    prev = GENESIS_HASH
    for i, rec in enumerate(read_records(ledger_dir, kind)):
        if rec.get("prev_hash") != prev:
            errs.append(f"{kind}[{i}] prev_hash does not match the previous line")
        if rec.get("hash") != compute_hash(rec):
            errs.append(f"{kind}[{i}] hash does not match its content (line was edited)")
        prev = rec.get("hash", "")
    return errs


def _append(ledger_dir: str, kind: str, rec: dict, month: str) -> None:
    d = _kind_dir(ledger_dir, kind)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{month}.jsonl"), "a", encoding="utf-8", newline="\n") as f:
        f.write(canonical_json(rec) + "\n")


def _tail_hash(ledger_dir: str, kind: str) -> str:
    recs = read_records(ledger_dir, kind)
    return recs[-1]["hash"] if recs else GENESIS_HASH


def append_call(ledger_dir: str, call: dict, now: Optional[datetime] = None) -> dict:
    """Stamp and append one call. Raises ValueError listing every problem if invalid."""
    rec = dict(call)
    rec["schema"] = CALL_SCHEMA
    rec["created_at"] = _now_iso(now)
    rec.setdefault("reaffirms", None)
    stamp = rec["created_at"].replace("-", "").replace(":", "")
    same_second = [r for r in read_records(ledger_dir, "calls")
                   if r["call_id"].startswith(f"{rec.get('track', '?')}-{stamp}")]
    rec["call_id"] = f"{rec.get('track', '?')}-{stamp}-{len(same_second) + 1:03d}"
    rec["prev_hash"] = _tail_hash(ledger_dir, "calls")
    rec["hash"] = compute_hash(rec)
    errs = validate_call(rec)
    if errs:
        raise ValueError("invalid call: " + "; ".join(errs))
    _append(ledger_dir, "calls", rec, rec["created_at"][:7])
    return rec


def append_run(ledger_dir: str, run: dict, now: Optional[datetime] = None) -> dict:
    rec = dict(run)
    rec["schema"] = RUN_SCHEMA
    rec.setdefault("finished_at", _now_iso(now))
    rec["prev_hash"] = _tail_hash(ledger_dir, "runs")
    rec["hash"] = compute_hash(rec)
    errs = validate_run(rec)
    if errs:
        raise ValueError("invalid run: " + "; ".join(errs))
    _append(ledger_dir, "runs", rec, rec["finished_at"][:7])
    return rec


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="intel.ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("append-call", "append-run"):
        p = sub.add_parser(name)
        p.add_argument("--dir", required=True)
        p.add_argument("--json", required=True, dest="payload")
    v = sub.add_parser("verify")
    v.add_argument("--dir", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "verify":
        errs = verify_chain(args.dir, "calls") + verify_chain(args.dir, "runs")
        for e in errs:
            print("CHAIN ERROR:", e)
        print("ledger chain OK" if not errs else f"{len(errs)} chain error(s)")
        return 1 if errs else 0

    try:
        payload = json.loads(args.payload)
        rec = (append_call if args.cmd == "append-call" else append_run)(args.dir, payload)
    except Exception as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "id": rec.get("call_id") or rec.get("run_id")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
