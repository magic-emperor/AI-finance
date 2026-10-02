"""
nse_calendar.py — is today an NSE trading day? One deterministic answer for the scouts and the agent.

Source: NSE's own holiday master (`/api/holiday-master?type=trading`, segment CM), fetched by the
scouts on GitHub Actions -- NSE's APIs answer there, while some of them refuse the agent's cloud IP --
and cached on agent-data as `state/nse_holidays.json`. The agent only reads that file.

When the calendar is missing or doesn't cover the year, the answer is UNKNOWN and callers treat the
day as a trading day: skipping a real session loses data, wrongly running on a holiday only costs a
short run. Weekends are always closed.

    python -m intel.nse_calendar --data ../data [--date YYYY-MM-DD]     (date defaults to today, IST)

prints one line: TRADING_DAY | HOLIDAY: <name> | CLOSED: weekend | UNKNOWN: <why>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
CALENDAR_PATH = os.path.join("state", "nse_holidays.json")
REFRESH_AFTER = timedelta(hours=24)
IST = timedelta(hours=5, minutes=30)
SESSION_CLOSE_UTC = time(10, 0)                 # 15:30 IST


def parse_holidays(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """NSE payload -> [{"date": "YYYY-MM-DD", "description": ...}] for the cash market (CM)."""
    out = []
    for row in payload.get("CM") or []:
        d = datetime.strptime(row["tradingDate"].strip(), "%d-%b-%Y").date()
        out.append({"date": d.isoformat(), "description": (row.get("description") or "").strip()})
    if not out:
        raise ValueError("holiday master has no CM rows")
    return sorted(out, key=lambda r: r["date"])


def load(data_dir: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(data_dir, CALENDAR_PATH)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def refresh(data_dir: str, session, now: datetime) -> Optional[Dict[str, Any]]:
    """Re-fetch at most once per REFRESH_AFTER. Returns a run-record source line, or None if skipped.

    A failed fetch keeps the previous file untouched and is reported FAILED -- never papered over.
    """
    cal = load(data_dir)
    if cal and now - datetime.fromisoformat(cal["fetched_at"].replace("Z", "+00:00")) < REFRESH_AFTER:
        return None
    try:
        holidays = parse_holidays(session.get_json(HOLIDAY_URL))
    except Exception as e:
        return {"source": "nse_holidays", "status": "FAILED", "n_items": 0, "error": str(e)[:150]}
    path = os.path.join(data_dir, CALENDAR_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": HOLIDAY_URL,
                   "holidays": holidays}, f, indent=1, sort_keys=True)
    return {"source": "nse_holidays", "status": "OK", "n_items": len(holidays)}


def _holiday_map(cal: Optional[Dict[str, Any]]) -> Dict[str, str]:
    return {h["date"]: h["description"] for h in (cal or {}).get("holidays", [])}


def _covered_years(cal: Optional[Dict[str, Any]]) -> Set[int]:
    return {int(d[:4]) for d in _holiday_map(cal)}


def status_for(cal: Optional[Dict[str, Any]], day: date) -> Tuple[str, str]:
    if day.weekday() >= 5:
        return "CLOSED", "weekend"
    if cal is None:
        return "UNKNOWN", "no calendar file (state/nse_holidays.json) on agent-data"
    if day.year not in _covered_years(cal):
        return "UNKNOWN", f"calendar has no holidays listed for {day.year}"
    name = _holiday_map(cal).get(day.isoformat())
    return ("HOLIDAY", name) if name else ("TRADING_DAY", "")


def is_trading_day(cal: Optional[Dict[str, Any]], day: date) -> bool:
    """UNKNOWN counts as trading (see module docstring)."""
    return status_for(cal, day)[0] in ("TRADING_DAY", "UNKNOWN")


def next_session_close(cal: Optional[Dict[str, Any]], after: datetime) -> datetime:
    """The first NSE session close (15:30 IST) at or after `after` (UTC-aware)."""
    d = after.astimezone(timezone.utc).date()
    for _ in range(30):
        close = datetime.combine(d, SESSION_CLOSE_UTC, tzinfo=timezone.utc)
        if close >= after and is_trading_day(cal, d):
            return close
        d += timedelta(days=1)
    raise RuntimeError("no trading day within 30 days -- calendar is wrong")


def today_ist(now: Optional[datetime] = None) -> date:
    return ((now or datetime.now(timezone.utc)).astimezone(timezone.utc) + IST).date()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="intel.nse_calendar")
    ap.add_argument("--data", required=True, help="checkout of agent-data")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in IST)")
    args = ap.parse_args(argv)
    day = date.fromisoformat(args.date) if args.date else today_ist()
    verdict, detail = status_for(load(args.data), day)
    print(f"{verdict}: {detail}" if detail else verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
