"""
ForexFactory Economic Calendar Integration

Fetches economic events from ForexFactory for Forex/Crypto analysis.
Uses their public calendar page with BeautifulSoup parsing.
"""

import structlog
import feedparser
from typing import List, Dict, Any
from datetime import datetime, timedelta
import time

logger = structlog.get_logger()


class ForexFactoryCalendar:
    """
    Fetches and parses ForexFactory economic calendar events.
    Falls back to RSS-based economic calendar if direct scraping fails.
    """

    # Alternative RSS economic calendars (reliable fallbacks)
    CALENDAR_FEEDS = {
        "Investing.com Economic Calendar": "https://www.investing.com/rss/economic_calendar.rss",
        "FX Street Calendar": "https://www.fxstreet.com/rss/economic-calendar",
        "DailyFX Calendar": "https://www.dailyfx.com/feeds/economic-calendar",
    }

    # Major currency impact mapping
    CURRENCY_SYMBOLS = {
        "USD": ["BTC-USD", "ETH-USD", "XAU-USD", "EUR-USD", "GBP-USD", "USDT"],
        "EUR": ["EUR-USD", "EUR-GBP"],
        "GBP": ["GBP-USD", "EUR-GBP"],
        "JPY": ["USD-JPY", "EUR-JPY"],
        "INR": [".NS"],  # All NSE stocks affected by INR events
    }

    # Known high-impact events and their default impact scores
    HIGH_IMPACT_EVENTS = {
        "non-farm payrolls": 1.0,
        "nfp": 1.0,
        "interest rate decision": 0.95,
        "fed": 0.9,
        "fomc": 0.9,
        "cpi": 0.85,
        "inflation": 0.85,
        "gdp": 0.8,
        "unemployment": 0.75,
        "retail sales": 0.7,
        "trade balance": 0.65,
        "pmi": 0.65,
        "rbi": 0.85,  # Reserve Bank of India
        "ecb": 0.9,   # European Central Bank
        "boe": 0.9,   # Bank of England
        "boj": 0.85,  # Bank of Japan
    }

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_time = 0
        self._cache_ttl = 1800  # 30 minutes
        self._news_cache: List[Dict[str, Any]] = []
        self._news_cache_time = 0

    def fetch_community_news(self) -> List[Dict[str, Any]]:
        """
        Scrapes ForexFactory 'Hot Stories' and 'Breaking News' for community alpha.
        """
        now = time.time()
        if self._news_cache and (now - self._news_cache_time) < 600: # 10 min cache
            return self._news_cache

        try:
            import requests
            from bs4 import BeautifulSoup
            
            url = "https://www.forexfactory.com/news"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = requests.get(url, headers=headers, timeout=10)
            
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            stories = []
            
            # Find story containers (heuristic-based as FF layout changes)
            containers = soup.find_all("div", class_="flexBox")
            for item in containers:
                title_tag = item.find("a", class_="flexBox__item")
                if not title_tag: continue
                
                title = title_tag.get_text(strip=True)
                impact_tag = item.find("span", class_="flexBox__item--impact")
                impact = impact_tag.get_text(strip=True).upper() if impact_tag else "LOW"
                
                # Deduce sentiment from title (basic)
                sentiment = 0.0
                if any(w in title.lower() for w in ["surge", "bull", "jump", "gain", "up"]): sentiment = 0.5
                if any(w in title.lower() for w in ["drop", "bear", "plunge", "loss", "down"]): sentiment = -0.5

                stories.append({
                    "title": title,
                    "source": "ForexFactory News",
                    "link": "https://www.forexfactory.com" + title_tag['href'] if title_tag.has_attr('href') else url,
                    "impact_level": impact,
                    "sentiment_score": sentiment,
                    "published": datetime.now().isoformat()
                })
            
            self._news_cache = stories
            self._news_cache_time = now
            return stories
        except Exception as e:
            logger.debug("ff_community_news_failed", error=str(e))
            return []

    def fetch_economic_events(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Fetch upcoming economic events relevant to the given symbol.
        Uses RSS feeds as the data source.
        """
        # Check cache
        cache_key = f"events_{symbol or 'all'}"
        now = time.time()
        if cache_key in self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache[cache_key]

        all_events = []

        # 0. Try direct ForexFactory HTML calendar first (best for FX/Crypto intraday)
        try:
            ff_events = self._fetch_from_forex_factory()
            all_events.extend(ff_events)
        except Exception as e:
            logger.debug("forex_factory_html_failed", error=str(e))

        # 1. Try each RSS feed as a robust fallback
        for source_name, feed_url in self.CALENDAR_FEEDS.items():
            try:
                events = self._parse_rss_calendar(feed_url, source_name)
                all_events.extend(events)
            except Exception as e:
                logger.debug("calendar_feed_failed", source=source_name, error=str(e))
                continue

        # If RSS feeds fail, generate known upcoming events from static schedule
        if not all_events:
            all_events = self._get_known_recurring_events()

        # Filter by symbol relevance
        if symbol:
            all_events = self._filter_by_symbol(all_events, symbol)

        # Score impact
        scored = self._score_events(all_events)

        # Sort by impact
        scored.sort(key=lambda x: x.get("impact_score", 0), reverse=True)

        # Cache
        self._cache[cache_key] = scored
        self._cache_time = now

        logger.info("economic_events_fetched", total=len(scored), symbol=symbol)
        return scored

    def _fetch_from_forex_factory(self) -> List[Dict[str, Any]]:
        """
        Lightweight HTML scraper for ForexFactory's public calendar.

        NOTE:
        - This is best-effort and fully optional.
        - If requests/bs4 are not available or HTML layout changes, we fall back silently.
        """
        try:
            import requests
            from bs4 import BeautifulSoup  # type: ignore
        except Exception:
            # Dependencies missing in this environment; caller will fall back to RSS.
            return []

        url = "https://www.forexfactory.com/calendar"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            events: List[Dict[str, Any]] = []

            # The exact structure can change; we keep parsing defensive and generic.
            # We look for table rows that contain typical calendar data attributes.
            rows = soup.find_all("tr")
            now = datetime.utcnow().isoformat()

            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                text_row = " ".join(c.get_text(strip=True) for c in cols).lower()
                # Heuristic: skip rows that clearly aren't events
                if not any(k in text_row for k in ["gdp", "cpi", "payroll", "rate", "unemployment", "inflation"]):
                    continue

                title = cols[-1].get_text(strip=True) or "Economic Event"
                time_cell = cols[0].get_text(strip=True) or now
                impact_text = " ".join(c.get_text(strip=True).lower() for c in cols)

                # Rough currency detection from row text
                currency = "USD"
                for cur in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "INR"]:
                    if cur in impact_text.upper():
                        currency = cur
                        break

                events.append(
                    {
                        "title": title,
                        "time": time_cell,
                        "source": "ForexFactory Calendar (HTML)",
                        "summary": title,
                        "link": url,
                        "currency": currency,
                    }
                )

            return events
        except Exception:
            # Any network/HTML issue -> silent fallback to RSS calendars
            return []

    def _parse_rss_calendar(self, feed_url: str, source_name: str) -> List[Dict[str, Any]]:
        """Parse RSS feed for economic events."""
        feed = feedparser.parse(feed_url)
        events = []

        for entry in feed.entries[:20]:
            events.append({
                "title": entry.get("title", "Economic Event"),
                "time": entry.get("published", datetime.now().isoformat()),
                "source": source_name,
                "summary": entry.get("summary", ""),
                "link": entry.get("link", "#"),
                "currency": self._detect_currency(entry.get("title", "")),
            })

        return events

    def _detect_currency(self, text: str) -> str:
        """Detect which currency an event relates to."""
        text_upper = text.upper()
        for currency in ["USD", "EUR", "GBP", "JPY", "INR", "AUD", "CAD", "CHF"]:
            if currency in text_upper:
                return currency
        # Default to USD for unknown
        return "USD"

    def _filter_by_symbol(self, events: List[Dict[str, Any]], symbol: str) -> List[Dict[str, Any]]:
        """Filter events relevant to the given symbol."""
        relevant = []
        for event in events:
            currency = event.get("currency", "")
            related_symbols = self.CURRENCY_SYMBOLS.get(currency, [])

            # Check if symbol matches any related pattern
            is_relevant = False
            for pattern in related_symbols:
                if pattern in symbol.upper():
                    is_relevant = True
                    break

            # USD events are relevant to everything
            if currency == "USD":
                is_relevant = True

            # INR events for Indian stocks
            if ".NS" in symbol.upper() and currency == "INR":
                is_relevant = True

            if is_relevant:
                relevant.append(event)

        return relevant if relevant else events[:5]  # Return top 5 if nothing matches

    def _score_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Score each event by expected market impact."""
        for event in events:
            title_lower = event.get("title", "").lower()
            summary_lower = event.get("summary", "").lower()
            combined = title_lower + " " + summary_lower

            # Score based on known high-impact keywords
            max_score = 0.3  # Default: low impact
            matched_keyword = None
            for keyword, score in self.HIGH_IMPACT_EVENTS.items():
                if keyword in combined:
                    if score > max_score:
                        max_score = score
                        matched_keyword = keyword

            event["impact_score"] = max_score
            event["impact_level"] = (
                "HIGH" if max_score >= 0.8
                else "MEDIUM" if max_score >= 0.6
                else "LOW"
            )
            if matched_keyword:
                event["matched_trigger"] = matched_keyword

        return events

    def _get_known_recurring_events(self) -> List[Dict[str, Any]]:
        """
        Fallback: Generate known recurring economic events.
        These are approximate — real dates should come from RSS when available.
        """
        now = datetime.now()
        events = []

        # FOMC meetings are scheduled (roughly every 6 weeks)
        events.append({
            "title": "FOMC Interest Rate Decision (Upcoming)",
            "time": now.isoformat(),
            "source": "Economic Calendar (Scheduled)",
            "summary": "Federal Reserve interest rate decision and policy statement. High market impact expected.",
            "link": "https://www.federalreserve.gov/monetarypolicy.htm",
            "currency": "USD",
        })

        # US CPI (monthly, usually mid-month)
        events.append({
            "title": "US Consumer Price Index (CPI) - Monthly",
            "time": now.isoformat(),
            "source": "Economic Calendar (Scheduled)",
            "summary": "Key inflation indicator. Significant impact on interest rate expectations and all USD pairs.",
            "link": "#",
            "currency": "USD",
        })

        # NFP (first Friday of each month)
        events.append({
            "title": "US Non-Farm Payrolls (NFP) - Monthly",
            "time": now.isoformat(),
            "source": "Economic Calendar (Scheduled)",
            "summary": "Employment data release. Typically the most volatile event for USD pairs and crypto.",
            "link": "#",
            "currency": "USD",
        })

        # RBI for Indian markets
        events.append({
            "title": "RBI Monetary Policy Decision",
            "time": now.isoformat(),
            "source": "Economic Calendar (Scheduled)",
            "summary": "Reserve Bank of India interest rate and liquidity policy decision.",
            "link": "#",
            "currency": "INR",
        })

        return events

    def get_session_info(self) -> Dict[str, Any]:
        """
        Get current forex trading session status.
        Shows which sessions are active and their overlap status.
        """
        now = datetime.utcnow()
        hour = now.hour

        sessions = {
            "Sydney": {"open": 22, "close": 7, "status": "CLOSED", "emoji": "🦘"},
            "Tokyo": {"open": 0, "close": 9, "status": "CLOSED", "emoji": "🗼"},
            "London": {"open": 8, "close": 17, "status": "CLOSED", "emoji": "🏛️"},
            "New York": {"open": 13, "close": 22, "status": "CLOSED", "emoji": "🗽"},
        }

        active_sessions = []
        for name, info in sessions.items():
            open_h = info["open"]
            close_h = info["close"]

            if open_h < close_h:
                is_open = open_h <= hour < close_h
            else:  # Wraps midnight (Sydney)
                is_open = hour >= open_h or hour < close_h

            if is_open:
                sessions[name]["status"] = "OPEN"
                active_sessions.append(name)

        # Detect overlaps
        overlaps = []
        if "London" in active_sessions and "New York" in active_sessions:
            overlaps.append("London-New York (HIGHEST VOLUME)")
        if "Tokyo" in active_sessions and "London" in active_sessions:
            overlaps.append("Tokyo-London (Asian-European)")
        if "Sydney" in active_sessions and "Tokyo" in active_sessions:
            overlaps.append("Sydney-Tokyo (Asian)")

        # Volume estimation
        if overlaps and "London-New York" in overlaps[0]:
            volume_level = "PEAK"
        elif active_sessions:
            volume_level = "ACTIVE"
        else:
            volume_level = "LOW"

        return {
            "sessions": sessions,
            "active": active_sessions,
            "overlaps": overlaps,
            "volume_level": volume_level,
            "utc_time": now.strftime("%H:%M UTC"),
        }


# Module-level singleton
forex_calendar = ForexFactoryCalendar()
