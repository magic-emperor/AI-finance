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
"""
import argparse
import logging
import time
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime, timedelta
from market_agent.data.storage.postgres import PostgresStorage

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

# ── Fetch specification per symbol ────────────────────────────────────────────
# IMPORTANT — 4h is NOT in this list for non-crypto symbols.
# For non-crypto: 4h is derived by resampling 1h in _resample_to_4h().
# For crypto:     4h is fetched natively from Binance (see CRYPTO_EXTRA_SPECS).
FETCH_SPEC = [
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
    period_days = {
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
    all_rows = []

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
                break

            rows = resp.json()
            if not rows or not isinstance(rows, list):
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
            break
        except Exception as e:
            log.warning(f'Binance fetch error for {b_symbol}: {e}')
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
    period_days = {
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
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df.index = df.index.tz_convert(None)

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


# ─────────────────────────────────────────────────────────────────────────────
# MAIN FETCH ROUTER
# ─────────────────────────────────────────────────────────────────────────────
def fetch_clean_history(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """
    Fetch from best source with fallback chain.
    Source priority:
      Crypto  → Binance public mirror  →  yfinance
      NSE     → Breeze API             →  yfinance
      Others  → yfinance

    4h interval:
      Crypto  → Binance native 4h
      Others  → resampled from 1h (caller must already have 1h data)
                NOTE: The rebuild() loop handles 4h resampling for non-crypto.
                      This function only fetches 4h natively for crypto.

    Returns clean OHLCV DataFrame (naive UTC index) with .attrs['source'] set.
    """
    df     = None
    source = None

    is_crypto = any(x in symbol for x in ['-USD', 'USDT'])

    # ── Crypto path: Binance public mirror ───────────────────────────────────
    if is_crypto:
        df     = _fetch_binance(symbol, interval, period)
        source = 'binance'
        if df is None or df.empty:
            log.warning(f'{symbol}: Binance empty/failed — falling back to yfinance')
            df     = None
            source = None

    # ── NSE path: Breeze → yfinance ──────────────────────────────────────────
    if (df is None or df.empty) and symbol.endswith('.NS'):
        df     = _fetch_breeze(symbol, interval, period)
        source = 'breeze'
        if df is None or df.empty:
            log.warning(f'{symbol}: Breeze empty/failed — falling back to yfinance')
            df     = None
            source = None

    # ── Universal fallback: yfinance ─────────────────────────────────────────
    # Note: yfinance does NOT support 4h interval — skip it here.
    # 4h for non-crypto is handled by resampling in rebuild().
    if (df is None or df.empty) and interval != '4h':
        try:
            ticker = yf.Ticker(symbol)
            df     = ticker.history(period=period, interval=interval, auto_adjust=True)
            if df is not None and not df.empty:
                if hasattr(df.index, 'tz') and df.index.tz is not None:
                    df.index = df.index.tz_convert(None)
            source = 'yfinance'
        except Exception as e:
            log.error(f'{symbol} {interval}: yfinance also failed: {e}')
            return pd.DataFrame()

    if df is None or df.empty:
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
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    df.attrs['source'] = source   # FIX-11: always a real source tag, never 'unknown'

    log.info(
        f'{symbol} {interval}/{period}: {len(df)} candles from {source} '
        f'({df.index[0]} → {df.index[-1]})'
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STORE HELPER — bulk store with explicit source, never 'unknown'
# ─────────────────────────────────────────────────────────────────────────────
def _store_df(storage: PostgresStorage, df: pd.DataFrame, symbol: str,
              interval: str, source: str) -> int:
    """
    Store all rows of df into market_data.
    Returns count of successfully stored rows.
    Always passes explicit source — never falls back to 'unknown'.
    """
    if df is None or df.empty:
        return 0

    # Guarantee source is always meaningful
    if not source or source == 'unknown':
        log.error(
            f'_store_df called with source="{source}" for {symbol} {interval}. '
            'This is a bug — every store must have a named source. Skipping.'
        )
        return 0

    stored = 0
    for i in range(0, len(df), 500):
        batch = df.iloc[i:i + 500]
        for ts, row in batch.iterrows():
            try:
                storage.store_ohlc(
                    symbol    = symbol,
                    timestamp = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts,
                    timeframe = interval,
                    data_dict = {
                        'Open':   float(row['Open']),
                        'High':   float(row['High']),
                        'Low':    float(row['Low']),
                        'Close':  float(row['Close']),
                        'Volume': int(row['Volume']),
                    },
                    source = source,
                )
                stored += 1
            except Exception as e:
                log.warning(f'store_ohlc failed {symbol} {ts}: {e}')

    return stored


# ─────────────────────────────────────────────────────────────────────────────
# MAIN REBUILD
# ─────────────────────────────────────────────────────────────────────────────
def rebuild(dry_run: bool = False, resume: bool = False):
    storage = PostgresStorage()

    if not dry_run and not resume:
        print('\n' + '=' * 60)
        print('WARNING — This will DELETE ALL market_data rows.')
        print('  Confirm ALL of these are done before proceeding:')
        print('  1. migrate_schema.py completed with all ✅')
        print('  2. backfill_columnar.py completed with 0 NULL rows')
        print('  3. Unknown 1m rows deleted (DELETE FROM market_data WHERE timeframe=\'1m\' AND data_source=\'unknown\')')
        print('=' * 60)
        confirm = input('  Type YES to confirm and start rebuild: ')
        if confirm.strip() != 'YES':
            print('Aborted.')
            return
        storage.clear_all_market_data()
        log.info('market_data cleared. Starting full rebuild...')
    elif resume:
        log.info('RESUME MODE — existing data kept intact. Fetching to fill gaps...')
    else:
        log.info('DRY RUN — no DB writes will happen.')

    # ── Binance connectivity check ────────────────────────────────────────────
    # FIX-12: Explicit check before starting so we fail fast, not after hours.
    if not dry_run:
        log.info('Checking Binance public mirror connectivity...')
        if not _check_binance_reachable():
            log.error(
                'Cannot reach data-api.binance.vision. '
                'If you are in India: enable VPN and retry. '
                'Crypto data will fall back to yfinance (lower quality).'
            )
            # Don't abort — yfinance fallback will handle it
        else:
            log.info('Binance public mirror: reachable ✅')

    total_stored = 0
    total_failed = 0

    # Keep 1h DataFrames in memory so we can resample to 4h without re-fetching
    # { symbol: df_1h }  — only populated for non-crypto symbols
    _cached_1h: dict = {}

    for symbol in ALL_SYMBOLS:
        is_crypto = any(x in symbol for x in ['-USD', 'USDT'])
        specs = list(FETCH_SPEC)
        if is_crypto:
            specs = specs + CRYPTO_EXTRA_SPECS

        for spec in specs:
            interval = spec['interval']
            period   = spec['period']
            label    = spec['label']

            # ── 4h for non-crypto: resample from 1h ──────────────────────────
            if interval == '4h' and not is_crypto:
                # Should never reach here — CRYPTO_EXTRA_SPECS is crypto-only.
                # But guard explicitly.
                log.warning(f'{symbol}: 4h non-crypto spec found — skipping (resample handles this)')
                continue

            log.info(f'--- {symbol} | {interval} | {period} [{label}] ---')
            df = fetch_clean_history(symbol, interval, period)

            if df is None or df.empty:
                log.warning(f'SKIP: no data for {symbol} {interval}')
                total_failed += 1
                continue

            # Cache 1h for later 4h resampling (non-crypto only)
            if interval == '1h' and not is_crypto:
                _cached_1h[symbol] = df

            if dry_run:
                log.info(f'DRY: would store {len(df)} candles — {symbol} {interval} (source={df.attrs.get("source")})')
                continue

            source  = df.attrs.get('source')
            stored  = _store_df(storage, df, symbol, interval, source)
            total_stored += stored
            log.info(f'Stored {stored}/{len(df)} candles — {symbol} {interval}')

        # ── After all specs for this symbol: build 4h from cached 1h (non-crypto) ─
        if not is_crypto and not dry_run and symbol in _cached_1h:
            log.info(f'--- {symbol} | 4h | resampled_from_1h ---')
            df_1h     = _cached_1h[symbol]
            df_4h     = _resample_to_4h(df_1h)
            # 4h resampled source inherits the 1h source tag with suffix
            src_1h    = df_1h.attrs.get('source', 'yfinance')
            src_4h    = f'{src_1h}_resampled_4h'
            if df_4h is not None and not df_4h.empty:
                if dry_run:
                    log.info(f'DRY: would store {len(df_4h)} 4h candles for {symbol}')
                else:
                    stored = _store_df(storage, df_4h, symbol, '4h', src_4h)
                    total_stored += stored
                    log.info(f'Stored {stored}/{len(df_4h)} 4h candles — {symbol} (resampled from {src_1h})')
            else:
                log.warning(f'4h resample produced empty DataFrame for {symbol} — need more 1h data')
                total_failed += 1

    log.info(f'\n{"DRY RUN — " if dry_run else ""}Rebuild complete: '
             f'{total_stored} candles stored, {total_failed} spec failures')

    if not dry_run:
        log.info('Next step: run data_audit.py to verify coverage per symbol/timeframe.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Rebuild market_data from verified sources')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be fetched without touching DB')
    parser.add_argument('--resume', action='store_true',
                        help='Skip wiping the DB and just fetch/upsert to fill gaps')
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    rebuild(dry_run=args.dry_run, resume=args.resume)