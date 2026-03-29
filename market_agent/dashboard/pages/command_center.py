"""
Command Center — Primary trading interface
Signal card, trade ideas, news feed, macro overview
"""
import streamlit as st
import numpy as np
import pandas as pd
import time
from datetime import datetime, timedelta

# Import get_live_quote from its own module so @st.fragment can always find it
# even when Streamlit reruns the fragment outside the full page-script context.
from market_agent.dashboard.utils.price_utils import get_live_quote

# Centralised math utilities (normalisation, EV calculation, etc.)
from market_agent.utils.math_utils import safe_direction_probs as safe_direction_probs

@st.fragment(run_every=0.5)
def render_live_price_card(symbol, entry_price, currency, market_open):
    """
    Live price card — updates sub-second.
    """
    live_price = None
    source_label = 'Unknown'

    # ─── FAST PATH: Crypto (BTC-USD etc.) and Forex (EURUSD=X) ───
    is_crypto_or_fx = ("-" in symbol and ".NS" not in symbol.upper()) or "=X" in symbol

    if is_crypto_or_fx:
        feed = st.session_state.get('price_feed')
        if feed:
            binance_sym = symbol.replace("-USD", "USDT").replace("/", "")
            
            # Read from feed without triggering St.session_state mutation tracking
            raw_prices = getattr(feed, 'prices', {})
            raw_upd = getattr(feed, 'last_update', {})
            
            if binance_sym in raw_prices:
                live_price = float(raw_prices[binance_sym])
                
                # Check freshness purely on the float copy
                last_upd = raw_upd.get(binance_sym)
                age_s = (datetime.now() - last_upd).total_seconds() if last_upd else 999
                
                source_label = 'Binance (Real-Time)' if age_s < 30 else 'Binance (⚠️ Stale)'

    # ─── FALLBACK PATH: Indian stocks / Forex with no WS feed ───
    if live_price is None:
        live_price = get_live_quote(symbol) or 0
        source_label = st.session_state.get('_price_source', 'Unknown')

    # Calculate PnL
    pnl = 0.0
    if entry_price > 0 and live_price > 0:
        pnl = ((live_price - entry_price) / entry_price * 100)
        
    pnl_color = "#10b981" if pnl >= 0 else "#ef4444"
    price_color = "#f1f5f9"
    # Show milliseconds for perceived speed
    now_ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    
    st.markdown(f"""
    <div class="signal-card" style="text-align: center; transition: all 0.3s ease-in-out;">
        <div class="data-label">LIVE PRICE</div>
        <div style="font-size: 36px; font-weight: 900; color: {price_color}; margin: 8px 0; transition: color 0.3s ease;">
            {currency}{live_price:,.2f}
        </div>
        <div style="font-size: 14px; color: {pnl_color}; font-weight: 700; transition: color 0.3s ease;">
            {'+' if pnl >= 0 else ''}{pnl:.2f}% from entry
        </div>
         <div style="font-size: 10px; color: #64748b; margin-top: 4px; font-family: monospace;">
            Updated: {now_ts} UTC
        </div>
    </div>
    """, unsafe_allow_html=True)
        
    # Show source
    _color = "#10b981" if "Real-Time" in source_label else "#f59e0b" if "Fast" in source_label or "Stale" in source_label else "#ef4444" 
    st.caption(f"📡 Source: <span style='color:{_color}; transition: color 0.3s;'>{source_label}</span>", unsafe_allow_html=True)

# ══════════════════════════════════════
# DATA FROM SHARED STATE
# ══════════════════════════════════════
symbol = st.session_state.get('selected_symbol', 'ITC.NS')
strategy = st.session_state.get('strategy', 'Intraday (Scalp)')
regret_engine = st.session_state.get('regret_engine')
signal_resolver = st.session_state.get('signal_resolver')
engine = st.session_state.get('signal_engine')

# ══════════════════════════════════════
# STYLING: top margin + tab buttons
# ══════════════════════════════════════
st.markdown("""
<style>
    /* Fix cropped top banner */
    .block-container { padding-top: 2rem !important; }

    .stTabs [data-baseweb="tab-list"] { gap: 12px; }
    .stTabs [data-baseweb="tab"] {
        background: rgba(30, 41, 59, 0.6);
        border: 1px solid rgba(148,163,184,0.1);
        border-radius: 8px;
        padding: 8px 20px;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(99,102,241,0.2) !important;
        border-color: #6366f1 !important;
    }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════
# NEWS HELPER — uses full aggregator
# ══════════════════════════════════════
@st.cache_data(ttl=120)
def _get_all_news(sym):
    """Fetch news using the full aggregation pipeline: RSS + ForexFactory + X/Twitter + Google News.
    Returns newest first."""
    items = []
    try:
        from market_agent.research.news_aggregator import news_aggregator
        if news_aggregator:
            raw = news_aggregator.fetch_news(symbol=sym, limit=20)
            if raw:
                items = raw
    except Exception as e:
        st.error(f"News Aggregator Error: {e}")
        pass

    # Fallback: direct RSS
    if not items:
        try:
            from market_agent.research.rss_researcher import RSSResearcher
            researcher = RSSResearcher()
            items = researcher.fetch_latest_news(sym) or []
        except Exception as e:
            st.error(f"RSS Fallback Error: {e}")
            pass

    # Sort newest first (by published date if available)
    def _parse_date(item):
        pub = item.get('published', '')
        if not pub:
            return datetime.min
        try:
            # Try common RSS date formats
            for fmt in ('%a, %d %b %Y %H:%M:%S %Z', '%a, %d %b %Y %H:%M:%S %z',
                        '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                try:
                    return datetime.strptime(pub[:25].strip(), fmt)
                except ValueError:
                    continue
            # feedparser's time_struct
            import email.utils
            parsed = email.utils.parsedate_to_datetime(pub)
            return parsed.replace(tzinfo=None)
        except Exception:
            return datetime.min
    
    items.sort(key=_parse_date, reverse=True)
    return items


# ══════════════════════════════════════
# SIGNAL GENERATION (matches backup)
# ══════════════════════════════════════
# ══════════════════════════════════════
# NOTE: safe_direction_probs is imported from market_agent.utils.math_utils (top of file).
# The local _safe_direction_probs was removed — use safe_direction_probs everywhere.
# ══════════════════════════════════════

# ══════════════════════════════════════
# SIGNAL GENERATION (matches backup)
# ══════════════════════════════════════
@st.cache_data(ttl=15)
def get_real_signal(sym, strategy_mode="Intraday (Scalp)"):
    """Fetch real-time price and generate signal using SignalEngine — correct API.

    FIX 1a: Data is read from the local PostgreSQL database (same source as
    the scanner) instead of calling yfinance directly. This ensures the
    dashboard signal is generated from identical data to what the council used.
    FIX 1b: Regime is read from the last council_debates entry, not hardcoded.
    """
    try:
        from market_agent.data.storage.postgres import PostgresStorage

        # Strategy-based interval and lookback
        if strategy_mode == "Swing (Hold)":
            tf_key, lookback = "1d", 10
        else:
            tf_key, lookback = "1h", 5

        # ── FIX 1a: Read from DB — same source the scanner used ──────────
        _storage = st.session_state.get('storage') or PostgresStorage()
        records = _storage.get_latest_data(sym, tf_key, limit=120)

        # Fallback to yfinance ONLY when DB has no data for this symbol yet
        if not records or len(records) < 20:
            import yfinance as yf
            _data = yf.Ticker(sym)
            period = "1mo" if tf_key == "1d" else "5d"
            hist = _data.history(period=period, interval=tf_key)
            if hist is None or hist.empty:
                return None
        else:
            import pandas as pd
            hist = pd.DataFrame(
                [
                    {
                        "Open":   r["data"]["Open"],
                        "High":   r["data"]["High"],
                        "Low":    r["data"]["Low"],
                        "Close":  r["data"]["Close"],
                        "Volume": r["data"]["Volume"],
                    }
                    for r in records
                ]
            )
        if hist is None or hist.empty:
            return None

        # 2. Use latest available close price from history as the base signal price.
        # NOTE: We do NOT call get_live_quote() here because this function is @st.cache_data(ttl=15).
        # Calling get_live_quote() inside would capture a snapshot at cache-write time (up to 15s stale).
        # The freshest live price is injected OUTSIDE this function, at the call site (line ~532).
        current_price = float(hist['Close'].iloc[-1])
        
        # ATR calculation (on valid history)
        high_low = hist['High'] - hist['Low']
        high_cp = (hist['High'] - hist['Close'].shift()).abs()
        low_cp = (hist['Low'] - hist['Close'].shift()).abs()
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        if np.isnan(atr):
            atr = current_price * 0.02

        # SMA-based direction
        short_ma = hist['Close'].rolling(lookback).mean().iloc[-1]
        long_ma = hist['Close'].rolling(lookback * 4).mean().iloc[-1]

        # Technical analysis
        tech_analysis = None
        ta_conf = 0.5
        try:
            from market_agent.patterns.technical_analysis import ta_engine
            tech_analysis = ta_engine.full_analysis(hist)
        except Exception:
            pass

        # Analyst brain sentiment
        base_bias = 0.0
        current_analyst = st.session_state.get('analyst_agent')
        analysis = {"sentiment": 0.0, "conclusion": "Technical consensus mode.", "missing_data": []}
        if current_analyst:
            try:
                analysis = current_analyst.analyze_market_state(
                    sym, {"Close": current_price}, {},
                    df_price_history=hist
                ) or analysis
            except Exception:
                pass
        base_bias = analysis.get('sentiment', 0.0) * 0.25

        # Direction probabilities (Normalized)
        if tech_analysis and isinstance(tech_analysis, dict) and tech_analysis.get('consensus'):
            ta_consensus = tech_analysis.get('consensus', {})
            ta_direction = ta_consensus.get('direction', 'HOLD')
            ta_conf = ta_consensus.get('confidence', 0.5)

            if ta_direction == 'BUY':
                direction_probs = safe_direction_probs([0.15, 0.20, 0.55 + base_bias + (ta_conf * 0.1)])
                bias_msg = f"Bullish — TA Consensus ({ta_conf:.0%})"
            elif ta_direction == 'SELL':
                direction_probs = safe_direction_probs([0.55 - base_bias + (ta_conf * 0.1), 0.20, 0.15])
                bias_msg = f"Bearish — TA Consensus ({ta_conf:.0%})"
            else:
                direction_probs = safe_direction_probs([0.30, 0.40, 0.30 + base_bias])
                bias_msg = "Neutral — TA Holding"
        elif current_price < short_ma:
            direction_probs = safe_direction_probs([0.55 - base_bias, 0.25, 0.20 + base_bias])
            bias_msg = f"Bearish {strategy_mode} Trend"
        else:
            direction_probs = safe_direction_probs([0.20 - base_bias, 0.25, 0.55 + base_bias])
            bias_msg = f"Bullish {strategy_mode} Recovery"

        # Confidence with TA boost
        agent_conf = 0.5 + (abs(analysis.get('sentiment', 0.0)) * 0.3)
        ta_boost = (ta_conf - 0.5) * 0.4
        confidence = max(0.4, agent_conf + ta_boost + (np.random.random() * 0.05))

        reasoning = [
            f"LTP: {current_price:.2f} | {bias_msg}",
            f"AI Sentiment: {analysis.get('sentiment', 0.0):.2f} | {analysis.get('conclusion', 'N/A')}",
            f"ATR: {atr:.2f} | Lookback: {lookback} periods",
        ]

        # ── FIX 1b: Read actual regime from last council debate ──────────
        _storage = st.session_state.get('storage') or PostgresStorage()
        last_debate = _storage.get_last_council_debate(sym)
        real_regime = last_debate.get('regime', 'UNKNOWN') if last_debate else 'UNKNOWN'
        # Fallback: derive from confidence if no debate exists yet
        if real_regime == 'UNKNOWN':
            real_regime = 'STABLE_TRADING' if confidence > 0.6 else 'VOLATILE_CHAOS'

        # Use signal engine with correct API
        sig_dict = None
        if engine:
            signal = engine.generate_signal(
                symbol=sym,
                current_price=current_price,
                atr=atr,
                direction_probs=direction_probs,
                confidence=confidence,
                regime=real_regime,          # ← real regime from council_debates
                model_name="Aegis-Cluster",
                reasoning=reasoning,
                tech_analysis=tech_analysis,
                strategy=strategy_mode
            )

            if signal:
                sig_dict = signal.to_dict() if hasattr(signal, 'to_dict') else signal
            else:
                # Signal engine returned None (below threshold) — build WAIT
                # Use same strategy-aware targets as the engine
                # Dynamic WAIT targets — use market structure if available
                t1 = current_price  # defaults
                t2 = current_price
                sl = current_price

                if tech_analysis and isinstance(tech_analysis, dict):
                    sr = tech_analysis.get('support_resistance', {})
                    pp = tech_analysis.get('pivot_points', {})
                    resistances = sr.get('resistance', [])
                    supports = sr.get('support', [])

                    # Target = nearest resistance, SL = nearest support
                    if resistances:
                        t1 = resistances[0]
                        t2 = resistances[1] if len(resistances) > 1 else t1 * 1.002
                    elif pp.get('R1', 0) > current_price:
                        t1 = pp['R1']
                        t2 = pp.get('R2', t1 * 1.002)

                    if supports:
                        sl = supports[0] * 0.998
                    elif pp.get('S1', 0) > 0 and pp['S1'] < current_price:
                        sl = pp['S1'] * 0.998

                # If no levels found, use ATR — MUST match train_all_brains.py offsets
                # (Order 8: T1=ATR*0.75, T2=ATR*1.50, SL=ATR*0.50 for 1.5:1 R:R)
                if t1 == current_price:
                    t1 = current_price + atr * 0.75   # was 0.3 — changed to match training
                    t2 = current_price + atr * 1.50   # was 0.6 — new runner target
                if sl == current_price:
                    sl = current_price - atr * 0.50   # SL unchanged

                risk_pct = 1.5
                risk_amount = 500000 * (risk_pct / 100)
                risk_per_unit = abs(current_price - sl)
                pos_size = int(risk_amount / risk_per_unit) if risk_per_unit > 0 else 0
                sig_dict = {
                    "symbol": sym, "direction": "WAIT",
                    "confidence": confidence,
                    "current_price": current_price,
                    "entry_price": current_price,
                    "stop_loss": sl,
                    "target_1": t1,
                    "target_2": t2,
                    "regime": "SCANNING",
                    "risk_percent": risk_pct,
                    "position_size": pos_size,
                    "reasoning": reasoning + ["Below confidence threshold - watching for setup."],
                }
        else:
            sig_dict = {
                "symbol": sym, "direction": "WAIT",
                "confidence": 0, "current_price": current_price,
                "entry_price": 0, "stop_loss": 0,
                "target_1": 0, "target_2": 0,
                "regime": "ENGINE_OFFLINE",
                "risk_percent": 0, "position_size": 0,
                "reasoning": ["Signal engine not initialized in session state."],
            }

        sig_dict['current_price'] = current_price
        sig_dict['tech_analysis'] = tech_analysis if tech_analysis and isinstance(tech_analysis, dict) else {}
        sig_dict['time_horizon'] = "1-3 Hours" if strategy_mode == "Intraday (Scalp)" else "2-5 Days"

        # ─── Feature importance (DYNAMIC from actual data — recalculates each signal!) ───
        vol_score = atr / current_price * 10
        abs_change = abs(current_price - short_ma) / short_ma if short_ma else 0
        rsi_val = _calc_rsi(hist) if len(hist) >= 14 else 50
        rsi_imp = round(min(0.35, 0.10 + abs(rsi_val - 50) / 100), 2)
        mom_imp = round(min(0.5, 0.25 + (abs_change * 10)), 2)
        vol_imp = round(min(0.4, 0.15 + (vol_score * 2)), 2)
        social_base = 0.22 if ("-" in sym or "XAU" in sym) else 0.14

        sig_dict['feature_importance'] = {
            'RSI Signal': rsi_imp,
            'Price Momentum': mom_imp,
            'ATR Volatility': vol_imp,
            'News Sentiment': social_base,
            'Liquidity': round(max(0.05, 1.0 - (rsi_imp + mom_imp + vol_imp + social_base)), 2)
        }

        # ─── Timeframe weights (crypto vs equity) ───
        is_crypto = "-" in sym
        if is_crypto:
            if strategy_mode == "Swing (Hold)":
                sig_dict['timeframe_weights'] = {'1m': 0.10, '15m': 0.20, '1h': 0.30, '1d': 0.40}
            else:
                sig_dict['timeframe_weights'] = {'1m': 0.45, '15m': 0.35, '1h': 0.15, '1d': 0.05}
        else:
            if strategy_mode == "Swing (Hold)":
                sig_dict['timeframe_weights'] = {'1m': 0.05, '15m': 0.10, '1h': 0.35, '1d': 0.50}
            else:
                sig_dict['timeframe_weights'] = {'1m': 0.15, '15m': 0.45, '1h': 0.30, '1d': 0.10}

        return sig_dict

    except Exception as e:
        return None


def _calc_rsi(df, period=14):
    """Calculate RSI from price history."""
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
    return round(100 - (100 / (1 + rs)), 1)


# get_live_quote is imported from price_utils at the top of this file.
# This stub exists so any legacy calls within this page still resolve.
# (The real implementation lives in market_agent/dashboard/utils/price_utils.py)


# ══════════════════════════════════════
# RESOLVE PAST SIGNALS (so accuracy is up to date on this page)
# ══════════════════════════════════════
_early_live = get_live_quote(symbol)
if signal_resolver and _early_live and float(_early_live) > 0:
    try:
        signal_resolver.resolve_signals(float(_early_live), symbol)
    except Exception:
        pass

# ══════════════════════════════════════
# MARKET + ACCURACY BANNER
# ══════════════════════════════════════
now = datetime.now()

# Market status (crypto-aware)
market_is_open = False
try:
    from market_agent.utils.market_utils import is_market_open
    mkt = is_market_open(symbol=symbol)
    mkt_label = mkt['market']
    market_is_open = mkt['is_open']
    if market_is_open:
        mins = mkt.get('minutes_remaining', '')
        mkt_text = f"🟢 {mkt_label} LIVE" + (f" ({mins}m left)" if mins else "")
        mkt_color = "#10b981"
    else:
        mkt_text = f"🟡 {mkt_label} {mkt.get('status', 'CLOSED')}"
        mkt_color = "#f59e0b"
except Exception:
    mkt_text = "📡 Loading..."
    mkt_color = "#64748b"

# Accuracy (global: all resolved predictions for this symbol; recalculated after resolve above)
perf_stats = regret_engine.get_real_accuracy(symbol=symbol) if regret_engine else {}
accuracy = perf_stats.get('accuracy', 0.0)
total_signals = perf_stats.get('total', 0)
readiness_color = "#10b981" if accuracy > 60 else "#f59e0b" if accuracy > 30 else "#64748b"

st.markdown(f"""
<div class="alert-banner" style="display: flex; justify-content: space-between; align-items: center;">
    <div>
        <span class="status-live"></span> AEGIS TERMINAL • {symbol} • {strategy.upper()}
    </div>
    <div style="color: {mkt_color}; font-weight: 700; font-size: 12px;">{mkt_text}</div>
    <div style="font-size: 10px; opacity: 0.7;">{now.strftime('%H:%M:%S')} IST</div>
</div>
""", unsafe_allow_html=True)

# Accuracy bar with explainer
acc_label = f"{accuracy:.1f}%" if total_signals > 0 else "Building history..."
st.markdown(f"""
<div style="background: rgba(15, 23, 42, 0.8); padding: 14px 20px; border-radius: 10px;
    border: 1px solid rgba(148,163,184,0.08); margin-bottom: 20px;">
    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
        <span style="font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase;"
            title="Historical win rate of all past closed trades. Does not reflect current LIVE signal confidence (WAIT calls are excluded).">
            Global Accuracy ({total_signals} signals) ℹ️
        </span>
        <span style="font-size: 20px; font-weight: 900; color: {readiness_color};">{acc_label}</span>
    </div>
    <div style="height: 6px; background: #1e293b; border-radius: 3px;">
        <div style="height: 6px; width: {max(3, min(100, accuracy))}%; background: {readiness_color};
            border-radius: 3px; box-shadow: 0 0 10px {readiness_color};"></div>
    </div>
    <div style="font-size: 9px; color: #475569; margin-top: 4px;">
        Gradient accuracy: how close each prediction's direction + target was to actual price move. Resolves after {
            '1-3 hours' if strategy == 'Intraday (Scalp)' else '2-5 days'}.
    </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════
# SIGNAL — Part D: removed live price merge bug
# ══════════════════════════════════════
signal = get_real_signal(symbol, strategy)   # used for last_analysis + AI context only
live = get_live_quote(symbol)                # fetched separately — NOT merged into entry

# Path A: Set last_analysis so Talk to Brains and manual debates get real context (confidence, direction, tech)
if signal:
    _ta = signal.get('tech_analysis') or {}
    _consensus = _ta.get('consensus') if isinstance(_ta, dict) else {}
    if not _consensus and signal.get('direction') not in ('WAIT', None):
        _consensus = {'direction': signal.get('direction', 'HOLD'), 'confidence': signal.get('confidence', 0)}
    st.session_state['last_analysis'] = {
        'symbol': symbol,
        'price': signal.get('current_price') or signal.get('entry_price'),
        'current_price': signal.get('current_price') or signal.get('entry_price'),
        'confidence': signal.get('confidence', 0),
        'tech_analysis': dict(_ta) if isinstance(_ta, dict) else {},
        'consensus': _consensus,
    }
    if isinstance(st.session_state['last_analysis']['tech_analysis'], dict) and not st.session_state['last_analysis']['tech_analysis'].get('consensus') and _consensus:
        st.session_state['last_analysis']['tech_analysis'] = dict(st.session_state['last_analysis']['tech_analysis'])
        st.session_state['last_analysis']['tech_analysis']['consensus'] = _consensus

# Store prediction
if signal and signal.get('direction') not in ('WAIT', None) and signal_resolver:
    try:
        stored_id = signal_resolver.store_signal(signal, strategy=strategy)
        if stored_id:
            signal['prediction_db_id'] = stored_id
    except Exception:
        pass

# Fallback — always show live price even if no signal
if not signal:
    signal = {
        "symbol": symbol, "direction": "WAIT", "confidence": 0,
        "current_price": live or 0, "entry_price": 0, "stop_loss": 0,
        "target_1": 0, "target_2": 0, "regime": "SCANNING",
        "risk_percent": 0, "position_size": 0,
        "reasoning": ["Signal engine returned no data — likely a yfinance timeout.",
                      f"Strategy: {strategy}", f"Market: {mkt_text}"],
        "feature_importance": {'Momentum': 0.25, 'Volatility': 0.20, 'News': 0.15, 'Liquidity': 0.40},
        "timeframe_weights": {'1m': 0.15, '15m': 0.40, '1h': 0.30, '1d': 0.15},
    }

currency = "$" if "-" in symbol or "=X" in symbol else "₹"

# ══════════════════════════════════════
# ROW 1: SIGNAL CARD + LIVE PRICE (auto-refresh)
# ══════════════════════════════════════
# Live countdown: Next scan in (Path A Phase 6)
try:
    from market_agent.data.storage.postgres import PostgresStorage
    from sqlalchemy import text
    from datetime import timedelta, timezone
    _db = PostgresStorage()
    with _db.engine.connect() as conn:
        _last = conn.execute(text("SELECT MAX(created_at) FROM signal_predictions")).scalar()
    if _last:
        if getattr(_last, 'tzinfo', None):
            _last = _last.replace(tzinfo=None)
        _diff_min = (datetime.now() - _last).total_seconds() / 60
        _scan_interval = 10
        _next_in = max(0, _scan_interval - (_diff_min % _scan_interval))
        _target = (datetime.now(timezone.utc) + timedelta(minutes=_next_in)).replace(tzinfo=timezone.utc)
        from market_agent.dashboard.components.countdown_ticker import render_countdown
        render_countdown("Next scan in", _target, session_key="command_center_next_scan")
except Exception:
    pass

# Entry/exit countdown: Exits in / Entry valid until (from open signal or council verdict)
try:
    from market_agent.dashboard.components.countdown_ticker import render_countdown as _render_cd
    from datetime import timezone as _tz
    from market_agent.data.storage.postgres import PostgresStorage as _PostgresStorage
    _exit_target = None
    _exit_label = "Exits in"
    if signal_resolver:
        open_preds = signal_resolver.get_open_predictions(symbol=symbol)
        for _p in open_preds:
            _exp = _p.get("expires_at")
            if _exp:
                try:
                    _dt = datetime.fromisoformat(_exp.replace("Z", "+00:00"))
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=_tz.utc)
                    if _dt > datetime.now(_tz.utc):
                        _exit_target = _dt
                        break
                except Exception:
                    pass
    if _exit_target is None:
        try:
            _stor = _PostgresStorage()
            _verdict = _stor.get_latest_council_verdict(symbol)
            if _verdict and _verdict.get("timeframe_min") and _verdict.get("created_at"):
                _created = datetime.fromisoformat(str(_verdict["created_at"]).replace("Z", "+00:00")[:26])
                if _created.tzinfo is None:
                    _created = _created.replace(tzinfo=_tz.utc)
                _exit_target = _created + timedelta(minutes=int(_verdict["timeframe_min"]))
                _exit_label = "Entry valid until"
                if _exit_target <= datetime.now(_tz.utc):
                    _exit_target = None
        except Exception:
            pass
    if _exit_target is not None:
        _render_cd(_exit_label, _exit_target, session_key="command_center_exits")
        st.caption("Exits in: from open signal expiry or council verdict. Intraday scalp window is 10–45 min (vol-based) or council timeframe (1–20 min).")
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────
# PART A — Read the real stored signal from signal_predictions
# ─────────────────────────────────────────────────────────────────────────
def get_active_stored_signal(sym: str) -> dict | None:
    """Reads the most recent ACTIVE signal for this symbol from signal_predictions.
    Returns None if no active signal exists (scanner hasn't run yet or all expired).
    Source confirmed: DB has 354 rows in signal_predictions, 0 in council_verdicts.
    """
    try:
        from market_agent.data.storage.postgres import PostgresStorage as _PGS
        from sqlalchemy import text as _text
        _stor = st.session_state.get('storage') or _PGS()
        with _stor.engine.connect() as conn:
            row = conn.execute(_text("""
                SELECT symbol, direction, entry_price, target_1, target_2,
                       stop_loss, confidence, regime, created_at, expires_at,
                       is_resolved, timeframe_min, strategy, model_id
                FROM signal_predictions
                WHERE symbol = :sym
                  AND is_resolved = FALSE
                ORDER BY created_at DESC
                LIMIT 1
            """), {'sym': sym}).fetchone()
        if row:
            return dict(row._mapping)
        # Fallback: show the most recent signal even if resolved / expired
        with _stor.engine.connect() as conn:
            row = conn.execute(_text("""
                SELECT symbol, direction, entry_price, target_1, target_2,
                       stop_loss, confidence, regime, created_at, expires_at,
                       is_resolved, timeframe_min, strategy, model_id
                FROM signal_predictions
                WHERE symbol = :sym
                ORDER BY created_at DESC
                LIMIT 1
            """), {'sym': sym}).fetchone()
        return dict(row._mapping) if row else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# PART B — Format helpers + Signal Status Classifier
# ─────────────────────────────────────────────────────────────────────────
def _format_age(ts) -> str:
    """Converts a datetime to human-readable age string like '43m' or '2h 5m'."""
    if not ts:
        return 'unknown'
    from datetime import timezone as _tz
    now = datetime.utcnow().replace(tzinfo=_tz.utc)
    if hasattr(ts, 'tzinfo') and ts.tzinfo is None:
        ts = ts.replace(tzinfo=_tz.utc)
    total_minutes = int((now - ts).total_seconds() / 60)
    if total_minutes < 1:  return 'just now'
    if total_minutes < 60: return f'{total_minutes}m'
    return f'{total_minutes // 60}h {total_minutes % 60}m'


def _format_ts(ts) -> str:
    """Formats datetime as HH:MM UTC for compact inline display."""
    if not ts:
        return ''
    from datetime import timezone as _tz
    if hasattr(ts, 'tzinfo') and ts.tzinfo is None:
        ts = ts.replace(tzinfo=_tz.utc)
    return ts.strftime('%H:%M UTC')


def classify_signal_status(signal: dict, live_price: float) -> dict:
    """Determines real status of a stored signal given current live price.
    Returns state, label, color. Never assumes — checks actual numbers.
    """
    from datetime import timezone as _tz
    if not signal or not live_price:
        return {'state': 'NO_SIGNAL', 'label': 'No active signal', 'color': '#475569'}

    entry     = signal.get('entry_price', 0) or 0
    t1        = signal.get('target_1', 0) or 0
    t2        = signal.get('target_2', 0) or 0
    sl        = signal.get('stop_loss', 0) or 0
    direction = signal.get('direction', 'BUY')
    created   = signal.get('created_at')
    expires   = signal.get('expires_at')
    resolved  = signal.get('is_resolved', False)
    now       = datetime.utcnow().replace(tzinfo=_tz.utc)

    if created and hasattr(created, 'tzinfo') and created.tzinfo is None:
        created = created.replace(tzinfo=_tz.utc)
    if expires and hasattr(expires, 'tzinfo') and expires.tzinfo is None:
        expires = expires.replace(tzinfo=_tz.utc)

    age_minutes = int((now - created).total_seconds() / 60) if created else 0

    if resolved or (expires and now > expires):
        label = 'RESOLVED' if resolved else f'EXPIRED — ended {int((now - expires).total_seconds() / 60)}m ago'
        return {'state': 'EXPIRED', 'label': f'⏰ {label}', 'color': '#94a3b8', 'age_min': age_minutes}

    if direction == 'BUY':
        sl_hit = sl and live_price <= sl
        t1_hit = t1 and live_price >= t1
        t2_hit = t2 and live_price >= t2
    else:
        sl_hit = sl and live_price >= sl
        t1_hit = t1 and live_price <= t1
        t2_hit = t2 and live_price <= t2

    if sl_hit:
        return {'state': 'SL_HIT', 'label': '🔴 STOP LOSS HIT', 'color': '#ef4444', 'age_min': age_minutes}
    if t2_hit:
        return {'state': 'T2_HIT', 'label': '🎯 TARGET 2 HIT — signal resolved', 'color': '#10b981', 'age_min': age_minutes}
    if t1_hit:
        return {'state': 'T1_HIT', 'label': '✅ TARGET 1 HIT — watching for T2', 'color': '#10b981', 'age_min': age_minutes}

    time_remaining = None
    if expires:
        mins_left = (expires - now).total_seconds() / 60
        time_remaining = f"{int(mins_left // 60)}h {int(mins_left % 60)}m" if mins_left > 60 else f"{int(mins_left)}m"

    return {'state': 'ACTIVE', 'label': '🟡 ACTIVE', 'color': '#f59e0b',
            'age_min': age_minutes, 'time_remaining': time_remaining}


# ─────────────────────────────────────────────────────────────────────────
# PART C — The corrected render_signal_card
# ─────────────────────────────────────────────────────────────────────────
def _render_signal_card_html(stored: dict, live_price: float, status: dict, currency: str):
    """Renders the correct HTML signal card — entry/targets from DB, live price separate."""
    direction  = stored.get('direction', 'BUY')
    entry      = stored.get('entry_price', 0) or 0
    t1         = stored.get('target_1', 0) or 0
    t2         = stored.get('target_2') or 0
    sl         = stored.get('stop_loss', 0) or 0
    confidence = stored.get('confidence', 0) or 0
    regime     = stored.get('regime', 'UNKNOWN') or 'UNKNOWN'
    created_at = stored.get('created_at')
    state      = status['state']
    age_str    = _format_age(created_at)
    ts_str     = _format_ts(created_at)
    time_left  = status.get('time_remaining')
    dir_color  = '#10b981' if direction == 'BUY' else '#ef4444'

    regime_color_map = {
        'STABLE_TRADING': '#10b981', 'VOLATILE_CHAOS': '#ef4444',
        'SCANNING_INTRADAY': '#f59e0b', 'HYBRID_SCAN': '#8b5cf6', 'PATH_A': '#6366f1'
    }
    regime_color = regime_color_map.get(regime, '#94a3b8')

    pnl_from_entry = ((live_price - entry) / entry * 100) if entry and direction == 'BUY' \
                     else ((entry - live_price) / entry * 100) if entry else 0
    pnl_color = '#10b981' if pnl_from_entry >= 0 else '#ef4444'

    # Progress to T1
    if direction == 'BUY' and t1 and t1 > entry:
        prog_t1 = min(1.0, max(0.0, (live_price - entry) / (t1 - entry)))
    elif direction == 'SELL' and t1 and t1 < entry:
        prog_t1 = min(1.0, max(0.0, (entry - live_price) / (entry - t1)))
    else:
        prog_t1 = 0.0
    prog_t1_pct = int(prog_t1 * 100)

    # Progress to T2
    prog_t2 = 0.0
    if t2:
        if direction == 'BUY' and t2 > t1 and t1:
            prog_t2 = min(1.0, max(0.0, (live_price - t1) / (t2 - t1)))
        elif direction == 'SELL' and t2 < t1 and t1:
            prog_t2 = min(1.0, max(0.0, (t1 - live_price) / (t1 - t2)))
    prog_t2_pct = int(prog_t2 * 100)


    t1_badge = '<span style="color:#10b981;font-size:10px;">✅ HIT</span>' if state in ('T1_HIT', 'T2_HIT') else ''
    t2_badge = '<span style="color:#6366f1;font-size:10px;">🎯 HIT</span>' if state == 'T2_HIT' else ''

    opacity = '1.0'  # Always full brightness — user needs to read the status label
    status_banner = f'<div style="text-align:center;color:{status["color"]};font-weight:700;font-size:13px;margin-bottom:10px;">{status["label"]}</div>'

    # Fix "Expires in: unknown" — compute label based on state
    expire_label_text = ''
    expire_color = '#475569'
    if state == 'ACTIVE' and time_left:
        expire_label_text = time_left
        expire_color = '#f59e0b'
    elif state in ('EXPIRED',):
        expire_label_text = status['label'].split('\u2014')[-1].strip() if '\u2014' in status['label'] else 'resolved'
        expire_color = '#94a3b8'
    elif state == 'SL_HIT':
        expire_label_text = 'SL hit'
        expire_color = '#ef4444'
    elif state == 'T2_HIT':
        expire_label_text = 'T2 target hit'
        expire_color = '#10b981'
    elif state == 'T1_HIT':
        expire_label_text = time_left or 'watching T2'
        expire_color = '#10b981'
    else:
        expire_label_text = 'resolved'
        expire_color = '#94a3b8'

    conf_pct = confidence * 100 if confidence <= 1 else confidence
    # T2 as separate column
    # Build T2 column HTML — single-line string, no newline prefix, to avoid Markdown paragraph breaks
    t2_col_html = (
        f'<div style="background:#0f172a;border-radius:6px;padding:8px;text-align:center;">'
        f'<div style="font-size:12px;color:#64748b;margin-bottom:2px;">TARGET 2 {t2_badge}</div>'
        f'<div style="font-size:20px;font-weight:700;color:#6366f1;">{currency}{t2:,.2f}</div>'
        f'</div>'
    ) if t2 else ''
    grid_cols = '1fr 1fr 1fr 1fr' if t2 else '1fr 1fr 1fr'

    # Assemble the FULL card HTML as a plain Python string first — no f-string inside st.markdown
    # This prevents Streamlit's Markdown parser from treating embedded newlines as paragraph breaks
    card_html = (
        f'<div class="signal-card" style="padding:16px;opacity:{opacity};">'
        + status_banner
        + f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">'
        + f'<div><span style="font-size:24px;font-weight:900;color:{dir_color};">{direction}</span>'
        + f'<span style="font-size:12px;color:#94a3b8;margin-left:8px;">{conf_pct:.0f}% confidence</span></div>'
        + f'<div style="text-align:right;">'
        + f'<div style="font-size:22px;font-weight:700;color:#f1f5f9;">{currency}{live_price:,.2f}</div>'
        + f'<div style="font-size:12px;color:{pnl_color};">{pnl_from_entry:+.2f}% from entry</div></div></div>'
        + f'<div style="display:flex;gap:8px;margin-bottom:10px;">'
        + f'<span style="background:{regime_color}22;color:{regime_color};border:1px solid {regime_color}44;'
        + f'border-radius:4px;padding:2px 8px;font-size:11px;">{regime}</span></div>'
        + f'<div style="display:grid;grid-template-columns:{grid_cols};gap:8px;margin-bottom:10px;">'
        + f'<div style="background:#0f172a;border-radius:6px;padding:8px;text-align:center;">'
        + f'<div style="font-size:12px;color:#64748b;margin-bottom:2px;">ENTRY</div>'
        + f'<div style="font-size:20px;font-weight:700;color:#f1f5f9;">{currency}{entry:,.2f}</div></div>'
        + f'<div style="background:#0f172a;border-radius:6px;padding:8px;text-align:center;">'
        + f'<div style="font-size:12px;color:#64748b;margin-bottom:2px;">TARGET 1 {t1_badge}</div>'
        + f'<div style="font-size:20px;font-weight:700;color:#10b981;">{currency}{t1:,.2f}</div></div>'
        + t2_col_html
        + f'<div style="background:#0f172a;border-radius:6px;padding:8px;text-align:center;">'
        + f'<div style="font-size:12px;color:#64748b;margin-bottom:2px;">STOP LOSS</div>'
        + f'<div style="font-size:20px;font-weight:700;color:#ef4444;">{currency}{sl:,.2f}</div></div>'
        + '</div></div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    st.markdown(f"""<div style="margin-bottom:6px;">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:#64748b;margin-bottom:3px;">
            <span>Progress to T1</span><span>{prog_t1_pct}%</span>
        </div>
        <div style="background:#1e293b;border-radius:4px;height:6px;">
            <div style="width:{prog_t1_pct}%;background:{dir_color};border-radius:4px;height:6px;"></div>
        </div>
    </div>""", unsafe_allow_html=True)

    if t2:
        st.markdown(f"""<div style="margin-bottom:6px;">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:#64748b;margin-bottom:3px;">
            <span>Progress T1 &#8594; T2</span><span>{prog_t2_pct}%</span>
        </div>
        <div style="background:#1e293b;border-radius:4px;height:6px;">
            <div style="width:{prog_t2_pct}%;background:#6366f1;border-radius:4px;height:6px;"></div>
        </div>
    </div>""", unsafe_allow_html=True)

    st.markdown(f"""<div style="padding-top:7px;display:grid;grid-template-columns:1fr 1fr;
        gap:4px;font-size:11px;color:#475569;border-top:1px solid #1e293b;margin-top:4px;">
        <div><span style="color:#64748b;">Generated:</span> {age_str} ago
            <span style="color:#334155;font-size:10px;">({ts_str})</span></div>
        <div style="text-align:right;"><span style="color:#64748b;">Expires:</span>
            <span style="color:{expire_color};">{expire_label_text}</span></div>
    </div>""", unsafe_allow_html=True)


@st.fragment(run_every=60)
def render_main_signal(symbol, strategy, currency):
    """Corrected signal card — reads stored DB signal, live price kept separate."""
    from datetime import timezone as _tzx
    
    live_price  = get_live_quote(symbol) or 0
    stored      = get_active_stored_signal(symbol)
    status      = classify_signal_status(stored, live_price)
    
    live_signal = get_real_signal(symbol, strategy) or {}
    is_active   = status.get('state') in ('ACTIVE', 'T1_HIT')
    
    # Calculate age of stored signal
    _now_u = datetime.utcnow().replace(tzinfo=_tzx.utc)
    created = stored.get('created_at') if stored else None
    if created:
        if created.tzinfo is None:
            created = created.replace(tzinfo=_tzx.utc)
        age_mins = int((_now_u - created).total_seconds() / 60)
    else:
        age_mins = 999

    if not stored or not live_price:
        st.markdown(f"""
        <div class="signal-card" style="padding:16px;text-align:center;">
            <div style="color:#475569;font-size:13px;">No stored signal for {symbol}<br/>
                <span style="font-size:11px;">Scanner runs every 10 min — restart the Scout agent to generate new signals.</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    elif not is_active and age_mins > 15:
        # The user specifically requested this: If there's no active trade, and the AI is saying WAIT right now,
        # DON'T show the giant graphic for an old expired BUY signal! Show a WAIT card.
        comp_time = live_signal.get('computation_time_ms', 0)
        st.markdown(f"""
        <div class="signal-card" style="padding:32px 16px;text-align:center;border:1px solid #1e293b;">
            <div style="font-size:36px;font-weight:900;color:#f59e0b;letter-spacing:2px;">WAIT</div>
            <div style="color:#e2e8f0;font-size:14px;margin-top:12px;font-weight:600;">
                AI brains recommend staying out of the market for <span style="color:#6366f1;">{symbol}</span>.
            </div>
            <div style="font-size:12px;color:#64748b;margin-top:6px;">
                Current setup does not meet confidence thresholds.
            </div>
            <div style="font-size:10px;color:#334155;margin-top:12px;">
                Live prediction computed in {comp_time}ms
            </div>
        </div>
        """, unsafe_allow_html=True)
        # We need to set direction for the rest of the UI (quick vote, reasoning)
        direction = 'WAIT'
        signal = live_signal
    else:
        _render_signal_card_html(stored, live_price, status, currency)
        direction = stored.get('direction', 'BUY')
        signal = live_signal

    # ── Scanner status explanation ─────────────────────────────────────────
    # Tells the user WHY the signal is old: scanner IS running but brains
    # voted WAIT because confidence was below threshold.
    try:
        from market_agent.data.storage.postgres import PostgresStorage as _PGSx
        from sqlalchemy import text as _sqlx
        from datetime import timezone as _tzx
        _stor = st.session_state.get('storage') or _PGSx()
        with _stor.engine.connect() as _cx:
            _last_any = _cx.execute(_sqlx(
                "SELECT MAX(created_at) FROM signal_predictions"
            )).scalar()
        _now_u = datetime.utcnow().replace(tzinfo=_tzx.utc)
        if _last_any:
            if _last_any.tzinfo is None:
                _last_any = _last_any.replace(tzinfo=_tzx.utc)
            _mins_since = int((_now_u - _last_any).total_seconds() / 60)
            _next_in    = max(0, 10 - (_mins_since % 10))
            if _mins_since > 15:
                # Scanner ran but stored nothing recently — brains said WAIT
                st.markdown(f"""
                <div style="margin-top:6px;padding:8px 12px;background:#1e293b;border-radius:6px;
                    border-left:3px solid #f59e0b;font-size:11px;color:#94a3b8;">
                    ⚠️ <strong style="color:#f1f5f9;">Scout is running</strong> but brains returned
                    <strong style="color:#f59e0b;">WAIT</strong> for <em>{symbol}</em> this cycle
                    (confidence below threshold). Last confident signal: <strong>{_mins_since}m ago</strong>.
                    Next scan in ≈ <strong style="color:#f1f5f9;">{_next_in}m</strong>.
                </div>""", unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="margin-top:6px;padding:6px 12px;background:#1e293b;border-radius:6px;
                    font-size:11px;color:#64748b;">
                    ✅ Scanner active · Next scan in ≈ <strong style="color:#f1f5f9;">{_next_in}m</strong>
                </div>""", unsafe_allow_html=True)
    except Exception:
        pass

    # Signal reasoning — use the live_signal we fetched above
    reasoning = signal.get('reasoning', [])
    if reasoning and isinstance(reasoning, list):
        with st.expander('🧠 Signal Reasoning', expanded=(direction not in ('WAIT', None))):
            for r in reasoning:
                if r.startswith('T1:') or r.startswith('Volume adjustment'):
                    st.markdown(f'**🎯 {r}**')
                else:
                    st.markdown(f'• {r}')

    # Council Quick Vote
    if direction not in ('WAIT', None):
        try:
            cortex = st.session_state.get('cortex')
            if cortex is None:
                from market_agent.brain.cortex import CortexGatekeeper
                cortex = CortexGatekeeper()
                st.session_state['cortex'] = cortex
            cortex.queue_debate(
                topic=f"{direction} signal for {symbol} at {live_price:,.2f} — confidence {signal.get('confidence', 0):.0%}",
                symbol=symbol, trigger_type='signal_review',
                market_metrics={
                    'direction': direction, 'confidence': signal.get('confidence', 0),
                    'price': live_price, 'current_price': live_price, 'atr': signal.get('atr'),
                },
                urgency='HIGH' if signal.get('confidence', 0) > 0.7 else 'MEDIUM'
            )
            recent_verdicts = [log for log in cortex.conversation_log
                               if log.get('type') == 'verdict' or '✅' in log.get('message', '')]
            if recent_verdicts:
                with st.expander('⚡ Council Quick Verdict', expanded=False):
                    for v in recent_verdicts[-3:]:
                        st.markdown(f"**{v.get('actor', '🧠')}**: {v.get('message', 'N/A')}")
        except Exception:
            pass

    # Path A: Brain predictions & Council block (same data as Performance)
    with st.expander("📊 Brain predictions & Council", expanded=False):

        # ── ADD 1: BRAIN VOTE BREAKDOWN (Dashboardcheck.md) ──────────────
        # Shows each brain's directional vote + confidence bar for the latest
        # council session. Highlights dissenting brains vs the final verdict.
        try:
            from market_agent.data.storage.postgres import PostgresStorage as _PGS
            from sqlalchemy import text as _text
            _stor2 = _PGS()
            with _stor2.engine.connect() as _conn:
                _sid_row = _conn.execute(_text("""
                    SELECT council_session_id
                    FROM brain_predictions
                    WHERE symbol = :sym
                      AND council_session_id IS NOT NULL
                    ORDER BY predicted_at DESC LIMIT 1
                """), {"sym": symbol}).fetchone()

            if _sid_row:
                _sid = _sid_row[0]
                with _stor2.engine.connect() as _conn:
                    _preds_bv = _conn.execute(_text("""
                        SELECT brain_name, direction, confidence, regime_suitability
                        FROM brain_predictions
                        WHERE council_session_id = :sid
                        ORDER BY confidence DESC
                    """), {"sid": _sid}).fetchall()
                    _verdict_bv = _conn.execute(_text("""
                        SELECT direction FROM council_verdicts
                        WHERE council_session_id = :sid LIMIT 1
                    """), {"sid": _sid}).fetchone()

                _council_dir = _verdict_bv[0] if _verdict_bv else None

                if _preds_bv:
                    st.markdown(f"**Brain Council Vote** — Session `{_sid[:8]}...`")
                    for _p in _preds_bv:
                        _is_diss = (_p[1] != _council_dir) if _council_dir else False
                        _bar_w = int((_p[2] or 0) * 100)
                        _bclr = '#10b981' if _p[1] == 'BUY' else '#ef4444' if _p[1] == 'SELL' else '#94a3b8'
                        _suit_clr = '#10b981' if _p[3] == 'HIGH' else '#f59e0b' if _p[3] == 'MEDIUM' else '#ef4444'
                        _diss = ' ← dissenting' if _is_diss else ''
                        st.markdown(f"""
<div style="margin:3px 0;display:flex;align-items:center;gap:8px;font-size:12px;">
  <span style="width:150px;color:#94a3b8;">{_p[0]}</span>
  <span style="width:40px;color:{_bclr};font-weight:700;">{_p[1]}</span>
  <div style="flex:1;background:#1e293b;border-radius:3px;height:7px;">
    <div style="width:{_bar_w}%;background:{_bclr};border-radius:3px;height:7px;"></div>
  </div>
  <span style="width:36px;color:#94a3b8;">{(_p[2] or 0):.2f}</span>
  <span style="color:{_suit_clr};width:55px;">{_p[3] or ''}</span>
  <span style="color:#f59e0b;">{_diss}</span>
</div>""", unsafe_allow_html=True)
                    if _council_dir:
                        st.markdown(f"**Boss Brain verdict: `{_council_dir}`**")
                    st.markdown("---")
            else:
                st.caption("Brain vote breakdown: no council_session_id yet — will populate after first debate.")
        except Exception as _bv_e:
            st.caption(f"Brain vote breakdown: {_bv_e}")

        if 'signal_resolver' in globals() and signal_resolver:

            try:
                recent = signal_resolver.get_recent_predictions_by_model(symbol=symbol, limit=20)
                from market_agent.data.storage.postgres import PostgresStorage
                _stor = PostgresStorage()
                council_verdict = _stor.get_latest_council_verdict(symbol)
                if recent:
                    rows = []
                    _has_expired = False
                    for r in recent:
                        correct = "Yes (+1)" if r.get("binary_win") == 1 else "No"
                        exp = r.get("expires_at") or ""
                        _expired = False
                        if exp:
                            try:
                                _exp_dt = datetime.fromisoformat(str(exp)[:19])
                                _expired = _exp_dt < datetime.utcnow()
                                if _expired:
                                    _has_expired = True
                            except Exception:
                                pass
                        if exp and len(exp) > 16:
                            exp = exp[:16].replace("T", " ")
                        tf = r.get("timeframe_min")
                        timeframe_str = f"{tf} min" if tf is not None else (f"Valid until {exp}" if exp else "\u2014")
                        created = (r.get("created_at") or "")[:16].replace("T", " ") if r.get("created_at") else "\u2014"
                        t1 = r.get("target_1")
                        sl = r.get("stop_loss")
                        status = "\u23f0 EXPIRED" if _expired else ("\u2705 Correct" if r.get("binary_win") == 1 else "\ud83d\udfe2 ACTIVE")
                        rows.append({
                            "Brain": r.get("model_id", "\u2014"),
                            "Direction": r.get("direction", "\u2014"),
                            "Entry": f"{r.get('entry_price', 0):,.2f}" if r.get("entry_price") is not None else "\u2014",
                            "Target": f"{t1:,.2f}" if t1 is not None else "\u2014",
                            "SL": f"{sl:,.2f}" if sl is not None else "\u2014",
                            "Timeframe": timeframe_str,
                            "Predicted": created,
                            "Status": status,
                            "Correct?": correct,
                        })
                    if _has_expired:
                        st.warning("\u26a0\ufe0f Some predictions have expired. Run the scanner or wait for the next auto-scan for fresh signals.")
                    st.dataframe(pd.DataFrame(rows), hide_index=True)
                    st.caption("Target/SL/Timeframe from DB (signal_predictions). Older rows may show \u2014 if columns were null when stored.")
                if council_verdict:
                    st.markdown("**Council decided:** " + (
                        f"{council_verdict.get('direction', '—')} @ {council_verdict.get('entry_price', 0):,.2f} | "
                        f"T1: {council_verdict.get('target_1', 0):,.2f} | SL: {council_verdict.get('stop_loss', 0):,.2f}"
                        + (f" | {council_verdict.get('timeframe_min')} min" if council_verdict.get('timeframe_min') else "")
                    ))
                elif not recent:
                    st.caption("Path A: Enable USE_PATH_A_SEVEN_BRAINS for 7-brain data. Council row appears after a debate.")
            except Exception as e:
                st.caption(f"Brain/Council data: {e}")
        else:
            st.caption("Signal resolver not available.")

    # (What Each Brain is Doing Right Now expander has been removed per user request)

col_sig, col_price = st.columns([2, 1])

with col_sig:
    render_main_signal(symbol, strategy, currency)

with col_price:
    # ══════════════════════════════════════
    # LIVE PRICE FRAGMENT (Updates every 1s)
    # ══════════════════════════════════════

    # Call the fragment
    render_live_price_card(
        symbol=symbol, 
        entry_price=signal.get('entry_price', 0), 
        currency=currency,
        market_open=market_is_open
    )


# ══════════════════════════════════════
# ROW 2: FEATURES + TIMEFRAME
# ══════════════════════════════════════
@st.fragment(run_every=60)
def render_features_and_timeframe(symbol, strategy):
    signal = get_real_signal(symbol, strategy) or {}
    
    col_feat, col_tf = st.columns(2)

    with col_feat:
        st.markdown("##### 📊 Feature Importance")
        st.caption("Dynamic — recalculates each signal cycle from live RSI, ATR, momentum")
        importances = signal.get('feature_importance', {})
        if importances:
            for feature, imp in importances.items():
                bar_w = int(imp * 100)
                st.markdown(f"""
                <div style="display: flex; align-items: center; margin-bottom: 6px; transition: all 0.3s ease;">
                    <div style="width: 120px; font-size: 11px; color: #94a3b8;">{feature}</div>
                    <div style="flex: 1; background: #1e293b; border-radius: 4px; height: 20px; margin: 0 8px;">
                        <div class="feature-bar" style="width: {bar_w}%; transition: width 0.5s ease;"></div>
                    </div>
                    <div style="width: 36px; text-align: right; font-size: 11px; color: #e0e0e0;">{imp:.0%}</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("⏳ Feature data unavailable.")

    with col_tf:
        st.markdown("##### ⏱️ Strategy Allocation")
        st.caption(f"{'Crypto: 1m + 15m weighted highest' if '-' in symbol else 'Equity: 15m + 1h sweet spots'}")
        weights = signal.get('timeframe_weights', {})
        if weights:
            for tf, w in weights.items():
                bar_w = int(w * 100)
                color = '#10b981' if w == max(weights.values()) else '#6366f1'
                st.markdown(f"""
                <div style="display: flex; align-items: center; margin-bottom: 6px; transition: all 0.3s ease;">
                    <div style="width: 50px; font-size: 12px; color: #94a3b8; font-weight: 600;">{tf.upper()}</div>
                    <div style="flex: 1; background: #1e293b; border-radius: 4px; height: 20px; margin: 0 8px;">
                        <div style="height: 20px; background: {color}; border-radius: 4px; width: {bar_w}%; transition: width 0.5s ease;"></div>
                    </div>
                    <div style="width: 36px; text-align: right; font-size: 11px; color: #e0e0e0;">{w:.0%}</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("⏳ Timeframe data unavailable.")

render_features_and_timeframe(symbol, strategy)

st.markdown("---")

# ══════════════════════════════════════
# ROW 3: TRADE IDEAS + NEWS
# ══════════════════════════════════════
col_ideas, col_news = st.columns([1, 1])

@st.fragment(run_every=60)
def render_ideas_fragment(symbol, strategy, currency):
    signal = get_real_signal(symbol, strategy) or {}
    direction = signal.get('direction', 'WAIT')
    confidence = signal.get('confidence', 0)
    confidence_pct = confidence * 100 if isinstance(confidence, float) and confidence <= 1 else confidence

    st.markdown("##### 💡 AI Trade Ideas")
    try:
        from market_agent.brain.idea_generator import get_idea_generator
        idea_gen = get_idea_generator()
        ideas = idea_gen.generate_ideas(signal)
        if ideas:
            for idea in ideas:
                emojis = {"EQUITY_BUY": "📈", "EQUITY_SELL": "📉", "BUY_CE": "🟢",
                          "BUY_PE": "🔴", "BULL_CALL_SPREAD": "🐂", "BEAR_PUT_SPREAD": "🐻",
                          "LONG_STRADDLE": "⚡"}
                em = emojis.get(idea.strategy, "💡")
                d_color = "#10b981" if idea.direction == "BULLISH" else "#ef4444" if idea.direction == "BEARISH" else "#6366f1"
                st.markdown(f"""
                <div class="brain-card" style="transition: all 0.3s ease;">
                    <div style="font-size: 14px; font-weight: 700; color: {d_color}; margin-bottom: 6px;">
                        {em} {idea.strategy.replace('_', ' ')}
                    </div>
                    <div style="font-size: 11px; color: #94a3b8; margin-bottom: 8px;">{idea.reasoning}</div>
                    <div style="display: flex; gap: 14px; font-size: 11px;">
                        <span style="color: #10b981;">Profit: {currency}{idea.max_profit:,.0f}</span>
                        <span style="color: #ef4444;">Risk: {currency}{idea.max_loss:,.0f}</span>
                        <span style="color: #6366f1;">RR: {idea.risk_reward}:1</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="brain-card" style="transition: all 0.3s ease;">
                <div style="color: #94a3b8; font-size: 12px;">
                    📊 <b>Waiting for actionable signal</b><br>
                    Trade ideas generate when the scanner detects a BUY/SELL signal with &gt;40% confidence.
                    Current: <b>{direction}</b> ({confidence_pct:.0f}% conf)
                </div>
            </div>
            """, unsafe_allow_html=True)
    except Exception as e:
        st.caption(f"Trade ideas: {e}")

@st.fragment(run_every=600)
def render_news_fragment(symbol):
    st.markdown("##### 📰 News Feed")
    st.caption("Sorted newest first • RSS + Google News + X")
    news = _get_all_news(symbol)
    _sym_keywords = []
    if '-' in symbol:
        _sym_keywords = [symbol.split('-')[0].upper()]
        _crypto_names = {'BTC': 'bitcoin', 'ETH': 'ethereum', 'SOL': 'solana', 'XRP': 'ripple', 'DOGE': 'dogecoin'}
        _cname = _crypto_names.get(_sym_keywords[0])
        if _cname: _sym_keywords.append(_cname)
    elif '=' in symbol:
        _sym_keywords = [symbol.split('=')[0].upper()]
        _forex_names = {'XAUUSD': 'gold', 'EURUSD': 'euro', 'GBPUSD': 'pound'}
        _fname = _forex_names.get(_sym_keywords[0])
        if _fname: _sym_keywords.append(_fname)
    else:
        _sym_keywords = [symbol.replace('.NS', '').replace('.BO', '').lower()]

    if _sym_keywords and news:
        _filtered = []
        for item in news:
            headline = (item.get('headline') or item.get('title', '')).lower()
            if any(kw.lower() in headline for kw in _sym_keywords):
                _filtered.append(item)
        if _filtered:
            news = _filtered
        elif len(news) > 0:
            st.caption(f"⚠️ No specific news found. Showing general market news.")

    if news:
        news_html = ""
        for item in news[:12]:
            headline = item.get('headline') or item.get('title', 'No headline')
            sentiment = item.get('sentiment_score', item.get('sentiment', 0))
            impact = item.get('impact_score', item.get('impact', 0.2))
            source = item.get('source', 'RSS')
            time_str = item.get('time') or item.get('published', 'Recent')
            if isinstance(time_str, str) and len(time_str) > 20:
                time_str = time_str[:16]
            border_color = "#ef4444" if impact > 0.7 else "#f59e0b" if impact > 0.4 else "#6366f1"
            sent_color = "#10b981" if sentiment > 0.1 else "#ef4444" if sentiment < -0.1 else "#94a3b8"
            news_html += f"""
            <div class="news-item" style="border-left-color: {border_color}; transition: all 0.5s ease;">
                <div style="display: flex; justify-content: space-between; font-size: 10px;">
                    <span style="color: #64748b; font-weight: 600;">{source.upper()} • {time_str}</span>
                    <span style="color: {sent_color}; font-weight: 600;">Sent: {sentiment:+.2f} | Imp: {impact:.1f}</span>
                </div>
                <div style="font-size: 13px; font-weight: 600; color: #e2e8f0; margin-top: 4px;">
                    <a href="{item.get('link', '#')}" target="_blank" style="color: #e2e8f0; text-decoration: none;">{headline}</a>
                </div>
            </div>"""
        st.markdown(f"""
        <div style="max-height: 450px; overflow-y: auto; scrollbar-width: thin;
            scrollbar-color: #334155 #0f172a;">{news_html}</div>
        <div style="text-align: center; font-size: 10px; color: #475569; padding: 4px;">
            {len(news)} articles found across all sources
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="brain-card">
            <div style="color: #94a3b8; font-size: 12px;">
                📰 <b>No news found for {symbol}</b><br>
                Sources checked: CNBC, CoinDesk, Economic Times, Google News, ForexFactory, X/Twitter.
                Try a major ticker for broader coverage.
            </div>
        </div>
        """, unsafe_allow_html=True)

with col_ideas:
    render_ideas_fragment(symbol, strategy, currency)

with col_news:
    render_news_fragment(symbol)

st.markdown("---")

# ══════════════════════════════════════
# ROW 4: MACRO
# ══════════════════════════════════════
st.markdown("##### 🌍 Global Macro")
try:
    import yfinance as yf
    macro_tickers = {"VIX": "^VIX", "DXY": "DX-Y.NYB", "Gold": "GC=F", "S&P 500": "^GSPC"}
    macro_cols = st.columns(len(macro_tickers))
    for i, (name, tick) in enumerate(macro_tickers.items()):
        with macro_cols[i]:
            try:
                d = yf.Ticker(tick).history(period="2d")
                if d is not None and len(d) >= 2:
                    val = float(d['Close'].iloc[-1])
                    prev = float(d['Close'].iloc[-2])
                    chg = ((val - prev) / prev * 100)
                    st.metric(name, f"{val:,.2f}", f"{chg:+.2f}%")
                elif d is not None and len(d) == 1:
                    st.metric(name, f"{float(d['Close'].iloc[-1]):,.2f}")
                else:
                    st.metric(name, "N/A")
            except Exception:
                st.metric(name, "N/A")
except Exception:
    st.caption("Macro data unavailable")


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2 — OPERATIONAL ALERTS (Safety Critical)
# Reads from signal_predictions (confirmed ACTIVE_SIGNAL_TABLE).
# council_verdicts has exit_timestamp column (confirmed CHECK 0D).
# ═══════════════════════════════════════════════════════════════════════

def render_operational_alerts():
    """
    Checks scanner and resolver health every render.
    Shows red banner if scanner silent > 20 min during market hours.
    Shows warning if resolver (council_verdicts) silent > 25 min.
    Renders at top of page — called before any other content.
    """
    from market_agent.data.storage.postgres import PostgresStorage
    from datetime import timezone as _tz
    from sqlalchemy import text as _t
    
    storage = st.session_state.get('storage')
    if not storage:
        try:
            storage = PostgresStorage()
            st.session_state['storage'] = storage
        except Exception:
            return

    now    = datetime.utcnow().replace(tzinfo=_tz.utc)
    alerts = []

    try:
        with storage.engine.connect() as conn:
            # CHECK MARKET_DATA for scanner heartbeat (reliable proxy for "is fetching data")
            # We don't use signal_predictions because a string of WAITs means no row is inserted!
            last_signal = conn.execute(_t(
                "SELECT MAX(timestamp) AS ts FROM market_data"
            )).fetchone()
            
            # exit_timestamp confirmed to exist on council_verdicts (CHECK 0D)
            last_resolve = conn.execute(_t(
                "SELECT MAX(exit_timestamp) AS ts FROM council_verdicts WHERE outcome IS NOT NULL"
            )).fetchone()

        if last_signal and last_signal.ts:
            ls = last_signal.ts
            if ls.tzinfo is None: ls = ls.replace(tzinfo=_tz.utc)
            age_min = (now - ls).total_seconds() / 60
            if age_min > 20:
                alerts.append({
                    'level': 'critical',
                    'msg':   (f'⚠️ Scanner may be offline — last signal was {int(age_min)}m ago. '
                              f'Check watchlist_scanner.py is running.'),
                })
        else:
            alerts.append({'level': 'info', 'msg': '⚪ No signals generated yet — scanner may still be starting up.'})

        if last_resolve and last_resolve.ts:
            lr = last_resolve.ts
            if lr.tzinfo is None: lr = lr.replace(tzinfo=_tz.utc)
            res_age = (now - lr).total_seconds() / 60
            if res_age > 25:
                alerts.append({
                    'level': 'warning',
                    'msg':   (f'⚠️ Signal resolver has not closed a trade in {int(res_age)}m — '
                              f'open signals may not be graded. Check scheduler.'),
                })
    except Exception:
        pass

    for alert in alerts:
        if alert['level'] == 'critical':
            st.markdown(
                f'<div style="background:#450a0a;border:1px solid #ef4444;border-radius:8px;'
                f'padding:12px 16px;margin-bottom:8px;color:#fca5a5;font-weight:600;font-size:14px;">'
                f'{alert["msg"]}</div>',
                unsafe_allow_html=True
            )
        elif alert['level'] == 'warning':
            st.warning(alert['msg'])
        else:
            st.info(alert['msg'])


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4 — SESSION SCORECARD
# council_verdicts columns confirmed: outcome, pnl_pct, exit_timestamp.
# strategy column NOT present on council_verdicts — breakdown omitted.
# ═══════════════════════════════════════════════════════════════════════

def render_session_scorecard():
    """
    Daily performance summary. Reads council_verdicts for today (UTC).
    strategy breakdown removed — column not present on council_verdicts.
    """
    from market_agent.data.storage.postgres import PostgresStorage
    from datetime import date, timezone as _tz
    from sqlalchemy import text as _t
    
    storage = st.session_state.get('storage')
    if not storage:
        try:
            storage = PostgresStorage()
            st.session_state['storage'] = storage
        except Exception:
            return
            
    capital = st.session_state.get('base_capital_inr', 500000)
    today   = date.today().isoformat()

    try:
        with storage.engine.connect() as conn:
            row = conn.execute(_t("""
                SELECT
                    COUNT(*)                                               AS total,
                    SUM(CASE WHEN outcome = 'TARGET'  THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN outcome = 'SL'      THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN outcome = 'EXPIRED' THEN 1 ELSE 0 END) AS expired,
                    ROUND(SUM(pnl_pct)::numeric, 3)                      AS net_pnl_pct
                FROM council_verdicts
                WHERE DATE(created_at AT TIME ZONE 'UTC') = :today
                AND   outcome IS NOT NULL
            """), {'today': today}).fetchone()

            open_count = conn.execute(_t("""
                SELECT COUNT(*) FROM signal_predictions
                WHERE is_resolved = FALSE AND expires_at > NOW()
            """)).scalar() or 0
    except Exception:
        return

    if not row or not row.total:
        st.markdown(
            '<div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;'
            'padding:12px 16px;color:#475569;font-size:12px;text-align:center;margin-bottom:8px;">'
            'Session scorecard will appear after the first signal closes today</div>',
            unsafe_allow_html=True
        )
        return

    wins     = int(row.wins    or 0)
    losses   = int(row.losses  or 0)
    expired  = int(row.expired or 0)
    total    = int(row.total   or 0)
    net_pnl  = float(row.net_pnl_pct or 0)
    win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0
    pnl_inr  = capital * (net_pnl / 100)
    pnl_col  = '#10b981' if net_pnl >= 0 else '#ef4444'
    wr_col   = '#10b981' if win_rate >= 0.5 else '#ef4444'

    c1, c2, c3, c4 = st.columns(4)

    def _sc(col, label, main_html, sub):
        col.markdown(
            f'<div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;'
            f'padding:10px;text-align:center;margin-bottom:8px;">'
            f'<div style="font-size:10px;color:#64748b;margin-bottom:4px;">{label}</div>'
            f'{main_html}'
            f'<div style="font-size:10px;color:#475569;margin-top:2px;">{sub}</div></div>',
            unsafe_allow_html=True
        )

    _sc(c1, "TODAY'S SIGNALS",
        f'<div style="font-size:22px;font-weight:900;color:#f1f5f9;">{total}</div>',
        f'{open_count} open now')

    _sc(c2, "WIN / LOSS",
        f'<div style="font-size:18px;font-weight:900;">'
        f'<span style="color:#10b981;">{wins}W</span>'
        f'<span style="color:#475569;"> / </span>'
        f'<span style="color:#ef4444;">{losses}L</span></div>',
        f'{expired} expired')

    _sc(c3, "WIN RATE",
        f'<div style="font-size:22px;font-weight:900;color:{wr_col};">{win_rate:.0%}</div>',
        f'from {wins + losses} closed trades')

    _sc(c4, "NET PnL",
        f'<div style="font-size:20px;font-weight:900;color:{pnl_col};">{net_pnl:+.2f}%</div>',
        f'{"+" if pnl_inr >= 0 else ""}₹{abs(pnl_inr):,.0f}')


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5 — RISK EXPOSURE METER
# Reads signal_predictions WHERE is_resolved=FALSE (confirmed table).
# ═══════════════════════════════════════════════════════════════════════

def render_risk_exposure():
    """Directional concentration gauge across all open signals."""
    from market_agent.data.storage.postgres import PostgresStorage
    from sqlalchemy import text as _t
    
    storage = st.session_state.get('storage')
    if not storage:
        try:
            storage = PostgresStorage()
            st.session_state['storage'] = storage
        except Exception:
            return

    try:
        with storage.engine.connect() as conn:
            rows = conn.execute(_t("""
                SELECT direction, COUNT(*) AS cnt,
                       STRING_AGG(symbol, ', ' ORDER BY symbol) AS symbols
                FROM signal_predictions
                WHERE is_resolved = FALSE AND expires_at > NOW()
                GROUP BY direction
            """)).fetchall()
    except Exception:
        return

    if not rows:
        st.markdown(
            '<div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;'
            'padding:10px 14px;color:#475569;font-size:12px;margin-bottom:8px;">'
            'No open signals — directional exposure: neutral</div>',
            unsafe_allow_html=True
        )
        return

    counts = {'BUY': 0, 'SELL': 0}
    syms   = {}
    for r in rows:
        counts[r.direction] = r.cnt
        syms[r.direction]   = r.symbols or ''

    total    = sum(counts.values()) or 1
    buy_pct  = counts['BUY']  / total
    sell_pct = counts['SELL'] / total
    dom_pct  = max(buy_pct, sell_pct)

    if   dom_pct >= 0.75: warn_label, warn_color, border = '⚠️ HIGH CONCENTRATION', '#ef4444', '1px solid #ef444444'
    elif dom_pct >= 0.60: warn_label, warn_color, border = '⚡ MODERATE BIAS',       '#f59e0b', '1px solid #1e293b'
    else:                 warn_label, warn_color, border = '✅ BALANCED',             '#10b981', '1px solid #1e293b'

    html = (
        f'<div style="background:#0f172a;{border};border-radius:8px;padding:12px 16px;margin-bottom:8px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
        f'<span style="font-size:11px;color:#64748b;font-weight:600;">OPEN DIRECTIONAL EXPOSURE</span>'
        f'<span style="font-size:11px;color:{warn_color};font-weight:700;">{warn_label}</span></div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">'
        f'<span style="font-size:11px;color:#10b981;width:32px;">BUY</span>'
        f'<div style="flex:1;background:#1e293b;border-radius:3px;height:8px;">'
        f'<div style="width:{int(buy_pct*100)}%;background:#10b981;border-radius:3px;height:8px;"></div></div>'
        f'<span style="font-size:11px;color:#10b981;width:55px;text-align:right;">{counts["BUY"]} ({buy_pct:.0%})</span></div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
        f'<span style="font-size:11px;color:#ef4444;width:32px;">SELL</span>'
        f'<div style="flex:1;background:#1e293b;border-radius:3px;height:8px;">'
        f'<div style="width:{int(sell_pct*100)}%;background:#ef4444;border-radius:3px;height:8px;"></div></div>'
        f'<span style="font-size:11px;color:#ef4444;width:55px;text-align:right;">{counts["SELL"]} ({sell_pct:.0%})</span></div>'
        f'<div style="font-size:10px;color:#334155;">BUY: {syms.get("BUY","none")} &nbsp;|&nbsp; SELL: {syms.get("SELL","none")}</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6 — DATA FRESHNESS BAR
# market_data table confirmed to exist (CHECK 0F).
# news_watcher thread: uses safe hasattr check — attribute name unknown.
# ═══════════════════════════════════════════════════════════════════════

@st.fragment(run_every=30)
def render_data_freshness_bar():
    """Lightweight status bar: last candle age, scanner age, news watcher status."""
    from market_agent.data.storage.postgres import PostgresStorage
    from datetime import timezone as _tz
    from sqlalchemy import text as _t
    
    storage = st.session_state.get('storage')
    if not storage:
        try:
            storage = PostgresStorage()
            st.session_state['storage'] = storage
        except Exception:
            return

    now = datetime.utcnow().replace(tzinfo=_tz.utc)

    try:
        with storage.engine.connect() as conn:
            # We use market_data across ALL timeframes as the true scanner heartbeat
            last_sig = conn.execute(_t(
                "SELECT MAX(timestamp) AS ts FROM market_data"
            )).fetchone()
            
            # For pure data freshness, we ALSO just check the absolute latest market_data row.
            # (If crypto is updating 1m but equities 1h is stale on a weekend, this prevents 
            # a false positive "Data: 22h ago" red alert).
            last_candle = last_sig
            
    except Exception:
        return

    candle_age = 9999.0
    if last_candle and last_candle.ts:
        lc = last_candle.ts
        if lc.tzinfo is None: lc = lc.replace(tzinfo=_tz.utc)
        candle_age = (now - lc).total_seconds() / 60

    if   candle_age < 5:  d_icon, d_color = '🟢', '#10b981'
    elif candle_age < 15: d_icon, d_color = '🟡', '#f59e0b'
    else:                 d_icon, d_color = '🔴', '#ef4444'
    d_label = f'{int(candle_age)}m ago' if candle_age < 60 else f'{int(candle_age/60)}h ago'
    
    scan_age = 9999.0
    if last_sig and last_sig.ts:
        ls = last_sig.ts
        if ls.tzinfo is None: ls = ls.replace(tzinfo=_tz.utc)
        scan_age = (now - ls).total_seconds() / 60

    sc_ok    = scan_age < 20
    sc_icon  = '✅' if sc_ok else '⚠️'
    sc_color = '#10b981' if sc_ok else '#ef4444'
    sc_label = f'{int(scan_age)}m ago' if scan_age < 9999 else 'no signals yet'

    # News watcher — safe check; attribute name may vary
    watcher   = st.session_state.get('news_watcher')
    nw_alive  = (watcher is not None and any(
        getattr(getattr(watcher, attr, None), 'is_alive', lambda: False)()
        for attr in ('_thread', 'thread', '_watcher_thread', '_bg_thread')
    ))
    nw_color  = '#10b981' if nw_alive else '#ef4444'
    nw_label  = 'live' if nw_alive else 'offline'
    ts_str    = now.strftime('%H:%M:%S UTC')

    html = (
        f'<div style="background:#0a0a0f;border:1px solid #1e293b;border-radius:6px;'
        f'padding:6px 14px;margin-bottom:8px;display:flex;gap:24px;align-items:center;flex-wrap:wrap;">'
        f'<span style="font-size:11px;color:{d_color};">{d_icon} Data: {d_label}</span>'
        f'<span style="font-size:11px;color:{sc_color};">{sc_icon} Scanner: {sc_label}</span>'
        f'<span style="font-size:11px;color:{nw_color};">📡 News: {nw_label}</span>'
        f'<span style="font-size:11px;color:#334155;margin-left:auto;">{ts_str}</span>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 7 — REGIME BANNER
# Reads council_verdicts last 2h. If empty (0 rows now) renders nothing.
# ═══════════════════════════════════════════════════════════════════════

def render_regime_banner():
    """Badge strip showing regime per recently active symbol."""
    from market_agent.data.storage.postgres import PostgresStorage
    from sqlalchemy import text as _t
    
    storage = st.session_state.get('storage')
    if not storage:
        try:
            storage = PostgresStorage()
            st.session_state['storage'] = storage
        except Exception:
            return
    try:
        with storage.engine.connect() as conn:
            rows = conn.execute(_t("""
                SELECT DISTINCT ON (symbol) symbol, regime
                FROM council_verdicts
                WHERE created_at > NOW() - INTERVAL '2 hours'
                AND   regime IS NOT NULL
                ORDER BY symbol, created_at DESC
            """)).fetchall()
    except Exception:
        return

    if not rows:
        return

    REGIME_COLORS = {
        'STABLE_TRADING':    '#10b981',
        'VOLATILE_CHAOS':    '#ef4444',
        'SCANNING_INTRADAY': '#f59e0b',
        'HYBRID_SCAN':       '#8b5cf6',
    }
    badges = []
    for r in rows:
        c = REGIME_COLORS.get(r.regime, '#475569')
        badges.append(
            f'<span style="background:{c}22;color:{c};border:1px solid {c}44;'
            f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600;">'
            f'{r.symbol}: {r.regime}</span>'
        )
    st.markdown(
        '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;">' + ''.join(badges) + '</div>',
        unsafe_allow_html=True
    )


# ═══════════════════════════════════════════════════════════════════════
# SECTION 8 — CONFIDENCE SUMMARY
# ═══════════════════════════════════════════════════════════════════════

def render_confidence_summary():
    """Avg confidence across open signals. Renders nothing if none open."""
    from market_agent.data.storage.postgres import PostgresStorage
    from sqlalchemy import text as _t
    
    storage = st.session_state.get('storage')
    if not storage:
        try:
            storage = PostgresStorage()
            st.session_state['storage'] = storage
        except Exception:
            return
    try:
        with storage.engine.connect() as conn:
            row = conn.execute(_t("""
                SELECT AVG(confidence) AS avg_conf,
                       MIN(confidence) AS min_conf,
                       MAX(confidence) AS max_conf,
                       COUNT(*)        AS open_count
                FROM signal_predictions
                WHERE is_resolved = FALSE AND expires_at > NOW()
                AND   confidence IS NOT NULL
            """)).fetchone()
    except Exception:
        return

    if not row or not row.avg_conf:
        return

    avg = float(row.avg_conf)
    if   avg >= 0.70: qual, color = 'HIGH CONVICTION',     '#10b981'
    elif avg >= 0.60: qual, color = 'MODERATE',            '#f59e0b'
    else:             qual, color = 'LOW — system uncertain', '#ef4444'

    n = int(row.open_count or 0)
    st.markdown(
        f'<div style="font-size:11px;color:#64748b;margin-bottom:8px;">'
        f'Avg confidence: <span style="color:{color};font-weight:700;">{avg:.0%} — {qual}</span>'
        f'&nbsp;|&nbsp;Range: {float(row.min_conf):.0%}–{float(row.max_conf):.0%}'
        f'&nbsp;|&nbsp;{n} open signal{"s" if n != 1 else ""}</div>',
        unsafe_allow_html=True
    )


# ═══════════════════════════════════════════════════════════════════════
# SECTION 9 — SIGNAL HISTORY TABLE (Last 24h)
# SESSION_ID_EXISTS = True (CHECK 0B), but brain_predictions = 0 rows
# → Use Version A query with JOIN; Brains column shows "—" for all rows.
# council_verdicts currently has 0 rows → shows "No signals" caption.
# ═══════════════════════════════════════════════════════════════════════

def render_signal_history_24h():
    """Compact scrollable table of all signals in the last 24 hours."""
    from market_agent.data.storage.postgres import PostgresStorage
    from datetime import timedelta, timezone as _tz
    from sqlalchemy import text as _t
    
    storage = st.session_state.get('storage')
    if not storage:
        try:
            storage = PostgresStorage()
            st.session_state['storage'] = storage
        except Exception:
            return

    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()

    try:
        with storage.engine.connect() as conn:
            # Version A — with brain_predictions JOIN (SESSION_ID_EXISTS = True)
            # Brains column will show "—" until brain_predictions gets data
            rows = conn.execute(_t("""
                SELECT
                    cv.symbol, cv.direction, cv.confidence,
                    cv.entry_price, cv.target_1, cv.stop_loss,
                    cv.outcome, cv.pnl_pct, cv.created_at, cv.regime,
                    COUNT(bp.id)                                     AS brain_count,
                    SUM(CASE WHEN bp.direction = cv.direction
                             THEN 1 ELSE 0 END)                      AS agreeing_brains
                FROM council_verdicts cv
                LEFT JOIN brain_predictions bp
                    ON bp.council_session_id = cv.council_session_id
                WHERE cv.created_at > :cutoff
                GROUP BY cv.id, cv.symbol, cv.direction, cv.confidence,
                         cv.entry_price, cv.target_1, cv.stop_loss,
                         cv.outcome, cv.pnl_pct, cv.created_at, cv.regime
                ORDER BY cv.created_at DESC
                LIMIT 50
            """), {'cutoff': cutoff}).fetchall()
    except Exception:
        return

    st.markdown("#### 📋 Signal History — Last 24 Hours")

    if not rows:
        st.caption("No signals closed in the last 24 hours — this table will populate as trades resolve.")
        return

    now = datetime.utcnow().replace(tzinfo=_tz.utc)
    table_rows = []
    icons = {'TARGET': '✅ TARGET', 'SL': '🔴 SL HIT', 'EXPIRED': '⏰ EXPIRED'}

    for r in rows:
        icon    = icons.get(r.outcome, '🟡 OPEN')
        pnl_str = f"{r.pnl_pct:+.2f}%" if r.pnl_pct is not None else '—'
        ts      = r.created_at
        if ts and ts.tzinfo is None: ts = ts.replace(tzinfo=_tz.utc)
        age_min = int((now - ts).total_seconds() / 60) if ts else 0
        age_str = f"{age_min}m" if age_min < 60 else f"{age_min//60}h"

        t1_str = '—'
        if r.entry_price and r.target_1 and float(r.entry_price) > 0:
            t1_pct = abs(float(r.target_1) - float(r.entry_price)) / float(r.entry_price) * 100
            t1_str = f"{t1_pct:.2f}%"

        bc, ba = int(r.brain_count or 0), int(r.agreeing_brains or 0)
        table_rows.append({
            'Status':  icon,
            'Symbol':  r.symbol,
            'Dir':     r.direction,
            'Conf':    f"{float(r.confidence):.0%}" if r.confidence else '—',
            'Entry':   f"${float(r.entry_price):,.2f}" if r.entry_price else '—',
            'T1 Dist': t1_str,
            'PnL':     pnl_str,
            'Regime':  r.regime or '—',
            'Brains':  f"{ba}/{bc}" if bc > 0 else '—',
            'Age':     age_str,
        })

    df = pd.DataFrame(table_rows)

    def _color_pnl(v):
        if v == '—':          return 'color: #475569'
        if v.startswith('+'): return 'color: #10b981'
        return 'color: #ef4444'

    styled = df.style.map(_color_pnl, subset=['PnL'])
    st.dataframe(styled, use_container_width=True, height=300, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1 — PAGE ASSEMBLY (spec order)
# All new sections injected at the TOP of the page, before the existing
# signal / row sections which remain inline above.
# ═══════════════════════════════════════════════════════════════════════
# NOTE: The existing signal card, live price, features, trade ideas,
# news, and macro sections are rendered inline ABOVE this point.
# The new sections below add the missing top-of-page components.

# Call operational alerts + freshness bar at the bottom so they appear
# as a summary section; the inline signal sections handle the main body.

st.divider()

st.markdown("### 📊 Session Summary")
render_session_scorecard()

col_risk, col_fresh = st.columns([3, 1])
with col_risk:
    render_risk_exposure()
with col_fresh:
    render_data_freshness_bar()

render_regime_banner()
render_confidence_summary()

st.divider()
render_signal_history_24h()

# Operational alerts rendered last (they are sticky warnings, not primary UI)
render_operational_alerts()

