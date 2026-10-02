"""
escalate.py — decides which flags are worth waking the reasoning agent, and does the waking.

The agent costs real subscription quota, so this is the gate:
  * threshold: a symbol's combined importance must reach ESCALATE_AT
  * confluence: independent kinds on the SAME symbol raise it (a promoter buy AND a
    multi-source news cluster is a different situation from either alone)
  * dedup: a flag already sent is never re-sent
  * throttle: at most MAX_FIRES_PER_DAY fires, at least MIN_GAP between them
  * unsent flags stay pending and retry next run -- nothing is dropped because of a throttle
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

ESCALATE_AT = 0.70
CONFLUENCE_BONUS = 0.15
MAX_GROUPS_PER_FIRE = 10
MAX_FIRES_PER_DAY = 8
MIN_GAP = timedelta(minutes=30)
MAX_TEXT_CHARS = 6000
FIRE_BETA = "experimental-cc-routine-2026-04-01"


# Confluence only counts INDEPENDENT information, so kinds are collapsed into families.
# A bulk deal and a volume spike on the same stock are one event seen twice (the deal IS
# the volume); counting them as two signals made a lone bulk deal score 1.00 in the first
# live run. Families: who is buying (holder), what the tape did (tape), what the company
# said (disclosure), what the press says (media).
FAMILY = {
    "sast_acquisition": "holder",
    "bulk_deal_buy": "tape", "block_deal_buy": "tape", "volume_breakout": "tape", "macro_move": "tape",
    "volume_spurt_notice": "tape",
    "announcement": "disclosure", "regulator_release": "disclosure",
    "news_multi_source": "media",
}


def opportunity_id(flags: List[Dict[str, Any]]) -> str:
    """Deterministic id for one investigated opportunity: its symbol plus the exact flag set.

    Decisions carry it (plan v2 §7.1) so the grader can pair each decision with the
    mechanical baseline on the very same flags.
    """
    ids = ",".join(sorted(f["flag_id"] for f in flags))
    return f"opp:{flags[0]['symbol'] or 'MACRO'}:{hashlib.sha1(ids.encode('utf-8')).hexdigest()[:12]}"


def group_flags(flags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group by symbol (symbol-less flags stand alone), with confluence-boosted importance."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for f in flags:
        groups.setdefault(f["symbol"] or f"_{f['flag_id']}", []).append(f)
    out = []
    for key, fl in groups.items():
        kinds = {f["kind"] for f in fl}
        families = {FAMILY.get(k, k) for k in kinds}
        combined = min(1.0, max(f["importance"] for f in fl) + CONFLUENCE_BONUS * (len(families) - 1))
        out.append({"key": key, "symbol": fl[0]["symbol"], "importance": round(combined, 3),
                    "kinds": sorted(kinds), "flags": fl, "opportunity_id": opportunity_id(fl)})
    return sorted(out, key=lambda g: -g["importance"])


def select(flags: List[Dict[str, Any]], escalated_ids: set) -> List[Dict[str, Any]]:
    fresh = [f for f in flags if f["flag_id"] not in escalated_ids]
    return [g for g in group_flags(fresh) if g["importance"] >= ESCALATE_AT][:MAX_GROUPS_PER_FIRE]


def throttle_ok(fires: List[str], now: datetime) -> Tuple[bool, str]:
    times = sorted(datetime.fromisoformat(t.replace("Z", "+00:00")) for t in fires)
    recent = [t for t in times if now - t < timedelta(hours=24)]
    if len(recent) >= MAX_FIRES_PER_DAY:
        return False, f"daily cap {MAX_FIRES_PER_DAY} reached"
    if recent and now - recent[-1] < MIN_GAP:
        return False, "min gap since last fire not elapsed"
    return True, "ok"


def build_text(groups: List[Dict[str, Any]], now: datetime) -> str:
    """The `text` body of the routine API fire: compact, self-describing, size-capped."""
    lines = [f"ESCALATION at {now:%Y-%m-%dT%H:%M:%SZ}. {len(groups)} candidate name(s), ranked by importance.",
             "Each block: symbol | combined importance | signal kinds | the underlying flags with evidence URLs."]
    for g in groups:
        lines.append(f"\n## {g['symbol'] or 'MACRO'} | importance {g['importance']} | {', '.join(g['kinds'])} "
                     f"| {g['opportunity_id']}")
        for f in g["flags"]:
            ev = f["evidence"][0] if f["evidence"] else {}
            lines.append(f"- [{f['kind']}] {f['summary']} (published {ev.get('published_at')}, "
                         f"hint {f.get('direction_hint')}, flag_id {f['flag_id']}, {ev.get('url')})")
    text = "\n".join(lines)
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS - 40] + "\n...[truncated]"


def fire(url: str, token: str, text: str, http=None) -> str:
    """POST to the routine's /fire endpoint; returns the session URL. Raises on any failure."""
    import requests
    http = http or requests
    r = http.post(url, timeout=30, data=json.dumps({"text": text}), headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": FIRE_BETA,
        "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
    if r.status_code >= 300:
        raise RuntimeError(f"fire failed: HTTP {r.status_code} {r.text[:120]}")
    return r.json().get("claude_code_session_url", "")
