"""
macro.py — policy/regulator releases and abnormal FX/index/commodity moves.

Regulator feeds (RBI, SEBI, PIB) are primary sources: any NEW item is flagged, because a
policy release is the one kind of news that is simultaneously rare, official, and market-moving.
FX/index moves flag a >= 2-sigma day in INR, majors, Nifty/BankNifty, gold, crude -- the
reasoning agent decides whether it is news-driven or noise.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from intel.scouts.common import make_flag, now_utc_iso

REGULATOR_FEEDS = {
    "rbi": ("https://www.rbi.org.in/pressreleases_rss.xml", "RBI"),
    "sebi": ("https://www.sebi.gov.in/sebirss.xml", "SEBI"),
    "pib": ("https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "PIB"),
}
WATCHED = ["USDINR=X", "EURINR=X", "EURUSD=X", "GBPUSD=X", "USDJPY=X",
           "^NSEI", "^NSEBANK", "GC=F", "CL=F"]
MAX_AGE = timedelta(hours=24)


def regulator_flags(items: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    flags = []
    for it in items:
        try:
            ts = datetime.fromisoformat(it["published_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if now - ts > MAX_AGE:
            continue
        flags.append(make_flag(
            "regulator_release", f"{it['publisher']}|{it['link']}", None, 0.60,
            f"[{it['publisher']}] {it['title'][:200]}",
            [{"url": it["link"], "publisher": it["publisher"], "published_at": it["published_at"],
              "claim": it["title"][:180]}], it["published_at"]))
    return flags


def move_flags(closes_by_symbol: Dict[str, List[float]], asof: str) -> List[Dict[str, Any]]:
    flags = []
    for sym, closes in closes_by_symbol.items():
        if len(closes) < 22:
            continue
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
        window, last = rets[-21:-1], rets[-1]
        mean = sum(window) / len(window)
        sigma = math.sqrt(sum((x - mean) ** 2 for x in window) / (len(window) - 1))
        if sigma <= 0:
            continue
        z = (last - mean) / sigma
        if abs(z) < 2.0:
            continue
        flags.append(make_flag(
            "macro_move", f"{sym}|{asof}", sym, 0.50 + 0.10 * min(abs(z) - 2.0, 3.0),
            f"{sym} moved {last * 100:+.2f}% ({z:+.1f} sigma) on {asof}",
            [{"url": f"https://finance.yahoo.com/quote/{sym}", "publisher": "Yahoo Finance",
              "published_at": now_utc_iso(), "claim": f"daily move {last * 100:+.2f}%, {z:+.1f} sigma vs 20d"}],
            # hint = the move's own direction, as for volume_breakout; B0 then tests continuation
            now_utc_iso(), direction_hint="UP" if z > 0 else "DOWN",
            extra={"z": round(z, 2), "reactive_by_construction": True}))
    return flags


def run(http=None) -> Dict[str, Any]:
    import feedparser
    import requests
    http = http or requests.Session()
    flags: List[Dict[str, Any]] = []
    statuses = []
    now = datetime.now(timezone.utc)

    items: List[Dict[str, Any]] = []
    for name, (url, publisher) in REGULATOR_FEEDS.items():
        try:
            r = http.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            entries = feedparser.parse(r.content).entries
            if not entries:
                raise RuntimeError("0 entries")
            for e in entries:
                tp = e.get("published_parsed") or e.get("updated_parsed")
                if tp and e.get("title") and e.get("link"):
                    items.append({"title": e["title"].strip(), "link": e["link"], "publisher": publisher,
                                  "published_at": datetime(*tp[:6], tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            statuses.append({"source": f"feed_{name}", "status": "OK", "n_items": len(entries)})
        except Exception as e:
            statuses.append({"source": f"feed_{name}", "status": "FAILED", "n_items": 0, "error": str(e)[:120]})
    flags += regulator_flags(items, now)

    try:
        import yfinance as yf
        closes = {}
        for sym in WATCHED:
            h = yf.Ticker(sym).history(period="2mo", interval="1d", auto_adjust=False)
            if h is not None and len(h) >= 22:
                closes[sym] = [float(x) for x in h["Close"].dropna().tolist()]
        if not closes:
            raise RuntimeError("no price history returned")
        asof = now.strftime("%Y-%m-%d")
        flags += move_flags(closes, asof)
        statuses.append({"source": "yahoo_macro_prices", "status": "OK", "n_items": len(closes)})
    except Exception as e:
        statuses.append({"source": "yahoo_macro_prices", "status": "FAILED", "n_items": 0, "error": str(e)[:120]})
    return {"flags": flags, "sources": statuses}
