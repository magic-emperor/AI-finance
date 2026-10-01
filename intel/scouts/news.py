"""
news.py — scout over Indian business/markets RSS.

Flags a listed company only when it is named by at least TWO INDEPENDENT publisher groups
within a 6-hour window. This is the same-event corroboration the old crisis_classifier
claimed to do but did not (it counted source diversity, not whether sources described the
same company/event). Moneycontrol + CNBC-TV18 are one ownership group, ET + ET Markets
another, and so on, so one outlet's syndicated story cannot corroborate itself.

Honest limit: matching is by company name / ticker in the headline, so it can miss a story
that names neither and can mis-attach a name shared by two firms. It flags *attention*; the
reasoning agent decides whether a flag means anything.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from intel.scouts.common import make_flag, now_utc_iso

WINDOW = timedelta(hours=6)

# name -> (url, publisher, ownership group). Feeds verified alive on 2026-10-01 (Phase 0);
# Reuters (shut 2020), CNBC-TV18 (HTTP 400) and ET /markets/stocks (empty) were dropped.
FEEDS = {
    "et_economy": ("https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
                   "Economic Times", "times_group"),
    "business_standard": ("https://www.business-standard.com/rss/markets-106.rss",
                          "Business Standard", "business_standard"),
    "livemint": ("https://www.livemint.com/rss/markets", "Mint", "ht_media"),
    "moneycontrol": ("https://www.moneycontrol.com/rss/business.xml", "Moneycontrol", "network18"),
    "investing_india": ("https://in.investing.com/rss/news.rss", "Investing.com", "investing"),
}

_NOISE_WORDS = {"limited", "ltd", "industries", "corporation", "company", "india", "and", "the", "of"}
_AMBIGUOUS_SYMBOLS = {"INDIA", "POWER", "STEEL", "GOLD", "BANK", "TATA", "TEXTILES", "GLOBAL", "PRIME"}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower().replace("&", " and ")).split())


def build_company_index(equity_l_csv: str) -> Dict[str, str]:
    """EQUITY_L.csv text -> {normalized distinctive name or ticker: SYMBOL}."""
    idx: Dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(equity_l_csv)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        sym, name = row.get("SYMBOL"), row.get("NAME OF COMPANY")
        if not sym or not name or row.get("SERIES", "EQ") not in ("EQ", "BE", "SM"):
            continue
        tokens = [t for t in _norm(name).split() if t not in _NOISE_WORDS]
        distinctive = " ".join(tokens)
        if len(tokens) >= 2 or (tokens and len(tokens[0]) >= 7):
            idx.setdefault(distinctive, sym)
        if len(sym) >= 5 and sym not in _AMBIGUOUS_SYMBOLS:
            idx.setdefault(sym.lower(), sym)
    return idx


def mentions(title: str, index: Dict[str, str]) -> List[str]:
    padded = f" {_norm(title)} "
    found = {sym for key, sym in index.items() if f" {key} " in padded}
    return sorted(found)


def news_flags(items: List[Dict[str, Any]], index: Dict[str, str], min_groups: int = 2) -> List[Dict[str, Any]]:
    """items: {title, link, published_at (UTC ISO), publisher, group}."""
    by_symbol: Dict[str, List[Tuple[datetime, Dict[str, Any]]]] = {}
    for it in items:
        try:
            ts = datetime.fromisoformat(it["published_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        for sym in mentions(it["title"], index):
            by_symbol.setdefault(sym, []).append((ts, it))

    flags = []
    for sym, entries in by_symbol.items():
        entries.sort(key=lambda e: e[0])
        newest = entries[-1][0]
        recent = [e for e in entries if newest - e[0] <= WINDOW]
        groups = {e[1]["group"] for e in recent}
        if len(groups) < min_groups:
            continue
        best_per_group: Dict[str, Dict[str, Any]] = {}
        for _, it in recent:
            best_per_group.setdefault(it["group"], it)
        evidence = [{"url": it["link"], "publisher": it["publisher"],
                     "published_at": it["published_at"], "claim": it["title"][:180]}
                    for it in best_per_group.values()]
        importance = min(0.75, 0.30 + 0.15 * len(groups))
        flags.append(make_flag(
            "news_multi_source", f"{sym}|{newest:%Y%m%d%H}", sym, importance,
            f"{sym} named by {len(groups)} independent publishers within 6h: "
            + " | ".join(it["title"][:90] for it in best_per_group.values()),
            evidence, newest.strftime("%Y-%m-%dT%H:%M:%SZ"),
            extra={"n_groups": len(groups)}))
    return flags


def fetch_items(http=None) -> Dict[str, Any]:
    """Network layer: pull every feed; per-feed failures are reported, not hidden."""
    import feedparser
    import requests
    http = http or requests.Session()
    items: List[Dict[str, Any]] = []
    statuses = []
    for name, (url, publisher, group) in FEEDS.items():
        try:
            r = http.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            parsed = feedparser.parse(r.content)
            n = 0
            for e in parsed.entries:
                tp = e.get("published_parsed") or e.get("updated_parsed")
                if not tp or not e.get("title") or not e.get("link"):
                    continue
                items.append({"title": e["title"].strip(), "link": e["link"], "publisher": publisher,
                              "group": group,
                              "published_at": datetime(*tp[:6], tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                n += 1
            if n == 0:
                raise RuntimeError("feed returned 0 usable items")
            statuses.append({"source": f"rss_{name}", "status": "OK", "n_items": n})
        except Exception as e:
            statuses.append({"source": f"rss_{name}", "status": "FAILED", "n_items": 0, "error": str(e)[:120]})
    return {"items": items, "sources": statuses}


def run(session, http=None) -> Dict[str, Any]:
    fetched = fetch_items(http)
    statuses = fetched["sources"]
    flags: List[Dict[str, Any]] = []
    try:
        csv_text = session.http.get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
                                    headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
        index = build_company_index(csv_text)
        if not index:
            raise RuntimeError("empty company index")
        flags = news_flags(fetched["items"], index)
        statuses.append({"source": "nse_equity_list", "status": "OK", "n_items": len(index)})
    except Exception as e:
        statuses.append({"source": "nse_equity_list", "status": "FAILED", "n_items": 0, "error": str(e)[:120]})
    return {"flags": flags, "sources": statuses}
