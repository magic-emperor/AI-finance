"""
Breeze API Client — ICICI Direct
Fixed: lazy reconnect (reads env vars at call-time, not at module import).
This solves the 'not_installed' false-negative when other modules load this
before their own load_dotenv() runs.
"""
import os
import structlog
from dotenv import load_dotenv

logger = structlog.get_logger()


class BreezeClient:
    """
    Client for ICICI Direct Breeze API.
    Provides quotes and historical OHLCV for Indian markets (NSE).

    Key design decisions:
    - Lazy connection: _ensure_connected() is called on each API method.
      This means the singleton at bottom of file is safe to import at any time.
    - graceful degradation: if Breeze fails, all methods return None/empty
      so callers can fall back to yfinance without crashing.
    """

    def __init__(self):
        self.breeze        = None
        self.is_connected  = False
        self._api_key      = None
        self._secret_key   = None
        self._session_token = None

    def _ensure_connected(self) -> bool:
        """
        Lazy connect — reads credentials fresh each call so a new session token
        added to .env after process start is picked up immediately.
        Returns True if connected (or newly connected), False on failure.
        """
        # Reload .env to pick up any runtime updates (e.g. fresh session token)
        load_dotenv(override=True)

        api_key       = os.getenv('BREEZE_API_KEY')
        secret_key    = os.getenv('BREEZE_SECRET')
        session_token = os.getenv('BREEZE_SESSION_TOKEN')

        if not all([api_key, secret_key, session_token]):
            logger.info('breeze_missing_credentials', status='inactive')
            return False

        # Already connected with same credentials — skip reconnect
        if (self.is_connected and
                self._api_key      == api_key and
                self._session_token == session_token):
            return True

        # Connect (or reconnect with refreshed token)
        try:
            from breeze_connect import BreezeConnect
            self.breeze = BreezeConnect(api_key=api_key)
            self.breeze.generate_session(
                api_secret=secret_key,
                session_token=session_token
            )
            self._api_key       = api_key
            self._secret_key    = secret_key
            self._session_token = session_token
            self.is_connected   = True
            logger.info('breeze_connected', status='active')
            return True
        except ImportError:
            logger.warning('breeze_connect_not_installed')
            return False
        except Exception as e:
            logger.error('breeze_connect_failed', error=str(e))
            self.is_connected = False
            return False

    # ── Live Quotes ─────────────────────────────────────────────────────

    def get_price(self, symbol: str) -> float:
        """Fetch Last Traded Price for an NSE symbol (e.g. 'ITC.NS')."""
        if not self._ensure_connected():
            return None
        try:
            clean = symbol.replace('.NS', '').replace('^', '').upper()
            res   = self.breeze.get_quotes(
                stock_code=clean, exchange_code='NSE',
                expiry_date='', product_type='cash',
                right='', strike_price=''
            )
            if res.get('Status') == 200 and res.get('Success'):
                data = res['Success']
                if data:
                    return float(data[0].get('ltp', 0) or 0)
        except Exception as e:
            logger.error('breeze_quote_failed', symbol=symbol, error=str(e))
        return None

    # Keep backward-compat alias used by dashboard/command_center
    def get_quotes(self, symbol: str) -> float:
        return self.get_price(symbol)

    # ── Historical OHLCV ────────────────────────────────────────────────

    def get_historical_data(self, symbol: str, interval: str,
                            from_date: str, to_date: str) -> list:
        """
        Fetch historical OHLCV candles from Breeze.

        Args:
            symbol    : NSE symbol WITHOUT .NS suffix (e.g. 'ITC')
            interval  : '1minute', '5minute', '15minute', '30minute',
                        '1hour', '1day'
            from_date : 'YYYY-MM-DDT00:00:00.000Z'
            to_date   : 'YYYY-MM-DDT00:00:00.000Z'

        Returns:
            list of dicts with keys: datetime, open, high, low, close, volume
            Empty list on failure.
        """
        if not self._ensure_connected():
            return []
        try:
            res = self.breeze.get_historical_data_v2(
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                stock_code=symbol.replace('.NS', '').upper(),
                exchange_code='NSE',
                product_type='cash',
            )
            if res.get('Status') == 200 and res.get('Success'):
                return res['Success']
        except Exception as e:
            logger.error('breeze_hist_failed', symbol=symbol,
                         interval=interval, error=str(e))
        return []


# Module-level singleton — safe to import at any time due to lazy connect
breeze_client = BreezeClient()
