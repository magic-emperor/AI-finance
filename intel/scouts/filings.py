"""
filings.py — scout over NSE's live filing streams: SAST Reg 29 substantial-acquisition
filings, bulk/block deals, and corporate announcements.

Source change on record (2026-10-01, found in Phase 0): NSE's insider-trading (PIT) feed
`/api/corporates-pit` is STALE -- its newest record is dated 2026-05-02 and every later
window returns zero rows. It is therefore NOT used. The "important holder is buying" idea is
carried instead by SAST Reg 29 filings (a holder crossing or moving within 5%+ stakes) and
bulk/block deals, both verified live on the probe date. If PIT starts returning fresh data
again, adding it back is an explicit, versioned change -- never a silent runtime fallback.

Asymmetry fixed in advance: only BUYS are flagged. Insider/large-holder sales are mostly
liquidity-driven (Lakonishok & Lee 2001; Cohen, Malloy & Pomorski 2012).
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from intel.scouts.common import NSESession, make_flag, ist_to_utc_iso, now_utc_iso

CRORE = 1e7
BULK_MIN_VALUE_CR = 5.0

SAST_URL = "https://www.nseindia.com/api/corporate-sast-reg29?index=equities&from_date={a}&to_date={b}"
DEALS_URL = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
ANN_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={a}&to_date={b}"

# announcement categories carrying real information, with a base importance
ANNOUNCEMENT_BASE = {
    "Action(s) taken or orders passed": 0.65,
    "Spurt in Volume": 0.60,
    "Acquisition": 0.60,
    "Bagging/Receiving of orders/contracts": 0.55,
    "Disclosure under SEBI Takeover Regulations": 0.50,
}


def _num(x) -> float:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


# A Reg 29 filing reports the CUMULATIVE change since the holder's last disclosure, over the
# period in `acquirerDate`. GRASIM's "2.01% promoter buy" (filed 2026-10-01) spanned
# 07-FEB-2024 to 24-SEP-2026: 2.6 years of small lots, not a fresh purchase. Measured over
# 2026-09-01..10-01: 275 of 339 open-market acquisitions cover a single day and 333 cover <=7
# days, so this only touches the long-period minority. Multipliers are judgment, not fitted.
SAST_SPAN_FACTORS = ((7, 1.00), (30, 0.85), (90, 0.70))     # (max span in days, factor)
SAST_LONG_SPAN_FACTOR = 0.55                                 # > 90 days
SAST_UNKNOWN_SPAN_FACTOR = 0.80                              # period missing or unparseable


def parse_sast_period(text: Optional[str]) -> Optional[Tuple[date, date]]:
    """'07-FEB-2024 to 24-SEP-2026' or '16-SEP-2026' -> (start, end); None if unparseable."""
    parts = [p.strip() for p in (text or "").split(" to ")]
    try:
        days = [datetime.strptime(p.title(), "%d-%b-%Y").date() for p in parts if p]
    except ValueError:
        return None
    if len(days) == 1:
        return days[0], days[0]
    if len(days) == 2 and days[0] <= days[1]:
        return days[0], days[1]
    return None


def sast_span_factor(span_days: Optional[int]) -> float:
    if span_days is None:
        return SAST_UNKNOWN_SPAN_FACTOR
    for max_days, factor in SAST_SPAN_FACTORS:
        if span_days <= max_days:
            return factor
    return SAST_LONG_SPAN_FACTOR


def sast_flags(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        if (r.get("acqSaleType") or "").lower() != "acquisition":
            continue
        # Only genuine market purchases carry information. Inter-se transfers are promoters
        # reshuffling among themselves, preferential allotments are share issues, "Others" is
        # an unexplained bucket. Measured 2026-10-01: of ~100 meaningful (>=0.5%) acquisitions
        # in two weeks only 22 were open-market -- the rest was noise.
        if (r.get("acquisitionMode") or "").strip().lower() != "open market":
            continue
        acquired_pct, after_pct = _num(r.get("totAcqShare")), _num(r.get("totAftShare"))
        promoter = (r.get("promoterType") or "").upper() == "Y"
        if promoter and acquired_pct >= 0.5:
            importance = 0.75 + min(0.2, acquired_pct / 10)     # promoters know their own firm best
        elif after_pct >= 5.0 and acquired_pct >= 1.0:
            importance = 0.65 + min(0.2, acquired_pct / 10)
        else:
            continue
        sym = r.get("symbol")
        ts = ist_to_utc_iso(r.get("timestamp") or r.get("sysTime")) or now_utc_iso()
        who = "Promoter" if promoter else "Holder"
        period = parse_sast_period(r.get("acquirerDate"))
        span_days = (period[1] - period[0]).days if period else None
        importance *= sast_span_factor(span_days)
        if period:
            when = (f"on {period[0]:%d-%b-%Y}" if span_days == 0
                    else f"over {period[0]:%d-%b-%Y} to {period[1]:%d-%b-%Y} ({span_days} days)")
            lag_days = (datetime.fromisoformat(ts.replace("Z", "+00:00")).date() - period[1]).days
        else:
            when, lag_days = "over an unstated period", None
        out.append(make_flag(
            "sast_acquisition", f"{sym}|{r.get('application_no')}", sym, importance,
            f"{who} {r.get('acquirerName')} bought {acquired_pct:.2f}% of {r.get('company')} "
            f"{when} ({r.get('acquisitionMode')}), holding now {after_pct:.2f}%",
            [{"url": r.get("attachement") or "https://www.nseindia.com/companies-listing/corporate-filings-regulation-29",
              "publisher": "NSE", "published_at": ts,
              "claim": f"Reg29 filing: acquired {acquired_pct:.2f}% {when}, now {after_pct:.2f}%"[:200]}],
            ts, direction_hint="UP",
            extra={"promoter": promoter, "acquired_pct": acquired_pct, "after_pct": after_pct,
                   "period_start": period[0].isoformat() if period else None,
                   "period_end": period[1].isoformat() if period else None,
                   "span_days": span_days, "days_end_to_filing": lag_days}))
    return out


def deal_flags(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    asof = payload.get("as_on_date")
    for bucket, label in (("BULK_DEALS_DATA", "bulk"), ("BLOCK_DEALS_DATA", "block")):
        for d in payload.get(bucket) or []:
            if (d.get("buySell") or "").upper() != "BUY":
                continue
            value_cr = _num(d.get("qty")) * _num(d.get("watp")) / CRORE
            if value_cr < BULK_MIN_VALUE_CR:
                continue
            # Deliberately modest: most bulk-deal buyers are brokers/prop desks/algo shops
            # (NK Securities, AlphaGrep...), not informed investors. A deal alone should never
            # wake the agent; it can only matter alongside an independent signal.
            importance = 0.30 + 0.15 * math.log10(value_cr / BULK_MIN_VALUE_CR)
            sym = d.get("symbol")
            ts = ist_to_utc_iso(d.get("date") or asof) or now_utc_iso()
            out.append(make_flag(
                f"{label}_deal_buy", f"{sym}|{d.get('clientName')}|{d.get('qty')}|{d.get('date')}", sym,
                importance,
                f"{d.get('clientName')} bought Rs {value_cr:.1f} cr of {d.get('name')} in a {label} deal",
                [{"url": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
                  "publisher": "NSE", "published_at": ts,
                  "claim": f"{label} deal BUY {d.get('qty')} @ {d.get('watp')} = Rs {value_cr:.1f} cr"}],
                ts, direction_hint="UP", extra={"value_cr": round(value_cr, 2)}))
    return out


def announcement_flags(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        base = ANNOUNCEMENT_BASE.get(r.get("desc"))
        if base is None or not r.get("symbol"):
            continue
        sym = r["symbol"]
        ts = ist_to_utc_iso(r.get("an_dt") or r.get("exchdisstime")) or now_utc_iso()
        text = (r.get("attchmntText") or "").strip()
        # NSE's "Spurt in Volume" notice is the company answering an exchange query ABOUT the
        # volume spike -- the same tape event, not independent corroboration of it.
        kind = "volume_spurt_notice" if r.get("desc") == "Spurt in Volume" else "announcement"
        out.append(make_flag(
            kind, f"{sym}|{r.get('seq_id')}", sym, base,
            f"[{r.get('desc')}] {r.get('sm_name') or sym}: {text[:200]}",
            [{"url": r.get("attchmntFile") or "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
              "publisher": "NSE", "published_at": ts, "claim": (text[:180] or r.get("desc"))}],
            ts, direction_hint=None, extra={"desc": r.get("desc")}))
    return out


def run(session: NSESession, date_from: str, date_to: str) -> Dict[str, Any]:
    """Fetch all three streams; each failure is isolated and reported, never papered over."""
    flags: List[Dict[str, Any]] = []
    statuses = []
    for name, fetch, build in (
        ("nse_sast_reg29", lambda: session.get_json(SAST_URL.format(a=date_from, b=date_to)),
         lambda d: sast_flags(d["data"] if isinstance(d, dict) else d)),
        ("nse_bulk_block_deals", lambda: session.get_json(DEALS_URL), deal_flags),
        ("nse_announcements", lambda: session.get_json(ANN_URL.format(a=date_from, b=date_to)),
         lambda d: announcement_flags(d if isinstance(d, list) else d.get("data", []))),
    ):
        try:
            made = build(fetch())
            flags += made
            statuses.append({"source": name, "status": "OK", "n_items": len(made)})
        except Exception as e:
            statuses.append({"source": name, "status": "FAILED", "n_items": 0, "error": str(e)[:150]})
    return {"flags": flags, "sources": statuses}
