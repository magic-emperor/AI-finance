"""
Phase 3.5: Market Utilities

Centralized utilities for:
1. Market hours awareness (NSE/US)
2. Weight decay scheduler (call after each scan cycle)
3. Multi-market report generator
4. Anomaly pre-filter (spike/gap detection before signal generation)
5. News per-symbol filter
"""

import structlog
from datetime import datetime, time, timedelta
from typing import Dict, List, Any, Optional
import json

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════
# 1. MARKET HOURS (NSE / US)
# ═══════════════════════════════════════════════════════════════

MARKET_SCHEDULES = {
    "NSE": {
        "open": time(9, 15),
        "close": time(15, 30),
        "pre_open": time(9, 0),
        "post_close": time(15, 40),
        "timezone": "Asia/Kolkata",
        "weekdays": [0, 1, 2, 3, 4],  # Mon-Fri
    },
    "US": {
        "open": time(9, 30),
        "close": time(16, 0),
        "pre_open": time(9, 0),
        "post_close": time(16, 30),
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
    },
    "CRYPTO": {
        "open": time(0, 0),
        "close": time(23, 59),
        "pre_open": time(0, 0),
        "post_close": time(23, 59),
        "timezone": "UTC",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],  # 24/7
    },
}


def get_market_for_symbol(symbol: str) -> str:
    """Determine which market a symbol belongs to."""
    if symbol.endswith(".NS") or symbol.startswith("^NSE"):
        return "NSE"
    if "-USD" in symbol or "-INR" in symbol or symbol in ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD"):
        return "CRYPTO"
    return "US"


def is_market_open(symbol: str = None, market: str = None) -> Dict[str, Any]:
    """
    Check if market is currently open.
    Returns: {is_open, market, status, next_event, next_event_at}
    """
    if symbol and not market:
        market = get_market_for_symbol(symbol)
    market = market or "NSE"

    schedule = MARKET_SCHEDULES.get(market, MARKET_SCHEDULES["NSE"])
    now = datetime.now()
    current_time = now.time()
    weekday = now.weekday()

    # Weekend check
    if weekday not in schedule["weekdays"]:
        days_until_monday = (7 - weekday) % 7 or 7
        next_open = now.replace(
            hour=schedule["open"].hour,
            minute=schedule["open"].minute,
            second=0
        ) + timedelta(days=days_until_monday)
        return {
            "is_open": False,
            "market": market,
            "status": "WEEKEND",
            "next_event": "MARKET_OPEN",
            "next_event_at": next_open.isoformat(),
        }

    # Pre-market
    if current_time < schedule["open"]:
        return {
            "is_open": False,
            "market": market,
            "status": "PRE_MARKET",
            "next_event": "MARKET_OPEN",
            "next_event_at": now.replace(
                hour=schedule["open"].hour,
                minute=schedule["open"].minute
            ).isoformat(),
        }

    # Market open
    if schedule["open"] <= current_time <= schedule["close"]:
        minutes_left = (
            datetime.combine(now.date(), schedule["close"]) -
            datetime.combine(now.date(), current_time)
        ).seconds // 60

        return {
            "is_open": True,
            "market": market,
            "status": "OPEN",
            "minutes_remaining": minutes_left,
            "next_event": "MARKET_CLOSE",
            "next_event_at": now.replace(
                hour=schedule["close"].hour,
                minute=schedule["close"].minute
            ).isoformat(),
        }

    # Post-market
    next_day = now + timedelta(days=1)
    # Skip weekends
    while next_day.weekday() not in schedule["weekdays"]:
        next_day += timedelta(days=1)

    return {
        "is_open": False,
        "market": market,
        "status": "CLOSED",
        "next_event": "MARKET_OPEN",
        "next_event_at": next_day.replace(
            hour=schedule["open"].hour,
            minute=schedule["open"].minute
        ).isoformat(),
    }


def should_scan(symbol: str) -> bool:
    """
    Whether to scan a symbol right now.
    Returns True during market hours + 30 min buffer on each side.
    """
    market = get_market_for_symbol(symbol)
    schedule = MARKET_SCHEDULES.get(market, MARKET_SCHEDULES["NSE"])
    now = datetime.now()

    if now.weekday() not in schedule["weekdays"]:
        return False

    # Allow scanning 30 min before open and 30 min after close
    buffer = timedelta(minutes=30)
    open_dt = datetime.combine(now.date(), schedule["open"]) - buffer
    close_dt = datetime.combine(now.date(), schedule["close"]) + buffer

    return open_dt <= now <= close_dt


# ═══════════════════════════════════════════════════════════════
# 2. WEIGHT DECAY SCHEDULER
# ═══════════════════════════════════════════════════════════════

def apply_weight_decay_if_due():
    """
    Apply weight decay once per day (prevents runaway weights).
    Safe to call every scan cycle — it self-throttles.
    """
    try:
        from market_agent.brain.health_monitor import get_health_monitor
        monitor = get_health_monitor()

        # Check if decay was already applied today
        cache_key = "_last_decay_date"
        last_decay = getattr(monitor, cache_key, None)
        today = datetime.now().date()

        if last_decay == today:
            return  # Already applied today

        monitor.apply_weight_decay(decay_rate=0.02)
        setattr(monitor, cache_key, today)
        logger.info("weight_decay_applied", date=str(today))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# 3. ANOMALY PRE-FILTER
# ═══════════════════════════════════════════════════════════════

def check_anomaly(price_data: Dict, atr: float = 0) -> Dict[str, Any]:
    """
    Check for anomalies that should suppress or flag signals.
    Call BEFORE generating a signal.

    Checks:
    - Gap open (>2% from previous close)
    - Volume spike (>3x average)
    - Price spike (move > 3x ATR in one candle)

    Returns: {has_anomaly, anomaly_type, severity, should_suppress}
    """
    result = {
        "has_anomaly": False,
        "anomaly_type": None,
        "severity": "NONE",
        "should_suppress": False,
        "details": "",
    }

    if not price_data:
        return result

    open_price = price_data.get("open", 0)
    close = price_data.get("close", 0)
    prev_close = price_data.get("prev_close", 0)
    volume = price_data.get("volume", 0)
    avg_volume = price_data.get("avg_volume", 0)

    # Gap open check
    if prev_close > 0 and open_price > 0:
        gap_pct = abs(open_price - prev_close) / prev_close * 100
        if gap_pct > 2.0:
            result["has_anomaly"] = True
            result["anomaly_type"] = "GAP_OPEN"
            result["severity"] = "HIGH" if gap_pct > 4.0 else "MEDIUM"
            result["should_suppress"] = gap_pct > 4.0
            result["details"] = f"Gap open: {gap_pct:.1f}%"
            return result

    # Volume spike check
    if avg_volume > 0 and volume > 0:
        vol_ratio = volume / avg_volume
        if vol_ratio > 3.0:
            result["has_anomaly"] = True
            result["anomaly_type"] = "VOLUME_SPIKE"
            result["severity"] = "HIGH" if vol_ratio > 5.0 else "MEDIUM"
            result["should_suppress"] = False  # News-driven, not an error
            result["details"] = f"Volume {vol_ratio:.1f}x average"
            return result

    # Price spike check (intraday)
    if atr > 0 and close > 0 and open_price > 0:
        move = abs(close - open_price)
        if move > 3 * atr:
            result["has_anomaly"] = True
            result["anomaly_type"] = "PRICE_SPIKE"
            result["severity"] = "HIGH"
            result["should_suppress"] = True
            result["details"] = f"Price moved {move:.2f} vs ATR {atr:.2f}"
            return result

    return result


# ═══════════════════════════════════════════════════════════════
# 4. MULTI-MARKET REPORT GENERATOR
# ═══════════════════════════════════════════════════════════════

def generate_market_report(storage=None, symbol: str = None) -> Dict[str, Any]:
    """
    Generate a comprehensive multi-market report.
    Covers: system health, per-market status, top/bottom performers,
    pending proposals, and recent debate outcomes.
    Pass storage so recent_debates and DB-dependent sections work.
    Pass symbol to scope accuracy to selected symbol (optional).
    """
    report = {
        "timestamp": datetime.now().isoformat(),
        "markets": {},
        "system_health": {},
        "top_performers": [],
        "underperformers": [],
        "pending_proposals": 0,
        "recent_debates": 0,
        "ai_narrative": "",
    }

    # Market status
    for market in ["NSE", "US"]:
        report["markets"][market] = is_market_open(market=market)

    # Ensure we have storage for debates and PR
    if storage is None:
        try:
            from market_agent.data.storage.postgres import PostgresStorage
            storage = PostgresStorage()
        except Exception:
            pass

    # System health: add Signal Engine (real data) + per-brain from health monitor
    brain_list = []
    try:
        from market_agent.learning.signal_resolver import SignalResolver
        if storage:
            resolver = SignalResolver(storage)
            from market_agent.config import ACCURACY_STATS_LAST_N
            global_stats = resolver.get_accuracy_stats(symbol=symbol, last_n=ACCURACY_STATS_LAST_N)
            if global_stats.get("total", 0) > 0:
                brain_list.append({
                    "brain": "Signal Engine",
                    "accuracy": global_stats.get("accuracy", 0),
                    "total": global_stats.get("total", 0),
                    "weight": 1.0,
                })
    except Exception:
        pass

    try:
        from market_agent.brain.health_monitor import get_health_monitor
        monitor = get_health_monitor()
        health = monitor.full_health_check(symbol=symbol)

        report["system_health"] = {
            "system_accuracy": health.get("system_accuracy", 0),
            "total_brains": len(health.get("brains", {})),
            "active_brains": sum(
                1 for b in health.get("brains", {}).values()
                if b.get("total_predictions", 0) > 0
            ),
        }

        for name, data in health.get("brains", {}).items():
            if data.get("total_predictions", 0) >= 1:
                brain_list.append({
                    "brain": name,
                    "accuracy": data.get("overall_accuracy", 0),
                    "total": data.get("total_predictions", 0),
                    "weight": data.get("weight", 1.0),
                })
    except Exception:
        if not report["system_health"]:
            report["system_health"] = {"system_accuracy": 0, "total_brains": 0, "active_brains": 0}

    brain_list.sort(key=lambda x: x["accuracy"], reverse=True)
    report["top_performers"] = brain_list[:5]
    report["underperformers"] = brain_list[-3:] if len(brain_list) > 3 else []

    # Pending AEPs
    try:
        from market_agent.brain.pr_system import get_pr_system
        pr = get_pr_system(storage)
        report["pending_proposals"] = len(pr.get_pending_proposals())
    except Exception:
        pass

    # Recent debates (requires storage)
    if storage:
        try:
            from sqlalchemy import text
            session = storage.Session()
            count = session.execute(text(
                "SELECT COUNT(*) FROM council_debates "
                "WHERE created_at > NOW() - INTERVAL '24 hours'"
            )).scalar()
            report["recent_debates"] = count or 0
            session.close()
        except Exception:
            pass

    # AI narrative (only if we have something to summarize)
    try:
        from market_agent.brain.gemini_client import gemini_client
        if gemini_client and gemini_client.is_available:
            top = report["top_performers"]
            bottom = report["underperformers"]
            prompt = (
                f"Market report summary:\n"
                f"System accuracy: {report['system_health'].get('system_accuracy', 0):.1f}%\n"
                f"Active brains: {report['system_health'].get('active_brains', 0)}\n"
                f"Top: {[b['brain'] + ':' + str(b.get('accuracy', 0)) + '%' for b in top[:3]]}\n"
                f"Bottom: {[b['brain'] + ':' + str(b.get('accuracy', 0)) + '%' for b in bottom[:3]]}\n"
                f"Pending proposals: {report['pending_proposals']}\n"
                f"Recent debates (24h): {report['recent_debates']}\n"
                f"In 2-3 sentences, summarize system status and key actions needed."
            )
            report["ai_narrative"] = gemini_client._call_ai(prompt, "report") or ""
    except Exception:
        pass

    return report


# ═══════════════════════════════════════════════════════════════
# 5. NEWS PER-SYMBOL FILTER
# ═══════════════════════════════════════════════════════════════

def filter_news_by_symbol(news_items: List[Dict], symbol: str) -> List[Dict]:
    """
    Filter news articles to only those relevant to a specific symbol.
    Matches by: ticker, company name, sector keywords.
    """
    if not news_items or not symbol:
        return news_items or []

    # Build search terms from symbol
    clean = symbol.replace(".NS", "").replace("^", "")
    search_terms = [clean.lower()]

    # Add common company names
    SYMBOL_NAMES = {
        "ITC": ["itc", "itc limited"],
        "RELIANCE": ["reliance", "jio", "reliance industries"],
        "TCS": ["tcs", "tata consultancy"],
        "INFY": ["infosys", "infy"],
        "HDFCBANK": ["hdfc bank", "hdfc"],
        "ICICIBANK": ["icici bank", "icici"],
        "SBIN": ["sbi", "state bank"],
        "KOTAKBANK": ["kotak", "kotak mahindra"],
        "BAJFINANCE": ["bajaj finance", "bajaj finserv"],
        "HINDUNILVR": ["hindustan unilever", "hul"],
        "BHARTIARTL": ["bharti airtel", "airtel"],
        "TATAMOTORS": ["tata motors"],
        "LT": ["larsen", "l&t"],
        "AXISBANK": ["axis bank"],
        "WIPRO": ["wipro"],
        "TATASTEEL": ["tata steel"],
        "SUNPHARMA": ["sun pharma"],
        "TITAN": ["titan"],
        "NSEBANK": ["bank nifty", "nifty bank"],
        "BTC": ["bitcoin", "btc", "crypto"],
        "ETH": ["ethereum", "eth", "ether"],
        "SOL": ["solana", "sol"],
        "XRP": ["ripple", "xrp"],
        "DOGE": ["dogecoin", "doge"],
    }

    extra = SYMBOL_NAMES.get(clean, [])
    search_terms.extend(extra)

    filtered = []
    for item in news_items:
        text = (
            (item.get("title", "") + " " + item.get("summary", ""))
            .lower()
        )
        if any(term in text for term in search_terms):
            filtered.append(item)

    return filtered
