"""
run_scouts.py — one scout pass. The scheduler IS the loop: no threads, no sleeping.

    python -X utf8 -m intel.scouts.run_scouts --data-dir _agent_data [--only filings news macro market]

Writes to <data-dir> (a checkout of the `agent-data` branch):
    state/scout_state.json        dedup + pending + bhavcopy history + fire log
    archive/flags/YYYY-MM-DD.jsonl  every new flag ever seen (provenance)
    ledger/runs/YYYY-MM.jsonl     one hash-chained run record per pass, including quiet and failed ones

Wakes the reasoning agent only if AGENT_FIRE_URL and AGENT_FIRE_TOKEN are set. Without them it
still scouts and queues, and says so in the run record -- it never pretends to have escalated.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from intel.ledger import append_run
from intel.scouts import escalate, filings, macro, market, news
from intel.scouts.common import NSESession

VERSION = "scouts_v1"
KEEP_SEEN_DAYS = 14
PENDING_MAX_AGE = timedelta(hours=24)


def load_state(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}, "escalated": {}, "pending": [], "fires": [], "bhav": {"processed": [], "history": {}}}


def save_state(path: str, state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, sort_keys=True)
    os.replace(tmp, path)                       # atomic: a crash never leaves a half-written state


def archive_flags(data_dir: str, flags: List[Dict[str, Any]], now: datetime) -> None:
    if not flags:
        return
    d = os.path.join(data_dir, "archive", "flags")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{now:%Y-%m-%d}.jsonl"), "a", encoding="utf-8", newline="\n") as f:
        for fl in flags:
            f.write(json.dumps(fl, sort_keys=True) + "\n")


def prune(state: Dict[str, Any], now: datetime) -> None:
    cutoff = (now - timedelta(days=KEEP_SEEN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for k in ("seen", "escalated"):
        state[k] = {fid: ts for fid, ts in state[k].items() if ts >= cutoff}
    pend_cut = (now - PENDING_MAX_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["pending"] = [f for f in state["pending"] if f["observed_at"] >= pend_cut]
    state["fires"] = [t for t in state["fires"] if t >= cutoff]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--only", nargs="*", choices=["filings", "news", "macro", "market"])
    args = ap.parse_args(argv)
    run_set = set(args.only or ["filings", "news", "macro", "market"])

    now = datetime.now(timezone.utc)
    started = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path = os.path.join(args.data_dir, "state", "scout_state.json")
    state = load_state(state_path)
    session = NSESession()

    new_flags: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []

    def collect(result):
        new_flags.extend(result["flags"])
        sources.extend(result["sources"])

    if "filings" in run_set:
        d_from = (now - timedelta(days=2)).strftime("%d-%m-%Y")
        collect(filings.run(session, d_from, now.strftime("%d-%m-%Y")))
    if "news" in run_set:
        collect(news.run(session))
    if "macro" in run_set:
        collect(macro.run())
    if "market" in run_set:
        collect(market.run(session, state, today=now.date()))

    fresh = [f for f in new_flags if f["flag_id"] not in state["seen"]]
    for f in fresh:
        state["seen"][f["flag_id"]] = f["observed_at"]
    archive_flags(args.data_dir, fresh, now)
    state["pending"] = state["pending"] + [f for f in fresh if f["importance"] >= 0.4]

    groups = escalate.select(state["pending"], set(state["escalated"]))
    fired, note = False, "nothing above the escalation threshold"
    if groups:
        url, token = os.environ.get("AGENT_FIRE_URL"), os.environ.get("AGENT_FIRE_TOKEN")
        ok, why = escalate.throttle_ok(state["fires"], now)
        if not url or not token:
            note = f"{len(groups)} group(s) qualified but AGENT_FIRE_URL/TOKEN not configured -- queued, not fired"
        elif not ok:
            note = f"{len(groups)} group(s) qualified but throttled: {why} -- stay pending"
        else:
            try:
                session_url = escalate.fire(url, token, escalate.build_text(groups, now))
                sent = {fl["flag_id"] for g in groups for fl in g["flags"]}
                for fid in sent:
                    state["escalated"][fid] = started
                state["pending"] = [f for f in state["pending"] if f["flag_id"] not in sent]
                state["fires"].append(started)
                fired, note = True, f"fired agent for {len(groups)} group(s): {session_url}"
            except Exception as e:
                note = f"fire attempt FAILED: {str(e)[:150]} -- flags stay pending"
                sources.append({"source": "agent_fire", "status": "FAILED", "n_items": 0, "error": str(e)[:150]})

    prune(state, now)
    save_state(state_path, state)

    failed = sum(s["status"] == "FAILED" for s in sources)
    ok_sources = len(sources) - failed
    if ok_sources == 0:
        status = "FAILED"
    elif failed:
        status = "DEGRADED"
    elif not fresh:
        status = "NO_NEW_INFORMATION"
    else:
        status = "OK"
    append_run(os.path.join(args.data_dir, "ledger"), {
        "run_id": f"scout-{started}", "track": "scout", "version": VERSION, "started_at": started,
        "status": status, "sources": sources, "n_calls": 0,
        "notes": f"{len(fresh)} new flag(s); {note}"[:500]})

    print(f"{status}: {len(fresh)} new flag(s), {failed}/{len(sources)} source(s) failed; {note}")
    return 2 if status == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())
