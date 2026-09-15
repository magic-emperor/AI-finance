"""
Watchlist Scanner + Learning Engine

The brain's core loop:
1. SCAN  — Fetch prices, generate signals, store with validation
2. RESOLVE — Check past signals against FRESH prices (not creation-time prices)
3. LEARN — Evaluate accuracy, trigger retraining when performance drops
4. OBSERVE — Run analyst (news+TA), persist insights to long-term memory
5. RESEARCH — Fetch fundamentals from multiple sources (daily)

Intended to run on a schedule via autonomous_scout.py (every 10-15 minutes).
"""

import os
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")  # Suppress gRPC validate_metadata_from_plugin warning

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import random
import time
import yfinance as yf
import structlog

# Load .env so Gemini/Groq/Mistral API keys are available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from market_agent.data.storage.postgres import PostgresStorage
from market_agent.learning.evaluator import RegretEngine
from market_agent.learning.signal_resolver import SignalResolver
from market_agent.dashboard.signal_engine import SignalEngine
from market_agent.patterns.technical_analysis import ta_engine
from market_agent.research.forex_factory import forex_calendar

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════
# DAILY SIGNAL CACHE — Steps 1.2 & 1.3
# FII/DII and PCR are once-per-day signals.  Calling nsepython
# or NSE on every 10-min scan cycle would (a) get you rate-limited
# and (b) waste time.  Cache by date.today() key.
# ═══════════════════════════════════════════════════════════
import datetime as _dt

_daily_cache: dict = {}  # {f"{key}_{YYYY-MM-DD}": signal_dict}

def _get_cached_daily_signal(key: str, fetch_fn) -> dict:
    """Return cached result for today; only calls fetch_fn once per trading day."""
    today     = str(_dt.date.today())
    cache_key = f"{key}_{today}"
    if cache_key not in _daily_cache:
        _daily_cache[cache_key] = fetch_fn()
    return _daily_cache[cache_key]


# ═══════════════════════════════════════════════════════════
# WATCHLIST
# ═══════════════════════════════════════════════════════════

WATCHLIST: List[str] = [
    # India / NSE
    "ITC.NS",
    "^NSEBANK",
    "HDFCBANK.NS",
    "RELIANCE.NS",
    "TATASTEEL.NS",
    "LT.NS",
    "M&M.NS",       # Mahindra & Mahindra — correct yfinance symbol (was M&M.NS)
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    # US tech / AI
    "NVDA",
    "GOOGL",
    "AAPL",
    "AMD",
    # FX / Commodities / Crypto
    "BTC-USD",
    "GC=F",
    "GBPJPY=X",
    "USDJPY=X",
    "CL=F",
]

# Equity symbols (not indices/forex/crypto/commodities) — for fundamentals collection
# Only real stocks have P/E, market cap, etc. Indices (^NSEBANK), forex (GBPJPY=X),
# crypto (BTC-USD), and commodities (GC=F, CL=F) don't have these.
EQUITY_SYMBOLS = [s for s in WATCHLIST if ".NS" in s or s in ("NVDA", "GOOGL", "AAPL", "AMD")]

# Track symbols that fail repeatedly → auto-skip for 1 hour after 3 consecutive failures
_symbol_failures: Dict[str, Dict] = {}  # {symbol: {"count": N, "skip_until": timestamp}}

# _is_market_open defined below (after duplicate removed) — single authoritative version at line ~159


def _should_skip_symbol(symbol: str) -> bool:
    """Check if symbol should be skipped due to repeated failures."""
    info = _symbol_failures.get(symbol)
    if not info:
        return False
    if info.get("skip_until", 0) > time.time():
        return True  # Still in cooldown
    # Cooldown expired, reset
    if info.get("skip_until", 0) <= time.time() and info["count"] >= 3:
        _symbol_failures[symbol] = {"count": 0, "skip_until": 0}
    return False

def _record_symbol_failure(symbol: str):
    """Record a failure for a symbol. After 3 failures, skip for 1 hour."""
    info = _symbol_failures.get(symbol, {"count": 0, "skip_until": 0})
    info["count"] += 1
    if info["count"] >= 3:
        info["skip_until"] = time.time() + 3600  # Skip for 1 hour
        logger.warning("symbol_degraded", symbol=symbol,
                       failures=info["count"], skip_hours=1)
    _symbol_failures[symbol] = info

def _record_symbol_success(symbol: str):
    """Reset failure counter on success."""
    if symbol in _symbol_failures:
        _symbol_failures[symbol] = {"count": 0, "skip_until": 0}


# ═══════════════════════════════════════════════════════════
# 1. SCAN — Generate signals with validation
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# 1. SCAN — Generate signals with validation
# ═══════════════════════════════════════════════════════════

def _is_market_open(symbol: str) -> bool:
    """
    Market hours guard (Section 5 of Implementation Guide).
    Returns True if the market for this symbol is currently open.
    All checks done in UTC then converted to local market time.
    """
    from datetime import timezone as _tz, timedelta as _td
    now_utc = datetime.now(_tz.utc)

    if '.NS' in symbol or 'NIFTY' in symbol.upper():
        # NSE India: Mon-Fri, 09:15 to 15:30 IST (UTC+5:30)
        now_ist = now_utc.astimezone(_tz((_td(hours=5, minutes=30))))
        hour, minute, weekday = now_ist.hour, now_ist.minute, now_ist.weekday()
        if weekday >= 5:
            return False
        if hour < 9 or hour > 15:
            return False
        if hour == 9 and minute < 15:
            return False
        if hour == 15 and minute > 30:
            return False
        return True

    elif 'USDT' in symbol or '-USD' in symbol or '=X' in symbol or '=F' in symbol:
        return True  # Crypto & Forex: 24/7

    else:
        # US stocks / indices: rough Mon-Fri guard
        return now_utc.weekday() < 5


def _fetch_hybrid_data(symbol: str, storage: PostgresStorage) -> Optional[Any]:
    """
    Hybrid Data Fetching Strategy (Phase 3):
    1. Fetch recent history from DB (Postgres).
    2. Check age of last candle.
    3. If stale (>10m), fetch gap from yfinance.
    4. Store gap to DB.
    5. Return merged DataFrame.
    
    SAFETY: Enforces symbol match and timestamp ordering.
    """
    import pandas as pd
    from market_agent.config import TRADING
    
    # 1. Try DB first
    db_records = []
    try:
        db_records = storage.get_latest_data(symbol, timeframe="1m", limit=1000)
    except Exception:
        pass
        
    cols = ["Open", "High", "Low", "Close", "Volume"]
    
    # Convert DB records to DataFrame if any
    if db_records:
        data_list = []
        indices = []
        for r in db_records:
            d = r['data']
            # SAFETY CHECK 1: Ensure critical columns exist
            if not all(k in d for k in cols):
                continue
            data_list.append(d)
            indices.append(r['timestamp'])
        
        hist = pd.DataFrame(data_list, index=indices, columns=cols)
        hist.index.name = "Datetime"
        hist = hist.sort_index()
    else:
        hist = pd.DataFrame(columns=cols)

    # 2. Check staleness — using NAIVE UTC to match DB timestamp storage (Sec 5 fix).
    # BEFORE: datetime.now() was naive IST — off by +5:30h vs UTC DB timestamps.
    # AFTER : datetime.now(timezone.utc).replace(tzinfo=None) = naive UTC = same epoch as DB.
    from datetime import timezone as _tz
    last_ts = hist.index[-1] if not hist.empty else datetime.now(_tz.utc).replace(tzinfo=None) - timedelta(days=7)
    now = datetime.now(_tz.utc).replace(tzinfo=None)   # naive UTC — matches DB storage
    diff_minutes = (now - last_ts).total_seconds() / 60
    
    # If gap > 10 mins AND market is open for this symbol, fetch from provider
    if diff_minutes > 10 and _is_market_open(symbol):
        logger.info("gap_fill_triggered", symbol=symbol, gap_min=diff_minutes)
        try:
            gap_df = None
            # Fetch from Binance if Crypto/Forex
            if any(s in symbol for s in ("-USD", "=X", "=F")):
                try:
                    from market_agent.data.ingestion.realtime_feed import price_feed
                    gap_df = price_feed.fetch_binance_history(symbol, limit=1000)
                except Exception:
                    pass
            
            # Fallback (or Primary for NSE) to yfinance
            if gap_df is None or gap_df.empty:
                ticker = yf.Ticker(symbol)
                gap_df = ticker.history(period="5d", interval="1m")
            
            if gap_df is not None and not gap_df.empty:
                # Normalize columns and index (Strip TZ)
                if gap_df.index.tz is not None:
                    gap_df.index = gap_df.index.tz_localize(None)
                gap_df = gap_df[cols]
                
                # Filter strictly new data
                if not hist.empty:
                    gap_df = gap_df[gap_df.index > last_ts]
                
                if not gap_df.empty:
                    # Determine source tag for these gap-fill rows.
                    # Previously no source was passed → defaulted to 'unknown' →
                    # this was the confirmed cause of the 39,002 mystery 1m rows.
                    gap_source = (
                        'binance' if any(s in symbol for s in ("-USD", "=X", "=F"))
                        else 'yfinance'
                    )
                    # Store new data to DB
                    for ts, row in gap_df.iterrows():
                        data_dict = {
                            "Open": float(row["Open"]),
                            "High": float(row["High"]),
                            "Low": float(row["Low"]),
                            "Close": float(row["Close"]),
                            "Volume": int(row["Volume"])
                        }
                        storage.store_ohlc(symbol, ts.to_pydatetime(), "1m", data_dict,
                                           source=gap_source)
                    
                    # Merge
                    hist = pd.concat([hist, gap_df])
                    
        except Exception as e:
            logger.error("gap_fetch_failed", symbol=symbol, error=str(e))
            
    return hist

def _build_intraday_signal_for_symbol(
    engine: SignalEngine,
    symbol: str,
    storage: Optional[PostgresStorage] = None,
    neural_model: Optional[Any] = None,
    preprocessor: Optional[Any] = None,
    causal_ensemble: Optional[Any] = None,
    rl_agent: Optional[Any] = None
) -> Optional[dict]:
    """
    Generate an intraday signal.
    Uses Hybrid Data (DB + Live Patch) and Dynamic Confidence.
    """
    from market_agent.config import MACRO_RISK, BRAIN_CONFIDENCE, TRADING
    import pandas as pd
    import torch
    import numpy as np
    
    # 1. Hybrid Data Fetch
    if storage:
        hist = _fetch_hybrid_data(symbol, storage)
    else:
        # Fallback if no storage passed (shouldn't happen in prod)
        hist = yf.Ticker(symbol).history(period="5d", interval="1m")

    if hist is None or hist.empty:
        return None

    # 2. REAL-TIME PATCH: AngelOne Live Tick
    current_price = float(hist["Close"].iloc[-1])
    try:
        real_time_price = _get_fresh_price(symbol)
        if real_time_price and real_time_price > 0:
            current_price = real_time_price
            
            # Append phantom candle for TA
            last_idx = hist.index[-1]
            if (datetime.now() - last_idx).total_seconds() > 60:
                new_idx = datetime.now()
                new_row = pd.DataFrame({
                    "Open": [current_price],
                    "High": [current_price],
                    "Low": [current_price],
                    "Close": [current_price],
                    "Volume": [0]
                }, index=[new_idx], columns=["Open", "High", "Low", "Close", "Volume"])
                hist = pd.concat([hist, new_row])
    except Exception as e:
        logger.warning("rt_patch_failed", symbol=symbol, error=str(e))

    # 3. Validation
    if current_price <= 0: return None
    
    # ATR Calc
    try:
        atr = hist["High"].sub(hist["Low"]).rolling(14).mean().iloc[-1]
        if pd.isna(atr) or atr <= 0: atr = current_price * 0.01
    except:
        atr = current_price * 0.01

    # 4. Technical Analysis
    try:
        tech_analysis = ta_engine.full_analysis(hist)
    except Exception:
        tech_analysis = None

    # 5. Smart Confidence (Dynamic from Config)
    # Default baseline
    confidence = TRADING["MIN_CONFIDENCE"]
    
    if tech_analysis and tech_analysis.get("consensus"):
        # We don't have individual brain votes here yet, so we use the consensus confidence
        # But we act *as if* the specific active logic triggered.
        # Ideally, ta_engine should return which brain triggered.
        # For now, we trust the consensus score but cap/floor it based on config.
        raw_conf = tech_analysis["consensus"].get("confidence", 0.5)
        
        # Boost if FVG present (it's a high quality setup)
        if tech_analysis.get("fvg"):
            raw_conf += 0.1
            
        confidence = min(0.95, float(raw_conf))
    else:
        confidence = 0.4

    # 6. Macro Risk (Externalized Config)
    macro_risk_factor = 1.0
    reasoning = [f"LTP: {current_price:.2f}", "Logic: Smart Hybrid Analysis"]
    
    try:
        events = forex_calendar.fetch_economic_events(symbol)
        now = datetime.now()
        for event in events:
            if event.get("title") in MACRO_RISK["EVENTS"] or event.get("impact_level") == "HIGH":
                event_time = datetime.fromisoformat(event["time"])
                minutes_to_event = (event_time - now).total_seconds() / 60
                
                # Check Lookahead/Lookback windows
                if -MACRO_RISK["LOOKBACK_MINUTES"] < minutes_to_event < MACRO_RISK["LOOKAHEAD_MINUTES"]:
                    macro_risk_factor = MACRO_RISK["HIGH_IMPACT_FACTOR"]
                    reasoning.append(f"⚠️ MACRO LIMIT: {event['title']} (Risk x{macro_risk_factor})")
                    break
    except Exception:
        pass

    # 7. Direction Logic - NOW WITH REAL BRAIN
    # TA Direction
    consensus = tech_analysis.get("consensus", {}) if tech_analysis else {}
    direction = consensus.get("direction", "HOLD")
    
    ta_probs = [0.33, 0.33, 0.33]
    if direction == "BUY":
        ta_probs = [0.1, 0.2, 0.7]
    elif direction == "SELL":
        ta_probs = [0.7, 0.2, 0.1]
    
    # Neural Network Inference
    nn_probs = None
    if neural_model and preprocessor and len(hist) > 20:
        try:
            # 1. Transform last 20 candles
            # Neural Preprocessor expects lowercase columns
            input_df = hist.iloc[-50:].copy()
            input_df.columns = [c.lower() for c in input_df.columns]
            scaled = preprocessor.transform(input_df)
            
            # 2. Reshape for LSTM (Batch=1, Seq=10, Feat=7)
            # We need strictly 10 steps (Sequence Length)
            if len(scaled) >= 10:
                seq = scaled[-10:] # Last 10 steps
                device = next(neural_model.parameters()).device
                x_tensor = torch.tensor(seq, dtype=torch.float32).unsqueeze(0).to(device)
                
                # 3. Predict
                with torch.no_grad():
                    nn_prob_tensor, range_est = neural_model(x_tensor)
                    nn_probs = nn_prob_tensor.cpu().numpy()[0] # [Down, Flat, Up]
                    
                # 4. Reason
                reasoning.append(f"🧠 Brain-v1: [D:{nn_probs[0]:.2f}, F:{nn_probs[1]:.2f}, U:{nn_probs[2]:.2f}]")
        except Exception as e:
            logger.warning("neural_inference_failed", error=str(e))
    
    # MIXING LOGIC (Ensemble)
    if nn_probs is not None:
        # Default Mix (Fallback)
        final_probs = [
            0.6 * ta_probs[0] + 0.4 * nn_probs[0], # Down
            0.6 * ta_probs[1] + 0.4 * nn_probs[1], # Flat
            0.6 * ta_probs[2] + 0.4 * nn_probs[2]  # Up
        ]
        model_name = "Brain-v3 (Hybrid+Neural)"
        
        # RL Weighting (If available)
        if rl_agent:
            try:
                # Construct simplified context (RSI, Vol, Trend, Vol, Ret)
                # Need to match train_rl.py get_market_features shape (1, 5)
                # We approximate from what we have
                rsi_val = tech_analysis.get('rsi', 50)/100.0 if tech_analysis else 0.5
                vol_val = (atr / current_price) * 10 
                ctx = torch.tensor([[rsi_val, vol_val, 0.0, 1.0, 0.0]], dtype=torch.float32)
                
                # Stack models: TA, NN, Neutral
                # We format inputs as (Batch, Models, Classes) -> (1, 3, 3)
                # Model 0: TA, Model 1: NN, Model 2: Neutral Baseline
                ta_t = torch.tensor(ta_probs)
                nn_t = torch.tensor(nn_probs)
                neu_t = torch.tensor([0.33, 0.33, 0.33])
                
                model_stack = torch.stack([ta_t, nn_t, neu_t]).unsqueeze(0)
                
                # Get learned weights
                with torch.no_grad():
                    rl_probs_t, weights_t = rl_agent(model_stack, ctx)
                    final_probs = rl_probs_t[0].numpy().tolist()
                    w = weights_t[0].numpy()
                    
                model_name = f"Brain-RL (W:[TA:{w[0]:.2f}, NN:{w[1]:.2f}])"
                reasoning.append(f"🤖 RL Weights: TA={w[0]:.2f}, NN={w[1]:.2f}")
            except Exception as e:
                logger.warning("rl_inference_failed", error=str(e))

    else:
        final_probs = ta_probs
        model_name = "Brain-v2 (Hybrid+TA)"
        
    # Causal Boost (If available)
    if causal_ensemble:
        # We need access to *other* active signals, but this function runs per symbol.
        # So we pass 'active_signals' dict from the runner loop.
        # TODO: Refactor runner to pass active_signals context.
        # For now, we simulate or skip until next refactor.
        pass

    # 8. Generate Signal
    signal_obj = engine.generate_signal(
        symbol=symbol,
        current_price=current_price,
        atr=atr,
        direction_probs=final_probs,
        confidence=confidence,
        regime="HYBRID_SCAN",
        model_name=model_name,
        macro_risk_factor=macro_risk_factor,
        reasoning=reasoning,
        tech_analysis=tech_analysis,
    )
    
    if not signal_obj: return None
    
    sig_dict = signal_obj.to_dict()
    sig_dict["tech_analysis"] = tech_analysis
    
    # Final price check
    if sig_dict.get("entry_price", 0) <= 0:
        sig_dict["entry_price"] = current_price

    return sig_dict


# ═══════════════════════════════════════════════════════════
# 2. RESOLVE — Check past signals with FRESH prices
# ═══════════════════════════════════════════════════════════

def _get_fresh_price(symbol: str) -> Optional[float]:
    """
    Tiered Price Fetching Strategy:
    1. RealTimePriceFeed (WebSocket - Binance/Crypto)
    2. Breeze API (Priority - Indian/Placeholder)
    3. nsepython (Indian Fallback)
    4. CoinGecko (Crypto/FX Fallback)
    5. AngelOne (Indian Last Resort)
    6. yfinance (Universal Last Resort)
    """
    # Source 0: RealTimePriceFeed (High-Velocity WebSocket)
    try:
        from market_agent.data.ingestion.realtime_feed import price_feed
        # Map symbol if needed (BTC-USD -> BTCUSDT)
        binance_sym = symbol.replace("-USD", "USDT").replace("/", "")
        price = price_feed.get_price(binance_sym)
        if price and price > 0:
            return price
    except Exception:
        pass

    # Source 1: Breeze API (Priority - Indian/Placeholder)
    if ".NS" in symbol or symbol.startswith("^NSE"):
        try:
            from market_agent.data.ingestion.breeze_client import breeze_client
            price = breeze_client.get_price(symbol)
            if price and price > 0:
                return price
        except Exception:
            pass

    # Source 2: nsepython (Robust Indian Fallback)
    if ".NS" in symbol or symbol.startswith("^NSE"):
        try:
            from market_agent.data.ingestion.nse_python_client import fetch_nse_price
            price = fetch_nse_price(symbol)
            if price and price > 0:
                # logger.info("price_source_nsepython", symbol=symbol, price=price)
                return price
        except Exception:
            pass

    # Source 3: CoinGecko (Crypto/Forex Fallback)
    if any(s in symbol for s in ("-USD", "=X", "=F")):
        try:
            from market_agent.data.ingestion.coingecko import fetch_coingecko_price
            price = fetch_coingecko_price(symbol)
            if price and price > 0:
                # logger.info("price_source_coingecko", symbol=symbol, price=price)
                return price
        except Exception:
            pass

    # Source 4: AngelOne (Maintenance/Legacy)
    if ".NS" in symbol:
        try:
            from market_agent.data.ingestion.angel_one_client import angel_client
            ltp = angel_client.get_market_quote(symbol)
            if ltp and ltp > 0:
                return ltp
        except Exception:
            pass

    # Source 5: yfinance (Broad Fallback)
    try:
        # ticker = yf.Ticker(symbol)
        # fast = ticker.fast_info
        # price = getattr(fast, 'last_price', None) or getattr(fast, 'previous_close', None)
        # if price and price > 0: return float(price)
        
        # Slower backup fetch
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m")
        if hist is not None and not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if price > 0: return price
    except Exception:
        pass

    return None


def resolve_past_signals(resolver: SignalResolver, symbols: List[str] = None):
    """
    Resolve old (unresolved) signals using FRESH prices.
    This is the fix for the 0% accuracy bug.
    """
    symbols = symbols or WATCHLIST
    total_resolved = 0

    for symbol in symbols:
        if not _is_market_open(symbol):
            continue
            
        try:
            fresh_price = _get_fresh_price(symbol)
            if not fresh_price or fresh_price <= 0:
                continue

            resolved = resolver.resolve_signals(
                current_price=fresh_price,
                symbol=symbol,
                macro_data={},
                recent_news=[],
            )
            if resolved:
                total_resolved += len(resolved)
                avg_acc = sum(r.get("accuracy", 0) for r in resolved) / len(resolved)
                print(f"  RESOLVED {symbol}: {len(resolved)} signals, avg accuracy={avg_acc:.1f}%")
        except Exception as e:
            logger.error("resolve_failed", symbol=symbol, error=str(e))

    # Gap 6: Unified Council Verdict Resolution
    try:
        from market_agent.runner.signal_resolver import resolve_signals as council_resolve
        c_resolved = council_resolve(resolver.storage)
        if c_resolved > 0:
            print(f"  RESOLVED Council Verdicts: {c_resolved}")
            total_resolved += c_resolved
    except Exception as e:
        logger.error("council_resolve_failed", error=str(e))

    return total_resolved


# ═══════════════════════════════════════════════════════════
# 3. LEARN — The brain's feedback loop
# ═══════════════════════════════════════════════════════════


# ── PANIC-LOOP FIX: Session-aware cooldown for accuracy alarms ──────────────
def _should_fire_accuracy_alarm(symbol: str, storage) -> bool:
    """
    Returns True only if no accuracy alarm has fired since the last
    relevant market session open for this symbol.

    NSE symbols (.NS, .BO):
        Only fires once per trading session (since 09:15 IST today, or
        yesterday 09:15 if we are before today's open). This prevents the
        10-minute scout cycle from spamming alarms every weekend.

    Crypto / FX / Commodities (24/7 markets):
        Simple 4-hour cooldown — market never closes so session concept
        does not apply.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import text as _text

    is_nse = symbol.endswith(".NS") or symbol.endswith(".BO")

    if is_nse:
        # Compute the start of the most recent NSE session (09:15 IST)
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        ist_now = now_utc + timedelta(hours=5, minutes=30)
        nse_open = ist_now.replace(hour=9, minute=15, second=0, microsecond=0)

        if ist_now < nse_open:
            # Before today's open — use yesterday's 09:15 IST
            nse_open = nse_open - timedelta(days=1)

        # Convert back to UTC for DB comparison
        last_open_utc = nse_open - timedelta(hours=5, minutes=30)
        cutoff = last_open_utc.replace(tzinfo=None).isoformat()
    else:
        # Crypto / FX — simple 4-hour cooldown
        cutoff = (datetime.utcnow() - timedelta(hours=4)).isoformat()

    try:
        with storage.engine.connect() as conn:
            cnt = conn.execute(_text("""
                SELECT COUNT(*) FROM council_debates
                WHERE symbol     = :sym
                AND   topic      LIKE '%accuracy dropped%'
                AND   created_at > :cutoff
            """), {"sym": symbol, "cutoff": cutoff}).scalar() or 0
        return cnt == 0
    except Exception:
        return True  # DB check failed — fail open (allow alarm, don't silently suppress)
# ─────────────────────────────────────────────────────────────────────────────


def run_learning_cycle(storage: PostgresStorage, resolver: SignalResolver,
                       engine: SignalEngine, regret: RegretEngine, cortex=None):
    """
    The brain's self-improvement loop. Called after every scan+resolve.
    
    1. Check which models are underperforming
    2. Trigger quick_study for worst symbols
    3. Log training runs
    4. Track source performance (news vs charts)
    5. Consult boss brain on repeated failures
    """
    print("  [LEARN] Running learning cycle...")

    # 1. Find underperformers
    try:
        underperformers = resolver.get_models_needing_training(accuracy_threshold=55.0)
    except Exception:
        underperformers = []

    if underperformers:
        print(f"  [LEARN] {len(underperformers)} models below 55% accuracy:")
        for m in underperformers:
            print(f"    - {m['model']}: {m['accuracy']}% over {m['total_predictions']} predictions")

    # 2. Get accuracy stats per symbol for targeted study
    study_count = 0
    for symbol in WATCHLIST:
        try:
            stats = resolver.get_accuracy_stats(symbol=symbol, last_n=20)
            accuracy = stats.get("accuracy", 0)
            total = stats.get("total", 0)

            if total < 5:
                continue  # Not enough data yet

            # Brain decides to train when accuracy drops
            if accuracy < 55:
                print(f"  [LEARN] {symbol} accuracy={accuracy:.1f}% — triggering quick_study + scheduled retrain")
                study_result = engine.quick_study(symbol)

                if study_result.get("status") == "SUCCESS":
                    study_count += 1
                    # Log real training run
                    storage.log_brain_training_run(
                        model_id=f"Aegis-{symbol.replace('.', '-')}",
                        mode=f"quick_study_{symbol}",
                        bars_learned=study_result.get("bars_learned", 0),
                        epochs=1,
                        notes=f"Auto-triggered: accuracy {accuracy:.1f}% < 55% over {total} predictions. "
                              f"Volatility regime: {study_result.get('volatility_regime', 'N/A')}. "
                              f"ATR multiplier adapted to: {study_result.get('adapted_stop_mult', 'N/A')}"
                    )
                    print(f"    -> Studied {study_result.get('bars_learned', 0)} bars, "
                          f"ATR adapted to {study_result.get('adapted_stop_mult', 'N/A')}")

                # Also trigger training_scheduler for deeper retrain
                try:
                    from market_agent.training.training_scheduler import get_training_scheduler
                    ts = get_training_scheduler()
                    retrain_result = ts.accuracy_triggered_train(symbol, current_accuracy=accuracy)
                    if retrain_result.get('accuracy', 0) > accuracy:
                        print(f"    -> Scheduled retrain improved: {accuracy:.1f}% -> {retrain_result['accuracy']:.1f}%")
                except Exception as e:
                    logger.error("scheduled_retrain_failed", symbol=symbol, error=str(e)[:80])

            elif accuracy >= 75 and total >= 10:
                print(f"  [LEARN] {symbol} accuracy={accuracy:.1f}% — PERFORMING WELL")

        except Exception as e:
            logger.error("learning_cycle_symbol_failed", symbol=symbol, error=str(e))

    # 3. Track overall brain performance
    try:
        global_stats = resolver.get_accuracy_stats(last_n=50)
        global_acc = global_stats.get("accuracy", 0)
        trend = global_stats.get("trend", "STABLE")
        status = global_stats.get("status", "UNKNOWN")
        print(f"  [LEARN] Global brain: {global_acc:.1f}% accuracy, trend={trend}, status={status}")

        # If globally poor, consult boss brain — threshold 50% (was 40%, too strict)
        if global_acc < 50 and global_stats.get("total", 0) >= 10:
            print(f"  [LEARN] CRITICAL: Global accuracy {global_acc:.1f}% — consulting boss brain")
            try:
                regret.consult_boss_brain(
                    model_id="Aegis-Global",
                    failure_context={
                        "global_accuracy": global_acc,
                        "trend": trend,
                        "underperformers": [m['model'] for m in underperformers],
                    }
                )
            except Exception:
                pass

        # Per-symbol council trigger: if any symbol has 3+ recent SL hits, queue debate
        for symbol in WATCHLIST:
            try:
                # ── PANIC-LOOP FIX: skip accuracy evaluation when market is closed ──
                # Closed market = no price movement = all EXPIRED scores are 0.0 noise.
                # get_accuracy_stats() now excludes those signals, but we also skip here
                # to avoid unnecessary DB queries and log noise on every weekend cycle.
                if not _is_market_open(symbol):
                    logger.debug("learning_cycle_skipped_market_closed", symbol=symbol)
                    continue
                # ─────────────────────────────────────────────────────────────────

                sym_stats = resolver.get_accuracy_stats(symbol=symbol, last_n=10)
                sym_acc = sym_stats.get("accuracy", 100)
                sym_total = sym_stats.get("total", 0)
                if sym_total >= 5 and sym_acc < 45:
                    # ── PANIC-LOOP FIX: cooldown guard — once per session, not per cycle ──
                    if _should_fire_accuracy_alarm(symbol, storage):
                        print(f"  [LEARN] {symbol} at {sym_acc:.1f}% in last {sym_total} — auto-queuing council debate")
                        try:
                            # Use shared cortex instance (passed from run_watchlist_scan)
                            _ctx = cortex
                            if _ctx is None:
                                from market_agent.brain.cortex import CortexGatekeeper
                                _ctx = CortexGatekeeper()
                            _ctx.queue_debate(
                                topic=f"{symbol} accuracy dropped to {sym_acc:.1f}% — review strategy",
                                symbol=symbol,
                                trigger_type="per_symbol_failure",
                                market_metrics={"accuracy": sym_acc, "total": sym_total},
                                urgency="HIGH"
                            )
                            logger.info("accuracy_alarm_fired", symbol=symbol, accuracy=sym_acc)
                        except Exception as e:
                            logger.error("learn_debate_queue_failed", symbol=symbol, error=str(e))
                    else:
                        logger.debug(
                            "accuracy_alarm_suppressed_cooldown",
                            symbol=symbol,
                            accuracy=sym_acc,
                        )
                    # ─────────────────────────────────────────────────────────────────
            except Exception:
                pass
    except Exception:
        pass

    if study_count > 0:
        print(f"  [LEARN] Completed {study_count} study sessions this cycle")
    else:
        print(f"  [LEARN] No retraining needed this cycle")


# ═══════════════════════════════════════════════════════════
# 4. OBSERVE — News + Analysis from ALL sources
# ═══════════════════════════════════════════════════════════

def run_analyst_observation(symbol: str, storage: PostgresStorage, hist=None):
    """
    GEMINIIFLOW: Reads news sentiment from NewsCache — ZERO Gemini calls here.

    The NewsWatcher (started in app.py) polls RSS feeds every 2 minutes and
    calls Gemini ONLY when headlines actually change for a symbol. This function
    reads the already-scored result from cache and returns immediately.

    Falls back to neutral 0.0 if the watcher hasn't been initialized yet
    (e.g., running in headless mode without the dashboard).
    """
    try:
        from market_agent.watchers.news_watcher import get_news_cache
        news_cache = get_news_cache()

        if news_cache:
            ctx = news_cache.get_sentiment(symbol)
            sentiment  = ctx.get("sentiment", 0.0)
            summary    = ctx.get("summary", "")
            headlines  = ctx.get("headlines", [])
            fresh      = ctx.get("fresh", False)
            print(
                f"    Analyst (cache): sentiment={sentiment:.2f}, "
                f"headlines={len(headlines)}, fresh={fresh}, summary={summary[:60]}"
            )
        else:
            # Watcher not running (headless/script mode) — log neutral, no Gemini call
            print(f"    Analyst (cache): watcher not initialized — using neutral 0.0")

    except Exception as e:
        logger.warning("analyst_cache_read_failed", symbol=symbol, error=str(e)[:80])

    # Also pull additional news from dedicated scrapers (per-source rate limits, no Gemini)
    _fetch_additional_news(symbol, storage)



def _fetch_additional_news(symbol: str, storage: PostgresStorage):
    """
    Aggregate news from all available scrapers beyond the AutonomousAnalyst.
    Each source has its own rate limits — we respect them individually.
    """
    additional_news = []

    # 1. Moneycontrol — for Indian stocks (5 min cache)
    if ".NS" in symbol:
        try:
            from market_agent.data.scrapers.moneycontrol_scraper import MoneycontrolScraper
            mc = MoneycontrolScraper()
            news = mc.fetch(symbol)
            for n in news[:5]:
                additional_news.append({
                    "title": n.get("headline", ""),
                    "source": "Moneycontrol",
                    "sentiment_score": n.get("sentiment", 0.5),
                    "impact_score": 0.5,
                    "published": n.get("timestamp", datetime.now().isoformat()),
                    "link": n.get("url", "#"),
                })
        except Exception:
            pass

    # 2. ForexFactory — for FX/crypto/commodities (30 min cache)
    if any(s in symbol for s in ("=X", "=F", "-USD")):
        try:
            from market_agent.research.forex_factory import forex_calendar
            ff_news = forex_calendar.fetch_community_news()
            for n in ff_news[:3]:
                additional_news.append({
                    "title": n.get("title", ""),
                    "source": "ForexFactory",
                    "sentiment_score": n.get("sentiment_score", 0.0),
                    "impact_score": 0.6,
                    "published": n.get("published", datetime.now().isoformat()),
                    "link": n.get("link", "#"),
                })
            # Also get economic calendar events
            events = forex_calendar.fetch_economic_events(symbol)
            for e in events[:3]:
                if e.get("impact_score", 0) >= 0.6:
                    additional_news.append({
                        "title": f"[ECON] {e.get('title', '')}",
                        "source": "ForexFactory Calendar",
                        "sentiment_score": 0.0,
                        "impact_score": e.get("impact_score", 0.5),
                        "published": e.get("time", datetime.now().isoformat()),
                        "link": e.get("link", "#"),
                    })
        except Exception:
            pass

    # 3. Social sentiment — StockTwits + Reddit (10 min cache)
    try:
        from market_agent.research.social_scraper import social_scraper
        st_data = social_scraper.get_stocktwits_sentiment(symbol)
        if st_data.get("mood") != "NEUTRAL":
            additional_news.append({
                "title": f"StockTwits community sentiment: {st_data.get('mood', 'NEUTRAL')}",
                "source": "StockTwits",
                "sentiment_score": st_data.get("sentiment", 0.5),
                "impact_score": 0.3,
                "published": datetime.now().isoformat(),
                "link": f"https://stocktwits.com/symbol/{symbol.replace('-USD', '.X')}",
            })
    except Exception:
        pass

    # Save critical news (high impact) to DB as corporate actions for archival
    for n in additional_news:
        if n.get("impact_score", 0) >= 0.7:
            try:
                storage.store_corporate_action(
                    symbol=symbol,
                    action_type="HIGH_IMPACT_NEWS",
                    action_date=datetime.now(),
                    description=n.get("title", "")[:500],
                    value=n.get("sentiment_score"),
                    impact_score=n.get("impact_score"),
                    source=n.get("source", "Unknown"),
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
# 5. RESEARCH — Fundamentals collection (daily, multi-source)
# ═══════════════════════════════════════════════════════════

_last_fundamentals_fetch: Dict[str, datetime] = {}

def _fetch_fundamentals_if_stale(symbol: str, storage: PostgresStorage):
    """
    Fetch fundamentals from multiple sources.
    Only runs once per day per symbol to avoid rate limits.
    Each source has its own limits — we use them independently.
    """
    global _last_fundamentals_fetch

    # Skip non-equity symbols (indices, forex, crypto)
    if symbol not in EQUITY_SYMBOLS:
        return

    # Check if already fetched today
    last = _last_fundamentals_fetch.get(symbol)
    if last and (datetime.now() - last).total_seconds() < 86400:  # 24 hours
        return

    fundamentals = {}

    # Source 1: yfinance .info (works for all equities)
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        fundamentals.update({
            "market_cap": info.get("marketCap"),
            "revenue": info.get("totalRevenue"),
            "net_profit": info.get("netIncomeToCommon"),
            "eps": info.get("trailingEps"),
            "pe_ratio": info.get("trailingPE"),
            "book_value": info.get("bookValue"),
            "total_debt": info.get("totalDebt"),
            "total_cash": info.get("totalCash"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "free_cash_flow": info.get("freeCashflow"),
        })
        print(f"    Fundamentals (yfinance): market_cap={info.get('marketCap', 'N/A')}")
    except Exception as e:
        logger.debug("yfinance_fundamentals_failed", symbol=symbol, error=str(e))

    # Source 2: Screener.in for Indian stocks (FREE, comprehensive)
    if ".NS" in symbol:
        try:
            from market_agent.data.scrapers.screener_scraper import ScreenerScraper
            screener = ScreenerScraper()
            screen_data = screener.fetch(symbol)
            if not screen_data.get("error"):
                # Merge screener data (fills gaps from yfinance)
                shareholding = screen_data.get("shareholding", {})
                if shareholding:
                    fundamentals["promoter_holding"] = shareholding.get("promoter_holding")
                    fundamentals["fii_holding"] = shareholding.get("fii_holding")
                    fundamentals["dii_holding"] = shareholding.get("dii_holding")
                    fundamentals["public_holding"] = shareholding.get("public_holding")
                print(f"    Fundamentals (screener.in): shareholding loaded")
        except Exception as e:
            logger.debug("screener_fundamentals_failed", symbol=symbol, error=str(e))

    # Source 3: NSE API for shareholding (backup, official source)
    if ".NS" in symbol and not fundamentals.get("promoter_holding"):
        try:
            from market_agent.data.scrapers.nse_scraper import NSEScraper
            nse = NSEScraper()
            nse_data = nse.fetch(symbol.replace(".NS", ""))
            shareholding = nse_data.get("shareholding", {})
            if shareholding:
                fundamentals["promoter_holding"] = shareholding.get("promoter")
                fundamentals["fii_holding"] = shareholding.get("fii")
                fundamentals["dii_holding"] = shareholding.get("dii")
                fundamentals["public_holding"] = shareholding.get("public")
                print(f"    Fundamentals (NSE): shareholding loaded")
        except Exception as e:
            logger.debug("nse_fundamentals_failed", symbol=symbol, error=str(e))

    # Store if we got anything useful
    if any(v is not None for v in fundamentals.values()):
        try:
            storage.store_fundamentals(symbol=symbol, data_dict=fundamentals, source="multi-source")
            _last_fundamentals_fetch[symbol] = datetime.now()
            print(f"    Fundamentals stored for {symbol}")
        except Exception as e:
            logger.error("fundamentals_storage_failed", symbol=symbol, error=str(e))


# ═══════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

def run_watchlist_scan():
    """
    Main entry: scan all symbols in WATCHLIST once.
    WIRED FOR BRAIN ACTIVATION PHASE 3 (Neural + Hybrid + Safety)
    """
    # 0. Load Models Components
    neural_model = None
    preprocessor = None
    causal_ensemble = None 
    rl_agent = None

    try:
        from market_agent.config import NEURAL_MODELS, MODELS_DIR
        from market_agent.models.micro_price_nn import MicroPriceNN
        from market_agent.models.preprocessing import DataPreprocessor
        from market_agent.models.causal_rl_models import RLEnsembleWeighter
        import torch
        import os
        import json
        
        preprocessor = DataPreprocessor()
        
        # Load Neural Brain (LSTM)
        if os.path.exists(NEURAL_MODELS["MICRO_PRICE"]):
            neural_model = MicroPriceNN(input_size=7) 
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            neural_model.load_state_dict(torch.load(NEURAL_MODELS["MICRO_PRICE"], map_location=device))
            neural_model.to(device)
            neural_model.eval()
            print("  [BRAIN] Neural Model Loaded (MicroPriceNN)")
        else:
             print(f"  [BRAIN] Model checkpoint not found at {NEURAL_MODELS['MICRO_PRICE']}")

        # Load Causal Graph
        causal_graph_path = os.path.join(MODELS_DIR, "causal_graph.json")
        if os.path.exists(causal_graph_path):
            with open(causal_graph_path, 'r') as f:
                graph_data = json.load(f)
            # Simple wrapper to query the graph
            class CausalGraphWrapper:
                def __init__(self, data): self.data = data
                def get_impact(self, symbol, active_signals):
                    boost = 0.0
                    if symbol not in self.data: return 0.0
                    # Check if any *causes* of this symbol have active signals
                    # e.g. If NVDA causes AMD, and NVDA has BUY signal -> Boost AMD
                    for lead_symbol, relation in self.data.items():
                        if symbol in relation: # lead_symbol -> symbol
                            # Check if lead_symbol is active
                            sig = active_signals.get(lead_symbol)
                            if sig and sig['direction'] == 'BUY':
                                boost += 0.05 * relation[symbol]['strength']
                            elif sig and sig['direction'] == 'SELL':
                                boost -= 0.05 * relation[symbol]['strength']
                    return boost
            
            causal_ensemble = CausalGraphWrapper(graph_data)
            print(f"  [BRAIN] Causal Graph Loaded ({len(graph_data)} nodes)")

        # Load RL Agent
        rl_path = os.path.join(MODELS_DIR, "rl_agent_v1.pth")
        if os.path.exists(rl_path):
            rl_agent = RLEnsembleWeighter(num_models=3, context_size=5) # Matches train_rl.py
            rl_agent.load_state_dict(torch.load(rl_path, map_location=device))
            rl_agent.eval()
            print("  [BRAIN] RL Agent Loaded (Policy Gradient)")
            
    except Exception as e:
        logger.error("model_load_failed", error=str(e))

    storage = PostgresStorage()
    regret = RegretEngine(storage)
    resolver = regret.signal_resolver or SignalResolver(storage)
    engine = SignalEngine()

    # Create cortex ONCE per scan cycle — reused across learning + debate triggers
    try:
        from market_agent.brain.cortex import CortexGatekeeper
        cortex = CortexGatekeeper()
        print("  [COUNCIL] Cortex initialized (shared instance for this cycle)")
    except Exception as e:
        cortex = None
        logger.error("cortex_init_failed", error=str(e))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now}] === WATCHLIST SCAN START ({len(WATCHLIST)} symbols) ===")

    # Market hours check
    try:
        from market_agent.utils.market_utils import should_scan, check_anomaly
    except ImportError:
        should_scan = lambda s: True
        check_anomaly = lambda d, a=0: {"should_suppress": False}

    signals_stored = 0
    skipped_hours = 0
    
    from market_agent.config import USE_PATH_A_SEVEN_BRAINS

    for symbol in WATCHLIST:
        try:
            # ── Hard market-hours guard: skip EVERYTHING for closed markets ──
            # This is position 0 — before data fetching, news, fundamentals, analyst.
            # Crypto (-USD), FX (=X), Commodities (=F) are 24/7: always pass.
            # NSE (.NS, ^NSE): 09:15-15:30 IST Mon-Fri only.
            # US stocks (no suffix): 09:30-16:00 ET Mon-Fri approx.
            if not _is_market_open(symbol):
                skipped_hours += 1
                continue  # Zero API calls for closed markets

            # Path A: 7 brain generators → store 7 real signals (no single-signal duplication)
            if USE_PATH_A_SEVEN_BRAINS and storage:
                path_a_hist = None
                try:
                    import pandas as pd
                    path_a_hist = _fetch_hybrid_data(symbol, storage)
                    if path_a_hist is not None and not path_a_hist.empty:
                        current_price = float(path_a_hist["Close"].iloc[-1])
                        rt = _get_fresh_price(symbol)
                        if rt and rt > 0:
                            current_price = rt
                        if current_price <= 0:
                            path_a_hist = None
                            raise ValueError("invalid price")
                        atr = path_a_hist["High"].sub(path_a_hist["Low"]).rolling(14).mean().iloc[-1]
                        if pd.isna(atr) or atr <= 0:
                            atr = current_price * 0.01
                        tech_analysis = ta_engine.full_analysis(path_a_hist) if hasattr(ta_engine, "full_analysis") else {}
                        from market_agent.brain.signal_generators import generate_brain_signals
                        brain_sigs = generate_brain_signals(
                            symbol, current_price, atr, path_a_hist, tech_analysis, regime="PATH_A"
                        )
                        for sig in brain_sigs:
                            if sig.get("direction") in ("WAIT", None):
                                continue
                            pred_id = resolver.store_signal(sig, strategy="Intraday (Scalp)")
                            if pred_id:
                                signals_stored += 1
                                print(f"  {symbol}: {sig['direction']} @ {sig.get('entry_price', 0):.2f} ({sig.get('model_used', '')})")

                        # ── Orders 12-14: Wire Sentiment Oracle, FII/DII, PCR into Path A ──
                        # These are uncorrelated alpha signals stored alongside the 7 technical brains.
                        # Must be wired BEFORE retrain so the training data includes them.
                        try:
                            from market_agent.brain.signal_generators import (
                                sentiment_oracle_signal, get_fii_dii_signal, get_pcr_signal
                            )
                            from market_agent.research.news_aggregator import news_aggregator

                            # Sentiment Oracle (Brain 8)
                            _news = news_aggregator.fetch_news(symbol, limit=10) if news_aggregator else []
                            _headlines = [(n.get("title") or "")[:100] for n in _news if n.get("title")]
                            _raw_sent = sum(n.get("sentiment_score", 0) for n in _news if n.get("sentiment_score") is not None)
                            _sent_score = max(-1.0, min(1.0, _raw_sent / max(len(_news), 1)))
                            sent_sig = sentiment_oracle_signal(_sent_score, _headlines)
                            if sent_sig.get("direction") not in ("HOLD", None):
                                from market_agent.signal_params import (
                                    BASE_ATR_T1_MULT, BASE_ATR_T2_MULT, BASE_ATR_SL_MULT,
                                )
                                _d = sent_sig["direction"]
                                _sign = 1 if _d == "BUY" else -1
                                _sig_dict = {
                                    "symbol": symbol, "direction": _d,
                                    "model_used": "Sentiment Oracle",
                                    "entry_price": current_price, "current_price": current_price,
                                    "target_1": current_price + _sign * atr * BASE_ATR_T1_MULT,
                                    "target_2": current_price + _sign * atr * BASE_ATR_T2_MULT,
                                    "stop_loss": current_price - _sign * atr * BASE_ATR_SL_MULT,
                                    "confidence": sent_sig.get("confidence", 0.5),
                                    "timeframe_min": 10,
                                }
                                pid = resolver.store_signal(_sig_dict, strategy="Intraday (Scalp)")
                                if pid:
                                    signals_stored += 1
                                    print(f"  {symbol}: Sentiment Oracle {_d} (score={_sent_score:.2f})")

                            # FII/DII signal — daily cache (once per day, not per cycle)
                            if ".NS" in symbol:
                                fii_sig = _get_cached_daily_signal(
                                    'fii_dii', get_fii_dii_signal
                                )
                                if fii_sig.get("direction") not in ("HOLD", "N/A", None):
                                    from market_agent.signal_params import (
                                        BASE_ATR_T1_MULT, BASE_ATR_T2_MULT, BASE_ATR_SL_MULT,
                                    )
                                    _d = fii_sig["direction"]
                                    _sign = 1 if _d == "BUY" else -1
                                    _sig_dict = {
                                        "symbol": symbol, "direction": _d,
                                        "model_used": "FII/DII Flow",
                                        "entry_price": current_price, "current_price": current_price,
                                        "target_1": current_price + _sign * atr * BASE_ATR_T1_MULT,
                                        "target_2": current_price + _sign * atr * BASE_ATR_T2_MULT,
                                        "stop_loss": current_price - _sign * atr * BASE_ATR_SL_MULT,
                                        "confidence": fii_sig.get("confidence", 0.5),
                                        "timeframe_min": 30,
                                    }
                                    pid = resolver.store_signal(_sig_dict, strategy="Intraday (Scalp)")
                                    if pid:
                                        signals_stored += 1
                                        print(f"  {symbol}: FII/DII {_d} ({fii_sig.get('evidence', '')})")

                                # PCR signal — daily cache (once per day, not per cycle)
                                _idx_sym = "BANKNIFTY" if "NSEBANK" in symbol else "NIFTY"
                                pcr_sig = _get_cached_daily_signal(
                                    f'pcr_{_idx_sym}',
                                    lambda s=_idx_sym: get_pcr_signal(s)
                                )
                                if pcr_sig.get("direction") not in ("HOLD", "N/A", None):
                                    from market_agent.signal_params import (
                                        BASE_ATR_T1_MULT, BASE_ATR_T2_MULT, BASE_ATR_SL_MULT,
                                    )
                                    _d = pcr_sig["direction"]
                                    _sign = 1 if _d == "BUY" else -1
                                    _sig_dict = {
                                        "symbol": symbol, "direction": _d,
                                        "model_used": "Options PCR",
                                        "entry_price": current_price, "current_price": current_price,
                                        "target_1": current_price + _sign * atr * BASE_ATR_T1_MULT,
                                        "target_2": current_price + _sign * atr * BASE_ATR_T2_MULT,
                                        "stop_loss": current_price - _sign * atr * BASE_ATR_SL_MULT,
                                        "confidence": pcr_sig.get("confidence", 0.5),
                                        "timeframe_min": 15,
                                    }
                                    pid = resolver.store_signal(_sig_dict, strategy="Intraday (Scalp)")
                                    if pid:
                                        signals_stored += 1
                                        print(f"  {symbol}: PCR {_d} ({pcr_sig.get('evidence', '')})")
                        except Exception as alpha_err:
                            logger.debug("alpha_signals_skipped", symbol=symbol, error=str(alpha_err)[:80])


                except Exception as path_a_err:
                    logger.warning("path_a_scan_failed", symbol=symbol, error=str(path_a_err))
                run_analyst_observation(symbol, storage, path_a_hist if path_a_hist is not None else yf.Ticker(symbol).history(period="5d", interval="1m"))
                _fetch_fundamentals_if_stale(symbol, storage)
                continue

            # Build Signal (Pass Models & Storage)
            sig = _build_intraday_signal_for_symbol(
                engine, 
                symbol, 
                storage=storage, 
                neural_model=neural_model, 
                preprocessor=preprocessor,
                causal_ensemble=causal_ensemble,
                rl_agent=rl_agent
            )
            
            if not sig or sig.get("direction") in ("WAIT", None):
                print(f"  {symbol}: no actionable signal")
                continue

            # Anomaly pre-filter: suppress signals on gap opens / price spikes
            anomaly = check_anomaly({
                "open": sig.get("entry_price", 0),
                "close": sig.get("current_price", 0),
            }, atr=sig.get("atr", 0))
            if anomaly.get("should_suppress"):
                print(f"  {symbol}: signal suppressed ({anomaly.get('details', 'anomaly')})")
                continue

            # Store prediction for LATER resolution (not now!)
            pred_id = resolver.store_signal(sig, strategy="Intraday (Scalp)")
            if pred_id:
                signals_stored += 1
                print(f"  {symbol}: {sig['direction']} @ {sig.get('entry_price', 0):.2f} "
                      f"(conf={sig['confidence']:.2f}, model={sig.get('model_used','Brain')})")
            else:
                print(f"  {symbol}: signal generated but storage returned None (validation failed)")

            # Run analyst observation (fetches & persists news)
            hist = yf.Ticker(symbol).history(period="5d", interval="1m")
            run_analyst_observation(symbol, storage, hist)

            # Fetch fundamentals (daily, respects per-source limits)
            _fetch_fundamentals_if_stale(symbol, storage)

        except Exception as e:
            print(f"  {symbol}: scan failed: {e}")

    if skipped_hours > 0:
        print(f"  [{skipped_hours} symbols skipped — market hours closed]")
    print(f"\n[{now}] Scan complete: {signals_stored} signals stored")

    # ── RESOLVE past signals with fresh prices ──
    print(f"\n[{now}] === RESOLVING PAST SIGNALS ===")
    resolved_count = resolve_past_signals(resolver, WATCHLIST)
    print(f"  Total resolved: {resolved_count}")

    # ── LEARN from resolved outcomes ──
    print(f"\n[{now}] === LEARNING CYCLE ===")
    run_learning_cycle(storage, resolver, engine, regret, cortex=cortex)

    # ── WEIGHT DECAY (once per day) ──
    try:
        from market_agent.utils.market_utils import apply_weight_decay_if_due
        apply_weight_decay_if_due()
    except Exception:
        pass

    # ── AUTO-TRIGGER DEBATES from Health Monitor ──
    try:
        from market_agent.brain.health_monitor import get_health_monitor
        
        # Use the shared cortex created above (not a fresh instance!)
        if cortex is None:
            from market_agent.brain.cortex import CortexGatekeeper
            cortex = CortexGatekeeper()
        monitor = get_health_monitor()
        
        # Generate debate triggers: pass brain_directions per symbol so "brain disagreement" can fire
        triggers = []
        for sym in WATCHLIST:
            brain_directions = resolver.get_latest_directions_per_model(sym) if resolver else {}
            sym_triggers = monitor.generate_debate_triggers(symbol=sym, brain_directions=brain_directions or None)
            for t in sym_triggers:
                if isinstance(t, dict) and t.get("symbol") is None:
                    t["symbol"] = sym
            triggers.extend(sym_triggers)
        queued = 0
        for trigger in triggers:
            added = cortex.queue_debate(
                topic=trigger.get("topic", "Performance review"),
                symbol=trigger.get("symbol", ""),
                trigger_type=trigger.get("trigger_type", "health_monitor"),
                market_metrics={},
                urgency=trigger.get("urgency", "MEDIUM")
            )
            if added:
                queued += 1
        
        if queued > 0:
            print(f"  [COUNCIL] {queued} debate(s) auto-queued from health monitor")
        
        # Process one queued debate per cycle
        verdict = cortex.process_debate_queue(regret_engine=regret)
        if verdict:
            print(f"  [COUNCIL] Debate verdict: {verdict}")
    except Exception as e:
        logger.error("auto_debate_trigger_failed", error=str(e))

    # ── AUTO-SCHEDULED TRAINING ──
    try:
        from market_agent.training.training_scheduler import get_training_scheduler
        ts = get_training_scheduler()

        # Post-market daily training
        if ts.should_post_market_train():
            print(f"\n[{now}] === POST-MARKET TRAINING ===")
            train_result = ts.post_market_train(list(WATCHLIST))
            print(f"  Training: {train_result.get('status', 'unknown')}")

        # Weekend deep training
        elif ts.should_weekend_train():
            print(f"\n[{now}] === WEEKEND DEEP TRAINING ===")
            train_result = ts.weekend_deep_train(list(WATCHLIST))
            print(f"  Deep training: {train_result.get('status', 'unknown')}")
    except Exception as e:
        logger.error("scheduled_training_failed", error=str(e)[:100])

    print(f"\n[{now}] === SCAN + RESOLVE + LEARN + COUNCIL + TRAIN COMPLETE ===\n")

if __name__ == "__main__":
    run_watchlist_scan()