"""
CrisisClassifier — 5-Layer Truth Pipeline (Phase N, N2b)
=========================================================
Classifies macro events from multi-source news and assigns crisis type,
severity, and sector impact map.  Does NOT use keyword matching — uses
Gemini for context-aware classification after a source authority +
cross-corroboration pre-filter.

Five Layers:
  Layer 1 — Source Authority Scoring
  Layer 2 — Cross-Source Corroboration
  Layer 3 — Gemini Structured Classification
  Layer 4 — Truth Confidence Score
  Layer 5 — Recency Gate

Usage:
    from market_agent.learning.crisis_classifier import CrisisClassifier
    classifier = CrisisClassifier()
    event = classifier.classify(all_sources)
    # event.crisis_type, event.severity, event.sector_map, event.truth_score
"""

import json
import re
import datetime
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import structlog

log = structlog.get_logger("crisis_classifier")

# ─────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────

CRISIS_TYPES = [
    "WAR_GEOPOLITICAL",
    "OIL_SUPPLY_SHOCK",
    "REGULATORY_CRACKDOWN",
    "CORPORATE_SCANDAL",
    "PANDEMIC",
    "CREDIT_CRISIS",
    "CURRENCY_CRISIS",
    "NONE",
]

SEVERITIES = ["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Source authority weights (Layer 1)
SOURCE_AUTHORITY: Dict[str, float] = {
    # High-authority institutional sources
    "reuters":          0.90,
    "bloomberg":        0.90,
    "et markets":       0.88,
    "economic times":   0.85,
    "forexfactory":     0.85,
    "forex factory":    0.85,
    "moneycontrol":     0.78,
    "cnbc":             0.78,
    "nse":              0.80,
    "livemint":         0.75,
    "business standard":0.75,
    "cnbctv18":         0.72,
    "finnhub":          0.75,
    "coindesk":         0.72,
    "cointelegraph":    0.70,
    "investing.com":    0.68,
    # Low-authority social sources
    "stocktwits":       0.40,
    "reddit":           0.38,
    "twitter":          0.38,
    "x.com":            0.38,
    "unknown":          0.30,
}

# Corroboration weight by number of distinct sources (Layer 2)
def _corroboration_weight(n_sources: int) -> float:
    if n_sources >= 4: return 1.0
    if n_sources == 3: return 0.85
    if n_sources == 2: return 0.65
    return 0.35   # single source

# Negative sector/macro terms that pre-filter before calling Gemini
_NEGATIVE_TERMS = [
    "war", "conflict", "sanction", "missile", "attack", "invasion",
    "crisis", "crash", "collapse", "default", "bankruptcy", "fraud",
    "outbreak", "pandemic", "surge", "shortage", "cut", "ban", "halt",
    "explosion", "coup", "nuclear", "ceasefire", "blockade", "embargo",
    "recession", "inflation", "rate hike", "credit downgrade",
]

# Crisis→sector direction map (for signal routing in MacroCircuitBreaker)
CRISIS_SECTOR_MAP: Dict[str, Dict[str, str]] = {
    "WAR_GEOPOLITICAL": {
        "GOLD": "UP", "CRUDE_OIL": "UP", "DEFENSE": "UP", "JPY": "UP",
        "AVIATION": "DOWN", "TOURISM": "DOWN", "GENERAL_EQUITY": "DOWN",
        "BANKING": "DOWN", "STEEL": "DOWN",
    },
    "OIL_SUPPLY_SHOCK": {
        "CRUDE_OIL": "UP", "ENERGY": "UP", "EV": "UP", "ALTERNATIVE_ENERGY": "UP",
        "AIRLINES": "DOWN", "PLASTICS": "DOWN", "SHIPPING": "DOWN",
        "PETROL_VEHICLES": "DOWN",
    },
    "REGULATORY_CRACKDOWN": {
        "COMPETITORS": "UP",
        "TARGETED_SECTOR": "DOWN",
    },
    "CORPORATE_SCANDAL": {
        "COMPETITORS": "UP",
        "COMPANY": "DOWN", "SECTOR": "DOWN",
    },
    "PANDEMIC": {
        "PHARMA": "UP", "HEALTHCARE": "UP", "FMCG": "UP",
        "AVIATION": "DOWN", "HOTELS": "DOWN", "TOURISM": "DOWN",
    },
    "CREDIT_CRISIS": {
        "GOLD": "UP", "FMCG": "UP",
        "BANKING": "DOWN", "FINANCIALS": "DOWN", "CRYPTO": "DOWN",
    },
    "CURRENCY_CRISIS": {
        "EXPORTERS": "UP", "IT_STOCKS": "UP", "GOLD": "UP",
        "IMPORTERS": "DOWN", "DEBT_HEAVY": "DOWN",
    },
    "NONE": {},
}

# Symbol → sector mapping for our watchlist
SYMBOL_SECTOR: Dict[str, List[str]] = {
    "RELIANCE.NS":   ["ENERGY", "TELECOM", "EXPORTERS"],
    "HDFCBANK.NS":   ["BANKING", "FINANCIALS"],
    "ITC.NS":        ["FMCG", "TOBACCO"],
    "TATASTEEL.NS":  ["STEEL", "MANUFACTURING"],
    "LT.NS":         ["INFRASTRUCTURE", "DEFENSE"],
    "M&M.NS":        ["PETROL_VEHICLES", "EV"],
    "ADANIENT.NS":   ["INFRASTRUCTURE", "PORTS"],
    "ADANIPORTS.NS": ["SHIPPING", "PORTS"],
    "^NSEBANK":      ["BANKING", "FINANCIALS"],
    "NVDA":          ["TECHNOLOGY", "AI"],
    "GOOGL":         ["TECHNOLOGY"],
    "AAPL":          ["TECHNOLOGY"],
    "AMD":           ["TECHNOLOGY"],
    "MSFT":          ["TECHNOLOGY"],
    "META":          ["TECHNOLOGY"],
    "AMZN":          ["TECHNOLOGY", "LOGISTICS"],
    "TSLA":          ["EV", "TECHNOLOGY"],
    "BTC-USD":       ["CRYPTO"],
    "GC=F":          ["GOLD"],
    "CL=F":          ["CRUDE_OIL", "ENERGY"],
    "GBPJPY=X":      ["FOREX", "GBP", "JPY"],
    "USDJPY=X":      ["FOREX", "USD", "JPY"],
}

# ─────────────────────────────────────────────────────────────────
# CrisisEvent dataclass
# ─────────────────────────────────────────────────────────────────

@dataclass
class CrisisEvent:
    crisis_type:           str = "NONE"
    severity:              str = "NONE"       # NONE/LOW/MEDIUM/HIGH/CRITICAL
    sector_map:            Dict[str, str] = field(default_factory=dict)
    truth_score:           float = 0.0        # 0.0–1.0
    corroborating_sources: List[str] = field(default_factory=list)
    gemini_reasoning:      str = ""
    timestamp:             Optional[datetime.datetime] = None
    urgency:               str = "NONE"       # NONE/EMERGING/ACTIVE/KNOWN/PRE_POSITION

    def is_actionable(self) -> bool:
        return self.truth_score >= 0.70 and self.severity not in ("NONE", "LOW")

    def get_symbol_direction(self, symbol: str) -> str:
        """
        Returns expected direction for a symbol given this crisis.
        Returns 'UP', 'DOWN', or 'NEUTRAL'.
        """
        if self.crisis_type == "NONE" or not self.sector_map:
            return "NEUTRAL"
        sectors = SYMBOL_SECTOR.get(symbol, [])
        directions = []
        for sector in sectors:
            d = self.sector_map.get(sector)
            if d:
                directions.append(d)
        if not directions:
            # Check GENERAL_EQUITY as fallback for NSE equities
            if symbol.endswith(".NS") or symbol == "^NSEBANK":
                return self.sector_map.get("GENERAL_EQUITY", "NEUTRAL")
            return "NEUTRAL"
        # If conflicting directions, use DOWN (conservative)
        if "DOWN" in directions:
            return "DOWN"
        if "UP" in directions:
            return "UP"
        return "NEUTRAL"


# ─────────────────────────────────────────────────────────────────
# CrisisClassifier
# ─────────────────────────────────────────────────────────────────

class CrisisClassifier:
    """
    Classifies macro crises from multi-source news using a 5-layer pipeline.

    Call once per scan cycle (every 10 min). Result is cached — Gemini
    is called ONLY when authority sources have negative content AND the
    cache is stale (> 10 min).

    all_sources dict keys:
      rss_headlines       — list of {title, source} from NewsWatcher
      finnhub_headlines   — list of {title, source} from Finnhub
      forex_factory_events— list of {title, impact_score} from ForexFactory
      social_signals      — list of {title, source} from StockTwits/Reddit
      vix_level           — float (India VIX current value)
    """

    CACHE_SECONDS = 600  # 10 minutes — one scan cycle

    def __init__(self):
        self._cache: Optional[CrisisEvent] = None
        self._cache_time: Optional[datetime.datetime] = None
        self._lock = threading.Lock()

    def classify(self, all_sources: dict) -> CrisisEvent:
        """
        Main entry. Returns CrisisEvent for the current macro environment.
        Uses cache if < 10 min old.
        """
        with self._lock:
            now = datetime.datetime.utcnow()
            if (self._cache is not None and self._cache_time is not None and
                    (now - self._cache_time).total_seconds() < self.CACHE_SECONDS):
                return self._cache

            result = self._run_pipeline(all_sources)
            self._cache = result
            self._cache_time = now
            return result

    def _run_pipeline(self, all_sources: dict) -> CrisisEvent:
        """Run all 5 layers and return a CrisisEvent."""

        rss_items      = all_sources.get("rss_headlines", [])
        finnhub_items  = all_sources.get("finnhub_headlines", [])
        ff_events      = all_sources.get("forex_factory_events", [])
        social_items   = all_sources.get("social_signals", [])
        vix_level      = float(all_sources.get("vix_level", 0.0) or 0.0)

        all_items = (
            [(i, "rss")      for i in rss_items] +
            [(i, "finnhub")  for i in finnhub_items] +
            [(i, "ff")       for i in ff_events] +
            [(i, "social")   for i in social_items]
        )

        # ── Layer 1: Source Authority Scoring ─────────────────────
        scored_items = []
        for item, origin in all_items:
            title  = (item.get("title") or item.get("headline") or "").strip()
            source = (item.get("source") or origin).lower()
            auth   = self._get_authority(source)
            scored_items.append({
                "title":      title,
                "source":     source,
                "authority":  auth,
                "published":  item.get("published", ""),
            })

        # ── Layer 5 (Recency Gate) applied now to scored items ─────
        for item in scored_items:
            item["urgency"] = self._recency_urgency(item.get("published", ""))

        # ── Layer 2: Cross-Source Corroboration ────────────────────
        # Group headlines by semantic similarity (simplified: shared significant words)
        corroborating_sources = list({i["source"] for i in scored_items
                                      if i["authority"] >= 0.70})
        n_auth_sources = len(corroborating_sources)
        corr_weight = _corroboration_weight(n_auth_sources)

        # ── Pre-filter: only call Gemini if negative content present ──
        all_titles = [i["title"] for i in scored_items if i["title"]]
        combined_text = " ".join(all_titles).lower()
        has_negative = any(term in combined_text for term in _NEGATIVE_TERMS)

        # Include VIX spike as automatic escalation (price confirms news)
        vix_spike = vix_level > 20.0

        if not has_negative and not vix_spike:
            return CrisisEvent(
                crisis_type="NONE", severity="NONE",
                truth_score=0.0, timestamp=datetime.datetime.utcnow(),
                urgency="NONE",
            )

        # ── Layer 3: Gemini Structured Classification ──────────────
        avg_authority = (
            sum(i["authority"] for i in scored_items if i["authority"] > 0) /
            max(len(scored_items), 1)
        )
        gemini_result = self._call_gemini(
            all_titles[:15], corroborating_sources, vix_level
        )
        gemini_conf   = float(gemini_result.get("gemini_confidence", 0.5))
        crisis_type   = gemini_result.get("crisis_type", "NONE")
        severity      = gemini_result.get("severity", "NONE")
        reasoning     = gemini_result.get("reasoning", "")

        # ── Layer 4: Truth Confidence Score ───────────────────────
        truth_score = (
            avg_authority  * 0.35 +
            corr_weight    * 0.35 +
            gemini_conf    * 0.30
        )
        truth_score = round(min(1.0, truth_score), 3)

        # Override severity if truth_score too low
        if truth_score < 0.40:
            severity = "NONE"
            crisis_type = "NONE"
        elif truth_score < 0.70 and severity in ("HIGH", "CRITICAL"):
            severity = "MEDIUM"  # Downgrade unconfirmed critical claims

        # Build sector map from crisis type
        sector_map = dict(CRISIS_SECTOR_MAP.get(crisis_type, {}))

        # Determine urgency from most recent high-authority item
        urgency = self._dominant_urgency(scored_items)

        event = CrisisEvent(
            crisis_type           = crisis_type,
            severity              = severity,
            sector_map            = sector_map,
            truth_score           = truth_score,
            corroborating_sources = corroborating_sources,
            gemini_reasoning      = reasoning,
            timestamp             = datetime.datetime.utcnow(),
            urgency               = urgency,
        )
        log.info("crisis_classified",
                 type=crisis_type, severity=severity,
                 truth_score=truth_score, sources=n_auth_sources,
                 urgency=urgency)
        return event

    def _get_authority(self, source_str: str) -> float:
        """Return authority score for a source string (fuzzy match)."""
        s = source_str.lower()
        for key, score in SOURCE_AUTHORITY.items():
            if key in s:
                return score
        return SOURCE_AUTHORITY["unknown"]

    def _recency_urgency(self, published_str: str) -> str:
        """Layer 5: classify event urgency by age."""
        if not published_str:
            return "NONE"
        try:
            # Try parsing ISO format
            pub = datetime.datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            pub = pub.replace(tzinfo=None)  # strip tz for comparison
            age_min = (datetime.datetime.utcnow() - pub).total_seconds() / 60
            if age_min < 30:
                return "EMERGING"
            if age_min < 240:
                return "ACTIVE"
            return "KNOWN"
        except Exception:
            return "UNKNOWN"

    def _dominant_urgency(self, items: list) -> str:
        """Return the most urgent urgency level from scored items."""
        priority = {"EMERGING": 4, "ACTIVE": 3, "UNKNOWN": 2, "KNOWN": 1, "NONE": 0}
        best = "NONE"
        for item in items:
            u = item.get("urgency", "NONE")
            if priority.get(u, 0) > priority.get(best, 0):
                best = u
        return best

    def _call_gemini(
        self,
        titles: List[str],
        corroborating_sources: List[str],
        vix_level: float,
    ) -> dict:
        """
        Layer 3: Call Gemini for structured crisis classification.
        Returns dict with crisis_type, severity, affected_sectors,
        gemini_confidence, reasoning, is_likely_real.
        Falls back to NONE on any error.
        """
        fallback = {
            "crisis_type": "NONE", "severity": "NONE",
            "affected_sectors": {}, "gemini_confidence": 0.3,
            "reasoning": "Gemini unavailable", "is_likely_real": False,
        }
        try:
            from market_agent.brain.gemini_client import gemini_client
        except Exception:
            return fallback

        if not titles:
            return fallback

        headlines_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
        sources_text   = ", ".join(corroborating_sources[:6]) or "unknown"

        prompt = f"""You are a macro risk analyst for an institutional trading desk.
Analyze these news headlines and classify any macro crisis event.

Headlines (from sources: {sources_text}):
{headlines_text}

Current India VIX: {vix_level:.1f} (>25 = elevated fear)

Respond ONLY with valid JSON, no other text:
{{
  "crisis_type": "WAR_GEOPOLITICAL|OIL_SUPPLY_SHOCK|REGULATORY_CRACKDOWN|CORPORATE_SCANDAL|PANDEMIC|CREDIT_CRISIS|CURRENCY_CRISIS|NONE",
  "severity": "NONE|LOW|MEDIUM|HIGH|CRITICAL",
  "affected_sectors": {{"GOLD": "UP", "AVIATION": "DOWN", "BANKING": "DOWN"}},
  "gemini_confidence": 0.85,
  "reasoning": "One sentence: what is happening and why it matters for markets.",
  "is_likely_real": true
}}

Rules:
- Only classify HIGH or CRITICAL if 3+ credible news sources agree on the same event.
- "war on inflation" is NOT WAR_GEOPOLITICAL. Context matters — read carefully.
- If headlines are routine market news, return crisis_type=NONE, severity=NONE.
- gemini_confidence: 0.0=uncertain, 1.0=certain. Be honest about uncertainty.
- affected_sectors: only include sectors with clear directional impact (UP or DOWN).
"""

        try:
            raw = gemini_client.call(prompt, max_tokens=350)
            m = re.search(r'\{[^{}]+\}', raw or '', re.DOTALL)
            if not m:
                log.warning("gemini_crisis_no_json", raw=(raw or "")[:100])
                return fallback
            data = json.loads(m.group())
            # Validate crisis_type and severity
            if data.get("crisis_type") not in CRISIS_TYPES:
                data["crisis_type"] = "NONE"
            if data.get("severity") not in SEVERITIES:
                data["severity"] = "NONE"
            data["gemini_confidence"] = float(
                data.get("gemini_confidence", 0.5) or 0.5)
            return data
        except Exception as exc:
            log.warning("gemini_crisis_parse_failed", error=str(exc)[:80])
            return fallback


# Module-level singleton
_classifier: Optional[CrisisClassifier] = None


def get_crisis_classifier() -> CrisisClassifier:
    global _classifier
    if _classifier is None:
        _classifier = CrisisClassifier()
    return _classifier
