"""
Multi-AI Client for Market Analysis

Integrates multiple AI providers with:
- Gemini (3 keys, round-robin) — Primary brain
- Groq (Llama 3.3 70B) — Fast backup
- Mistral (Mistral Small) — Deep analysis fallback
- Independent rate limiting per provider
- Response caching (2-minute TTL)
- RAG context injection (brain history + fundamentals)
- Source attribution tracking
"""

import os
import time
import json
import requests
import structlog
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from collections import deque


class _SafeLogger:
    """Swallow Windows console OSErrors (Errno 22) from structlog."""

    def __init__(self):
        try:
            self._base = structlog.get_logger()
        except Exception:
            self._base = None

    def _wrap_call(self, fn):
        def _wrapped(*args, **kwargs):
            try:
                if fn:
                    return fn(*args, **kwargs)
            except (OSError, UnicodeEncodeError, Exception):
                return None
        return _wrapped

    def __getattr__(self, name):
        fn = getattr(self._base, name, None) if self._base else None
        return self._wrap_call(fn)


logger = _SafeLogger()


# ═══════════════════════════════════════
# PROVIDER BACKENDS
# ═══════════════════════════════════════

class _RateLimiter:
    """Per-provider rate limiter with sliding window."""

    def __init__(self, max_rpm: int):
        self.max_rpm = max_rpm
        self.timestamps = deque(maxlen=max_rpm)

    def can_call(self) -> bool:
        self._prune()
        return len(self.timestamps) < self.max_rpm

    def record_call(self):
        self.timestamps.append(time.time())

    def wait_time(self) -> float:
        if self.can_call():
            return 0.0
        return max(0.0, 60.0 - (time.time() - self.timestamps[0]))

    def _prune(self):
        now = time.time()
        while self.timestamps and (now - self.timestamps[0]) > 60:
            self.timestamps.popleft()

    @property
    def calls_in_window(self) -> int:
        self._prune()
        return len(self.timestamps)


class _GeminiBackend:
    """Google Gemini with multi-key rotation (Geminiiflow: capped at MAX_ACTIVE_KEYS)."""

    # ── Geminiiflow key right-sizing ──
    # Keys 1-10 are active.
    MAX_ACTIVE_KEYS = 5

    def __init__(self):
        self.keys = []
        self.models = []
        self.current_key_idx = 0
        self.name = "Gemini"

        # Collect all API keys (support up to 10 keys: GOOGLE_API_KEY, _2 ... _10)
        env_vars = ["GOOGLE_API_KEY"] + [f"GOOGLE_API_KEY_{i}" for i in range(2, 11)]
        all_keys = []
        for env_var in env_vars:
            key = os.getenv(env_var, "").strip()
            if key and len(key) > 10:  # Basic validation
                all_keys.append(key)

        # Cap at MAX_ACTIVE_KEYS — keys beyond this are cold backup (not loaded)
        self.keys = all_keys[:self.MAX_ACTIVE_KEYS]
        if len(all_keys) > self.MAX_ACTIVE_KEYS:
            logger.info("gemini_keys_capped",
                        found=len(all_keys),
                        active=self.MAX_ACTIVE_KEYS,
                        cold_backup=len(all_keys) - self.MAX_ACTIVE_KEYS)

        # Initialize availability
        if self.keys:
            self.models = self.keys  # Just using length for availability check
            logger.info("gemini_multi_key_ready", key_count=len(self.keys), mode="REST_API")

        # Per-key rate limiters (14 RPM each, buffer of 1 below 15)
        self.limiters = [_RateLimiter(14) for _ in self.keys]
        self.total_calls = 0

        # ── Key Tier Reservation (Geminiiflow: always 1 with 3-key cap) ──
        # key[0..n-2] = open pool (news scoring, individual brains)
        # key[n-1]    = Boss Brain / EXECUTIVE reserved
        # With 1 key: no reservation (keeps system alive at minimum config)
        n = len(self.keys)
        self._council_reserved = min(1, max(0, n - 1))
        logger.info("gemini_key_tiers_set",
                    total=n, open_pool=n - self._council_reserved,
                    council_reserved=self._council_reserved)

    @property
    def is_available(self) -> bool:
        return len(self.models) > 0

    def call(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        """
        Standard call — uses the OPEN POOL (all keys minus council-reserved ones).
        Low-priority callers (analyst, news sentiment, fundamentals) use this.
        Council-reserved keys are NOT touched here, protecting Boss Brain quota.
        """
        if not self.models:
            return None

        # Council-reserved keys are off-limits for standard calls
        open_pool_size = max(1, len(self.keys) - self._council_reserved)
        
        for attempt in range(open_pool_size):
            idx = (self.current_key_idx + attempt) % open_pool_size
            limiter = self.limiters[idx]

            if not limiter.can_call():
                continue

            try:
                key = self.keys[idx]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
                
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": 0.7,
                    }
                }
                
                resp = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=45)
                
                if resp.status_code == 200:
                    data = resp.json()
                    text_response = data['candidates'][0]['content']['parts'][0]['text']
                    
                    limiter.record_call()
                    self.total_calls += 1
                    self.current_key_idx = (idx + 1) % open_pool_size
                    return text_response.strip()
                else:
                    raise Exception(f"HTTP {resp.status_code}: {resp.text}")

            except Exception as e:
                err_str = str(e).lower()
                is_quota = "429" in err_str or "quota" in err_str or "resource exhausted" in err_str or "permission" in err_str or "403" in err_str
                logger.warning("gemini_key_failed", key_idx=idx, error=str(e)[:80], quota_or_limit=is_quota)
                continue

        logger.warning(
            "all_gemini_keys_exhausted",
            key_count=len(self.keys),
            message="All Gemini keys exhausted; check quota/billing in Google Cloud.",
        )
        return None

    def call_for_council(self, prompt: str, max_tokens: int = 800) -> Optional[str]:
        """
        Council/Boss Brain call — tries RESERVED keys first, then falls back to
        the full key pool if reserved ones are also exhausted.

        This preserves at least (council_reserved) Gemini keys at full RPM for
        the most important synthesis task: the council verdict.
        Strategy:
          1. Try reserved keys (e.g. key[6], key[7]) — these are never touched by standard calls
          2. If all reserved keys are at RPM limit, try the open pool (best effort)
          3. Return None only if truly all keys fail (caller falls back to Groq)
        """
        if not self.models:
            return None

        total_keys = len(self.keys)
        reserved_start = max(0, total_keys - self._council_reserved)
        
        # Priority order: reserved indices first, then open pool
        priority_order = list(range(reserved_start, total_keys)) + list(range(0, reserved_start))

        for idx in priority_order:
            limiter = self.limiters[idx]
            if not limiter.can_call():
                continue
            try:
                key = self.keys[idx]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
                
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": 0.4,   # Lower temp for more decisive council verdicts
                    }
                }
                
                resp = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=60)
                
                if resp.status_code == 200:
                    data = resp.json()
                    text_response = data['candidates'][0]['content']['parts'][0]['text']
                    
                    limiter.record_call()
                    self.total_calls += 1
                    tier = "reserved" if idx >= reserved_start else "open_pool_fallback"
                    logger.info("gemini_council_call_success", key_idx=idx, tier=tier)
                    return text_response.strip()
                else:
                    raise Exception(f"HTTP {resp.status_code}: {resp.text}")

            except Exception as e:
                err_str = str(e).lower()
                is_quota = "429" in err_str or "quota" in err_str or "resource exhausted" in err_str or "permission" in err_str or "403" in err_str
                logger.warning("gemini_council_key_failed", key_idx=idx, error=str(e)[:80], quota_or_limit=is_quota)
                continue

        logger.warning("all_gemini_council_keys_exhausted",
                       message="All keys exhausted for council — falling back to Groq/Mistral.")
        return None

    def get_status(self) -> Dict:
        return {
            "available": self.is_available,
            "keys": len(self.keys),
            "total_calls": self.total_calls,
            "per_key": [
                {"rpm_used": l.calls_in_window, "max_rpm": l.max_rpm}
                for l in self.limiters
            ],
        }

    def get_wait_time_sec(self) -> float:
        """Seconds until at least one key is available (0 if any can call)."""
        if not self.limiters:
            return 0.0
        waits = [l.wait_time() for l in self.limiters]
        return min(waits)


class _GroqBackend:
    """Groq REST API (Llama 3.3 70B) — no SDK needed."""

    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.3-70b-versatile"

    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY", "")
        self.limiter = _RateLimiter(28)  # Stay under 30 RPM
        self.total_calls = 0
        self.name = "Groq"

        if self.api_key:
            logger.info("groq_backend_ready", model=self.MODEL)

    @property
    def is_available(self) -> bool:
        return bool(self.api_key) and self.limiter.can_call()

    def call(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        if not self.api_key or not self.limiter.can_call():
            return None

        try:
            self.limiter.record_call()
            self.total_calls += 1

            resp = requests.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

        except Exception as e:
            logger.warning("groq_call_failed", error=str(e)[:100])
            return None

    def get_status(self) -> Dict:
        return {
            "available": bool(self.api_key),
            "rpm_used": self.limiter.calls_in_window,
            "max_rpm": self.limiter.max_rpm,
            "total_calls": self.total_calls,
        }

    def get_wait_time_sec(self) -> float:
        return self.limiter.wait_time()


class _MistralBackend:
    """Mistral REST API (free tier) — deep analysis fallback."""

    ENDPOINT = "https://api.mistral.ai/v1/chat/completions"
    MODEL = "mistral-small-latest"

    def __init__(self):
        self.api_key = os.getenv("MISTRAL_API_KEY", "")
        self.limiter = _RateLimiter(1)  # Free tier: 1 RPM
        self.total_calls = 0
        self.name = "Mistral"

        if self.api_key:
            logger.info("mistral_backend_ready", model=self.MODEL)

    @property
    def is_available(self) -> bool:
        return bool(self.api_key) and self.limiter.can_call()

    def call(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        if not self.api_key or not self.limiter.can_call():
            return None

        try:
            self.limiter.record_call()
            self.total_calls += 1

            resp = requests.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

        except Exception as e:
            logger.warning("mistral_call_failed", error=str(e)[:100])
            return None

    def get_status(self) -> Dict:
        return {
            "available": bool(self.api_key),
            "rpm_used": self.limiter.calls_in_window,
            "max_rpm": self.limiter.max_rpm,
            "total_calls": self.total_calls,
        }

    def get_wait_time_sec(self) -> float:
        return self.limiter.wait_time()


class _CohereBackend:
    """Cohere API v2 — free tier 950 calls/month (use ~31/day to stay within limit)."""

    ENDPOINT = "https://api.cohere.com/v2/chat"

    def __init__(self):
        self.api_key = os.getenv("COHERE_API_KEY", "")
        self.name = "Cohere"
        self.total_calls = 0
        self._daily_date = None
        self._daily_count = 0
        self._max_per_day = min(31, int(os.getenv("COHERE_DAILY_LIMIT", "31")))  # 950/30

        if self.api_key:
            logger.info("cohere_backend_ready", max_per_day=self._max_per_day)

    def _can_call(self) -> bool:
        from datetime import date
        today = date.today()
        if self._daily_date != today:
            self._daily_date = today
            self._daily_count = 0
        return self._daily_count < self._max_per_day

    @property
    def is_available(self) -> bool:
        return bool(self.api_key) and self._can_call()

    def call(self, prompt: str, max_tokens: int = 500) -> Optional[str]:
        if not self.api_key or not self._can_call():
            return None
        try:
            resp = requests.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "stream": False,
                    "model": "command-r-plus",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=30,
            )
            resp.raise_for_status()
            self._daily_count += 1
            self.total_calls += 1
            data = resp.json()
            msg = data.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                )
            elif isinstance(content, str):
                text = content
            else:
                text = data.get("text", "")
            return text.strip() or None
        except Exception as e:
            logger.warning("cohere_call_failed", error=str(e)[:100])
            return None

    def get_status(self) -> Dict:
        return {
            "available": bool(self.api_key),
            "daily_used": self._daily_count,
            "max_per_day": self._max_per_day,
            "total_calls": self.total_calls,
        }

    def get_wait_time_sec(self) -> float:
        return 0.0 if self._can_call() else 86400.0  # next day


# ═══════════════════════════════════════
# MAIN CLIENT — ORCHESTRATES ALL BACKENDS
# ═══════════════════════════════════════

class GeminiClient:
    """
    Multi-AI orchestrator with cascade fallback.
    
    Priority: Gemini → Groq → Mistral → Cohere → Local fallback
    Each provider has independent rate limits.
    RAG context injected into prompts for grounded responses.
    """

    CACHE_TTL_SECONDS = 120
    ANALYSIS_INTERVAL = 3  # Call AI every 3rd cycle (more generous with 3 keys)

    def __init__(self):
        self.gemini = _GeminiBackend()
        self.groq = _GroqBackend()
        self.mistral = _MistralBackend()
        self.cohere = _CohereBackend()

        self.cache: Dict[str, Dict[str, Any]] = {}
        self.call_count = 0
        self.analysis_cycle = 0
        self.provider_attribution: Dict[str, int] = {
            "Gemini": 0, "Groq": 0, "Mistral": 0, "Cohere": 0, "Local": 0
        }
        self._initialized = (
            self.gemini.is_available or self.groq.is_available
            or self.mistral.is_available or self.cohere.is_available
        )

    @property
    def is_available(self) -> bool:
        return self._initialized

    # ─── Cache ─────────────────────────────────

    def _check_cache(self, cache_key: str) -> Optional[str]:
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            if time.time() - entry["timestamp"] < self.CACHE_TTL_SECONDS:
                return entry["response"]
            else:
                del self.cache[cache_key]
        return None

    def _store_cache(self, cache_key: str, response: str):
        self.cache[cache_key] = {
            "response": response,
            "timestamp": time.time()
        }
        # Prune old entries
        now = time.time()
        expired = [k for k, v in self.cache.items()
                    if now - v["timestamp"] > self.CACHE_TTL_SECONDS * 3]
        for k in expired:
            del self.cache[k]

    # ─── Cascade Call ──────────────────────────

    def _call_ai(self, prompt: str, cache_key: str = None,
                 max_tokens: int = 500, council_mode: bool = False) -> Optional[str]:
        """
        Try AI providers in cascade: Gemini → Groq → Mistral.

        council_mode=True: uses Gemini reserved keys first (Boss Brain / EXECUTIVE stance).
        council_mode=False: uses open-pool keys (analyst, news, individual brains).
        Council calls are never cached — verdicts must be fresh every time.
        """
        # Check cache first (standard calls only — council always re-evaluates)
        if cache_key and not council_mode:
            cached = self._check_cache(cache_key)
            if cached:
                return cached

        # Gemini backend — choose tier
        gemini_result = None
        if council_mode and hasattr(self.gemini, 'call_for_council'):
            gemini_result = self.gemini.call_for_council(prompt, max_tokens)
        elif self.gemini.is_available:
            gemini_result = self.gemini.call(prompt, max_tokens)

        if gemini_result:
            self.call_count += 1
            self.provider_attribution[self.gemini.name] += 1
            if cache_key and not council_mode:
                self._store_cache(cache_key, gemini_result)
            logger.info("ai_call_success",
                        provider=self.gemini.name,
                        total=self.call_count,
                        cache_key=cache_key,
                        council_mode=council_mode)
            return gemini_result

        # Groq + Mistral fallbacks (Cohere removed from LLM path — embeddings only)
        for backend in [self.groq, self.mistral]:
            result = backend.call(prompt, max_tokens)
            if result:
                self.call_count += 1
                self.provider_attribution[backend.name] += 1
                if cache_key and not council_mode:
                    self._store_cache(cache_key, result)
                logger.info("ai_call_success",
                            provider=backend.name,
                            total=self.call_count,
                            cache_key=cache_key)
                return result

        logger.warning(
            "all_ai_providers_exhausted",
            message="All AI providers (Gemini, Groq, Mistral) exhausted or rate-limited; check quota/billing.",
        )
        self.provider_attribution["Local"] += 1

        # Step 10: Telemetry — log call budget every 10 calls
        if self.call_count > 0 and self.call_count % 10 == 0:
            logger.info("gemini_call_budget",
                        total_calls=self.call_count,
                        attribution=self.provider_attribution)

        return None

    # ─── RAG Context Builder ───────────────────

    def _build_rag_context(self, symbol: str) -> str:
        """
        Build RAG context from our database for grounded responses.
        Pulls prediction history + accuracy + recent news + FAISS memory + brain health.
        """
        context_parts = []

        try:
            from market_agent.data.storage.postgres import PostgresStorage
            from market_agent.learning.signal_resolver import SignalResolver
            storage = PostgresStorage()
            resolver = SignalResolver(storage)

            # 1. Recent prediction accuracy (from SignalResolver, not PostgresStorage)
            from market_agent.config import ACCURACY_STATS_LAST_N
            stats = resolver.get_accuracy_stats(symbol=symbol, last_n=ACCURACY_STATS_LAST_N)
            if stats and stats.get('total', 0) > 0:
                context_parts.append(
                    f"PREDICTION HISTORY: {stats['total']} predictions, "
                    f"{stats.get('accuracy', 0):.1f}% accuracy. "
                    f"Recent trend: {stats.get('trend', 'UNKNOWN')}."
                )

            # 2. Recent brain analysis
            history = storage.get_brain_history(symbol, limit=3)
            if history:
                for entry in history[:2]:
                    conclusion = entry.get('conclusion', '')
                    if conclusion:
                        context_parts.append(
                            f"PAST ANALYSIS ({entry.get('timestamp', 'recent')}): "
                            f"{str(conclusion)[:200]}"
                        )

            # 3. Fundamentals (get_latest_fundamentals returns top-level keys, not nested 'data')
            fundamentals = storage.get_latest_fundamentals(symbol)
            if fundamentals:
                key_metrics = []
                for k in ['market_cap', 'revenue', 'eps', 'promoter_holding', 'altman_z', 'piotroski_f']:
                    v = fundamentals.get(k)
                    if v is not None:
                        key_metrics.append(f"{k}={v}")
                if key_metrics:
                    context_parts.append(
                        f"FUNDAMENTALS: {', '.join(key_metrics[:6])}"
                    )

        except Exception as e:
            logger.debug("rag_context_failed", error=str(e)[:60])

        # 4. FAISS Memory Recall — similar past situations
        try:
            from market_agent.brain.council_memory import get_council_memory
            memory = get_council_memory()
            recall_text = memory.recall_for_brain_prompt(
                symbol=symbol, regime="", current_context=symbol
            )
            if recall_text:
                context_parts.append(recall_text)
        except Exception as e:
            logger.debug("faiss_recall_failed", error=str(e)[:60])

        # 5. Brain Health Status — system self-awareness
        try:
            from market_agent.brain.health_monitor import get_health_monitor
            monitor = get_health_monitor()
            health_text = monitor.get_brain_summary_for_prompt(symbol)
            if health_text:
                context_parts.append(health_text)
        except Exception as e:
            logger.debug("health_monitor_failed", error=str(e)[:60])

        # 6. Path A: Past similar failures (attribution) — use to be cautious; do not blindly change rules
        try:
            from market_agent.brain.council_memory import get_council_memory
            mem = get_council_memory()
            similar = mem.recall_similar(
                query=f"{symbol} failure loss",
                memory_type="TRADE_LOSS",
                k=3,
            )
            if similar:
                failure_lines = [s.get("text", "")[:150] for s in similar if s.get("text")]
                if failure_lines:
                    context_parts.append(
                        "PAST SIMILAR FAILURES (use to be cautious; do not blindly change rules): "
                        + "; ".join(failure_lines)
                    )
        except Exception as e:
            logger.debug("rag_failures_recall_failed", error=str(e)[:60])

        # 7. Path A Plan A: Past similar sentiment → outcomes (news sentiment backfill)
        try:
            from market_agent.brain.council_memory import get_council_memory
            mem = get_council_memory()
            similar = mem.recall_similar(
                query=f"{symbol} news sentiment",
                memory_type="NEWS_SENTIMENT",
                k=3,
            )
            if similar:
                lines = []
                for s in similar:
                    d = s.get("date", "")
                    sent = s.get("sentiment_score", "")
                    ret = s.get("return_1d", "")
                    if d is not None and ret is not None:
                        lines.append(f"date={d} sentiment={sent} return_1d={ret}%")
                if lines:
                    context_parts.append(
                        "PAST SIMILAR SENTIMENT → OUTCOMES (use to inform caution or conviction; do not blindly follow): "
                        + "; ".join(lines)
                    )
        except Exception as e:
            logger.debug("rag_sentiment_recall_failed", error=str(e)[:60])

        # 8. Path A §10: Brain streaks — "Brain X was right on last N similar setups; consider their view"
        try:
            from market_agent.learning.signal_resolver import SignalResolver
            from market_agent.data.storage.postgres import PostgresStorage
            _st = PostgresStorage()
            _res = SignalResolver(_st)
            streaks = _res.get_brain_streaks(symbol=symbol)
            if streaks:
                strong = [f"{mid} {n} in a row" for mid, n in streaks.items() if n >= 2]
                if strong:
                    context_parts.append(
                        "BRAIN STREAKS (consider their view when agreeing): "
                        + "; ".join(strong)
                        + "."
                    )
        except Exception as e:
            logger.debug("rag_streaks_failed", error=str(e)[:60])

        # 9. Path A Phase 3 Optional: Latest news for council/brains (Boss can use; no separate "request" needed)
        try:
            from market_agent.research.news_aggregator import news_aggregator
            news_list = news_aggregator.fetch_news(symbol, limit=10)
            if news_list:
                lines = []
                for item in news_list[:8]:
                    title = (item.get("title") or "")[:80]
                    sent = item.get("sentiment_score")
                    s = f"{title}" + (f" (sentiment {sent:+.2f})" if sent is not None else "")
                    lines.append(s)
                if lines:
                    context_parts.append(
                        "LATEST NEWS (for context): " + " | ".join(lines)
                    )
        except Exception as e:
            logger.debug("rag_news_fetch_failed", error=str(e)[:60])

        # 10. Path A §11a Step 4: News sentiment rules — "when sentiment similar, avg return = Z"
        try:
            from market_agent.research.news_sentiment_rules import get_news_sentiment_guidance
            guidance = get_news_sentiment_guidance(symbol)
            if guidance:
                context_parts.append(guidance)
        except Exception as e:
            logger.debug("rag_news_sentiment_rules_failed", error=str(e)[:60])

        if context_parts:
            return "\n".join([
                "=== YOUR DATABASE CONTEXT (ground truth) ===",
                *context_parts,
                "=== END CONTEXT ==="
            ])
        return ""

    # ═══════════════════════════════════════
    # PUBLIC API METHODS
    # ═══════════════════════════════════════

    def analyze_market(self, symbol: str, tech_analysis: Dict[str, Any],
                       news_headlines: List[str] = None,
                       price: float = None) -> Optional[str]:
        """
        Generate an AI market analysis for a symbol.
        Calls AI every 3rd cycle (with 3 Gemini keys + Groq, more generous).
        Injects RAG context for grounded response.
        """
        self.analysis_cycle += 1
        cache_key = f"analysis_{symbol}"

        # Check cache first (always)
        cached = self._check_cache(cache_key)
        if cached:
            return cached

        # Only call AI every Nth cycle
        if self.analysis_cycle % self.ANALYSIS_INTERVAL != 0:
            return None

        # Build technical context
        consensus = tech_analysis.get("consensus", {})
        rsi = tech_analysis.get("rsi", {})
        macd = tech_analysis.get("macd", {})
        fib = tech_analysis.get("fibonacci", {})
        ema = tech_analysis.get("ema_ribbon", {})
        volume = tech_analysis.get("volume", {})
        bb = tech_analysis.get("bollinger", {})

        news_section = ""
        if news_headlines:
            headlines_text = "\n".join(f"- {h}" for h in news_headlines[:5])
            news_section = f"\nRecent News:\n{headlines_text}"

        # RAG context from our database
        rag_context = self._build_rag_context(symbol)

        prompt = f"""You are Aegis, an expert financial analyst AI. Analyze {symbol} and give a concise 3-4 sentence trading insight.

{rag_context}

Current Price: {price or tech_analysis.get('price', 'N/A')}
Technical Consensus: {consensus.get('direction', 'N/A')} (Confidence: {consensus.get('confidence', 0):.1%})
RSI: {rsi.get('value', 'N/A')} ({rsi.get('signal', 'N/A')})
MACD: {macd.get('bias', 'N/A')} {f"| {macd.get('crossover')}" if macd.get('crossover') else ''}
EMA Trend: {ema.get('trend', 'N/A')}
Bollinger: {bb.get('signal', 'N/A')} (Position: {bb.get('band_position', 'N/A')})
Volume: {volume.get('signal', 'N/A')} ({volume.get('trend', 'N/A')})
Fibonacci Direction: {fib.get('direction', 'N/A')}, Key retracement: {fib.get('retracements', {}).get('0.618', 'N/A')}
{news_section}

Rules:
- Be specific about {symbol}, not generic
- Mention specific numbers (RSI value, Fib levels, etc.)
- State if it's bullish, bearish, or consolidating and WHY
- If you have PREDICTION HISTORY context, mention if current signals align with past performance
- Keep it to 3-4 sentences max
- Do NOT say "I think" — state analysis directly"""

        return self._call_ai(prompt, cache_key)

    def brain_response(self, query: str, symbol: str, brain_role: str,
                       market_context: Dict[str, Any],
                       performance_history: str = "") -> Optional[str]:
        """
        Generate a brain-specific response for the council.
        Injects RAG context for grounded, non-hallucinated responses.
        """
        cache_key = f"brain_{brain_role}_{hash(query) % 10000}"

        price = market_context.get("price", "N/A")
        consensus = market_context.get("consensus", {})
        rsi = market_context.get("rsi", {})

        role_descriptions = {
            "AMV-LSTM": "You specialize in temporal pattern recognition and sequence prediction.",
            "Cross-Stock GNN": "You specialize in cross-asset correlations and sector relationships.",
            "RL Weighter": "You specialize in position sizing, risk management, and portfolio optimization.",
            "Multi-Timeframe": "You specialize in multi-timeframe analysis (1m, 15m, 1h, 4h, 1d).",
            "Regime Ensemble": "You specialize in market regime detection (trending, ranging, volatile).",
            "Multi-Modal Fusion": "You specialize in combining news sentiment with technical data.",
            "Cortex": "You are the executive brain that synthesizes all inputs. Give the final verdict.",
            "BOSS BRAIN": "You are the Senior Investment Strategist. Be direct and conversational. Example: 'Looking at the trend, I expect a small dip before a recovery.' Only then mention technicals.",
        }

        role_desc = role_descriptions.get(brain_role, "You are a financial analysis AI model.")

        # RAG context
        rag_context = self._build_rag_context(symbol)

        prompt = f"""You are {brain_role}, part of an AI trading council analyzing {symbol}.
{role_desc}
{f"Your recent track record: {performance_history}" if performance_history else ""}

{rag_context}

Current data for {symbol}:
- Price: {price}
- Technical Consensus: {consensus.get('direction', 'N/A')} ({consensus.get('confidence', 0):.0%} confidence)
- RSI: {rsi.get('value', 'N/A')}

User asked: "{query}"

Rules:
1. START with a direct, conversational answer GROUNDED in the data above and your database context.
2. Do NOT hallucinate. If signals are mixed, say "signals are conflicting" instead of forcing a call.
3. If you have PREDICTION HISTORY, factor past accuracy into your confidence.
4. Use human-friendly language, then list technical values as 'Supporting Evidence'.
5. Be concise (2-3 sentences max)."""

        return self._call_ai(prompt, cache_key)

    def brain_response_for_council(self, query: str, symbol: str, brain_role: str,
                                   market_context: Dict[str, Any],
                                   performance_history: str = "") -> Optional[str]:
        """
        Boss Brain / EXECUTIVE council call using RESERVED Gemini keys.
        Identical prompt structure to brain_response() but:
          - council_mode=True  → reserved keys first, never starved by analyst calls
          - No response caching (verdicts must always be fresh)
          - Higher token budget (800 vs 500) for richer Boss Brain synthesis
        """
        price     = market_context.get("price", "N/A")
        consensus = market_context.get("consensus", {})
        rsi       = market_context.get("rsi", {})

        role_desc = "You are the Senior Investment Strategist. Synthesise all brain evidence and give a clear, decisive verdict."
        rag_context = self._build_rag_context(symbol)

        prompt = f"""You are {brain_role}, the EXECUTIVE brain of an AI trading council analyzing {symbol}.
{role_desc}
{f"Brain track records: {performance_history}" if performance_history else ""}

{rag_context}

Current data for {symbol}:
- Price: {price}
- Technical Consensus: {consensus.get('direction', 'N/A')} ({consensus.get('confidence', 0):.0%} confidence)
- RSI: {rsi.get('value', 'N/A')}

TASK: "{query}"

Respond ONLY in JSON:
{{"verdict": "BUY|SELL|HOLD", "confidence": 0.0-1.0, "reasoning": "one decisive sentence"}}"""

        # council_mode=True: uses Gemini reserved keys, no caching
        return self._call_ai(prompt, cache_key=None, max_tokens=800, council_mode=True)

    def brain_response_volatile(self, query: str, symbol: str, brain_role: str,
                                market_context: Dict[str, Any],
                                performance_history: str = "") -> Optional[str]:
        """
        VOLATILE_CHAOS Boss Brain — Groq is the DEFAULT provider (speed > quality).

        Geminiiflow design: when market is crashing or spiking, you need a verdict
        in 1-2 seconds (Groq) not 3-5 seconds (Gemini). If Groq is rate-limited,
        falls back to Gemini reserved keys automatically via _call_ai council_mode.

        Routing:
          1. Try Groq directly (fastest, free, Llama 3.3 70B)
          2. If Groq unavailable/rate-limited → _call_ai with council_mode=True (Gemini reserved)
        """
        price     = market_context.get("price", "N/A")
        consensus = market_context.get("consensus", {})
        rsi       = market_context.get("rsi", {})

        prompt = f"""You are {brain_role}, VOLATILITY CRISIS decision mode for {symbol}.
Market regime: VOLATILE_CHAOS — extreme uncertainty, speed is critical.
{f"Brain track records: {performance_history}" if performance_history else ""}

Current data:
- Price: {price}
- Technical Consensus: {consensus.get('direction', 'N/A')} ({consensus.get('confidence', 0):.0%} confidence)
- RSI: {rsi.get('value', 'N/A')}

TASK: "{query}"

In VOLATILE_CHAOS: default to HOLD unless evidence is overwhelming.
Respond ONLY in JSON:
{{"verdict": "BUY|SELL|HOLD", "confidence": 0.0-1.0, "reasoning": "one decisive sentence, max 15 words"}}"""

        # Step 1: Try Groq first (fastest — primary for volatile markets)
        if self.groq and self.groq.is_available:
            try:
                result = self.groq.call(prompt, max_tokens=120)
                if result:
                    self.call_count += 1
                    self.provider_attribution["Groq"] += 1
                    logger.info("ai_call_success",
                                provider="Groq",
                                mode="VOLATILE_CHAOS",
                                total=self.call_count,
                                symbol=symbol)
                    return result
            except Exception as e:
                logger.warning("groq_volatile_failed", error=str(e)[:80])

        # Step 2: Groq unavailable/rate-limited → Gemini reserved keys (council_mode)
        logger.info("volatile_chaos_groq_exhausted_falling_back_to_gemini",
                    symbol=symbol)
        return self._call_ai(prompt, cache_key=None, max_tokens=500, council_mode=True)

    def score_sentiment(self, headlines: List[str],
                        symbol: str) -> Optional[Dict[str, Any]]:
        """
        Use AI to score sentiment of news headlines.
        Returns structured sentiment data.
        """
        if not headlines:
            return None

        cache_key = f"sentiment_{symbol}_{hash(str(headlines[:3])) % 10000}"

        headlines_text = "\n".join(
            f"{i+1}. {h}" for i, h in enumerate(headlines[:8])
        )

        prompt = f"""Score the sentiment of these {symbol} headlines on a scale of -1.0 (very bearish) to +1.0 (very bullish).
Also rate the impact from 0.0 (no impact) to 1.0 (market-moving).

Headlines:
{headlines_text}

Respond ONLY in this JSON format (no markdown, no code blocks):
{{"overall_sentiment": 0.0, "overall_impact": 0.0, "summary": "one line summary", "top_catalyst": "most important headline"}}"""

        result = self._call_ai(prompt, cache_key)
        if result:
            try:
                cleaned = result.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(cleaned)
            except (json.JSONDecodeError, IndexError):
                logger.warning("ai_sentiment_parse_failed")
                return None
        return None

    def generate_daily_report(self, symbols: List[str]) -> Optional[str]:
        """
        Generate daily summary report across all symbols.
        Uses 1 AI prompt — designed for EOD summary.
        """
        report_data = []
        try:
            from market_agent.data.storage.postgres import PostgresStorage
            from market_agent.learning.signal_resolver import SignalResolver
            storage = PostgresStorage()
            resolver = SignalResolver(storage)

            from market_agent.config import ACCURACY_STATS_LAST_N
            for symbol in symbols[:10]:  # Cap at 10 to stay within token limits
                stats = resolver.get_accuracy_stats(symbol=symbol, last_n=ACCURACY_STATS_LAST_N)
                if stats and stats.get('total', 0) > 0:
                    report_data.append(
                        f"- {symbol}: {stats['total']} predictions, "
                        f"{stats.get('accuracy', 0):.1f}% accuracy, "
                        f"trend: {stats.get('trend', 'N/A')}"
                    )
        except Exception:
            pass

        if not report_data:
            return None

        prompt = f"""You are the Chief Market Strategist AI. Generate a concise daily market report.

Today's Brain Performance:
{chr(10).join(report_data)}

AI Provider Usage Today:
- Gemini: {self.provider_attribution.get('Gemini', 0)} calls
- Groq: {self.provider_attribution.get('Groq', 0)} calls
- Mistral: {self.provider_attribution.get('Mistral', 0)} calls
- Cohere: {self.provider_attribution.get('Cohere', 0)} calls
- Local fallback: {self.provider_attribution.get('Local', 0)} calls

Generate a report with:
1. OVERALL: 1-2 sentence market summary
2. BEST PERFORMER: Which symbol had best accuracy and why
3. WORST PERFORMER: Which needs attention and suggested fix
4. CONFIDENCE: Your confidence in tomorrow's outlook (High/Medium/Low)
5. ACTION ITEMS: 2-3 specific things to watch tomorrow

Keep it under 200 words. Be direct and data-driven."""

        return self._call_ai(prompt, f"daily_report_{datetime.now().strftime('%Y%m%d')}",
                            max_tokens=600)

    def get_status(self) -> Dict[str, Any]:
        """Get combined API status for UI display."""
        gemini_status = self.gemini.get_status()
        total_rpm = sum(l.max_rpm for l in self.gemini.limiters)
        total_rpm += self.groq.limiter.max_rpm
        total_rpm += self.mistral.limiter.max_rpm

        used_rpm = sum(l.calls_in_window for l in self.gemini.limiters)
        used_rpm += self.groq.limiter.calls_in_window
        used_rpm += self.mistral.limiter.calls_in_window

        wait_gemini = self.gemini.get_wait_time_sec() if hasattr(self.gemini, 'get_wait_time_sec') else 0
        wait_groq = self.groq.get_wait_time_sec() if hasattr(self.groq, 'get_wait_time_sec') else 0
        wait_mistral = self.mistral.get_wait_time_sec() if hasattr(self.mistral, 'get_wait_time_sec') else 0
        wait_cohere = self.cohere.get_wait_time_sec() if hasattr(self.cohere, 'get_wait_time_sec') else 0
        waits = [wait_gemini, wait_groq, wait_mistral, wait_cohere]
        wait_seconds = min(w for w in waits if w is not None and w >= 0) if waits else 0

        return {
            "available": self.is_available,
            "calls_in_window": used_rpm,
            "max_rpm": total_rpm,
            "total_calls": self.call_count,
            "cached_items": len(self.cache),
            "rate_limited": used_rpm >= total_rpm,
            "wait_seconds": wait_seconds,
            "analysis_cycle": self.analysis_cycle,
            "providers": {
                "gemini": {**gemini_status, "wait_sec": wait_gemini},
                "groq": {**self.groq.get_status(), "wait_sec": wait_groq},
                "mistral": {**self.mistral.get_status(), "wait_sec": wait_mistral},
                "cohere": {**self.cohere.get_status(), "wait_sec": wait_cohere},
            },
            "attribution": dict(self.provider_attribution),
        }


# Module-level singleton
gemini_client = GeminiClient()
