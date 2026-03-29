"""
Aegis Intelligence Dashboard — Multi-Page Finance Terminal
Bloomberg-style dark theme, real-time data, no hardcoded values.
"""

import os
import sys
# Suppress gRPC validate_metadata_from_plugin warning from google-auth
os.environ["GRPC_VERBOSITY"] = "NONE"
# Ensure Graphviz binaries are findable in Streamlit subprocess environments
_gv_bin = r"C:\Program Files\Graphviz\bin"
if os.path.isdir(_gv_bin) and _gv_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] += os.pathsep + _gv_bin
# Ensure project root is on sys.path so `from market_agent...` imports work
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st
import time
from datetime import datetime, timedelta

# ══════════════════════════════════════
# PAGE CONFIG (must be first st call)
# ══════════════════════════════════════
st.set_page_config(
    page_title="Aegis Intelligence Dashboard",
    page_icon=":material/hub:",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ══════════════════════════════════════
# GLOBAL AUTO-REFRESH (Order 3 — Section 4.1)
# Uses streamlit-autorefresh to tick the entire app on a configurable
# interval. @st.fragment handles sub-second price cards independently.
# ══════════════════════════════════════
try:
    from streamlit_autorefresh import st_autorefresh
    _refresh_interval = st.session_state.get('price_refresh', 10)  # seconds, default 10
    st_autorefresh(interval=_refresh_interval * 1000, key='global_price_refresh')
except ModuleNotFoundError:
    # streamlit-autorefresh not installed — install with: pip install streamlit-autorefresh
    # App still works; manual Refresh button in sidebar will trigger rerun.
    pass

# ══════════════════════════════════════
# VERSION + FORCED REFRESH
# ══════════════════════════════════════
UI_VERSION = "2.1.0"
if st.session_state.get('ui_version') != UI_VERSION:
    st.session_state.clear()
    st.session_state['ui_version'] = UI_VERSION

# ══════════════════════════════════════
# CSS — Bloomberg Terminal Dark Theme
# ══════════════════════════════════════
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

    /* Base theme */
    .stApp {
        background: linear-gradient(135deg, #0a0a0f 0%, #0f172a 50%, #0a0a0f 100%);
        font-family: 'Inter', -apple-system, sans-serif;
    }

    /* Remove default padding */
    .block-container { padding-top: 1rem; }

    /* Signal cards */
    .signal-card {
        background: linear-gradient(145deg, #131b2e 0%, #0f172a 100%);
        border: 1px solid rgba(99, 102, 241, 0.15);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 12px;
    }

    /* Metric cards */
    .metric-card {
        background: rgba(15, 23, 42, 0.8);
        border: 1px solid rgba(148, 163, 184, 0.08);
        border-radius: 10px;
        padding: 16px;
        text-align: center;
    }

    /* Data labels */
    .data-label {
        font-size: 10px;
        font-weight: 600;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .data-value {
        font-size: 22px;
        font-weight: 800;
        color: #f1f5f9;
    }

    /* Price colors */
    .price-green { color: #10b981; font-weight: 700; }
    .price-red { color: #ef4444; font-weight: 700; }
    .price-blue { color: #6366f1; font-weight: 700; }

    /* Status indicators */
    .status-live {
        display: inline-block;
        width: 7px; height: 7px;
        background: #10b981;
        border-radius: 50%;
        margin-right: 6px;
        animation: pulse 2s infinite;
    }
    @keyframes pulse {
        0%, 100% { opacity: 1; box-shadow: 0 0 6px #10b981; }
        50% { opacity: 0.5; box-shadow: 0 0 12px #10b981; }
    }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }

    /* Feature bars */
    .feature-bar {
        height: 22px;
        background: linear-gradient(90deg, #6366f1 0%, #818cf6 100%);
        border-radius: 4px;
    }

    /* News items */
    .news-item {
        background: rgba(15, 23, 42, 0.6);
        border-left: 3px solid #6366f1;
        padding: 12px 14px;
        border-radius: 0 8px 8px 0;
        margin-bottom: 8px;
        min-height: 80px;
        box-sizing: border-box;
    }

    /* Log window */
    .log-window {
        background: #0a0a14;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        color: #94a3b8;
        padding: 12px;
        border-radius: 8px;
        max-height: 200px;
        overflow-y: auto;
        border: 1px solid #1e293b;
    }

    /* Brain card */
    .brain-card {
        background: linear-gradient(145deg, #131b2e 0%, #0f172a 100%);
        border: 1px solid rgba(99, 102, 241, 0.12);
        border-radius: 12px;
        padding: 18px;
        margin-bottom: 10px;
        transition: border-color 0.3s;
    }
    .brain-card:hover {
        border-color: rgba(99, 102, 241, 0.35);
    }

    /* Alert banner */
    .alert-banner {
        background: linear-gradient(90deg, #0f172a 0%, #1e1b4b 100%);
        color: #818cf8;
        padding: 10px 20px;
        border-radius: 8px;
        border: 1px solid rgba(99, 102, 241, 0.2);
        font-size: 12px;
        font-weight: 600;
        margin-bottom: 16px;
    }

    /* Tabs override */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2px;
        background: rgba(15, 23, 42, 0.5);
        border-radius: 8px;
        padding: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 6px;
        font-size: 13px;
        font-weight: 600;
    }

    /* Table styling */
    .stTable { font-size: 12px; }

    /* Safety */
    pre { white-space: pre-wrap !important; }
    code { color: #f59e0b; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════
# NIFTY 50 UNIVERSE
# ══════════════════════════════════════
from market_agent.models.cross_stock_gnn import NIFTY50Universe

KNOWN_ASSETS = {
    "BTC-USD": "Bitcoin (Crypto)", "ETH-USD": "Ethereum (Crypto)",
    "SOL-USD": "Solana (Crypto)", "GC=F": "Gold Futures (Metal)",
    "XAUUSD=X": "Gold Spot (Metal)", "EURUSD=X": "EUR/USD (Forex)",
    "^NSEBANK": "Bank Nifty (Index)",
    "GBPJPY=X": "GBP/JPY (Forex)", "USDJPY=X": "USD/JPY (Forex)",
    "CL=F": "Crude Oil Futures",
}

# Display names for NIFTY 50 stocks (sector from NIFTY50Universe.STOCKS)
NIFTY_DISPLAY_NAMES = {
    'HDFCBANK.NS': 'HDFC Bank', 'ICICIBANK.NS': 'ICICI Bank', 'SBIN.NS': 'SBI',
    'KOTAKBANK.NS': 'Kotak Bank', 'AXISBANK.NS': 'Axis Bank', 'INDUSINDBK.NS': 'IndusInd Bank',
    'TCS.NS': 'TCS', 'INFY.NS': 'Infosys', 'WIPRO.NS': 'Wipro',
    'HCLTECH.NS': 'HCL Tech', 'TECHM.NS': 'Tech Mahindra', 'LTIM.NS': 'LTIMindtree',
    'HINDUNILVR.NS': 'Hindustan Unilever', 'ITC.NS': 'ITC Ltd', 'NESTLEIND.NS': 'Nestle India',
    'BRITANNIA.NS': 'Britannia', 'TATACONSUM.NS': 'Tata Consumer',
    'MARUTI.NS': 'Maruti Suzuki', 'TATAMOTORS.NS': 'Tata Motors', 'M&M.NS': 'Mahindra & Mahindra',
    'BAJAJ-AUTO.NS': 'Bajaj Auto', 'EICHERMOT.NS': 'Eicher Motors', 'HEROMOTOCO.NS': 'Hero MotoCorp',
    'SUNPHARMA.NS': 'Sun Pharma', 'DRREDDY.NS': "Dr Reddy's", 'CIPLA.NS': 'Cipla',
    'DIVISLAB.NS': "Divi's Labs", 'APOLLOHOSP.NS': 'Apollo Hospitals',
    'RELIANCE.NS': 'Reliance Industries', 'ONGC.NS': 'ONGC', 'BPCL.NS': 'BPCL',
    'NTPC.NS': 'NTPC', 'POWERGRID.NS': 'Power Grid', 'ADANIGREEN.NS': 'Adani Green',
    'TATASTEEL.NS': 'Tata Steel', 'JSWSTEEL.NS': 'JSW Steel', 'HINDALCO.NS': 'Hindalco',
    'COALINDIA.NS': 'Coal India',
    'LT.NS': 'Larsen & Toubro', 'ULTRACEMCO.NS': 'UltraTech Cement', 'GRASIM.NS': 'Grasim',
    'ADANIPORTS.NS': 'Adani Ports', 'ADANIENT.NS': 'Adani Enterprises',
    'BHARTIARTL.NS': 'Bharti Airtel',
    'BAJFINANCE.NS': 'Bajaj Finance', 'BAJAJFINSV.NS': 'Bajaj Finserv',
    'HDFCLIFE.NS': 'HDFC Life', 'SBILIFE.NS': 'SBI Life',
    'TITAN.NS': 'Titan Company', 'ASIANPAINT.NS': 'Asian Paints',
    # US stocks
    'NVDA': 'NVIDIA', 'GOOGL': 'Alphabet (Google)', 'AAPL': 'Apple', 'AMD': 'AMD',
}

def get_asset_name(symbol):
    if symbol in KNOWN_ASSETS:
        return KNOWN_ASSETS[symbol]
    display = NIFTY_DISPLAY_NAMES.get(symbol)
    sector = NIFTY50Universe.STOCKS.get(symbol, '')
    if display:
        return f"{display} ({sector})" if sector else display
    return symbol

# ══════════════════════════════════════
# SHARED STATE INITIALIZATION
# ══════════════════════════════════════
import structlog
logger = structlog.get_logger()

# Force-reload modules to pick up latest code
import market_agent.learning.calibrator as calibrator_module
import market_agent.brain.cortex as cortex_module
import market_agent.agent.config_researcher as researcher_module
import market_agent.learning.evaluator as evaluator_module
import market_agent.data.storage.postgres as storage_module
import importlib
importlib.reload(calibrator_module)
importlib.reload(cortex_module)
importlib.reload(researcher_module)
importlib.reload(evaluator_module)
importlib.reload(storage_module)

from market_agent.learning.calibrator import PerformanceAuditor
from market_agent.brain.cortex import CortexGatekeeper
from market_agent.learning.evaluator import RegretEngine
from market_agent.data.storage.postgres import PostgresStorage

# Initialize core objects (once per session)
if 'auditor' not in st.session_state:
    st.session_state['auditor'] = PerformanceAuditor()
if 'regret_engine' not in st.session_state:
    st.session_state['regret_engine'] = RegretEngine(PostgresStorage())
if 'signal_resolver' not in st.session_state:
    re = st.session_state['regret_engine']
    if re and re.signal_resolver:
        st.session_state['signal_resolver'] = re.signal_resolver
    else:
        st.session_state['signal_resolver'] = None
if 'cortex' not in st.session_state:
    st.session_state['cortex'] = CortexGatekeeper()

regret_engine = st.session_state['regret_engine']
signal_resolver = st.session_state['signal_resolver']

# Signal engine (portfolio_value drives position sizing)
from market_agent.dashboard.signal_engine import SignalEngine
if 'signal_engine' not in st.session_state:
    st.session_state['signal_engine'] = SignalEngine(portfolio_value=500000)
engine = st.session_state['signal_engine']

# Gap 2: Start background scheduler for resolving stranded predictions + daily AEP
from market_agent.runner.scheduler import start_scheduler
if 'scheduler_started' not in st.session_state:
    start_scheduler(st.session_state['regret_engine'].storage)
    st.session_state['scheduler_started'] = True

# Analyst agent (wraps Gemini + TA + News + Storage)
if 'analyst_agent' not in st.session_state:
    try:
        from market_agent.brain.autonomous_analyst import analyst_agent
        st.session_state['analyst_agent'] = analyst_agent
    except Exception:
        st.session_state['analyst_agent'] = None

# ══════════════════════════════════════
# DATA FEEDS (Background Threads)
# ══════════════════════════════════════
if 'price_feed' not in st.session_state:
    try:
        from market_agent.data.ingestion.realtime_feed import RealTimePriceFeed
        import threading
        
        # Crypto watchlist to stream
        crypto_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
        
        feed = RealTimePriceFeed()
        
        # Start as daemon thread so it dies when app stops
        thread = threading.Thread(target=feed.start_binance_stream, args=(crypto_symbols,), daemon=True)
        thread.start()
        
        st.session_state['price_feed'] = feed
        logger.info("binance_feed_started", symbols=crypto_symbols)
    except Exception as e:
        # Sanitize error to prevent OSError on Windows 
        safe_err = repr(e).encode('ascii', 'ignore').decode('ascii')
        try:
            logger.error("binance_feed_init_failed", error=safe_err)
        except:
            print(f"Binance Init Failed: {safe_err}")
            
        st.session_state['price_feed'] = None

# News aggregator (direct access for news feeds)
if 'news_aggregator' not in st.session_state:
    try:
        from market_agent.research.news_aggregator import news_aggregator
        st.session_state['news_aggregator'] = news_aggregator
    except Exception:
        st.session_state['news_aggregator'] = None

# ══════════════════════════════════════
# GEMINIIFLOW: Event-Driven News Watcher
# Polls RSS feeds every 2 min via conditional GET (304 = zero cost).
# Calls Gemini ONLY when headlines actually change for a symbol.
# Scan cycles read from NewsCache — zero Gemini calls there.
# Expected: ~43 Gemini calls/day vs ~702 with old approach.
# ══════════════════════════════════════
if 'news_cache' not in st.session_state:
    try:
        import json
        import re as _re
        from market_agent.watchers.news_watcher import start_news_watcher

        # Watchlist must match watchlist_scanner.py WATCHLIST
        _WATCHER_WATCHLIST = [
            'ITC.NS', 'HDFCBANK.NS', 'RELIANCE.NS', 'TATASTEEL.NS',
            'LT.NS', 'M&M.NS', 'ADANIENT.NS', 'ADANIPORTS.NS',
            '^NSEBANK', 'NVDA', 'GOOGL', 'AAPL', 'AMD',
            'BTC-USD', 'GC=F', 'CL=F', 'GBPJPY=X', 'USDJPY=X',
        ]

        def _gemini_score_headlines(symbol: str, headlines: list) -> tuple:
            """
            The ONLY function that calls Gemini for news.
            Called by NewsWatcher ONLY when headlines changed.
            Returns (sentiment_float, summary_str).
            """
            from market_agent.brain.gemini_client import gemini_client
            titles = [h['title'] for h in headlines[:8] if h.get('title')]
            joined = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))

            prompt = (
                f"Analyze the trading sentiment of these {symbol} news headlines.\n"
                f"Respond ONLY in JSON with no other text: "
                f'{"{"}"score": 0.0, "summary": "one sentence"{"}"}\n'
                f"score: -1.0 (very bearish) to +1.0 (very bullish), 0.0 = neutral\n\n"
                f"Headlines:\n{joined}"
            )
            try:
                raw  = gemini_client.call(prompt, max_tokens=120)
                m    = _re.search(r'\{[^}]+\}', raw or '', _re.DOTALL)
                data = json.loads(m.group())
                return float(data['score']), str(data.get('summary', ''))
            except Exception:
                return 0.0, 'Sentiment scoring unavailable'

        _news_cache, _news_watcher = start_news_watcher(
            symbols           = _WATCHER_WATCHLIST,
            gemini_scorer     = _gemini_score_headlines,
            poll_interval_sec = 120,
        )
        st.session_state['news_cache']   = _news_cache
        st.session_state['news_watcher'] = _news_watcher
        logger.info("news_watcher_initialized", symbols=len(_WATCHER_WATCHLIST))

    except Exception as _e:
        st.session_state['news_cache']   = None
        st.session_state['news_watcher'] = None
        logger.warning("news_watcher_init_failed", error=str(_e)[:120])


# ══════════════════════════════════════
# SIDEBAR — Symbol + Strategy + Status
# ══════════════════════════════════════
with st.sidebar:
    st.markdown("### 🔱 Aegis Terminal")
    st.caption(f"v{UI_VERSION}")
    st.markdown("---")

    # Asset Class
    asset_class = st.selectbox("Asset Class", ["NIFTY 50", "Forex (Global)", "Crypto (24/7)"])
    st.session_state['asset_class'] = asset_class

    # Symbol Selector
    if asset_class == "NIFTY 50":
        symbols = sorted(list(NIFTY50Universe.STOCKS.keys()))
        symbols.insert(0, "^NSEBANK")
        default_sym = st.session_state.get('last_symbol', 'RELIANCE.NS')
        default_idx = symbols.index(default_sym) if default_sym in symbols else 0
        symbol = st.selectbox("Symbol", symbols, index=default_idx, format_func=get_asset_name)
    elif asset_class == "Forex (Global)":
        forex_pairs = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "XAUUSD=X"]
        symbol = st.selectbox("Pair", forex_pairs, format_func=get_asset_name)
    else:
        crypto_assets = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD"]
        symbol = st.selectbox("Coin", crypto_assets)

    # Custom ticker override
    use_custom = st.checkbox("Custom Ticker 🔍", value=False)
    if use_custom:
        symbol = st.text_input("Ticker", value=symbol).upper()

    st.session_state['selected_symbol'] = symbol
    st.session_state['last_symbol'] = symbol

    st.markdown("---")

    # Strategy
    strategy = st.radio("Strategy ⚡", ["Intraday (Scalp)", "Swing (Hold)"])
    st.session_state['strategy'] = strategy

    # Position Mode
    position_active = st.toggle("Position Active 🛡️", value=False)
    st.session_state['position_active'] = position_active

    st.markdown("---")

    # Market Status (real)
    try:
        from market_agent.utils.market_utils import is_market_open
        mkt = is_market_open(symbol=symbol)
        if mkt['is_open']:
            mins = mkt.get('minutes_remaining', 0)
            st.success(f"🟢 {mkt['market']} Open ({mins}m left)")
        else:
            st.warning(f"🟡 {mkt['market']} {mkt.get('status', 'CLOSED')}")
    except Exception:
        st.info("📡 Market status loading...")

    now = datetime.now()
    st.caption(f"🕐 {now.strftime('%H:%M:%S')} IST")

    st.markdown("---")

    # System vitals
    with st.expander("🔧 System Vitals", expanded=False):
        try:
            from sqlalchemy import text as sql_text
            with regret_engine.storage.engine.connect() as conn:
                conn.execute(sql_text("SELECT 1"))
            st.markdown("🟢 **Database**: Connected")
        except Exception:
            st.markdown("🔴 **Database**: Offline")

        import sys, platform
        st.markdown(f"📍 **Env**: {platform.system()} | Py {sys.version.split(' ')[0]}")

        # Gemini / AI status
        analyst = st.session_state.get('analyst_agent')
        if analyst:
            gemini_ok = hasattr(analyst, 'gemini') and analyst.gemini and getattr(analyst.gemini, 'is_available', False)
            ta_ok = hasattr(analyst, 'ta_engine') and analyst.ta_engine is not None
            news_ok = hasattr(analyst, 'news_aggregator') and analyst.news_aggregator is not None
            st.markdown(f"{'🟢' if gemini_ok else '🟡'} **Gemini AI**: {'Online' if gemini_ok else 'Idle (activates on analysis)'}")
            st.markdown(f"{'🟢' if ta_ok else '🔴'} **TA Engine**: {'Loaded' if ta_ok else 'Missing'}")
            st.markdown(f"{'🟢' if news_ok else '🔴'} **News Aggregator**: {'Ready' if news_ok else 'Missing'}")
        else:
            st.markdown("🟡 **AI**: Not initialized")

    # Refresh controls
    refresh_secs = st.slider("⚡ Auto-refresh (sec)", 5, 120, 10, help="Live price refresh when market is open")
    st.session_state['price_refresh'] = refresh_secs

    col_ref1, col_ref2 = st.columns(2)
    with col_ref1:
        if st.button("🔄 Refresh Data"):
            st.cache_data.clear()
            st.rerun()
    with col_ref2:
        if st.button("🗑️ Reset All"):
            st.session_state.clear()
            st.rerun()

# ══════════════════════════════════════
# NAVIGATION — 5 Pages
# ══════════════════════════════════════
import os
_dashboard_dir = os.path.dirname(os.path.abspath(__file__))
_pages_dir = os.path.join(_dashboard_dir, "pages")

page = st.navigation([
    st.Page(os.path.join(_pages_dir, "command_center.py"), title="Command Center", icon=":material/terminal:", default=True),
    st.Page(os.path.join(_pages_dir, "brain_monitor.py"), title="Brain Monitor", icon=":material/neurology:"),
    st.Page(os.path.join(_pages_dir, "performance.py"), title="Performance", icon=":material/trending_up:"),
    st.Page(os.path.join(_pages_dir, "holdings.py"), title="Holdings", icon=":material/account_balance_wallet:"),
    st.Page(os.path.join(_pages_dir, "settings.py"), title="Settings", icon=":material/tune:"),
    st.Page(os.path.join(_pages_dir, "aep_proposals.py"), title="AEP Proposals", icon=":material/lightbulb:"),
])
page.run()

