"""
NewsWatcher — Event-driven news detection (Geminiiflow Architecture).

Three Layers:
  LAYER 1 — RSS WATCHER: polls every 2 min using HTTP Conditional GET.
             Server returns 304 (empty body, zero cost) when nothing changed.
  LAYER 2 — CHANGE DETECTOR: MD5 fingerprint comparison per symbol.
             Gemini is never called if fingerprint hasn't changed.
  LAYER 3 — GEMINI SCORER: called ONLY when headlines actually changed.
             Result stored in NewsCache. Scan cycles read from cache — zero Gemini.

Expected Gemini calls:
  ~43/day across 18 symbols (vs ~702/day with old always-on approach)
"""

import hashlib
import threading
import time
import datetime
import structlog
from typing import Dict, Optional, Callable, Tuple, List

try:
    import feedparser
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

log = structlog.get_logger("news_watcher")


# ─────────────────────────────────────────────────────────────────
# RSS SOURCE REGISTRY
# All free. No API keys. Covers all 18 symbols in the watchlist.
# ─────────────────────────────────────────────────────────────────

RSS_SOURCES: Dict[str, str] = {
    # Indian markets
    "ET_MARKETS":    "https://economictimes.indiatimes.com/markets/stocks/rss.cms",
    "MONEYCONTROL":  "https://www.moneycontrol.com/rss/business.xml",
    "BUSINESS_STD":  "https://www.business-standard.com/rss/markets-106.rss",
    "LIVEMINT":      "https://www.livemint.com/rss/markets",
    "CNBCTV18":      "https://www.cnbctv18.com/commonfeeds/v1/eng/rss/market.xml",
    "NSE_CORP":      "https://www.nseindia.com/api/corporateActions?index=equities",
    # Crypto
    "COINDESK":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "COINTELEGRAPH": "https://cointelegraph.com/rss",
    # Global / US / Macro
    "REUTERS_BIZ":   "https://feeds.reuters.com/reuters/businessNews",
    "CNBC_MARKETS":  "https://www.cnbc.com/id/20409666/device/rss/rss.html",
    "INVESTING_IN":  "https://in.investing.com/rss/news.rss",
}

# Which RSS sources cover each symbol class
SYMBOL_SOURCES: Dict[str, List[str]] = {
    ".NS":      ["ET_MARKETS", "MONEYCONTROL", "BUSINESS_STD", "LIVEMINT", "NSE_CORP"],
    "^NSEBANK": ["ET_MARKETS", "MONEYCONTROL", "BUSINESS_STD", "LIVEMINT"],
    "-USD":     ["COINDESK", "COINTELEGRAPH", "REUTERS_BIZ"],
    "GC=F":     ["REUTERS_BIZ", "INVESTING_IN"],
    "CL=F":     ["REUTERS_BIZ", "INVESTING_IN"],
    "GBPJPY=X": ["REUTERS_BIZ", "INVESTING_IN"],
    "USDJPY=X": ["REUTERS_BIZ", "INVESTING_IN"],
    "US_STOCK": ["REUTERS_BIZ", "CNBC_MARKETS"],
}

US_STOCKS = {"NVDA", "GOOGL", "AAPL", "AMD"}

# Keyword aliases for matching headlines to symbols (pure Python, no LLM)
SYMBOL_KEYWORDS: Dict[str, List[str]] = {
    "RELIANCE.NS":   ["reliance", "ril", "mukesh ambani", "jio"],
    "HDFCBANK.NS":   ["hdfc bank", "hdfcbank", "hdfc"],
    "ITC.NS":        ["itc limited", "itc ltd", " itc "],
    "TATASTEEL.NS":  ["tata steel", "tatasteel"],
    "LT.NS":         ["larsen", "toubro", "l&t", "lnt"],
    "M&M.NS":        ["mahindra", "m&m"],
    "ADANIENT.NS":   ["adani enterprises", "adani group"],
    "ADANIPORTS.NS": ["adani ports", "adaniports"],
    "^NSEBANK":      ["bank nifty", "banknifty", "nse bank", "banking sector"],
    "BTC-USD":       ["bitcoin", "btc"],
    "NVDA":          ["nvidia", "nvda"],
    "GOOGL":         ["google", "alphabet", "googl"],
    "AAPL":          ["apple", "aapl", "iphone"],
    "AMD":           ["amd", "advanced micro"],
    "GC=F":          ["gold", "xau", "gold futures", "bullion"],
    "CL=F":          ["crude oil", "wti", "brent", "oil price"],
    "GBPJPY=X":      ["gbp/jpy", "pound yen", "gbpjpy", "sterling yen"],
    "USDJPY=X":      ["usd/jpy", "dollar yen", "usdjpy", "yen"],
}


def _get_source_keys(symbol: str) -> List[str]:
    """Map a symbol to its list of RSS source keys."""
    if symbol.endswith(".NS"):
        return SYMBOL_SOURCES[".NS"]
    elif symbol == "^NSEBANK":
        return SYMBOL_SOURCES["^NSEBANK"]
    elif "-USD" in symbol:
        return SYMBOL_SOURCES["-USD"]
    elif symbol in {"GC=F", "CL=F", "GBPJPY=X", "USDJPY=X"}:
        return SYMBOL_SOURCES.get(symbol, SYMBOL_SOURCES["US_STOCK"])
    elif symbol in US_STOCKS:
        return SYMBOL_SOURCES["US_STOCK"]
    return SYMBOL_SOURCES["US_STOCK"]  # default


def _match_headlines(symbol: str, entries: list, max_results: int = 8) -> List[dict]:
    """
    Filter RSS entries for relevance to a symbol.
    Pure Python keyword matching — zero LLM calls.
    Returns list of {title, published, link} dicts.
    """
    keywords = SYMBOL_KEYWORDS.get(symbol, [symbol.replace(".NS", "").lower()])
    matched = []

    for entry in entries:
        title   = (entry.get("title", "") or "").lower()
        summary = (entry.get("summary", "") or "").lower()
        text    = title + " " + summary

        if any(kw.lower() in text for kw in keywords):
            matched.append({
                "title":     (entry.get("title", "") or "").strip(),
                "published": str(entry.get("published", "")),
                "link":      entry.get("link", ""),
            })

    return matched[:max_results]


def _fingerprint(headlines: List[dict]) -> str:
    """MD5 hash of all headline titles — cheap change detection."""
    content = "|".join(h["title"] for h in headlines if h.get("title"))
    return hashlib.md5(content.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────
# NEWS CACHE
# Thread-safe per-symbol state store.
# Scan cycles READ from this — never touch Gemini.
# ─────────────────────────────────────────────────────────────────

class NewsCache:
    """
    Thread-safe per-symbol news state storage.

    Per symbol:
      fingerprint   — MD5 of current headlines (change detection)
      headlines     — list of headline title strings
      sentiment     — float -1.0..+1.0 (Gemini-scored, event-driven)
      summary       — str  (Gemini one-line summary)
      scored_at     — datetime of last Gemini call
      needs_scoring — True when headlines changed, awaiting Gemini

    RSS metadata (stored under key '_rss_meta'):
      etag          — HTTP ETag from last successful fetch
      modified      — HTTP Last-Modified from last successful fetch
    """

    # Max age before score is considered stale regardless of fingerprint
    STALE_HOURS = 6

    def __init__(self):
        self._data: Dict[str, dict] = {}
        self._lock = threading.Lock()

    # ── RSS metadata (one entry per RSS source URL, not per symbol) ──

    def set_rss_meta(self, source_key: str,
                     etag: Optional[str],
                     modified: Optional[str]) -> None:
        with self._lock:
            if "_rss_meta" not in self._data:
                self._data["_rss_meta"] = {}
            self._data["_rss_meta"][source_key] = {
                "etag":     etag,
                "modified": modified,
            }

    def get_rss_meta(self, source_key: str) -> dict:
        with self._lock:
            return self._data.get("_rss_meta", {}).get(source_key, {})

    # ── Headline updates (no Gemini here) ───────────────────────────

    def update_headlines(self, symbol: str, headlines: List[dict]) -> bool:
        """
        Store headlines. Returns True if content changed vs. last stored.
        Does NOT call Gemini — only fingerprints and sets needs_scoring flag.
        """
        new_fp = _fingerprint(headlines)
        with self._lock:
            existing = self._data.get(symbol, {})
            changed  = existing.get("fingerprint") != new_fp

            self._data[symbol] = {
                **existing,
                "fingerprint":   new_fp,
                "headlines":     [h["title"] for h in headlines],
                "needs_scoring": changed or not existing.get("sentiment"),
            }
        return changed

    def store_sentiment(self, symbol: str,
                        sentiment: float, summary: str) -> None:
        """Called once by NewsWatcher after Gemini scores the headlines."""
        with self._lock:
            if symbol not in self._data:
                self._data[symbol] = {}
            self._data[symbol].update({
                "sentiment":     float(sentiment),
                "summary":       str(summary),
                "scored_at":     datetime.datetime.utcnow(),
                "needs_scoring": False,
            })
        log.info("sentiment_stored", symbol=symbol,
                 sentiment=round(float(sentiment), 3))

    def needs_scoring(self, symbol: str) -> bool:
        """
        True if Gemini should be called for this symbol. Three triggers:
          1. Never scored before
          2. Headlines changed since last score (fingerprint mismatch)
          3. Score is more than STALE_HOURS old
        """
        with self._lock:
            entry = self._data.get(symbol)
            if not entry:
                return True
            if entry.get("needs_scoring", True):
                return True
            scored_at = entry.get("scored_at")
            if scored_at:
                age_h = (datetime.datetime.utcnow() - scored_at
                         ).total_seconds() / 3600
                if age_h > self.STALE_HOURS:
                    return True
        return False

    def get_sentiment(self, symbol: str) -> dict:
        """
        Read latest scored sentiment. Called by scan cycle.
        ZERO Gemini calls — always returns immediately from cache.
        """
        with self._lock:
            entry = self._data.get(symbol, {})
        return {
            "sentiment": entry.get("sentiment", 0.0),
            "summary":   entry.get("summary", "No news scored yet"),
            "headlines": entry.get("headlines", []),
            "scored_at": entry.get("scored_at"),
            "fresh":     not entry.get("needs_scoring", True),
        }

    def get_all_symbols(self) -> Dict[str, dict]:
        """Return snapshot of all symbol states (for dashboard display)."""
        with self._lock:
            return {
                k: v.copy()
                for k, v in self._data.items()
                if not k.startswith("_")
            }


# ─────────────────────────────────────────────────────────────────
# RSS CONDITIONAL FETCHER
# Sends ETag + Last-Modified on every request.
# Server returns 304 (zero body) when nothing changed.
# ─────────────────────────────────────────────────────────────────

class ConditionalFetcher:
    """
    Fetches RSS feeds using HTTP Conditional GET.

    First fetch: full response. Stores ETag + Last-Modified.
    All subsequent fetches: send stored ETag + Last-Modified.
      → 304 Not Modified: nothing changed, zero bytes transferred.
      → 200 OK: new content, full entries returned.

    Polling every 2 minutes is essentially free when nothing is changing.
    """

    def __init__(self, news_cache: NewsCache):
        self.cache = news_cache

    def fetch(self, source_key: str, url: str) -> Tuple[bool, list]:
        """
        Fetch one RSS feed using conditional GET.

        Returns:
          (changed: bool, entries: list)
          changed=False → 304, reuse previous entries.
          changed=True  → 200 with new content.
        """
        if not _FEEDPARSER_AVAILABLE:
            log.warning("feedparser_not_installed",
                        hint="pip install feedparser")
            return False, []

        meta     = self.cache.get_rss_meta(source_key)
        etag     = meta.get("etag")
        modified = meta.get("modified")

        try:
            feed = feedparser.parse(
                url,
                etag     = etag     or None,
                modified = modified or None,
                agent    = "AegisFinanceBot/1.0 (market intelligence)",
            )

            # 304 Not Modified — nothing changed
            if getattr(feed, "status", 200) == 304:
                log.debug("rss_304_not_modified", source=source_key)
                return False, []

            # Store new conditional headers for next request
            new_etag     = getattr(feed, "etag", None)
            new_modified = getattr(feed, "modified", None)
            self.cache.set_rss_meta(source_key, new_etag, new_modified)

            entries = list(feed.entries or [])
            log.debug("rss_200_updated", source=source_key,
                      entries=len(entries))
            return bool(entries), entries

        except Exception as e:
            log.warning("rss_fetch_failed",
                        source=source_key,
                        url=url[:60],
                        error=str(e)[:80])
            return False, []


# ─────────────────────────────────────────────────────────────────
# NEWS WATCHER — MAIN CLASS
# Background daemon thread. Fires Gemini only on actual change.
# ─────────────────────────────────────────────────────────────────

class NewsWatcher:
    """
    Background thread that watches RSS news for all symbols.

    Every poll_interval_sec (default 120s):
      LAYER 1: Fetch all RSS sources via conditional GET
               → 304: skip (near-zero cost)
               → 200: new content available, continue
      LAYER 2: Match entries to symbols, check MD5 fingerprint
               → unchanged: skip
               → changed: set needs_scoring=True
      LAYER 3: Call Gemini ONCE per changed symbol
               → Store result in NewsCache

    Scan cycle (every 10 min) reads from NewsCache.
    Zero Gemini calls in scan cycle — reads stored result only.
    """

    def __init__(
        self,
        symbols: List[str],
        news_cache: NewsCache,
        gemini_scorer: Callable[[str, List[dict]], Tuple[float, str]],
        poll_interval_sec: int = 120,
    ):
        self.symbols       = symbols
        self.cache         = news_cache
        self.scorer        = gemini_scorer
        self.poll_interval = poll_interval_sec
        self.fetcher       = ConditionalFetcher(news_cache)
        self._stop         = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Local cache of last fetched entries per source (used when 304 fires)
        self._source_cache: Dict[str, list] = {}

    def start(self) -> None:
        """Start the background watcher thread."""
        if self._thread and self._thread.is_alive():
            log.info("news_watcher_already_running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target = self._loop,
            name   = "NewsWatcher",
            daemon = True,
        )
        self._thread.start()
        log.info("news_watcher_started",
                 symbols     = len(self.symbols),
                 poll_sec    = self.poll_interval,
                 feedparser  = _FEEDPARSER_AVAILABLE)

    def stop(self) -> None:
        """Signal the watcher to stop after the current cycle."""
        self._stop.set()
        log.info("news_watcher_stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as e:
                log.error("watcher_cycle_error", error=str(e)[:120])
            self._stop.wait(self.poll_interval)

    def _cycle(self) -> None:
        """
        One complete poll + score cycle.

        Step 1: Fetch all unique RSS sources (conditional GET)
        Step 2: Match entries to each symbol
        Step 3: Fingerprint comparison — detect actual changes
        Step 4: Call Gemini for symbols with changed headlines only
        """
        # ── Step 1: Fetch all unique sources ──────────────────────
        all_needed_sources: set = set()
        for symbol in self.symbols:
            for key in _get_source_keys(symbol):
                all_needed_sources.add(key)

        fresh_entries: Dict[str, list] = {}
        for source_key in all_needed_sources:
            url = RSS_SOURCES.get(source_key)
            if not url:
                continue
            changed, entries = self.fetcher.fetch(source_key, url)
            if changed:
                fresh_entries[source_key] = entries
                self._source_cache[source_key] = entries
            else:
                # 304 — reuse entries from previous cycle
                fresh_entries[source_key] = self._source_cache.get(
                    source_key, []
                )

        # ── Step 2 + 3: Match headlines → check fingerprint ───────
        symbols_needing_score: List[Tuple[str, List[dict]]] = []
        for symbol in self.symbols:
            source_keys = _get_source_keys(symbol)
            all_entries: list = []
            for key in source_keys:
                all_entries.extend(fresh_entries.get(key, []))

            headlines = _match_headlines(symbol, all_entries)
            if not headlines:
                continue

            self.cache.update_headlines(symbol, headlines)
            if self.cache.needs_scoring(symbol):
                symbols_needing_score.append((symbol, headlines))

        # ── Step 4: Gemini scoring — only for changed symbols ─────
        if not symbols_needing_score:
            log.debug("watcher_cycle_no_changes",
                      sources_checked=len(all_needed_sources))
            return

        log.info("watcher_cycle_scoring",
                 symbols_to_score=[s for s, _ in symbols_needing_score])

        for symbol, headlines in symbols_needing_score:
            try:
                sentiment, summary = self.scorer(symbol, headlines)
                self.cache.store_sentiment(symbol, sentiment, summary)
            except Exception as e:
                log.warning("scoring_failed",
                            symbol=symbol,
                            error=str(e)[:80])


# ─────────────────────────────────────────────────────────────────
# MODULE-LEVEL SINGLETON
# Accessed via get_news_cache() / get_news_watcher() from app.py
# ─────────────────────────────────────────────────────────────────

_news_cache: Optional[NewsCache] = None
_news_watcher: Optional[NewsWatcher] = None


def get_news_cache() -> Optional[NewsCache]:
    """Returns the global NewsCache if the watcher has been started."""
    return _news_cache


def get_news_watcher() -> Optional[NewsWatcher]:
    """Returns the global NewsWatcher instance."""
    return _news_watcher


def start_news_watcher(
    symbols: List[str],
    gemini_scorer: Callable[[str, List[dict]], Tuple[float, str]],
    poll_interval_sec: int = 120,
) -> Tuple[NewsCache, NewsWatcher]:
    """
    Convenience function to start the news watcher.
    Called once from app.py session_state initialization.

    Returns (cache, watcher) — both stored in st.session_state.
    """
    global _news_cache, _news_watcher

    if _news_watcher and _news_watcher.is_running:
        log.info("news_watcher_reusing_existing")
        return _news_cache, _news_watcher

    _news_cache  = NewsCache()
    _news_watcher = NewsWatcher(
        symbols           = symbols,
        news_cache        = _news_cache,
        gemini_scorer     = gemini_scorer,
        poll_interval_sec = poll_interval_sec,
    )
    _news_watcher.start()
    return _news_cache, _news_watcher
