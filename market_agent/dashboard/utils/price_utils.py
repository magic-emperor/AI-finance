"""
price_utils.py — Standalone live price fetcher.

Extracted from command_center.py so that @st.fragment functions can always
import this module safely, even when Streamlit reruns the fragment independently
(outside of the full page-script execution context).

Priority order:
  Crypto/Forex : Binance WS cache  → CoinGecko  → yfinance
  Indian stocks: AngelOne SmartAPI → NSEPython   → yfinance
"""

import streamlit as st


def get_live_quote(sym: str):
    """
    Return the latest available price for *sym*, or None if unavailable.

    Sets st.session_state['_price_source'] to a human-readable source label.

    Symbol routing:
      BTC-USD, ETH-USD, SOL-USD … → Crypto path  (Binance → CoinGecko → Yahoo)
      EURUSD=X, XAUUSD=X …        → Forex  path  (Binance → Yahoo)
      BAJAJ-AUTO.NS, RELIANCE.NS … → Indian path  (AngelOne → NSEPython → Yahoo)
      Any other / US stocks         → Yahoo fallback
    """

    # ─── 1. CRYPTO / FX ───
    # Exclude Indian stocks like BAJAJ-AUTO.NS which also contain '-'
    is_crypto_or_fx = ("-" in sym and ".NS" not in sym.upper()) or "=X" in sym

    if is_crypto_or_fx:
        # A. Binance WebSocket (freshness-guarded — returns None if >30s old)
        binance_sym = sym.replace("-USD", "USDT").replace("/", "")
        feed = st.session_state.get("price_feed")
        if feed and hasattr(feed, "get_price"):
            fresh = feed.get_price(binance_sym)
            if fresh:
                st.session_state["_price_source"] = "Binance (Real-Time)"
                return fresh

        # B. CoinGecko REST API (Fallback 1)
        try:
            from market_agent.data.ingestion.coingecko import fetch_coingecko_price
            price = fetch_coingecko_price(sym)
            if price:
                st.session_state["_price_source"] = "CoinGecko (Live)"
                return price
        except Exception:
            pass

    # ─── 2. INDIAN STOCKS ───
    if ".NS" in sym.upper() or "NIFTY" in sym.upper():
        # A. AngelOne SmartAPI (Persistent session, auto-reconnect in background)
        try:
            from market_agent.data.ingestion.angel_one_client import angel_client
            ltp = angel_client.get_market_quote(sym)
            if ltp and float(ltp) > 0:
                st.session_state["_price_source"] = "AngelOne (Real-Time)"
                return round(float(ltp), 2)
        except Exception:
            pass

        # B. NSEPython (Official NSE fallback)
        try:
            from market_agent.data.ingestion.nse_python_client import fetch_nse_price
            ltp = fetch_nse_price(sym)
            if ltp and ltp > 0:
                st.session_state["_price_source"] = "NSE Official (Live)"
                return round(float(ltp), 2)
        except Exception:
            pass

    # ─── 3. UNIVERSAL FALLBACK (yfinance) ───
    try:
        import yfinance as yf
        t = yf.Ticker(sym)

        # C. fast_info (Near Real-Time, no extra request)
        try:
            fi = t.fast_info
            last = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
            if last and float(last) > 0:
                st.session_state["_price_source"] = "Yahoo Fast (Near Real-Time)"
                return round(float(last), 2)
        except Exception:
            pass

        # D. 1-minute history (Delayed 1–15 min)
        d = t.history(period="1d", interval="1m")
        if d is not None and not d.empty:
            st.session_state["_price_source"] = "Yahoo History (Delayed)"
            return round(float(d["Close"].iloc[-1]), 2)

        # E. Daily close (Worst case)
        d = t.history(period="1d")
        if d is not None and not d.empty:
            st.session_state["_price_source"] = "Yahoo Daily (Delayed)"
            return round(float(d["Close"].iloc[-1]), 2)

    except Exception:
        pass

    st.session_state["_price_source"] = "Unavailable"
    return None
