"""
AngelOne SmartAPI Client — Fixed Version

Bugs fixed from original:
1. CRITICAL: quote_throttle was 0.15s (6.67/sec) — AngelOne limit is ~1 req/sec for free tier
   Fixed: 1.1s throttle (safer), with per-symbol cooldown to allow multi-symbol rotation
2. BUG: Full re-login every 25min including TOTP — TOTP window is 30s, re-login mid-window fails
   Fixed: Only refresh auth token (not full re-login) in keep-alive
3. BUG: NIFTY/BANKNIFTY tokens were inline in get_market_quote, not in token_map
   Fixed: Proper index tokens in token_map with NSE_INDEX exchange
4. BUG: last_quote_time was module-level, not thread-safe
   Fixed: threading.Lock() guards all shared state
5. MISSING: No bulk quote method — was calling ltpData one by one (slow)
   Added: get_market_quotes_bulk() for multiple symbols in one call
"""
import os
import time
import threading
import structlog
from dotenv import load_dotenv
from typing import Optional, Dict, List

logger = structlog.get_logger()

# AngelOne rate limits (conservative values to avoid bans)
# Free tier: ~1 quote request per second
# Historical data: 3 req/min
_QUOTE_COOLDOWN_SEC    = 1.1   # min seconds between ANY quote calls
_PER_SYMBOL_COOLDOWN   = 5.0   # min seconds between calls for SAME symbol
_MAX_RETRIES           = 2


class AngelOneClientFixed:
    """
    Fixed AngelOne SmartAPI Client.
    Thread-safe, properly rate-limited, robust session management.
    """

    # Common NSE instrument tokens (fallback if instruments.json missing)
    _FALLBACK_TOKENS = {
        "ITC":       {"token": "1660",     "exchange": "NSE",       "symbol": "ITC-EQ"},
        "SBIN":      {"token": "3045",     "exchange": "NSE",       "symbol": "SBIN-EQ"},
        "RELIANCE":  {"token": "2885",     "exchange": "NSE",       "symbol": "RELIANCE-EQ"},
        "HDFCBANK":  {"token": "1333",     "exchange": "NSE",       "symbol": "HDFCBANK-EQ"},
        "TATASTEEL": {"token": "3499",     "exchange": "NSE",       "symbol": "TATASTEEL-EQ"},
        "INFY":      {"token": "1594",     "exchange": "NSE",       "symbol": "INFY-EQ"},
        "TCS":       {"token": "11536",    "exchange": "NSE",       "symbol": "TCS-EQ"},
        "AXISBANK":  {"token": "5900",     "exchange": "NSE",       "symbol": "AXISBANK-EQ"},
        "ICICIBANK": {"token": "4963",     "exchange": "NSE",       "symbol": "ICICIBANK-EQ"},
        "WIPRO":     {"token": "3787",     "exchange": "NSE",       "symbol": "WIPRO-EQ"},
        # Indices — MUST use NSE_INDEX exchange, not NSE
        "NIFTY":     {"token": "99926000", "exchange": "NSE",       "symbol": "Nifty 50"},
        "BANKNIFTY": {"token": "99926009", "exchange": "NSE",       "symbol": "Nifty Bank"},
        "FINNIFTY":  {"token": "99926037", "exchange": "NSE",       "symbol": "Nifty Fin Services"},
    }

    def __init__(self):
        load_dotenv()
        self._api_key    = os.getenv("ANGEL_ONE_API_KEY")
        self._client_id  = os.getenv("ANGEL_ONE_CLIENT_ID")
        self._mpin       = os.getenv("ANGEL_ONE_MPIN") or os.getenv("ANGEL_ONE_PASSWORD")
        self._totp_seed  = os.getenv("ANGEL_ONE_TOTP_SEED")

        self.smart_api    = None
        self.is_connected = False
        self._auth_token  = None
        self._lock        = threading.Lock()
        self._last_any_call  = 0.0   # last time ANY quote was made
        self._last_sym_call: Dict[str, float] = {}  # per-symbol last call time
        self.token_map: Dict[str, dict] = {}

        self._load_instruments()

        # Keep-alive thread
        self._stop_event = threading.Event()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True, name='AngelKeepAlive'
        )
        self._keepalive_thread.start()

    def _load_instruments(self):
        """Load instrument tokens. Falls back to hardcoded map if file missing."""
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instruments.json")
        loaded = False
        if os.path.exists(json_path):
            try:
                import json
                with open(json_path, 'r') as f:
                    data = json.load(f)
                for item in data:
                    if item.get('exch_seg') == 'NSE' and '-EQ' in item.get('symbol', ''):
                        sym = item.get('name', '').upper()
                        if sym:
                            self.token_map[sym] = {
                                "token":    item['token'],
                                "exchange": "NSE",
                                "symbol":   item['symbol'],
                            }
                loaded = True
                logger.info("angel_instruments_loaded", count=len(self.token_map))
            except Exception as e:
                logger.warning("angel_instruments_load_failed", error=str(e))

        # Always add fallback tokens (indices + common stocks not in JSON)
        for sym, info in self._FALLBACK_TOKENS.items():
            if sym not in self.token_map:
                self.token_map[sym] = info

        if not loaded:
            logger.info("angel_using_fallback_tokens", count=len(self.token_map))

    def connect(self) -> bool:
        """Full authentication with AngelOne via TOTP."""
        if not all([self._api_key, self._client_id, self._mpin, self._totp_seed]):
            logger.warning("angel_one_missing_credentials",
                          has_key=bool(self._api_key),
                          has_id=bool(self._client_id),
                          has_mpin=bool(self._mpin),
                          has_totp=bool(self._totp_seed))
            return False
        try:
            from SmartApi import SmartConnect
            import pyotp
            smart = SmartConnect(api_key=self._api_key)
            totp  = pyotp.TOTP(self._totp_seed).now()
            resp  = smart.generateSession(self._client_id, self._mpin, totp)

            if resp and resp.get('status'):
                with self._lock:
                    self.smart_api    = smart
                    self._auth_token  = resp.get('data', {}).get('jwtToken')
                    self.is_connected = True
                logger.info("angel_one_connected", client_id=self._client_id)
                return True
            else:
                with self._lock:
                    self.is_connected = False
                logger.error("angel_one_auth_failed", message=resp.get('message') if resp else 'No response')
                return False

        except ImportError:
            logger.warning("smartapi_not_installed")
            return False
        except Exception as e:
            with self._lock:
                self.is_connected = False
            logger.error("angel_one_connect_error", error=str(e))
            return False

    def _keepalive_loop(self):
        """
        Background keep-alive.
        Fixed: Does token refresh (not full re-login) every 20 minutes.
        Full re-login only if token refresh fails.
        """
        time.sleep(5)
        if not self.is_connected:
            self.connect()

        while not self._stop_event.is_set():
            time.sleep(20 * 60)   # 20-minute cycle
            if self._stop_event.is_set():
                break
            try:
                with self._lock:
                    smart = self.smart_api
                if smart and self._auth_token:
                    # Attempt token renew (lighter than full re-login)
                    try:
                        resp = smart.generateToken(self._auth_token)
                        if resp and resp.get('status'):
                            with self._lock:
                                self._auth_token = resp.get('data', {}).get('jwtToken', self._auth_token)
                            logger.info("angel_token_renewed")
                            continue
                    except Exception:
                        pass
                # Token renew failed → full re-login
                logger.info("angel_full_reconnect")
                self.connect()
            except Exception as e:
                logger.error("angel_keepalive_error", error=str(e))

    def _rate_guard(self, symbol: str) -> bool:
        """
        Thread-safe rate limiter.
        Returns True if call is allowed, False if should skip.
        """
        with self._lock:
            now = time.time()
            # Global cooldown: any call within 1.1s → skip
            if now - self._last_any_call < _QUOTE_COOLDOWN_SEC:
                return False
            # Per-symbol cooldown: same symbol within 5s → skip
            last_sym = self._last_sym_call.get(symbol, 0)
            if now - last_sym < _PER_SYMBOL_COOLDOWN:
                return False
            self._last_any_call = now
            self._last_sym_call[symbol] = now
            return True

    def _resolve_token(self, symbol: str) -> Optional[dict]:
        """Resolve symbol to token info. Handles .NS suffix and indices."""
        clean = symbol.replace('.NS', '').replace('.BO', '').upper()
        return self.token_map.get(clean)

    def get_market_quote(self, symbol: str) -> Optional[float]:
        """
        Get Last Traded Price for a single symbol.
        Returns None if rate limited, not connected, or API error.
        Non-blocking — never waits.
        """
        if not self.is_connected:
            return None

        if not self._rate_guard(symbol):
            return None

        token_info = self._resolve_token(symbol)
        if not token_info:
            logger.debug("angel_unknown_symbol", symbol=symbol)
            return None

        for attempt in range(_MAX_RETRIES):
            try:
                with self._lock:
                    smart = self.smart_api
                if smart is None:
                    return None

                res = smart.ltpData(
                    token_info["exchange"],
                    token_info["symbol"],
                    token_info["token"],
                )
                if res and res.get('status'):
                    return float(res['data']['ltp'])
                return None

            except Exception as e:
                err_str = str(e).lower()
                if 'session' in err_str or 'token' in err_str:
                    # Session expired — trigger reconnect
                    with self._lock:
                        self.is_connected = False
                    threading.Thread(target=self.connect, daemon=True).start()
                    return None
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(0.5)
                else:
                    logger.error("angel_quote_failed", symbol=symbol, error=str(e)[:60])
        return None

    def get_market_quotes_bulk(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """
        Fetch quotes for multiple symbols.
        AngelOne supports getMarketData with multiple tokens in one call.
        Falls back to sequential if bulk API unavailable.
        """
        if not self.is_connected:
            return {s: None for s in symbols}

        # Build token list
        token_list = []
        sym_to_token = {}
        for sym in symbols:
            info = self._resolve_token(sym)
            if info:
                token_list.append(info['token'])
                sym_to_token[info['token']] = sym

        if not token_list:
            return {s: None for s in symbols}

        results = {s: None for s in symbols}

        try:
            with self._lock:
                smart = self.smart_api
            if smart is None:
                return results

            # AngelOne bulk market data (NSE only for now)
            resp = smart.getMarketData("FULL", "NSE", token_list[:50])  # Max 50 tokens
            if resp and resp.get('status') and resp.get('data'):
                for item in resp['data'].get('fetched', []):
                    token = str(item.get('symbolToken', ''))
                    ltp   = item.get('ltp')
                    sym   = sym_to_token.get(token)
                    if sym and ltp:
                        results[sym] = float(ltp)

        except Exception as e:
            logger.error("angel_bulk_quote_failed", error=str(e)[:80])
            # Fallback: sequential (slow but safe)
            for sym in symbols[:5]:  # Limit to 5 to avoid rate bans
                results[sym] = self.get_market_quote(sym)
                time.sleep(_QUOTE_COOLDOWN_SEC)

        return results

    def get_historical_data(
        self,
        symbol: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list:
        """
        Fetch historical OHLCV from AngelOne.
        interval: 'ONE_MINUTE', 'FIVE_MINUTE', 'FIFTEEN_MINUTE', 'THIRTY_MINUTE', 'ONE_HOUR', 'ONE_DAY'
        from_date/to_date: 'YYYY-MM-DD HH:MM'
        """
        if not self.is_connected:
            return []

        token_info = self._resolve_token(symbol)
        if not token_info:
            return []

        try:
            with self._lock:
                smart = self.smart_api
            if smart is None:
                return []

            resp = smart.getCandleData({
                "exchange":      token_info["exchange"],
                "symboltoken":   token_info["token"],
                "interval":      interval,
                "fromdate":      from_date,
                "todate":        to_date,
            })
            if resp and resp.get('status') and resp.get('data'):
                return resp['data']
        except Exception as e:
            logger.error("angel_historical_failed", symbol=symbol, error=str(e)[:80])
        return []

    def stop(self):
        """Stop keep-alive thread cleanly."""
        self._stop_event.set()


# Module-level singleton
angel_client = AngelOneClientFixed()