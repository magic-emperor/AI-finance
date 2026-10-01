"""
agent_queue.py — what the prediction agent should look at this run, when it is woken by its own
schedule rather than by a scout's API fire.

Reads the scouts' pending flags (agent-data/state/scout_state.json), drops any flag the agent has
already investigated (agent/consumed.jsonl on its own ledger branch), applies the SAME escalation
gate the scouts use, and prints the same text an API fire would have carried.

    python -m intel.agent_queue --data ../data --consumed ../ledger/agent/consumed.jsonl
    python -m intel.agent_queue ... --mark <flag_id> <flag_id> ...    # record what was handled
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional, Set

from intel.scouts import escalate


def consumed_ids(path: str) -> Set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {json.loads(l)["flag_id"] for l in f if l.strip()}


def pending_groups(data_dir: str, consumed_path: str) -> List[dict]:
    state_path = os.path.join(data_dir, "state", "scout_state.json")
    if not os.path.exists(state_path):
        return []
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    done = consumed_ids(consumed_path) | set(state.get("escalated", {}))
    return escalate.select(state.get("pending", []), done)


def mark(consumed_path: str, flag_ids: List[str], now: Optional[datetime] = None) -> None:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(consumed_path) or ".", exist_ok=True)
    with open(consumed_path, "a", encoding="utf-8", newline="\n") as f:
        for fid in flag_ids:
            f.write(json.dumps({"flag_id": fid, "consumed_at": ts}) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="intel.agent_queue")
    ap.add_argument("--data", required=True, help="checkout of the agent-data branch")
    ap.add_argument("--consumed", required=True, help="agent/consumed.jsonl on the ledger branch")
    ap.add_argument("--mark", nargs="+", help="flag_ids investigated this run")
    args = ap.parse_args(argv)
    if args.mark:
        mark(args.consumed, args.mark)
        print(f"marked {len(args.mark)} flag(s) consumed")
        return 0
    groups = pending_groups(args.data, args.consumed)
    if not groups:
        print("QUEUE EMPTY: no flag group above the escalation threshold awaits investigation.")
        return 0
    print(escalate.build_text(groups, datetime.now(timezone.utc)))
    print("\nFLAG_IDS: " + " ".join(fl["flag_id"] for g in groups for fl in g["flags"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
