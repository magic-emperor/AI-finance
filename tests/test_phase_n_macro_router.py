"""
Phase N Tests — Crisis-to-Opportunity Macro Router
===================================================
Tests for CrisisClassifier and MacroCircuitBreaker.

Run with:
  cd "d:/AI Agent Finance"
  pytest tests/test_phase_n_macro_router.py -v
"""
import datetime
import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────────
# CrisisClassifier Tests
# ─────────────────────────────────────────────────────────────────

class TestCrisisClassifier:

    def _make_classifier(self):
        from market_agent.learning.crisis_classifier import CrisisClassifier
        return CrisisClassifier()

    def _iran_war_sources(self):
        """Realistic multi-source Iran war headlines."""
        return {
            "rss_headlines": [
                {"title": "Iran closes Strait of Hormuz to oil tankers", "source": "Reuters"},
                {"title": "US imposes fresh sanctions on Iran after missile strike", "source": "Reuters"},
                {"title": "Crude oil surges 8% as Iran conflict escalates", "source": "ET Markets"},
            ],
            "finnhub_headlines": [
                {"title": "Iran military action disrupts Gulf shipping lanes", "source": "Bloomberg"},
                {"title": "Gold hits record high amid Iran geopolitical crisis", "source": "Reuters"},
            ],
            "forex_factory_events": [
                {"title": "Emergency OPEC meeting called over Iran supply shock", "source": "forexfactory", "impact_score": 0.95},
            ],
            "social_signals": [
                {"title": "StockTwits mood: BEARISH", "source": "stocktwits"},
            ],
            "vix_level": 26.0,
        }

    def _false_positive_sources(self):
        """'war on inflation' should NOT trigger WAR_GEOPOLITICAL."""
        return {
            "rss_headlines": [
                {"title": "Fed declares war on inflation with rate hike", "source": "CNBC"},
                {"title": "Central banks wage war against rising prices", "source": "Reuters"},
            ],
            "finnhub_headlines": [],
            "forex_factory_events": [],
            "social_signals": [],
            "vix_level": 15.0,
        }

    def _empty_sources(self):
        return {
            "rss_headlines": [],
            "finnhub_headlines": [],
            "forex_factory_events": [],
            "social_signals": [],
            "vix_level": 12.0,
        }

    def test_empty_headlines_returns_none_crisis(self):
        """Empty news with low VIX → NONE crisis."""
        clf = self._make_classifier()
        event = clf._run_pipeline(self._empty_sources())
        assert event.crisis_type == "NONE"
        assert event.severity == "NONE"
        assert event.truth_score == 0.0

    def test_false_positive_inflation_war_not_geopolitical(self):
        """'war on inflation' headlines must NOT classify as WAR_GEOPOLITICAL."""
        clf = self._make_classifier()
        # Mock Gemini to return NONE (correct response for inflation war)
        with patch.object(clf, '_call_gemini', return_value={
            "crisis_type": "NONE", "severity": "NONE",
            "affected_sectors": {}, "gemini_confidence": 0.85,
            "reasoning": "Headlines discuss monetary policy, not military conflict.",
            "is_likely_real": False,
        }):
            event = clf._run_pipeline(self._false_positive_sources())
        assert event.crisis_type == "NONE", (
            f"'war on inflation' falsely classified as {event.crisis_type}"
        )

    def test_iran_war_classified_correctly(self):
        """Multi-source Iran war headlines → WAR_GEOPOLITICAL with high truth_score."""
        clf = self._make_classifier()
        with patch.object(clf, '_call_gemini', return_value={
            "crisis_type": "WAR_GEOPOLITICAL",
            "severity": "CRITICAL",
            "affected_sectors": {
                "GOLD": "UP", "CRUDE_OIL": "UP",
                "AVIATION": "DOWN", "GENERAL_EQUITY": "DOWN",
            },
            "gemini_confidence": 0.92,
            "reasoning": "Multiple authority sources confirm Iran military conflict affecting oil supply.",
            "is_likely_real": True,
        }):
            event = clf._run_pipeline(self._iran_war_sources())

        assert event.crisis_type == "WAR_GEOPOLITICAL"
        assert event.severity == "CRITICAL"
        assert event.truth_score >= 0.70, f"truth_score={event.truth_score} too low"
        assert event.sector_map.get("GOLD") == "UP"
        assert event.sector_map.get("AVIATION") == "DOWN"
        assert event.is_actionable()

    def test_single_source_low_truth_score(self):
        """Single rumour source → low truth_score, severity downgraded."""
        clf = self._make_classifier()
        sources = {
            "rss_headlines": [
                {"title": "Rumour: war starting somewhere", "source": "unknown blog"},
            ],
            "finnhub_headlines": [],
            "forex_factory_events": [],
            "social_signals": [],
            "vix_level": 18.0,
        }
        with patch.object(clf, '_call_gemini', return_value={
            "crisis_type": "WAR_GEOPOLITICAL",
            "severity": "CRITICAL",
            "affected_sectors": {"GOLD": "UP"},
            "gemini_confidence": 0.40,
            "reasoning": "Uncertain — only one low-authority source.",
            "is_likely_real": False,
        }):
            event = clf._run_pipeline(sources)

        # truth_score should be low due to unknown authority + single source
        assert event.truth_score < 0.70
        # Severity should be downgraded from CRITICAL to MEDIUM or lower
        assert event.severity not in ("CRITICAL", "HIGH"), (
            f"Severity not downgraded: {event.severity}"
        )

    def test_symbol_direction_gold_in_war(self):
        """GC=F (gold) should be UP in WAR_GEOPOLITICAL."""
        from market_agent.learning.crisis_classifier import CrisisEvent
        event = CrisisEvent(
            crisis_type="WAR_GEOPOLITICAL",
            severity="CRITICAL",
            sector_map={"GOLD": "UP", "CRUDE_OIL": "UP", "AVIATION": "DOWN",
                        "GENERAL_EQUITY": "DOWN"},
        )
        assert event.get_symbol_direction("GC=F") == "UP"

    def test_symbol_direction_steel_in_war(self):
        """TATASTEEL.NS (steel/manufacturing) → DOWN in WAR_GEOPOLITICAL."""
        from market_agent.learning.crisis_classifier import CrisisEvent
        event = CrisisEvent(
            crisis_type="WAR_GEOPOLITICAL",
            severity="HIGH",
            sector_map={"GOLD": "UP", "STEEL": "DOWN", "GENERAL_EQUITY": "DOWN"},
        )
        assert event.get_symbol_direction("TATASTEEL.NS") == "DOWN"

    def test_symbol_direction_nse_equity_general_fallback(self):
        """NSE equities not in sector map fall back to GENERAL_EQUITY direction."""
        from market_agent.learning.crisis_classifier import CrisisEvent
        event = CrisisEvent(
            crisis_type="WAR_GEOPOLITICAL",
            severity="HIGH",
            sector_map={"GENERAL_EQUITY": "DOWN"},
        )
        # LT.NS → INFRASTRUCTURE — not in sector_map explicitly, should use GENERAL_EQUITY
        direction = event.get_symbol_direction("LT.NS")
        assert direction == "DOWN"

    def test_cache_returns_same_object_within_ttl(self):
        """classify() returns cached result within 10-minute TTL."""
        clf = self._make_classifier()
        sources = self._empty_sources()
        with patch.object(clf, '_run_pipeline') as mock_run:
            mock_run.return_value = MagicMock(crisis_type="NONE")
            _ = clf.classify(sources)
            _ = clf.classify(sources)
            # _run_pipeline called only once — second call uses cache
            assert mock_run.call_count == 1


# ─────────────────────────────────────────────────────────────────
# MacroCircuitBreaker Tests
# ─────────────────────────────────────────────────────────────────

class TestMacroCircuitBreaker:

    def _make_breaker(self):
        from market_agent.learning.macro_circuit_breaker import MacroCircuitBreaker
        return MacroCircuitBreaker()

    def _mock_vix(self, india: float, cboe: float):
        return patch(
            'market_agent.learning.macro_circuit_breaker.MacroCircuitBreaker._fetch_vix',
            return_value=(india, cboe)
        )

    def _mock_classifier_none(self):
        from market_agent.learning.crisis_classifier import CrisisEvent
        mock_event = CrisisEvent(crisis_type="NONE", severity="NONE",
                                  truth_score=0.0)
        return patch(
            'market_agent.learning.macro_circuit_breaker.MacroCircuitBreaker._compute_state',
            wraps=None
        )

    def _compute_state_with_vix(self, breaker, india_vix, cboe_vix):
        """Helper: compute state with mocked VIX and no-op crisis classifier."""
        from market_agent.learning.macro_circuit_breaker import MacroState
        # Patch _fetch_vix and the crisis classifier imports inside _compute_state
        with self._mock_vix(india_vix, cboe_vix):
            with patch('market_agent.learning.crisis_classifier.get_crisis_classifier') as mock_clf:
                mock_clf.return_value.classify.return_value = MagicMock(
                    crisis_type="NONE", severity="NONE", truth_score=0.0,
                    sector_map={}, is_actionable=lambda: False
                )
                # Patch the local imports inside _compute_state by patching builtins.__import__
                # Simpler: override the entire crisis section by patching _compute_state's
                # crisis block via the classifier singleton
                import market_agent.learning.crisis_classifier as _cc_mod
                original_getter = _cc_mod.get_crisis_classifier
                _cc_mod.get_crisis_classifier = mock_clf
                try:
                    with patch('market_agent.watchers.news_watcher.get_news_cache', return_value=None):
                        with patch('market_agent.research.forex_factory.forex_calendar'):
                            with patch('market_agent.research.social_scraper.social_scraper'):
                                return breaker._compute_state()
                finally:
                    _cc_mod.get_crisis_classifier = original_getter

    def test_india_vix_above_25_is_halt(self):
        """India VIX 25.52 (current Iran war level) → HALT state."""
        breaker = self._make_breaker()
        state = self._compute_state_with_vix(breaker, 25.52, 23.0)
        assert state.name == "HALT", f"Expected HALT, got {state.name}"
        assert state.confidence_floor == 1.0

    def test_india_vix_below_20_is_clear(self):
        """India VIX 15.0 → CLEAR state."""
        breaker = self._make_breaker()
        state = self._compute_state_with_vix(breaker, 15.0, 14.0)
        assert state.name == "CLEAR"
        assert state.confidence_floor == 0.50

    def test_india_vix_22_is_watch(self):
        """India VIX 22 → WATCH state."""
        breaker = self._make_breaker()
        state = self._compute_state_with_vix(breaker, 22.0, 18.0)
        assert state.name == "WATCH"
        assert state.confidence_floor == 0.65

    def test_halt_blocks_buy_on_general_equity(self):
        """HALT + WAR: BUY on HDFCBANK.NS (banking = DOWN sector) → HOLD."""
        from market_agent.learning.macro_circuit_breaker import MacroState
        breaker = self._make_breaker()
        # Inject HALT state with war sector map
        halt_state = MacroState(
            name="HALT", confidence_floor=1.0,
            vix_india=26.0, vix_cboe=24.0,
            crisis_type="WAR_GEOPOLITICAL",
            crisis_severity="CRITICAL", crisis_truth=0.85,
            sector_map={"GOLD": "UP", "CRUDE_OIL": "UP",
                        "BANKING": "DOWN", "GENERAL_EQUITY": "DOWN",
                        "AVIATION": "DOWN"},
            reason="VIX HALT",
        )
        breaker._state = halt_state
        breaker._state_time = datetime.datetime.utcnow()

        direction, reason = breaker.route_signal("BUY", "HDFCBANK.NS", confidence=0.75)
        assert direction == "HOLD", f"Expected HOLD, got {direction}"
        assert "BLOCKED" in reason

    def test_halt_passes_sell_on_crash_sector(self):
        """HALT + WAR: SELL on TATASTEEL.NS (steel=DOWN) → SELL (crisis opportunity)."""
        from market_agent.learning.macro_circuit_breaker import MacroState
        breaker = self._make_breaker()
        halt_state = MacroState(
            name="HALT", confidence_floor=1.0,
            vix_india=26.0, vix_cboe=24.0,
            crisis_type="WAR_GEOPOLITICAL",
            crisis_severity="CRITICAL", crisis_truth=0.85,
            sector_map={"GOLD": "UP", "STEEL": "DOWN", "GENERAL_EQUITY": "DOWN"},
            reason="VIX HALT",
        )
        breaker._state = halt_state
        breaker._state_time = datetime.datetime.utcnow()

        direction, reason = breaker.route_signal("SELL", "TATASTEEL.NS", confidence=0.70)
        assert direction == "SELL", f"Expected SELL (crisis-aligned), got {direction}"
        assert "OPPORTUNITY" in reason

    def test_halt_passes_buy_on_gold(self):
        """HALT + WAR: BUY on GC=F (gold=UP) → BUY (safe-haven opportunity)."""
        from market_agent.learning.macro_circuit_breaker import MacroState
        breaker = self._make_breaker()
        halt_state = MacroState(
            name="HALT", confidence_floor=1.0,
            vix_india=26.0, vix_cboe=24.0,
            crisis_type="WAR_GEOPOLITICAL",
            crisis_severity="CRITICAL", crisis_truth=0.85,
            sector_map={"GOLD": "UP", "CRUDE_OIL": "UP", "GENERAL_EQUITY": "DOWN"},
            reason="VIX HALT",
        )
        breaker._state = halt_state
        breaker._state_time = datetime.datetime.utcnow()

        direction, reason = breaker.route_signal("BUY", "GC=F", confidence=0.72)
        assert direction == "BUY", f"Expected BUY (gold safe haven), got {direction}"
        assert "OPPORTUNITY" in reason

    def test_watch_blocks_low_confidence(self):
        """WATCH state: confidence 0.55 < floor 0.65 → HOLD."""
        from market_agent.learning.macro_circuit_breaker import MacroState
        breaker = self._make_breaker()
        watch_state = MacroState(
            name="WATCH", confidence_floor=0.65,
            vix_india=22.0, vix_cboe=19.0,
            crisis_type="NONE", crisis_severity="NONE", crisis_truth=0.0,
            sector_map={}, reason="VIX WATCH",
        )
        breaker._state = watch_state
        breaker._state_time = datetime.datetime.utcnow()

        direction, reason = breaker.route_signal("BUY", "RELIANCE.NS", confidence=0.55)
        assert direction == "HOLD"

    def test_watch_passes_high_confidence(self):
        """WATCH state: confidence 0.72 >= floor 0.65 → passes through."""
        from market_agent.learning.macro_circuit_breaker import MacroState
        breaker = self._make_breaker()
        watch_state = MacroState(
            name="WATCH", confidence_floor=0.65,
            vix_india=22.0, vix_cboe=19.0,
            crisis_type="NONE", crisis_severity="NONE", crisis_truth=0.0,
            sector_map={}, reason="VIX WATCH",
        )
        breaker._state = watch_state
        breaker._state_time = datetime.datetime.utcnow()

        direction, reason = breaker.route_signal("BUY", "RELIANCE.NS", confidence=0.72)
        assert direction == "BUY"

    def test_clear_passes_all(self):
        """CLEAR state: all signals pass regardless of confidence."""
        from market_agent.learning.macro_circuit_breaker import MacroState
        breaker = self._make_breaker()
        clear_state = MacroState(
            name="CLEAR", confidence_floor=0.50,
            vix_india=14.0, vix_cboe=13.0,
            crisis_type="NONE", crisis_severity="NONE", crisis_truth=0.0,
            sector_map={}, reason="VIX CLEAR",
        )
        breaker._state = clear_state
        breaker._state_time = datetime.datetime.utcnow()

        for symbol in ["RELIANCE.NS", "BTC-USD", "GC=F", "HDFCBANK.NS"]:
            direction, _ = breaker.route_signal("BUY", symbol, confidence=0.51)
            assert direction == "BUY", f"CLEAR should pass BUY for {symbol}"


# ─────────────────────────────────────────────────────────────────
# RL-Weighter macro_context Tests
# ─────────────────────────────────────────────────────────────────

class TestRLWeighterMacroContext:

    def test_halt_reduces_risk_multiplier_to_25pct(self):
        """In HALT macro state, risk_multiplier should be ≤ 0.375 (original * 0.25)."""
        from market_agent.brain.rl_weighter import rl_weighter_signal
        perf = [{"outcome": "TARGET", "regime": "RANGING"}] * 10 + \
               [{"outcome": "SL", "regime": "RANGING"}] * 5
        sig = rl_weighter_signal(
            base_signal={"direction": "BUY", "confidence": 0.7},
            performance_history=perf,
            current_regime="RANGING",
            macro_context={"state": "HALT", "crisis_type": "WAR_GEOPOLITICAL"},
        )
        rm = sig.measurements["risk_multiplier"]
        # Without macro: half-kelly ~0.5-1.0 range; with HALT *0.25 → max ~0.375
        assert rm <= 0.40, f"HALT risk_multiplier={rm} too high (expected ≤ 0.40)"
        assert "MACRO HALT" in sig.primary_evidence

    def test_watch_reduces_risk_multiplier_to_75pct(self):
        """In WATCH state, risk_multiplier scaled to 0.75x."""
        from market_agent.brain.rl_weighter import rl_weighter_signal
        perf = [{"outcome": "TARGET", "regime": "RANGING"}] * 10
        # Get baseline (no macro)
        base_sig = rl_weighter_signal(
            base_signal={"direction": "BUY", "confidence": 0.7},
            performance_history=perf,
            current_regime="RANGING",
        )
        base_rm = base_sig.measurements["risk_multiplier"]

        watch_sig = rl_weighter_signal(
            base_signal={"direction": "BUY", "confidence": 0.7},
            performance_history=perf,
            current_regime="RANGING",
            macro_context={"state": "WATCH"},
        )
        watch_rm = watch_sig.measurements["risk_multiplier"]
        assert abs(watch_rm - base_rm * 0.75) < 0.01, \
            f"WATCH: expected {base_rm*0.75:.3f}, got {watch_rm:.3f}"

    def test_clear_no_adjustment(self):
        """CLEAR macro state → no change to risk_multiplier."""
        from market_agent.brain.rl_weighter import rl_weighter_signal
        perf = [{"outcome": "TARGET", "regime": "RANGING"}] * 8
        base_sig = rl_weighter_signal(
            base_signal={"direction": "BUY", "confidence": 0.7},
            performance_history=perf,
            current_regime="RANGING",
        )
        clear_sig = rl_weighter_signal(
            base_signal={"direction": "BUY", "confidence": 0.7},
            performance_history=perf,
            current_regime="RANGING",
            macro_context={"state": "CLEAR"},
        )
        assert base_sig.measurements["risk_multiplier"] == \
               clear_sig.measurements["risk_multiplier"]

    def test_none_macro_context_backward_compatible(self):
        """macro_context=None (default) → same result as before this change."""
        from market_agent.brain.rl_weighter import rl_weighter_signal
        perf = [{"outcome": "TARGET", "regime": "RANGING"}] * 6
        sig = rl_weighter_signal(
            base_signal={"direction": "BUY", "confidence": 0.7},
            performance_history=perf,
            current_regime="RANGING",
        )
        assert sig.measurements["risk_multiplier"] > 0
        assert "MACRO" not in sig.primary_evidence
