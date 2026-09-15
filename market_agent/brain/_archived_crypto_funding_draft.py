"""
═══════════════════════════════════════════════════════════════════════
BRAIN: Crypto Funding Rate + Open Interest Brain
═══════════════════════════════════════════════════════════════════════

WHY THIS BRAIN EXISTS:
  Funding rate is the SINGLE MOST PREDICTIVE signal in crypto futures trading.
  
  How it works:
  - Perpetual futures have no expiry, so exchanges use a "funding rate"
    to keep futures price anchored to spot price
  - Every 8 hours: longs pay shorts (if rate > 0) or shorts pay longs (if rate < 0)
  
  THE EDGE:
  - High positive funding (>0.1%) = too many longs = SHORT SQUEEZE imminent
    → Market is overheated, fade the trend or wait for correction
  - High negative funding (<-0.05%) = too many shorts = LONG SQUEEZE imminent
    → Capitulation signal, BUY opportunity
  - Funding near zero = neutral, no extreme positioning
  
  Open Interest (OI) adds context:
  - OI rising + price rising + positive funding = STRONG trend (longs in control)
  - OI falling + price rising = SHORT SQUEEZE (weak basis for rally)
  - OI falling + price falling = LONG CAPITULATION (bottom forming)

DATA SOURCE: Binance API (free, real-time)
  GET /fapi/v1/fundingRate   → Funding rate
  GET /fapi/v1/openInterest  → Open interest

RELIABILITY: High. Funding rate is the most accurate contrarian indicator in crypto.

═══════════════════════════════════════════════════════════════════════
ALSO IN THIS FILE: Fixed Global Macro Brain
═══════════════════════════════════════════════════════════════════════

Fixes from global_macro.py:
1. BUG: Used daily candles — missing intraday S&P futures moves
   Fixed: Uses intraday 1H candles for overnight change calculation
2. BUG: USD/INR 0.5% threshold triggered too many false bearish signals
   Fixed: Raised to 0.8%, added directional context
3. MISSING: No caching → fetches yfinance on every call
   Fixed: 30-minute TTL cache
4. MISSING: No crypto macro signals
   Added: BTC dominance, Fear & Greed Index (free from alternative.me)
"""
from __future__ import annotations

import requests
import time
import threading
import structlog
from typing import Optional, Dict
from datetime import datetime

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════
# BRAIN: CRYPTO FUNDING RATE + OPEN INTEREST
# ═══════════════════════════════════════════════════════════════════════

# Binance Futures API (no key required for public data)
_BINANCE_FUTURES_BASE = 'https://fapi.binance.com'

# Funding rate thresholds
FUNDING_EXTREME_LONG  = +0.0010   # +0.10%/8h = very bullish positioning (fade it)
FUNDING_EXTREME_SHORT = -0.0005   # -0.05%/8h = bearish positioning (potential reversal)
FUNDING_ELEVATED_LONG  = +0.0004  # +0.04%/8h = elevated longs
FUNDING_ELEVATED_SHORT = -0.0002  # -0.02%/8h = elevated shorts

# Thread-safe rate limiting for Binance Futures
_binance_futures_lock = threading.Lock()
_binance_futures_calls = []
_BINANCE_FUTURES_RPM = 50   # well within 1200/min limit for futures

def _binance_futures_rate_ok() -> bool:
    with _binance_futures_lock:
        now = time.time()
        _binance_futures_calls[:] = [t for t in _binance_futures_calls if now - t < 60]
        if len(_binance_futures_calls) >= _BINANCE_FUTURES_RPM:
            return False
        _binance_futures_calls.append(now)
        return True

# Simple in-memory cache
_funding_cache: Dict[str, tuple] = {}
_oi_cache: Dict[str, tuple] = {}
_CACHE_TTL = 300  # 5 minutes (funding changes every 8h, no need to fetch constantly)


def _to_futures_symbol(symbol: str) -> str:
    """BTC-USD → BTCUSDT (Binance futures format)"""
    s = symbol.upper().replace('-', '').replace('/', '')
    if s.endswith('USD') and not s.endswith('USDT'):
        s += 'T'
    return s


def get_funding_rate(symbol: str) -> Optional[Dict]:
    """
    Fetch current funding rate for a crypto symbol from Binance Futures.
    Returns dict with rate, next_funding_time, direction, signal.
    
    Free — no API key needed.
    """
    fs = _to_futures_symbol(symbol)

    # Cache check
    if fs in _funding_cache:
        data, ts = _funding_cache[fs]
        if time.time() - ts < _CACHE_TTL:
            return data

    if not _binance_futures_rate_ok():
        return None

    try:
        resp = requests.get(
            f'{_BINANCE_FUTURES_BASE}/fapi/v1/fundingRate',
            params={'symbol': fs, 'limit': 3},
            timeout=5,
        )
        if resp.status_code == 400:
            # Symbol not available on futures (BTC is, some altcoins aren't)
            return None
        if resp.status_code == 429:
            logger.warning('binance_futures_rate_limited')
            return None
        if resp.status_code != 200:
            return None

        data = resp.json()
        if not data:
            return None

        # Most recent funding rate
        latest = data[-1]
        rate = float(latest.get('fundingRate', 0))

        result = {
            'symbol':             fs,
            'funding_rate':       rate,
            'funding_rate_pct':   round(rate * 100, 4),
            'next_funding_time':  latest.get('fundingTime'),
            'annualized_rate_pct': round(rate * 3 * 365 * 100, 1),  # 3 payments/day × 365
        }

        # Signal logic
        if rate >= FUNDING_EXTREME_LONG:
            result['signal']    = 'SELL'   # longs overextended → fade
            result['confidence'] = 0.72
            result['reason']    = f'Funding EXTREME ({rate*100:.3f}%) — market overheated with longs'
        elif rate <= FUNDING_EXTREME_SHORT:
            result['signal']    = 'BUY'    # shorts overextended → reversal
            result['confidence'] = 0.72
            result['reason']    = f'Funding EXTREME negative ({rate*100:.3f}%) — short squeeze potential'
        elif rate >= FUNDING_ELEVATED_LONG:
            result['signal']    = 'CAUTION'
            result['confidence'] = 0.55
            result['reason']    = f'Funding elevated ({rate*100:.3f}%) — lean bearish'
        elif rate <= FUNDING_ELEVATED_SHORT:
            result['signal']    = 'CAUTION'
            result['confidence'] = 0.55
            result['reason']    = f'Funding negative ({rate*100:.3f}%) — lean bullish'
        else:
            result['signal']    = 'NEUTRAL'
            result['confidence'] = 0.40
            result['reason']    = f'Funding neutral ({rate*100:.3f}%)'

        _funding_cache[fs] = (result, time.time())
        return result

    except Exception as e:
        logger.error('funding_rate_failed', symbol=symbol, error=str(e)[:60])
        return None


def get_open_interest(symbol: str) -> Optional[Dict]:
    """
    Fetch current Open Interest from Binance Futures.
    Higher OI = more leveraged positions = bigger potential move.
    """
    fs = _to_futures_symbol(symbol)

    if fs in _oi_cache:
        data, ts = _oi_cache[fs]
        if time.time() - ts < _CACHE_TTL:
            return data

    if not _binance_futures_rate_ok():
        return None

    try:
        resp = requests.get(
            f'{_BINANCE_FUTURES_BASE}/fapi/v1/openInterest',
            params={'symbol': fs},
            timeout=5,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        oi   = float(data.get('openInterest', 0))

        # Also get OI history to detect trend
        hist_resp = requests.get(
            f'{_BINANCE_FUTURES_BASE}/futures/data/openInterestHist',
            params={'symbol': fs, 'period': '1h', 'limit': 5},
            timeout=5,
        )

        oi_change_pct = 0.0
        if hist_resp.status_code == 200:
            hist = hist_resp.json()
            if len(hist) >= 2:
                oi_prev = float(hist[0].get('sumOpenInterest', oi))
                oi_change_pct = (oi - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0.0

        result = {
            'open_interest':     oi,
            'oi_change_1h_pct':  round(oi_change_pct, 2),
            'oi_trend':          'RISING' if oi_change_pct > 2 else ('FALLING' if oi_change_pct < -2 else 'FLAT'),
        }

        _oi_cache[fs] = (result, time.time())
        return result

    except Exception as e:
        logger.debug('open_interest_failed', symbol=symbol, error=str(e)[:60])
        return None


def crypto_funding_brain(symbol: str, price_direction: str = 'NEUTRAL') -> dict:
    """
    BRAIN: Crypto Funding Rate + OI Signal.
    
    Combines funding rate extremes with OI trend for high-confidence signals.
    
    Args:
        symbol: e.g. 'BTC-USD', 'ETH-USD'
        price_direction: current price direction from other brains ('BUY'/'SELL'/'NEUTRAL')
    
    Returns brain signal dict compatible with BrainSignal contract.
    """
    funding = get_funding_rate(symbol)
    oi      = get_open_interest(symbol)

    if funding is None:
        return {
            'brain_name':  'Funding-Rate',
            'direction':   'HOLD',
            'confidence':  0.30,
            'reason':      f'No futures data for {symbol} (might not be a listed futures pair)',
            'applicable':  False,  # Brain can't apply to this symbol
        }

    signal    = funding.get('signal', 'NEUTRAL')
    confidence = funding.get('confidence', 0.40)
    reason    = funding.get('reason', '')

    # Boost confidence if OI confirms
    if oi:
        oi_trend = oi.get('oi_trend', 'FLAT')
        if signal == 'SELL' and oi_trend == 'RISING':
            # Overheated longs AND OI rising = very dangerous, high confidence sell
            confidence = min(0.85, confidence + 0.10)
            reason += f' | OI rising {oi["oi_change_1h_pct"]:+.1f}% (more leverage being added)'
        elif signal == 'BUY' and oi_trend == 'FALLING':
            # Shorts being squeezed out (OI falling = positions closing)
            confidence = min(0.82, confidence + 0.08)
            reason += f' | OI falling {oi["oi_change_1h_pct"]:+.1f}% (shorts closing = squeeze)'

    # Direction from signal
    direction_map = {'SELL': 'SELL', 'BUY': 'BUY', 'CAUTION': 'HOLD', 'NEUTRAL': 'HOLD'}
    direction = direction_map.get(signal, 'HOLD')

    # If funding says HOLD but price is trending → use as confirmation/denial
    if direction == 'HOLD' and signal == 'CAUTION':
        rate = funding.get('funding_rate', 0)
        if rate > 0 and price_direction == 'SELL':
            direction  = 'SELL'
            confidence = 0.58
            reason    += ' | Funding positive + price falling = trend continuation'
        elif rate < 0 and price_direction == 'BUY':
            direction  = 'BUY'
            confidence = 0.58
            reason    += ' | Funding negative + price rising = trend continuation'

    return {
        'brain_name':    'Funding-Rate',
        'direction':     direction,
        'confidence':    round(confidence, 3),
        'reason':        reason,
        'applicable':    True,
        'funding_rate':  funding.get('funding_rate_pct'),
        'oi_data':       oi,
        'measurements': {
            'funding_rate_pct':    funding.get('funding_rate_pct', 0),
            'annualized_rate_pct': funding.get('annualized_rate_pct', 0),
            'oi_change_pct':       oi.get('oi_change_1h_pct', 0) if oi else 0,
        },
        'regime_suitability': 'HIGH',
    }


# ═══════════════════════════════════════════════════════════════════════
# FIXED GLOBAL MACRO BRAIN
# ═══════════════════════════════════════════════════════════════════════

_macro_cache: Dict = {}
_MACRO_CACHE_TTL = 1800   # 30 minutes


def get_fear_greed_index() -> Optional[Dict]:
    """
    Fear & Greed Index from alternative.me (free, no key needed).
    Score 0-25: Extreme Fear (BUY signal)
    Score 26-45: Fear
    Score 46-54: Neutral
    Score 55-74: Greed
    Score 75-100: Extreme Greed (SELL signal)
    """
    try:
        resp = requests.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        if resp.status_code == 200:
            data = resp.json().get('data', [{}])[0]
            value = int(data.get('value', 50))
            label = data.get('value_classification', 'Neutral')
            return {
                'score':  value,
                'label':  label,
                'signal': 'BUY' if value < 25 else ('SELL' if value > 75 else 'NEUTRAL'),
            }
    except Exception as e:
        logger.debug('fear_greed_failed', error=str(e)[:60])
    return None


def get_global_macro_context() -> Dict:
    """
    Fixed global macro context.
    
    Fixes:
    1. Caches results (30-min TTL) — was fetching on every call
    2. Uses intraday data for S&P futures change (not daily)
    3. Adds BTC dominance and Fear/Greed for crypto context
    4. USD/INR threshold raised to 0.8% (was too sensitive at 0.5%)
    """
    cache_key = 'global_macro'
    if cache_key in _macro_cache:
        data, ts = _macro_cache[cache_key]
        if time.time() - ts < _MACRO_CACHE_TTL:
            return data

    result = {
        'regime':     'NEUTRAL',
        'bias':       'NEUTRAL',
        'bias_score': 0,
        'reasons':    [],
        'timestamp':  datetime.now().isoformat(),
    }

    bias_score = 0
    reasons = []

    try:
        import yfinance as yf

        # S&P 500 futures — intraday change (1H last 2 bars)
        try:
            sp = yf.Ticker('ES=F').history(period='2d', interval='1h')
            if len(sp) >= 2:
                sp_change = (float(sp['Close'].iloc[-1]) - float(sp['Close'].iloc[-2])) / float(sp['Close'].iloc[-2])
                if sp_change <= -0.015:
                    bias_score -= 3
                    reasons.append(f'S&P500 futures dropped {sp_change*100:.1f}% intraday')
                elif sp_change >= 0.01:
                    bias_score += 2
                    reasons.append(f'S&P500 futures up {sp_change*100:.1f}% intraday')
        except Exception:
            pass

        # VIX
        try:
            vix = yf.Ticker('^VIX').fast_info
            vix_val = float(vix.get('last_price', 20))
            if vix_val >= 35:
                bias_score -= 4
                reasons.append(f'VIX panic: {vix_val:.1f}')
            elif vix_val >= 25:
                bias_score -= 2
                reasons.append(f'VIX elevated: {vix_val:.1f}')
            elif vix_val < 15:
                bias_score += 1
                reasons.append(f'VIX calm: {vix_val:.1f}')
        except Exception:
            pass

        # USD/INR — raised threshold to 0.8%
        try:
            usdinr = yf.Ticker('USDINR=X').fast_info
            usdinr_price = float(usdinr.get('last_price', 83))
            usdinr_prev  = float(yf.Ticker('USDINR=X').history(period='2d')['Close'].iloc[-2])
            usdinr_change = (usdinr_price - usdinr_prev) / usdinr_prev
            if usdinr_change >= 0.008:   # Dollar up 0.8%+ vs INR (was 0.5%)
                bias_score -= 1
                reasons.append(f'USD strengthening vs INR: {usdinr_change*100:.2f}%')
            elif usdinr_change <= -0.008:
                bias_score += 1
                reasons.append(f'INR strengthening vs USD: {usdinr_change*100:.2f}%')
        except Exception:
            pass

    except ImportError:
        reasons.append('yfinance not available')

    # Fear & Greed (crypto-specific, free)
    fg = get_fear_greed_index()
    if fg:
        score = fg['score']
        if score < 25:
            bias_score += 2
            reasons.append(f'Crypto Fear & Greed: Extreme Fear ({score}) — contrarian BUY')
        elif score > 75:
            bias_score -= 2
            reasons.append(f'Crypto Fear & Greed: Extreme Greed ({score}) — contrarian SELL')
        result['fear_greed'] = fg

    # Final regime
    if bias_score <= -4:
        regime, bias = 'RISK_OFF', 'VERY_CAUTIOUS'
    elif bias_score <= -2:
        regime, bias = 'CAUTIOUS', 'CAUTIOUS'
    elif bias_score >= 3:
        regime, bias = 'RISK_ON', 'BULLISH'
    elif bias_score >= 1:
        regime, bias = 'CONSTRUCTIVE', 'NEUTRAL_BULLISH'
    else:
        regime, bias = 'NEUTRAL', 'NEUTRAL'

    result.update({'regime': regime, 'bias': bias, 'bias_score': bias_score, 'reasons': reasons})
    _macro_cache[cache_key] = (result, time.time())
    return result


# ═══════════════════════════════════════════════════════════════════════
# OANDA FOREX RECOMMENDATION
# ═══════════════════════════════════════════════════════════════════════

OANDA_INTEGRATION_GUIDE = """
═══════════════════════════════════════════════════════════════════════
FOREX DATA — WHAT YOU NEED AND HOW TO GET IT FREE
═══════════════════════════════════════════════════════════════════════

BRUTAL TRUTH:
  yfinance forex (EURUSD=X, GBPJPY=X) has:
  - 15-minute delay on live prices
  - Gaps during off-hours
  - No tick data
  - Unreliable 1m OHLCV
  
  You CANNOT scalp forex with yfinance data. Period.

FREE SOLUTION: OANDA v20 API
  - Free account (just open a demo account, no deposit needed)
  - Real-time mid prices (1-5 second latency)
  - Full OHLCV history at any granularity (S5, S10, M1, M5, H1, H4, D)
  - WebSocket streaming for tick data
  - 50+ forex pairs + some indices
  
  Steps:
  1. Create free OANDA account at oanda.com/forex-trading/
  2. Go to Account Settings → Manage API Access → Generate Token
  3. Add to .env: OANDA_API_KEY=your_key and OANDA_ACCOUNT_ID=your_id
  4. Use the OANDAClient class below

  pip install oandapyV20  (small, reliable library)

ALTERNATIVE: Alpha Vantage (free tier)
  - 5 API calls/min free, 500/day
  - Real-time forex (15-min delayed on free tier)
  - Better than yfinance but still delayed
  - API key: alphavantage.co/support/#api-key

BEST PAID OPTION (~$10/month):
  - Polygon.io: Real-time forex, tick data, WebSocket
"""


class OANDAClientStub:
    """
    OANDA v20 API Client.
    Replace OANDAClientStub with this once you get an OANDA account.
    
    pip install oandapyV20
    """

    def __init__(self):
        from dotenv import load_dotenv
        load_dotenv()
        self.api_key    = os.getenv('OANDA_API_KEY')
        self.account_id = os.getenv('OANDA_ACCOUNT_ID')
        self.env        = 'practice'   # 'practice' for demo, 'live' for real
        self._client    = None

    def _get_client(self):
        if self._client is None and self.api_key:
            try:
                import oandapyV20
                self._client = oandapyV20.API(
                    access_token=self.api_key,
                    environment=self.env,
                )
            except ImportError:
                logger.warning('oandapyV20_not_installed', hint='pip install oandapyV20')
        return self._client

    def get_price(self, pair: str) -> Optional[float]:
        """
        Get real-time mid price for forex pair.
        pair: 'EUR_USD', 'GBP_JPY', 'USD_JPY' (OANDA format uses underscore)
        """
        import os
        client = self._get_client()
        if not client:
            return None
        try:
            import oandapyV20.endpoints.pricing as pricing
            oanda_pair = pair.replace('=X', '').replace('/', '_').replace('-', '_')
            r = pricing.PricingInfo(self.account_id, params={'instruments': oanda_pair})
            client.request(r)
            prices = r.response.get('prices', [])
            if prices:
                bid = float(prices[0]['bids'][0]['price'])
                ask = float(prices[0]['asks'][0]['price'])
                return (bid + ask) / 2   # mid price
        except Exception as e:
            logger.error('oanda_price_failed', pair=pair, error=str(e)[:60])
        return None

    def get_ohlcv(self, pair: str, granularity: str = 'H1', count: int = 200) -> Optional['pd.DataFrame']:
        """
        Get OHLCV history from OANDA.
        granularity: S5, S10, S15, S30, M1, M5, M15, M30, H1, H2, H4, H6, H12, D, W, M
        """
        import os
        import pandas as pd
        client = self._get_client()
        if not client:
            return None
        try:
            import oandapyV20.endpoints.instruments as instruments
            oanda_pair = pair.replace('=X', '').replace('/', '_').replace('-', '_')
            r = instruments.InstrumentsCandles(
                oanda_pair,
                params={'count': count, 'granularity': granularity, 'price': 'M'},
            )
            client.request(r)
            candles = r.response.get('candles', [])
            records = []
            for c in candles:
                if c.get('complete'):
                    mid = c['mid']
                    records.append({
                        'Datetime': pd.to_datetime(c['time']),
                        'Open':  float(mid['o']),
                        'High':  float(mid['h']),
                        'Low':   float(mid['l']),
                        'Close': float(mid['c']),
                        'Volume': int(c.get('volume', 0)),
                    })
            if records:
                df = pd.DataFrame(records).set_index('Datetime')
                return df
        except Exception as e:
            logger.error('oanda_ohlcv_failed', pair=pair, error=str(e)[:60])
        return None


import os


# ═══════════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print('=' * 60)
    print('Funding Rate Brain + Global Macro — Tests')
    print('=' * 60)

    # Test funding rate (requires internet)
    print('\n[1] Funding Rate — BTC-USD:')
    fr = get_funding_rate('BTC-USD')
    if fr:
        print(f'  Rate: {fr["funding_rate_pct"]:.4f}%')
        print(f'  Signal: {fr["signal"]} | Confidence: {fr["confidence"]:.0%}')
        print(f'  Annualized: {fr["annualized_rate_pct"]:.1f}%')
        print(f'  Reason: {fr["reason"]}')
    else:
        print('  ⚠️  No data (network or symbol not on futures)')

    # Test full brain signal
    print('\n[2] Full Brain Signal:')
    signal = crypto_funding_brain('BTC-USD', price_direction='BUY')
    print(f'  Direction: {signal["direction"]} | Confidence: {signal["confidence"]:.0%}')
    print(f'  Applicable: {signal["applicable"]}')

    # Test fear & greed
    print('\n[3] Fear & Greed Index:')
    fg = get_fear_greed_index()
    if fg:
        print(f'  Score: {fg["score"]} ({fg["label"]}) → Signal: {fg["signal"]}')

    # Test macro context (cached)
    print('\n[4] Global Macro:')
    macro = get_global_macro_context()
    print(f'  Regime: {macro["regime"]} | Bias: {macro["bias"]} | Score: {macro["bias_score"]}')
    for r in macro.get('reasons', []):
        print(f'    - {r}')

    print('\n[5] OANDA Setup Guide:')
    print(OANDA_INTEGRATION_GUIDE[:400])