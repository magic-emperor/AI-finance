"""
market.py — whole-market candle scout. This is what answers "a stock we never listed starts
moving": it screens EVERY NSE equity from the daily bhavcopy, not a fixed watchlist.

Flags a stock when volume >= 3x its 20-day average AND the day's move is >= 2 sigma of its
own recent daily moves AND turnover clears a Rs 5 cr liquidity floor (below that a spike is
untradeable or an operator's footprint).

Stated up front: every flag from this scout is REACTIVE by construction -- the move has
already happened. The grader tags calls made right after such moves separately, so chasing
cannot be mistaken for foresight.
"""
from __future__ import annotations

import csv
import io
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from intel.scouts.common import make_flag, ist_to_utc_iso, now_utc_iso, SourceUnavailable

BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
HISTORY_DAYS = 20
MIN_HISTORY = 15
VOLUME_MULT = 3.0
MOVE_SIGMA = 2.0
MIN_TURNOVER_CR = 5.0
MAX_ALONE = 0.65


def parse_bhavcopy(text: str) -> List[Dict[str, Any]]:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if r.get("SERIES") != "EQ":
            continue
        try:
            rows.append({
                "symbol": r["SYMBOL"], "close": float(r["CLOSE_PRICE"]),
                "prev_close": float(r["PREV_CLOSE"]), "volume": float(r["TTL_TRD_QNTY"]),
                "turnover_cr": float(r["TURNOVER_LACS"]) / 100.0,
            })
        except (KeyError, ValueError):
            continue
    return rows


def volume_spike_flags(today_rows: List[Dict[str, Any]], history: Dict[str, List[List[float]]],
                       date_text: str) -> List[Dict[str, Any]]:
    """history: {symbol: [[volume, close], ...]} for prior days, oldest first."""
    flags = []
    ts = ist_to_utc_iso(date_text) or now_utc_iso()
    for r in today_rows:
        hist = history.get(r["symbol"], [])
        if len(hist) < MIN_HISTORY or r["turnover_cr"] < MIN_TURNOVER_CR or r["prev_close"] <= 0:
            continue
        avg_vol = sum(h[0] for h in hist) / len(hist)
        if avg_vol <= 0 or r["volume"] < VOLUME_MULT * avg_vol:
            continue
        closes = [h[1] for h in hist]
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 5:
            continue
        mean = sum(rets) / len(rets)
        sigma = math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1))
        move = r["close"] / r["prev_close"] - 1
        if sigma <= 0 or abs(move - mean) < MOVE_SIGMA * sigma:
            continue
        ratio = r["volume"] / avg_vol
        z = abs(move - mean) / sigma
        # Capped BELOW the escalation threshold: a volume spike on its own is a move that has
        # already happened (often an operator's), so it may only wake the agent alongside an
        # independent signal (a holder filing, a company disclosure, multi-publisher news).
        # Measured live 2026-10-01: lone spikes of 63x-149x volume topped the list at 0.86+.
        importance = min(MAX_ALONE, 0.45 + 0.03 * min(ratio - VOLUME_MULT, 6) + 0.02 * min(z - MOVE_SIGMA, 4))
        sym = r["symbol"]
        flags.append(make_flag(
            "volume_breakout", f"{sym}|{date_text}", sym, importance,
            f"{sym} {move * 100:+.1f}% on {ratio:.1f}x average volume (turnover Rs {r['turnover_cr']:.0f} cr)",
            [{"url": "https://www.nseindia.com/all-reports", "publisher": "NSE bhavcopy", "published_at": ts,
              "claim": f"close {r['close']} vs prev {r['prev_close']}, volume {ratio:.1f}x 20d avg"}],
            ts, direction_hint="UP" if move > 0 else "DOWN",
            extra={"reactive_by_construction": True, "vol_ratio": round(ratio, 2), "move_pct": round(move * 100, 2)}))
    return flags


def update_history(history: Dict[str, List[List[float]]], rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        h = history.setdefault(r["symbol"], [])
        h.append([r["volume"], r["close"]])
        del h[:-HISTORY_DAYS]


def _fetch_day(session, d: date) -> Optional[str]:
    try:
        r = session.http.get(BHAV_URL.format(d=d.strftime("%d%m%Y")),
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
    except Exception as e:
        raise SourceUnavailable(f"bhavcopy {d}: {type(e).__name__}")
    if r.status_code == 200 and r.text.startswith("SYMBOL"):
        return r.text
    return None                                    # weekend / holiday / not yet published


def run(session, state: Dict[str, Any], today: Optional[date] = None, bootstrap_days: int = 32) -> Dict[str, Any]:
    """state['bhav'] = {'processed': [dd-Mon-YYYY...], 'history': {...}}; mutated in place."""
    today = today or date.today()
    bhav = state.setdefault("bhav", {"processed": [], "history": {}})
    flags: List[Dict[str, Any]] = []
    try:
        start = today - timedelta(days=bootstrap_days if not bhav["history"] else 6)
        day, fetched = start, 0
        while day <= today:
            if day.weekday() < 5:
                label = day.strftime("%d-%b-%Y")
                if label not in bhav["processed"]:
                    text = _fetch_day(session, day)
                    if text:
                        rows = parse_bhavcopy(text)
                        if bhav["history"]:                     # only flag once history exists
                            newest_flags = volume_spike_flags(rows, bhav["history"], label)
                            flags = newest_flags               # keep the latest day's flags only
                        update_history(bhav["history"], rows)
                        bhav["processed"] = (bhav["processed"] + [label])[-40:]
                        fetched += 1
            day += timedelta(days=1)
        status = {"source": "nse_bhavcopy", "status": "OK", "n_items": fetched}
    except Exception as e:
        status = {"source": "nse_bhavcopy", "status": "FAILED", "n_items": 0, "error": str(e)[:150]}
    return {"flags": flags, "sources": [status]}
