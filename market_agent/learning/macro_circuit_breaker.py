"""
MacroCircuitBreaker — Crisis-to-Opportunity Signal Router (Phase N, N2c)
========================================================================
Three-state machine:
  CLEAR  (India VIX < 20)          → confidence floor 0.50 (normal)
  WATCH  (India VIX 20-25)         → confidence floor 0.65
  HALT   (India VIX > 25, STRICT)  → block signals that fight crisis,
                                      PASS signals aligned with crisis direction

VIX is cached for 5 minutes (one scan cycle).
CrisisClassifier result feeds sector routing in HALT/WATCH states.

Gemini/Finnhub layer is INDEPENDENT — can escalate to WATCH even when
VIX < 20 if a CRITICAL crisis is detected with high truth_score.

Usage:
    from market_agent.learning.macro_circuit_breaker import get_macro_breaker
    breaker = get_macro_breaker()
    state   = breaker.get_state()
    # state.name: "CLEAR" | "WATCH" | "HALT"
    # state.confidence_floor: 0.50 | 0.65 | 1.0 (1.0 = effectively blocked)

    # Filter a consensus signal for a symbol:
    direction, reason = breaker.route_signal("BUY", "TATASTEEL.NS")
    # direction = "HOLD" if blocked, "BUY"/"SELL" if allowed
"""

import datetime
import threading
from dataclasses import dataclass, field
from typing import Optional, Tuple

import structlog

log = structlog.get_logger("macro_circuit_breaker")

# ─────────────────────────────────────────────────────────────────
# VIX thresholds (STRICT mode per user: India VIX > 25 = HALT)
# ─────────────────────────────────────────────────────────────────
VIX_INDIA_HALT  = 25.0   # India VIX above this → HALT
VIX_INDIA_WATCH = 20.0   # India VIX above this → WATCH
VIX_CBOE_HALT   = 30.0   # CBOE VIX above this → HALT
VIX_CBOE_WATCH  = 20.0   # CBOE VIX above this → WATCH

# VIX data cache TTL in seconds (5 minutes = one scan cycle)
VIX_CACHE_SECONDS = 300


# ─────────────────────────────────────────────────────────────────
# MacroState dataclass
# ─────────────────────────────────────────────────────────────────

@dataclass
class MacroState:
    name:             str   = "CLEAR"   # CLEAR | WATCH | HALT
    confidence_floor: float = 0.50
    vix_india:        float = 0.0
    vix_cboe:         float = 0.0
    crisis_type:      str   = "NONE"
    crisis_severity:  str   = "NONE"
    crisis_truth:     float = 0.0
    sector_map:       dict  = field(default_factory=dict)
    reason:           str   = ""
    updated_at:       Optional[datetime.datetime] = None

    def to_dict(self) -> dict:
        return {
            "state":            self.name,
            "confidence_floor": self.confidence_floor,
            "vix_india":        self.vix_india,
            "vix_cboe":         self.vix_cboe,
            "crisis_type":      self.crisis_type,
            "crisis_severity":  self.crisis_severity,
            "crisis_truth":     self.crisis_truth,
            "reason":           self.reason,
        }


# ─────────────────────────────────────────────────────────────────
# MacroCircuitBreaker
# ─────────────────────────────────────────────────────────────────

class MacroCircuitBreaker:
    """
    Singleton breaker — instantiate ONCE per scanner run, reuse across symbols.
    VIX is fetched once and cached for VIX_CACHE_SECONDS.
    CrisisClassifier is called once per scan cycle (it caches internally).
    """

    def __init__(self):
        self._state: Optional[MacroState] = None
        self._state_time: Optional[datetime.datetime] = None
        self._lock = threading.Lock()

    def get_state(self) -> MacroState:
        """
        Returns current MacroState. Refreshes VIX + crisis classification
        at most once per VIX_CACHE_SECONDS.
        """
        with self._lock:
            now = datetime.datetime.utcnow()
            if (self._state is not None and self._state_time is not None and
                    (now - self._state_time).total_seconds() < VIX_CACHE_SECONDS):
                return self._state

            state = self._compute_state()
            self._state = state
            self._state_time = now
            log.info("macro_state_computed",
                     state=state.name,
                     vix_india=state.vix_india,
                     vix_cboe=state.vix_cboe,
                     crisis=state.crisis_type,
                     severity=state.crisis_severity)
            return state

    def route_signal(
        self, direction: str, symbol: str, confidence: float = 0.5
    ) -> Tuple[str, str]:
        """
        Apply macro filter to a consensus signal.

        Returns (filtered_direction, reason):
          filtered_direction = original direction if allowed, "HOLD" if blocked
          reason             = human-readable explanation

        Logic:
          CLEAR  → pass all signals (floor check only)
          WATCH  → pass signals with confidence >= 0.65; else HOLD
          HALT   + crisis-aligned signal  → PASS (crisis opportunity)
          HALT   + signal fights crisis   → HOLD (blocked)
          HALT   + neutral sector symbol  → HOLD (general caution)
        """
        state = self.get_state()

        if state.name == "CLEAR":
            return direction, "CLEAR: normal operation"

        if state.name == "WATCH":
            if confidence >= 0.65:
                return direction, f"WATCH: confidence {confidence:.2f} passes floor 0.65"
            return "HOLD", f"WATCH: confidence {confidence:.2f} < floor 0.65"

        # HALT state — crisis-aware routing
        if state.crisis_type == "NONE" or not state.sector_map:
            # HALT with no classified crisis (pure VIX spike) → block everything
            return "HOLD", f"HALT: VIX={state.vix_india:.1f} spike, no crisis classified yet"

        from market_agent.learning.crisis_classifier import SYMBOL_SECTOR
        sectors = SYMBOL_SECTOR.get(symbol, [])
        crisis_sector_map = state.sector_map

        # Determine expected direction for this symbol in the current crisis
        expected_directions = []
        for sector in sectors:
            d = crisis_sector_map.get(sector)
            if d:
                expected_directions.append(d)

        # Fallback for NSE equities: use GENERAL_EQUITY direction
        if not expected_directions and (symbol.endswith(".NS") or symbol == "^NSEBANK"):
            ge = crisis_sector_map.get("GENERAL_EQUITY")
            if ge:
                expected_directions.append(ge)

        if not expected_directions:
            return "HOLD", (
                f"HALT ({state.crisis_type}): sector impact unknown for {symbol}, "
                f"blocking as precaution"
            )

        # Determine dominant expected direction
        expected = "DOWN" if "DOWN" in expected_directions else "UP"

        # Map expected to trade direction
        expected_trade = "SELL" if expected == "DOWN" else "BUY"

        if direction == expected_trade:
            return direction, (
                f"HALT ({state.crisis_type}): {symbol} is CRISIS-ALIGNED "
                f"({expected}→{direction}) — OPPORTUNITY PASS"
            )
        else:
            return "HOLD", (
                f"HALT ({state.crisis_type}): {symbol} direction {direction} "
                f"fights crisis (expected {expected_trade}) — BLOCKED"
            )

    def _compute_state(self) -> MacroState:
        """Fetch VIX + run CrisisClassifier, derive MacroState."""
        vix_india, vix_cboe = self._fetch_vix()

        # ── Determine VIX-based baseline state ────────────────────
        if vix_india > VIX_INDIA_HALT or vix_cboe > VIX_CBOE_HALT:
            vix_state = "HALT"
            reason = (f"VIX HALT: India={vix_india:.1f} "
                      f"(>{VIX_INDIA_HALT})" if vix_india > VIX_INDIA_HALT
                      else f"VIX HALT: CBOE={vix_cboe:.1f} (>{VIX_CBOE_HALT})")
        elif vix_india > VIX_INDIA_WATCH or vix_cboe > VIX_CBOE_WATCH:
            vix_state = "WATCH"
            reason = f"VIX WATCH: India={vix_india:.1f}, CBOE={vix_cboe:.1f}"
        else:
            vix_state = "CLEAR"
            reason = f"VIX CLEAR: India={vix_india:.1f}, CBOE={vix_cboe:.1f}"

        # ── Run CrisisClassifier for news-based escalation ────────
        crisis_type  = "NONE"
        crisis_sev   = "NONE"
        crisis_truth = 0.0
        sector_map   = {}

        try:
            from market_agent.learning.crisis_classifier import get_crisis_classifier
            from market_agent.watchers.news_watcher import get_news_cache
            from market_agent.research.forex_factory import forex_calendar
            from market_agent.research.social_scraper import social_scraper

            classifier = get_crisis_classifier()

            # Gather all sources for CrisisClassifier
            all_sources: dict = {"vix_level": vix_india}

            # RSS headlines from NewsCache
            nc = get_news_cache()
            rss_headlines = []
            if nc:
                for sym_data in nc.get_all_symbols().values():
                    for hl in sym_data.get("headlines", []):
                        rss_headlines.append({
                            "title": hl,
                            "source": "rss",
                        })
            all_sources["rss_headlines"] = rss_headlines

            # Finnhub general headlines (fetched by NewsWatcher, reuse cache)
            try:
                from market_agent.watchers.news_watcher import _fetch_finnhub_general
                all_sources["finnhub_headlines"] = _fetch_finnhub_general()
            except Exception:
                all_sources["finnhub_headlines"] = []

            # ForexFactory macro events
            try:
                ff_events = forex_calendar.fetch_economic_events()
                all_sources["forex_factory_events"] = [
                    {"title": e.get("title", ""), "source": "forexfactory",
                     "impact_score": e.get("impact_score", 0.5)}
                    for e in (ff_events or [])
                ]
            except Exception:
                all_sources["forex_factory_events"] = []

            # Social signals (low authority, tie-breaker only)
            try:
                st = social_scraper.get_stocktwits_sentiment("BTC-USD")
                all_sources["social_signals"] = [
                    {"title": f"StockTwits mood: {st.get('mood','NEUTRAL')}",
                     "source": "stocktwits"}
                ]
            except Exception:
                all_sources["social_signals"] = []

            event = classifier.classify(all_sources)
            crisis_type  = event.crisis_type
            crisis_sev   = event.severity
            crisis_truth = event.truth_score
            sector_map   = event.sector_map

            # ── News-based escalation (independent of VIX) ────────
            # Gemini sees CRITICAL + truth_score high → escalate baseline
            if event.is_actionable():
                if crisis_sev == "CRITICAL" and crisis_truth >= 0.70:
                    if vix_state == "CLEAR":
                        vix_state = "WATCH"
                        reason += f" | NEWS ESCALATION: {crisis_type} CRITICAL"
                    elif vix_state == "WATCH":
                        # CRITICAL news + VIX already elevated → HALT
                        if vix_india > VIX_INDIA_WATCH or vix_cboe > VIX_CBOE_WATCH:
                            vix_state = "HALT"
                            reason += f" | NEWS+VIX ESCALATION: {crisis_type} CRITICAL"
                elif crisis_sev == "HIGH" and crisis_truth >= 0.70:
                    if vix_state == "CLEAR":
                        vix_state = "WATCH"
                        reason += f" | NEWS ESCALATION: {crisis_type} HIGH"

        except Exception as exc:
            log.warning("crisis_classifier_failed", error=str(exc)[:120])

        conf_floor = {"CLEAR": 0.50, "WATCH": 0.65, "HALT": 1.0}.get(vix_state, 0.50)

        return MacroState(
            name             = vix_state,
            confidence_floor = conf_floor,
            vix_india        = vix_india,
            vix_cboe         = vix_cboe,
            crisis_type      = crisis_type,
            crisis_severity  = crisis_sev,
            crisis_truth     = crisis_truth,
            sector_map       = sector_map,
            reason           = reason,
            updated_at       = datetime.datetime.utcnow(),
        )

    def _fetch_vix(self) -> Tuple[float, float]:
        """
        Fetch India VIX (^INDIAVIX) and CBOE VIX (^VIX) via yfinance.
        Returns (india_vix, cboe_vix). Returns (0.0, 0.0) on failure.
        """
        try:
            import yfinance as yf
            import pandas as pd
            data = yf.download(
                ["^INDIAVIX", "^VIX"],
                period="1d", progress=False, auto_adjust=True
            )
            # Handle multi-index columns
            if isinstance(data.columns, pd.MultiIndex):
                close = data["Close"]
            else:
                close = data[["Close"]] if "Close" in data.columns else data

            india_vix = 0.0
            cboe_vix  = 0.0

            if isinstance(close, pd.DataFrame):
                if "^INDIAVIX" in close.columns:
                    v = close["^INDIAVIX"].dropna()
                    if not v.empty:
                        india_vix = float(v.iloc[-1])
                if "^VIX" in close.columns:
                    v = close["^VIX"].dropna()
                    if not v.empty:
                        cboe_vix = float(v.iloc[-1])
            else:
                # Single-ticker fallback
                v = close.dropna()
                if not v.empty:
                    india_vix = float(v.iloc[-1])

            log.debug("vix_fetched", india=india_vix, cboe=cboe_vix)
            return india_vix, cboe_vix

        except Exception as exc:
            log.warning("vix_fetch_failed", error=str(exc)[:80])
            return 0.0, 0.0


# ─────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────
_breaker: Optional[MacroCircuitBreaker] = None


def get_macro_breaker() -> MacroCircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = MacroCircuitBreaker()
    return _breaker
