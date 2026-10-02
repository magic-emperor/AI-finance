"""List the domains of every evidence URL in recent scout flags.

Phase 0 of docs/plans/prediction-agent-v2-plan.md (§6.1): the routine's cloud
environment only reaches hosts on its network allowlist, so the owner needs to
know which domains the evidence actually lives on.

Reads agent-data's archive/flags/YYYY-MM-DD.jsonl, either from a checked-out
directory or straight from a git ref (no worktree needed):

    python tools/evidence_domains.py --data ../data
    python tools/evidence_domains.py --ref ai-finance/agent-data --days 60

Prints one row per host: URLs, distinct flags, flag kinds, and whether the
plan's §6.1 allowlist covers it. With --calls-ref it also tabulates the
evidence the agent itself cited in ledger/calls/*.jsonl, which is where hosts
outside the scouts' sources show up. Exit code is always 0; this is a report.
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

FLAG_DIR = "archive/flags"

# docs/plans/prediction-agent-v2-plan.md §6.1, verbatim.
PLAN_ALLOWLIST = [
    "*.yahoo.com", "*.nseindia.com", "*.bseindia.com", "*.rbi.org.in",
    "*.sebi.gov.in", "*.pib.gov.in", "*.indiatimes.com", "*.livemint.com",
    "*.business-standard.com", "*.moneycontrol.com", "*.thehindubusinessline.com",
    "*.financialexpress.com", "*.reuters.com", "*.ndtvprofit.com",
    "*.cnbctv18.com", "*.mospi.gov.in",
]


def host_of(url: str) -> Optional[str]:
    try:
        host = urlparse(url.strip()).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def allowed(host: str, patterns: Iterable[str]) -> Optional[str]:
    """Return the pattern covering host, or None.

    "*.x.com" is treated as covering x.com itself as well as its subdomains.
    Whether the environment's matcher does the same for the bare domain is
    unverified, so the report marks those matches separately.
    """
    for p in patterns:
        p = p.lower()
        if p.startswith("*."):
            base = p[2:]
            if host == base or host.endswith("." + base):
                return p
        elif host == p:
            return p
    return None


def _in_window(name: str, start: datetime, end: datetime) -> bool:
    try:
        day = datetime.strptime(name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return start.date() <= day.date() <= end.date()


def read_flags_dir(data_dir: str, start: datetime, end: datetime) -> List[Tuple[str, dict]]:
    d = os.path.join(data_dir, FLAG_DIR)
    out = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.endswith(".jsonl") and _in_window(name, start, end):
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                out.extend(_parse(name, fh.read()))
    return out


def read_flags_ref(ref: str, start: datetime, end: datetime) -> List[Tuple[str, dict]]:
    names = subprocess.run(["git", "ls-tree", "--name-only", f"{ref}:{FLAG_DIR}"],
                           capture_output=True, text=True, check=True).stdout.split()
    out = []
    for name in sorted(names):
        if name.endswith(".jsonl") and _in_window(name, start, end):
            text = subprocess.run(["git", "show", f"{ref}:{FLAG_DIR}/{name}"],
                                  capture_output=True, check=True).stdout.decode("utf-8")
            out.extend(_parse(name, text))
    return out


def read_calls_ref(ref: str, start: datetime) -> List[Tuple[str, dict]]:
    """Agent calls from claude/agent-ledger, as (file, record) with kind = signal_family."""
    names = subprocess.run(["git", "ls-tree", "--name-only", f"{ref}:ledger/calls"],
                           capture_output=True, text=True, check=True).stdout.split()
    out = []
    for name in sorted(names):
        text = subprocess.run(["git", "show", f"{ref}:ledger/calls/{name}"],
                              capture_output=True, check=True).stdout.decode("utf-8")
        for fname, rec in _parse(name, text):
            if (rec.get("created_at") or "")[:10] >= start.strftime("%Y-%m-%d"):
                out.append((fname, {"flag_id": rec.get("call_id"), "kind": rec.get("signal_family", "?"),
                                    "evidence": rec.get("evidence")}))
    return out


def _parse(name: str, text: str) -> List[Tuple[str, dict]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append((name, json.loads(line)))
        except json.JSONDecodeError as e:
            print(f"warning: {name}:{i} unparseable ({e})", file=sys.stderr)
    return out


def tally(flags: List[Tuple[str, dict]]) -> Dict[str, dict]:
    rows: Dict[str, dict] = defaultdict(lambda: {"urls": 0, "flags": set(), "kinds": set()})
    for _, flag in flags:
        for ev in flag.get("evidence") or []:
            url = (ev or {}).get("url")
            host = host_of(url) if url else None
            key = host or "(no url)"
            rows[key]["urls"] += 1
            rows[key]["flags"].add(flag.get("flag_id"))
            rows[key]["kinds"].add(flag.get("kind", "?"))
    return rows


def render(rows: Dict[str, dict], n_flags: int, files: List[str], patterns: List[str],
           what: str = "Flags") -> str:
    lines = [f"{what} read: {n_flags} from {len(files)} file(s): {', '.join(files) or 'none'}",
             "",
             f"| Host | URLs | {what} | Kinds | Plan allowlist |",
             "|---|---|---|---|---|"]
    uncovered = []
    for host, r in sorted(rows.items(), key=lambda kv: (-kv[1]["urls"], kv[0])):
        if host == "(no url)":
            cover = "n/a"
        else:
            p = allowed(host, patterns)
            if p is None:
                cover = "**NOT COVERED**"
                uncovered.append(host)
            elif host == p[2:]:
                cover = f"{p} (bare domain, unverified)"
            else:
                cover = p
        lines.append(f"| {host} | {r['urls']} | {len(r['flags'])} | "
                     f"{', '.join(sorted(r['kinds']))} | {cover} |")
    lines += ["", f"Hosts not covered by the plan allowlist: {', '.join(uncovered) or 'none'}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="checked-out agent-data directory")
    src.add_argument("--ref", help="git ref of agent-data, e.g. ai-finance/agent-data")
    ap.add_argument("--calls-ref", help="git ref of claude/agent-ledger; adds a table of call evidence")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--today", help="YYYY-MM-DD (default: today UTC)")
    args = ap.parse_args(argv)

    end = (datetime.strptime(args.today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
           if args.today else datetime.now(timezone.utc))
    start = end - timedelta(days=args.days - 1)
    flags = read_flags_ref(args.ref, start, end) if args.ref else read_flags_dir(args.data, start, end)
    files = sorted({name for name, _ in flags})
    print(render(tally(flags), len(flags), files, PLAN_ALLOWLIST))
    if args.calls_ref:
        calls = read_calls_ref(args.calls_ref, start)
        print()
        print(render(tally(calls), len(calls), sorted({n for n, _ in calls}), PLAN_ALLOWLIST, what="Calls"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
