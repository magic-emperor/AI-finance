"""
common.py — the Flag record, IST->UTC time handling, and the NSE session helper.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

IST = timedelta(hours=5, minutes=30)

NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


class SourceUnavailable(Exception):
    """A pinned source did not answer usefully. The caller records FAILED; no fallback."""


def ist_to_utc_iso(text: str, fmts=("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y")) -> Optional[str]:
    """Parse an NSE IST timestamp like '01-Oct-2026 11:26' into 'YYYY-MM-DDTHH:MM:SSZ' (UTC)."""
    if not text:
        return None
    text = text.strip()
    for fmt in fmts:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return (dt - IST).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


_PLAIN_NSE_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _instrument_for(symbol: Optional[str]) -> Optional[str]:
    """Plain NSE ticker -> 'TICKER.NS'; indices, FX and futures symbols pass through."""
    if symbol and _PLAIN_NSE_SYMBOL.match(symbol):
        return f"{symbol}.NS"
    return symbol


def make_flag(kind: str, key: str, symbol: Optional[str], importance: float, summary: str,
              evidence: List[Dict[str, str]], observed_at: str, direction_hint: Optional[str] = None,
              extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One structured flag. flag_id is deterministic so re-seeing the same event dedups."""
    fid = hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:16]
    return {
        "flag_id": fid, "kind": kind, "symbol": symbol,
        "instrument": _instrument_for(symbol),
        "importance": round(max(0.0, min(1.0, importance)), 3),
        "direction_hint": direction_hint, "summary": summary[:300],
        "evidence": evidence, "observed_at": observed_at, "extra": extra or {},
    }


class NSESession:
    """NSE's JSON APIs want a browser-like session. Warm it with the homepage first."""

    def __init__(self, http=None, sleep=time.sleep):
        import requests
        self.http = http or requests.Session()
        self.sleep = sleep
        self._warm = False

    def _warmup(self) -> None:
        if self._warm:
            return
        try:
            self.http.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=20)
        except Exception:
            pass                              # the homepage 403s from some IPs; APIs still answer
        self.sleep(1.0)
        self._warm = True

    def get_json(self, url: str, retries: int = 2):
        self._warmup()
        last = "no attempt"
        for attempt in range(retries + 1):
            try:
                r = self.http.get(url, headers=NSE_HEADERS, timeout=40)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}"
            except ValueError:
                last = "non-JSON body"
            except Exception as e:                # network error
                last = type(e).__name__
            self.sleep(1.5 * (attempt + 1))
        raise SourceUnavailable(f"{url} -> {last}")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
