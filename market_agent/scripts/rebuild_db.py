"""
Phase 3, Step 3.2 — Full DB rebuild script.

Wipes market_data and refills with clean, verified OHLCV data from the
best available source per asset class:
  Crypto  → Binance public data mirror (data-api.binance.vision — no auth, no geo-block)
  NSE     → Breeze API (ICICI) first, yfinance fallback
  Others  → yfinance

4h timeframe:
  Crypto  → Fetched natively from Binance (4h interval supported)
  All others → Resampled from 1h candles (yfinance does not offer 4h natively)
  Resampling rule: Open=first, High=max, Low=min, Close=last, Volume=sum

RUN ORDER (MANDATORY):
  1. Confirm Phase 2 steps 2.1–2.4 are ALL complete (migrate_schema + backfill done).
  2. Confirm unknown 1m rows have been deleted from market_data.
  3. Dry run:  python -m market_agent.scripts.rebuild_db --dry-run
  4. Review output — confirm symbol count and candle estimates look right.
  5. Real run:  python -m market_agent.scripts.rebuild_db
     (will prompt for 'YES' — type it exactly)

Duration estimate: 3–6 hours depending on watchlist size and API speed.

Fixes applied vs previous version:
  FIX-1  Binance URL changed to data-api.binance.vision (no India geo-block, no auth)
  FIX-2  Added '2y': 730 to period_days (was missing — silently fetched only 1y)
  FIX-3  Added '4h', '5m', '30m' to Binance interval_map
  FIX-4  Added '5m', '30m' to Breeze interval_map
  FIX-5  Added 4h, 5m, 30m specs to FETCH_SPEC
  FIX-6  4h for non-crypto symbols built by resampling 1h (yfinance has no 4h)
  FIX-7  _fetch_breeze to_date now fetches end-of-day, not midnight (was dropping today)
  FIX-8  Added '2y' to Breeze period_days
  FIX-9  Added ETH-USD to CRYPTO list
  FIX-10 Added ^NSEI and INR=X to watchlist
  FIX-11 store_ohlc source fallback changed from 'unknown' to verified source tag
  FIX-12 Added Binance geo-block detection with clear error message
  FIX-13 Batch store now uses session-level bulk insert for performance
  FIX-14 Daily period extended 5y->10y to reach COVID (Feb-Apr 2020) crash and
         the 2022 bear market; added '10y' to both Binance/Breeze period_days
         maps (previously unmapped periods silently fell back to 365 days)
  FIX-15 Breeze get_historical_data_v2 caps at ~1000 candles/call with no
         pagination and does not error on truncation - detect a short return
         vs. requested period and fall back to yfinance instead of silently
         accepting a truncated "full" history
  FIX-16 Same truncation risk as FIX-15 existed in _fetch_binance's pagination
         loop (a rate-limited/errored page break returned partial data as if
         complete, with no check). Fixed the same way, but ONLY treats a short
         result as truncation when the loop broke due to an actual error -
         legitimately running out of history (e.g. BTC-USD before Binance's
         2017-08 listing date) is correct and must not be discarded in favor
         of a lower-quality yfinance fetch just because it's "short."
  FIX-17 Added 1m to FETCH_SPEC (period '8d') - previously 1m was not fetched
         by this script at all, left entirely to a separate live-drip path
         that had gone 5 months stale. 8d matches yfinance's actual hard cap
         ("Only 8 days worth of 1m granularity data are allowed to be fetched
         per request" - confirmed directly against the API) - there is no
         real use case for years of 1-minute bars regardless, so 8d is used
         consistently across all sources rather than letting Binance/Breeze
         fetch a longer, inconsistent depth just because they technically can.
"""
import argparse
import logging
import time
import hashlib
import json
import os
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime, timedelta
from market_agent.data.storage.postgres import PostgresStorage, normalize_bar_timestamp

log = logging.getLogger('rebuild_db')

# ── Watchlist ─────────────────────────────────────────────────────────────────
NSE_STOCKS = [
    'ITC.NS', 'HDFCBANK.NS', 'RELIANCE.NS', 'TATASTEEL.NS',
    'LT.NS', 'M&M.NS', 'ADANIENT.NS', 'ADANIPORTS.NS',
]
INDICES     = ['^NSEBANK', '^NSEI']          # FIX-10: added ^NSEI for regime detection
US_STOCKS   = ['NVDA', 'GOOGL', 'AAPL', 'AMD']
CRYPTO      = ['BTC-USD', 'ETH-USD']         # FIX-9: added ETH-USD
COMMODITIES = ['GC=F', 'CL=F']
FOREX       = ['GBPJPY=X', 'USDJPY=X', 'INR=X']  # FIX-10: added INR=X

ALL_SYMBOLS = NSE_STOCKS + INDICES + US_STOCKS + CRYPTO + COMMODITIES + FOREX


def _asset_class(symbol: str) -> str:
    if symbol in NSE_STOCKS or symbol in INDICES:
        return 'NSE'
    if symbol in CRYPTO:
        return 'CRYPTO'
    if symbol in COMMODITIES:
        return 'COMMODITY'
    if symbol in FOREX:
        return 'FOREX'
    if symbol in US_STOCKS:
        return 'US'
    return 'UNKNOWN'


# ── SOURCE_PLAN (data rebuild work order Part 4, R2/R3) ──────────────────────
# One row per (asset_class, interval), declared BEFORE any run starts, so
# which vendor answers a given series is never a function of which token or
# API happened to be alive that particular run. A pinned source that fails
# means the series is SKIPPED and reported — never silently substituted.
# Silent substitution (Breeze -> yfinance mid-run, both merged into the same
# series) is exactly what let the same NSE trading day get stored twice
# under two different price conventions (raw vs adjusted), ~24% apart.
#
# NSE-specific reasoning: Breeze natively serves only 1m/5m/30m/1d (never
# 15m/1h/4h at all) and caps at ~1000 rows/call with no pagination, so for
# anything Breeze cannot natively and fully serve, yfinance is the DECLARED
# choice up front — not a fallback discovered at runtime. Daily is yfinance
# ONLY: Breeze daily was the actual source of the ITC.NS corruption (raw
# price, 1000-row cap), so daily is moved off Breeze entirely rather than
# patched — that removes the ambiguity instead of managing it.
#
# 1m/5m/30m are ALSO pinned to yfinance, not Breeze, despite Breeze
# supporting these natively. Checked before deciding: FETCH_SPEC requests
# 1m@8d and 5m/30m@60d — both exactly at or within yfinance's own depth
# ceiling for those intervals, so Breeze offers ZERO additional history at
# these periods. Against that zero benefit: Breeze's session token expired
# 4 separate times in one working session, and Breeze daily was the
# confirmed, measured cause of the ITC.NS corruption. No evidence-based
# reason remains to prefer Breeze at the periods this file actually
# requests. Revisit only if FETCH_SPEC's periods are widened beyond
# yfinance's caps (Breeze still can't paginate past ~1000 rows/call even
# then, so widening would need Breeze pagination work first, not just a
# SOURCE_PLAN flip).
SOURCE_PLAN = {
    ('NSE', '1m'):  {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('NSE', '5m'):  {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('NSE', '15m'): {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('NSE', '30m'): {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('NSE', '1h'):  {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('NSE', '1d'):  {'source': 'yfinance', 'adjustment': 'adjusted'},

    ('US', '1m'):  {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('US', '5m'):  {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('US', '15m'): {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('US', '30m'): {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('US', '1h'):  {'source': 'yfinance', 'adjustment': 'adjusted'},
    ('US', '1d'):  {'source': 'yfinance', 'adjustment': 'adjusted'},

    ('CRYPTO', '1m'):  {'source': 'binance', 'adjustment': 'raw'},
    ('CRYPTO', '5m'):  {'source': 'binance', 'adjustment': 'raw'},
    ('CRYPTO', '15m'): {'source': 'binance', 'adjustment': 'raw'},
    ('CRYPTO', '30m'): {'source': 'binance', 'adjustment': 'raw'},
    ('CRYPTO', '1h'):  {'source': 'binance', 'adjustment': 'raw'},
    ('CRYPTO', '4h'):  {'source': 'binance', 'adjustment': 'raw'},
    ('CRYPTO', '1d'):  {'source': 'binance', 'adjustment': 'raw'},

    ('COMMODITY', '1m'):  {'source': 'yfinance', 'adjustment': 'raw'},
    ('COMMODITY', '5m'):  {'source': 'yfinance', 'adjustment': 'raw'},
    ('COMMODITY', '15m'): {'source': 'yfinance', 'adjustment': 'raw'},
    ('COMMODITY', '30m'): {'source': 'yfinance', 'adjustment': 'raw'},
    ('COMMODITY', '1h'):  {'source': 'yfinance', 'adjustment': 'raw'},
    ('COMMODITY', '1d'):  {'source': 'yfinance', 'adjustment': 'raw'},

    ('FOREX', '1m'):  {'source': 'yfinance', 'adjustment': 'raw'},
    ('FOREX', '5m'):  {'source': 'yfinance', 'adjustment': 'raw'},
    ('FOREX', '15m'): {'source': 'yfinance', 'adjustment': 'raw'},
    ('FOREX', '30m'): {'source': 'yfinance', 'adjustment': 'raw'},
    ('FOREX', '1h'):  {'source': 'yfinance', 'adjustment': 'raw'},
    ('FOREX', '1d'):  {'source': 'yfinance', 'adjustment': 'raw'},
}


def get_source_plan(symbol: str, interval: str) -> dict:
    ac = _asset_class(symbol)
    plan = SOURCE_PLAN.get((ac, interval))
    if plan is None:
        raise ValueError(
            f'No SOURCE_PLAN entry for asset_class={ac} interval={interval} '
            f'(symbol={symbol}). Add one explicitly rather than falling back.'
        )
    return plan

# ── Fetch specification per symbol ────────────────────────────────────────────
# IMPORTANT — 4h is NOT in this list for non-crypto symbols.
# For non-crypto: 4h is derived by resampling 1h in _resample_to_4h().
# For crypto:     4h is fetched natively from Binance (see CRYPTO_EXTRA_SPECS).
FETCH_SPEC = [
    # FIX-17: 1m added. yfinance hard-caps 1m at 8 days ("Only 8 days worth of
    # 1m granularity data are allowed to be fetched per request" - confirmed
    # directly against the API, not assumed). Binance/Breeze can technically
    # go deeper, but there is no real use case for years of 1-minute bars
    # (that's ~1440 rows/symbol/day - a 60d request alone took several
    # minutes to paginate through Binance) and no benefit to sourcing 1m
    # differently per asset class - 8d is the honest, consistent ceiling.
    # Previously 1m was not fetched by this script at all (see rebuild_db.py
    # history) - it was left to a separate, long-stale live-drip path.
    {'interval': '1m',  'period': '8d',  'label': 'scalp_1m_8d'},
    # yfinance hard-caps 1h data at 730 days
    {'interval': '1h',  'period': '2y',  'label': 'intraday_2yr'},
    # FIX-14: 5y didn't reach the COVID crash (Feb-Apr 2020) or the 2022 bear
    # market from a 2026 rebuild date. 10y is supported natively by yfinance
    # and was added explicitly to the Binance/Breeze period_days maps below
    # (do NOT change this to 'max' - the custom fetchers silently default to
    # 365 days via .get(period, 365) for any period string they don't recognize).
    {'interval': '1d',  'period': '10y', 'label': 'daily_10yr'},
    {'interval': '15m', 'period': '60d', 'label': 'scalp_60d'},
    {'interval': '30m', 'period': '60d', 'label': 'swing_60d'},   # FIX-5
    # 5m: yfinance caps at 60 days; Breeze can go further for NSE
    {'interval': '5m',  'period': '60d', 'label': 'entry_60d'},   # FIX-5
]

# Crypto-only extra specs (Binance supports these natively)
CRYPTO_EXTRA_SPECS = [
    {'interval': '4h',  'period': '3y',  'label': 'mtf_3yr'},    # FIX-5
]

# ─────────────────────────────────────────────────────────────────────────────
# BINANCE FETCH
# Uses data-api.binance.vision — the official public data mirror.
# No API key required. No geo-restriction. India-safe.
# ─────────────────────────────────────────────────────────────────────────────
# FIX-1: Changed from api.binance.com (geo-blocked in India) to
#         data-api.binance.vision (public mirror, no restriction).
BINANCE_BASE_URL = 'https://data-api.binance.vision/api/v3/klines'

def _check_binance_reachable() -> bool:
    """Quick connectivity check before starting a long fetch loop."""
    try:
        resp = requests.get(
            'https://data-api.binance.vision/api/v3/ping',
            timeout=8
        )
        return resp.status_code == 200
    except Exception:
        return False


def _fetch_binance(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """
    Binance public data mirror (free, no auth, India-safe).
    Used for Crypto symbols only.
    Returns OHLCV DataFrame with naive UTC index, or empty DataFrame on failure.
    """
    # FIX-2: Added '2y': 730 — was missing, silently defaulted to 1y
    # FIX-14: Added '10y' — was missing, would have silently defaulted to 1y
    # FIX-17: Added '8d' for the new 1m spec — same reason, would have
    # silently defaulted to 365 days without this.
    period_days = {
        '8d':  8,     # FIX-17
        '60d': 60,
        '1y':  365,
        '2y':  730,   # FIX-2
        '3y':  365 * 3,
        '5y':  365 * 5,
        '10y': 365 * 10,  # FIX-14
    }
    # FIX-3: Added '4h', '5m', '30m' — were missing, silently used '1h'
    interval_map = {
        '1m':  '1m',
        '5m':  '5m',   # FIX-3
        '15m': '15m',
        '30m': '30m',  # FIX-3
        '1h':  '1h',
        '4h':  '4h',   # FIX-3
        '1d':  '1d',
    }

    days       = period_days.get(period, 365)
    b_interval = interval_map.get(interval)
    if b_interval is None:
        log.error(f'_fetch_binance: unsupported interval "{interval}" — skipping')
        return pd.DataFrame()

    # BTC-USD → BTCUSDT
    b_symbol = symbol.replace('-USD', 'USDT').replace('/', '').upper()
    end_ms   = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    requested_start_ms = start_ms  # FIX-16: kept for truncation check below; start_ms is mutated during pagination
    all_rows = []
    # FIX-16: distinguishes "stopped early because of an error/rate-limit"
    # (a real truncation - should fall back to yfinance) from "stopped because
    # we legitimately ran out of history" (e.g. BTC-USD requesting 10y but
    # Binance only lists it from 2017-08 onward - NOT a bug, don't force a
    # fallback just because the result is shorter than requested).
    stopped_due_to_error = False

    while start_ms < end_ms:
        try:
            resp = requests.get(BINANCE_BASE_URL, params={
                'symbol':    b_symbol,
                'interval':  b_interval,
                'startTime': start_ms,
                'endTime':   end_ms,
                'limit':     1000,
            }, timeout=20)

            # Detect geo-block or auth error explicitly — FIX-12
            if resp.status_code == 451:
                log.error(
                    'Binance returned 451 (geo-restricted). '
                    'Enable VPN and retry. URL: ' + BINANCE_BASE_URL
                )
                return pd.DataFrame()
            if resp.status_code != 200:
                log.warning(f'Binance HTTP {resp.status_code} for {b_symbol} — stopping fetch')
                stopped_due_to_error = True
                break

            rows = resp.json()
            if not rows or not isinstance(rows, list):
                # Genuine exhaustion: Binance has nothing earlier than this for
                # this symbol (e.g. before its listing date) - not an error.
                break

            all_rows.extend(rows)
            # Advance window past last returned candle
            last_open_ms = rows[-1][0]
            if last_open_ms <= start_ms:
                # No progress — avoid infinite loop
                break
            start_ms = last_open_ms + 1
            time.sleep(0.15)   # polite rate limiting

        except requests.exceptions.Timeout:
            log.warning(f'Binance timeout for {b_symbol} {b_interval} — stopping early')
            stopped_due_to_error = True
            break
        except Exception as e:
            log.warning(f'Binance fetch error for {b_symbol}: {e}')
            stopped_due_to_error = True
            break

    if not all_rows:
        log.warning(f'Binance returned 0 rows for {b_symbol} {b_interval}')
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'qv', 'trades', 'tbb', 'tbq', 'ignore'
    ])
    df['ts']   = pd.to_datetime(df['ts'], unit='ms')
    df         = df.set_index('ts')[['open', 'high', 'low', 'close', 'volume']].astype(float)
    df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    df.index   = df.index.tz_localize(None)

    # FIX-16: same class of bug as FIX-15 (Breeze) - a rate-limited or
    # otherwise early-terminated pagination loop returns whatever partial data
    # it collected as a non-empty, "successful"-looking DataFrame, with no
    # indication it's short of what was requested. Only treat a short result
    # as truncation (and fall back to yfinance) when the loop actually broke
    # due to an error - a short result from legitimately running out of
    # history (e.g. BTC-USD before Binance's 2017-08 listing date) is correct
    # and must NOT be discarded in favor of a lower-quality yfinance fetch.
    if stopped_due_to_error:
        requested_start = pd.Timestamp(requested_start_ms, unit='ms')
        actual_start     = df.index.min()
        tolerance_days   = 3  # pagination retry slack - crypto trades 24/7, no weekend gap to allow for
        if (actual_start - requested_start).days > tolerance_days:
            log.warning(
                f'Binance truncated {b_symbol} {b_interval} (stopped early due to '
                f'an error): requested back to {requested_start.date()}, only got '
                f'back to {actual_start.date()} ({len(df)} rows) — falling back to '
                f'yfinance for full depth.'
            )
            return pd.DataFrame()

    return df


# ─────────────────────────────────────────────────────────────────────────────
# BREEZE FETCH (ICICI Direct)
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_breeze(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """
    Breeze API (ICICI Direct). Used for NSE stocks first.
    Falls back to yfinance if Breeze is unreachable or session expired.
    """
    try:
        from market_agent.data.ingestion.breeze_client import breeze_client
        if not breeze_client._ensure_connected():
            log.warning('Breeze not connected — session token may be expired')
            return pd.DataFrame()
    except Exception as e:
        log.warning(f'Breeze import/connect error: {e}')
        return pd.DataFrame()

    # FIX-8: Added '2y': 730
    # FIX-14: Added '10y' — was missing, would have silently defaulted to 1y
    # FIX-17: Added '8d' for the new 1m spec
    period_days = {
        '8d':  8,      # FIX-17
        '60d': 60,
        '1y':  365,
        '2y':  730,    # FIX-8
        '3y':  365 * 3,
        '5y':  365 * 5,
        '10y': 365 * 10,  # FIX-14
    }
    # Breeze v2 API only supports: 1minute, 5minute, 30minute, 1day.
    # 15m, 1h, and 4h are NOT supported natively and must fall back.
    interval_map = {
        '1m':  '1minute',
        '5m':  '5minute',
        '30m': '30minute',
        '1d':  '1day',
    }

    days       = period_days.get(period, 365)
    b_interval = interval_map.get(interval)
    if b_interval is None:
        log.error(f'_fetch_breeze: unsupported interval "{interval}" — skipping')
        return pd.DataFrame()

    nse_symbol = symbol.replace('.NS', '')
    from_dt    = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00.000Z')
    # FIX-7: Was '%Y-%m-%dT00:00:00.000Z' (midnight = today excluded). Now end-of-day.
    to_dt      = datetime.utcnow().strftime('%Y-%m-%dT23:59:59.000Z')

    try:
        data = breeze_client.get_historical_data(
            symbol=nse_symbol, interval=b_interval,
            from_date=from_dt, to_date=to_dt
        )
    except Exception as e:
        log.warning(f'Breeze get_historical_data failed for {symbol}: {e}')
        return pd.DataFrame()

    if not data:
        log.warning(f'Breeze returned empty data for {symbol} {interval}')
        return pd.DataFrame()

    rows = []
    for c in data:
        try:
            rows.append({
                'ts':     pd.to_datetime(c.get('datetime')),
                'Open':   float(c.get('open',   0)),
                'High':   float(c.get('high',   0)),
                'Low':    float(c.get('low',    0)),
                'Close':  float(c.get('close',  0)),
                'Volume': int(c.get('volume',   0)),
            })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df       = pd.DataFrame(rows).set_index('ts')
    df.index = pd.to_datetime(df.index)
    # ROOT-CAUSE FIX (data rebuild work order Part 4 Sec 4.1/4.2): Breeze's
    # 'datetime' field is a naive IST wall-clock string with no tz info, so
    # the old `if df.index.tz is not None` check never fired and this data
    # was stored as if it were already UTC -- off by 5:30 from true UTC.
    # For daily bars this didn't crash anything by itself, but it meant
    # Breeze's daily bar and yfinance's (correctly UTC-converted) daily bar
    # for the SAME real trading day ended up keyed under two different DB
    # dates, which is exactly how ITC.NS got 753 duplicated trading days
    # alternating between adjusted (yfinance) and raw (Breeze) prices.
    # Explicitly localize as IST, then convert to true UTC, matching the
    # convention yfinance and Binance already produce.
    if df.index.tz is None:
        df.index = df.index.tz_localize('Asia/Kolkata').tz_convert('UTC').tz_localize(None)
    else:
        df.index = df.index.tz_convert('UTC').tz_localize(None)

    # FIX-15: Breeze's get_historical_data_v2 caps out around 1000 candles per
    # call with no pagination - it does NOT error when the requested period
    # exceeds this, it just silently returns a truncated, more-recent-only
    # window (confirmed: a 10y daily request returned exactly 1000 rows
    # starting ~4 years back, not 10). Without this check, that truncated
    # result would be silently accepted as "the full period" since it isn't
    # empty - defeating the whole point of widening FETCH_SPEC's period.
    # Detect truncation and return empty so the existing yfinance fallback in
    # fetch_clean_history() kicks in instead of quietly under-delivering.
    requested_start = datetime.utcnow() - timedelta(days=days)
    actual_start     = df.index.min()
    tolerance_days   = 15  # weekends/holidays slack
    if (actual_start - requested_start).days > tolerance_days:
        log.warning(
            f'Breeze truncated {symbol} {interval}/{period}: requested back to '
            f'{requested_start.date()}, only got back to {actual_start.date()} '
            f'({len(df)} rows) — falling back to yfinance for full depth.'
        )
        return pd.DataFrame()

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 4H RESAMPLING (non-crypto only)
# ─────────────────────────────────────────────────────────────────────────────
def _resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """
    Build 4h candles by resampling 1h OHLCV data.
    Rule: Open=first, High=max, Low=min, Close=last, Volume=sum.
    Requires at least 4 rows of 1h data to produce one 4h candle.
    Returns empty DataFrame if input has fewer than 4 rows.
    """
    if df_1h is None or len(df_1h) < 4:
        return pd.DataFrame()

    df = df_1h[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df.index = pd.to_datetime(df.index)

    resampled = df.resample('4h', closed='left', label='left').agg({
        'Open':   'first',
        'High':   'max',
        'Low':    'min',
        'Close':  'last',
        'Volume': 'sum',
    }).dropna(subset=['Close'])

    # Drop any rows where Open is NaN (incomplete first candle)
    resampled = resampled[resampled['Open'].notna()]
    resampled = resampled[resampled['Close'] > 0]
    return resampled


def _fetch_yfinance(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """yfinance does NOT support 4h natively — that's handled by resampling
    1h in rebuild(), never routed here."""
    if interval == '4h':
        return pd.DataFrame()
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df is not None and not df.empty:
            if hasattr(df.index, 'tz') and df.index.tz is not None:
                df.index = df.index.tz_convert(None)
        return df
    except Exception as e:
        log.error(f'{symbol} {interval}: yfinance failed: {e}')
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN FETCH ROUTER — pinned source, no fallback (data rebuild work order R2)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_clean_history(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """
    Fetch from EXACTLY the source SOURCE_PLAN declares for this symbol's
    asset class and interval. No fallback chain: a failure here means this
    series is skipped for this run and must be reported as such by the
    caller, never silently filled from a different vendor mid-run. That
    silent substitution — Breeze failing over to yfinance and both landing
    in the same (symbol, timeframe) series — is what let the same NSE
    trading day get stored twice under two different price conventions.

    Returns clean OHLCV DataFrame (naive UTC index) with .attrs['source']
    and .attrs['price_adjustment'] set, or an empty DataFrame if the
    pinned source failed or returned nothing.
    """
    plan   = get_source_plan(symbol, interval)
    source = plan['source']

    if source == 'binance':
        df = _fetch_binance(symbol, interval, period)
    elif source == 'breeze':
        df = _fetch_breeze(symbol, interval, period)
    elif source == 'yfinance':
        df = _fetch_yfinance(symbol, interval, period)
    else:
        raise ValueError(f'Unknown source "{source}" in SOURCE_PLAN for {symbol} {interval}')

    if df is None or df.empty:
        log.warning(f'{symbol} {interval}/{period}: pinned source "{source}" '
                    f'returned no data — SKIPPING (no fallback per SOURCE_PLAN)')
        return pd.DataFrame()

    # ── Clean + validate ──────────────────────────────────────────────────────
    needed = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
    if 'Close' not in needed:
        log.error(f'{symbol} {interval}: DataFrame missing Close column — skipping')
        return pd.DataFrame()

    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])
    df = df[df['Close'] > 0]
    df = df[df['High'] >= df['Low']]
    # High>=Low alone does NOT catch a bar whose open/close sit entirely
    # outside its own high/low range -- confirmed on yfinance INR=X
    # 2023-11-02, where Open=Close=85.194 against High=83.33/Low=83.17
    # (a ~2.2% vendor data defect, verified by direct re-fetch, not
    # something this pipeline introduced).
    #
    # Threshold is 1%, not zero-tolerance: a strict zero-tolerance version
    # of this check was tried first and rejected because GBPJPY=X/
    # USDJPY=X/INR=X daily bars carry a small (<1%), consistent open/close-
    # vs-high/low inconsistency on ~5-6% of rows -- most plausibly how
    # yfinance aggregates a continuously-quoted FX "day" with no single
    # clean exchange close. Dropping all of those would remove ~100+ bars
    # per FX series for a sub-1% labeling quirk, a worse trade than
    # leaving them in and flagging them (verify_rebuild.py's V6 gate
    # reports the same <1% population as informational). Only the
    # unambiguous, large violations are dropped here.
    _n_before = len(df)
    _excess = pd.concat([
        (df['Open']  - df['High']).clip(lower=0), (df['Close'] - df['High']).clip(lower=0),
        (df['Low']   - df['Open']).clip(lower=0), (df['Low']   - df['Close']).clip(lower=0),
    ], axis=1).max(axis=1)
    _violation_pct = (_excess / df['High'].replace(0, pd.NA)).fillna(0)
    df = df[_violation_pct < 0.01]
    if len(df) < _n_before:
        log.warning(f'{symbol} {interval}: dropped {_n_before - len(df)} bar(s) with a '
                    f'>=1% open/close-vs-high/low inconsistency (vendor data defect)')
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    df.attrs['source']           = source
    df.attrs['price_adjustment'] = plan['adjustment']

    log.info(
        f'{symbol} {interval}/{period}: {len(df)} candles from {source} '
        f'({df.index[0]} → {df.index[-1]})'
    )
    return df


MANIFEST_PATH = os.path.join(os.path.dirname(__file__), 'data_manifest.json')


def _compute_content_hash(rows: list) -> str:
    """
    Deterministic hash over one series' content (data rebuild work order
    R6/V11). Re-running the rebuild for an unchanged source should produce
    an identical hash; a change means the upstream source's data changed
    under us, which is itself worth knowing, not just "huh, different
    numbers this time" as happened across the two earlier rebuild attempts.

    Rounds to 4 decimal places before hashing. Measured evidence: two
    successive yfinance fetches of the same historical daily bar can
    differ at the ~1e-7 relative level (e.g. 169.26828 vs
    169.2682647705078) -- float64 jitter from yfinance's own pipeline, not
    a real price change. Hashing at 6dp made every idempotence check fail
    on this noise alone, which is a false alarm, not a rebuild defect. 4dp
    is still far finer than any real tick size for the instruments here.
    """
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda x: x['timestamp']):
        h.update(
            f"{r['timestamp'].isoformat()}|{r['Open']:.4f}|{r['High']:.4f}|"
            f"{r['Low']:.4f}|{r['Close']:.4f}|{r['Volume']}".encode()
        )
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# STORE HELPER — atomic delete-then-write per series (R4), normalized (R1)
# ─────────────────────────────────────────────────────────────────────────────
def _store_df(storage: PostgresStorage, df: pd.DataFrame, symbol: str,
              interval: str, source: str) -> dict:
    """
    Replace the ENTIRE (symbol, interval) series atomically. Every row's
    timestamp is run through normalize_bar_timestamp first — this is the
    single point where the ITC.NS-class bug (the same trading day stored
    twice under two different DB dates, once per source's own convention)
    is prevented at the source rather than patched after the fact.

    Returns a manifest-ready dict describing what happened. Never partially
    writes: replace_ohlc_series is one transaction, and if it raises, the
    existing rows for this series are left untouched (nothing is deleted
    unless the new data was fully ready to replace it).
    """
    if df is None or df.empty:
        return {'status': 'SKIPPED', 'reason': 'pinned source returned no data'}

    if not source or source in ('unknown', 'unset'):
        return {'status': 'FAILED', 'reason': f'invalid source "{source}"'}

    rows = []
    for ts, row in df.iterrows():
        raw_ts  = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts
        norm_ts = normalize_bar_timestamp(raw_ts, interval, symbol)
        rows.append({
            'timestamp': norm_ts,
            'Open':   float(row['Open']),
            'High':   float(row['High']),
            'Low':    float(row['Low']),
            'Close':  float(row['Close']),
            'Volume': int(row['Volume']),
        })

    # Daily normalization can legitimately collapse two raw rows onto the
    # same trading date only if the source itself returned a duplicate —
    # dedupe defensively, keeping the later row (mirrors the upstream
    # `~df.index.duplicated(keep='last')` convention already applied
    # in fetch_clean_history).
    dedup = {}
    for r in rows:
        dedup[r['timestamp']] = r
    rows = list(dedup.values())

    try:
        written = storage.replace_ohlc_series(symbol, interval, rows, source)
    except Exception as e:
        return {'status': 'FAILED', 'reason': f'DB write error: {e}'}

    rows_sorted = sorted(rows, key=lambda r: r['timestamp'])
    return {
        'status':           'SUCCESS',
        'source':           source,
        'price_adjustment': df.attrs.get('price_adjustment'),
        'rows_written':     written,
        'first_bar':        rows_sorted[0]['timestamp'].isoformat(),
        'last_bar':         rows_sorted[-1]['timestamp'].isoformat(),
        'content_hash':     _compute_content_hash(rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN REBUILD
# ─────────────────────────────────────────────────────────────────────────────
def rebuild(dry_run: bool = False, resume: bool = False, only_symbols: list = None,
           only_intervals: list = None):
    """
    Full data rebuild per the data rebuild work order (plan Part 4).

    Design changes from the previous version, and why:
      - No global wipe-then-refill. Each (symbol, interval) series is only
        replaced once its replacement data has been fully fetched and
        validated (R4) — a fetch failure now leaves the existing series
        untouched rather than deleting first and hoping the refetch
        succeeds. `--resume` and the default mode are now the same in this
        respect; `resume`/`dry_run` are kept as CLI flags for compatibility.
      - Source is PINNED per (asset_class, interval) via SOURCE_PLAN (R2) —
        no opportunistic Breeze-then-yfinance fallback merged into one
        series. A pinned-source failure is reported as SKIPPED, not
        silently substituted.
      - An advisory lock (R5) prevents this script and the live scan /
        scheduled task from writing concurrently.
      - A manifest (R6) is written with per-series source, adjustment,
        row count, date range and content hash — the missing piece that
        let two earlier rebuild attempts diverge without anyone noticing.
    """
    storage = PostgresStorage()

    if not dry_run:
        log.info('Acquiring rebuild advisory lock...')
        if not storage.try_acquire_rebuild_lock():
            log.error(
                'Could not acquire the rebuild lock — another rebuild (or a '
                'process holding the same advisory lock) is already running '
                'against this database. Aborting rather than writing '
                'concurrently (R5). Try again once the other process exits.'
            )
            return
        log.info('Rebuild lock acquired.')

    try:
        if resume:
            log.info('RESUME MODE — per-series atomic replace, same as default. '
                     'Existing rows for a series are only touched once its '
                     'replacement data is fully fetched and validated.')
        elif dry_run:
            log.info('DRY RUN — no DB writes will happen.')
        else:
            log.info('Starting rebuild — per-series atomic replace (no global wipe).')

        # ── Binance connectivity check ────────────────────────────────────────
        if not dry_run:
            log.info('Checking Binance public mirror connectivity...')
            if not _check_binance_reachable():
                log.error(
                    'Cannot reach data-api.binance.vision. Crypto series are '
                    'pinned to binance only (SOURCE_PLAN) — they will be '
                    'reported SKIPPED this run, not silently degraded to '
                    'yfinance. If you are in India: enable VPN and retry.'
                )
            else:
                log.info('Binance public mirror: reachable.')

        manifest = {
            'run_started_at': datetime.utcnow().isoformat(),
            'series': {},   # "{symbol}|{interval}" -> result dict
        }
        total_stored  = 0
        total_skipped = 0
        total_failed  = 0

        symbols   = only_symbols or ALL_SYMBOLS
        intervals_filter = set(only_intervals) if only_intervals else None

        # Keep 1h DataFrames in memory so we can resample to 4h without re-fetching
        _cached_1h: dict = {}

        for symbol in symbols:
            is_crypto = any(x in symbol for x in ['-USD', 'USDT'])
            specs = list(FETCH_SPEC)
            if is_crypto:
                specs = specs + CRYPTO_EXTRA_SPECS
            if intervals_filter:
                specs = [s for s in specs if s['interval'] in intervals_filter]

            for spec in specs:
                interval = spec['interval']
                period   = spec['period']
                label    = spec['label']
                key      = f'{symbol}|{interval}'

                if interval == '4h' and not is_crypto:
                    continue  # handled by resampling below

                log.info(f'--- {symbol} | {interval} | {period} [{label}] ---')
                df = fetch_clean_history(symbol, interval, period)

                if df is None or df.empty:
                    log.warning(f'SKIP: no data for {symbol} {interval}')
                    manifest['series'][key] = {
                        'status': 'SKIPPED',
                        'reason': 'pinned source unavailable or returned no data',
                    }
                    total_skipped += 1
                    continue

                if interval == '1h' and not is_crypto:
                    _cached_1h[symbol] = df

                if dry_run:
                    log.info(f'DRY: would replace {len(df)} candles — {symbol} {interval} '
                            f'(source={df.attrs.get("source")})')
                    continue

                source = df.attrs.get('source')
                result = _store_df(storage, df, symbol, interval, source)
                manifest['series'][key] = result
                if result['status'] == 'SUCCESS':
                    total_stored += result['rows_written']
                    log.info(f"Replaced {result['rows_written']} candles — {symbol} {interval} "
                             f"[{result['first_bar']} -> {result['last_bar']}]")
                elif result['status'] == 'SKIPPED':
                    total_skipped += 1
                else:
                    total_failed += 1
                    log.error(f"FAILED {symbol} {interval}: {result['reason']}")

            # ── 4h from cached 1h (non-crypto) ────────────────────────────────
            if not is_crypto and not dry_run and symbol in _cached_1h:
                key = f'{symbol}|4h'
                log.info(f'--- {symbol} | 4h | resampled_from_1h ---')
                df_1h  = _cached_1h[symbol]
                df_4h  = _resample_to_4h(df_1h)
                src_1h = df_1h.attrs.get('source', 'yfinance')
                src_4h = f'{src_1h}_resampled_4h'
                if df_4h is not None and not df_4h.empty:
                    df_4h.attrs['price_adjustment'] = df_1h.attrs.get('price_adjustment')
                    result = _store_df(storage, df_4h, symbol, '4h', src_4h)
                    manifest['series'][key] = result
                    if result['status'] == 'SUCCESS':
                        total_stored += result['rows_written']
                        log.info(f"Replaced {result['rows_written']} 4h candles — {symbol} "
                                f"(resampled from {src_1h})")
                    else:
                        total_failed += 1
                else:
                    log.warning(f'4h resample produced empty DataFrame for {symbol} — need more 1h data')
                    manifest['series'][key] = {'status': 'SKIPPED', 'reason': 'insufficient 1h data to resample'}
                    total_skipped += 1

        manifest['run_finished_at'] = datetime.utcnow().isoformat()
        manifest['totals'] = {
            'stored': total_stored, 'skipped': total_skipped, 'failed': total_failed,
        }

        if not dry_run:
            with open(MANIFEST_PATH, 'w') as f:
                json.dump(manifest, f, indent=2, default=str)
            log.info(f'Manifest written: {MANIFEST_PATH}')

        log.info(f'\n{"DRY RUN — " if dry_run else ""}Rebuild complete: '
                 f'{total_stored} candles stored, {total_skipped} series skipped, '
                 f'{total_failed} series failed')

        skipped_keys = [k for k, v in manifest['series'].items() if v['status'] == 'SKIPPED']
        failed_keys  = [k for k, v in manifest['series'].items() if v['status'] == 'FAILED']
        if skipped_keys:
            log.warning(f'SKIPPED ({len(skipped_keys)}): {skipped_keys}')
        if failed_keys:
            log.error(f'FAILED ({len(failed_keys)}): {failed_keys}')

        if not dry_run:
            log.info('Next step: run scripts/verify_rebuild.py to check the 11 verification gates.')
    finally:
        if not dry_run:
            storage.release_rebuild_lock()
            log.info('Rebuild lock released.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Rebuild market_data from verified, pinned sources')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be fetched without touching DB')
    parser.add_argument('--resume', action='store_true',
                        help='(Compatibility flag — behavior is now the same as default: '
                             'per-series atomic replace, nothing wiped up front.)')
    parser.add_argument('--symbols', nargs='+', default=None,
                        help='Limit to these symbols (default: all)')
    parser.add_argument('--intervals', nargs='+', default=None,
                        help='Limit to these intervals (default: all in FETCH_SPEC)')
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    rebuild(dry_run=args.dry_run, resume=args.resume,
           only_symbols=args.symbols, only_intervals=args.intervals)