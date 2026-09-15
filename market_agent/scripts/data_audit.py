"""
Data Audit — verifies market_data coverage AND cleanliness, not just presence.

rebuild_db.py's own final log line promises this tool exists; it didn't.
Built per Phase 1 (data foundation) of the market_agent hardening plan.

Three checks, run in order:
  1. COVERAGE  — row count, date range, gap count per symbol/timeframe.
                 Answers "do we actually have COVID/2022/2023-25 history."
  2. OUTLIERS  — statistical return z-score + magnitude flags on data already
                 stored. Answers "is what we have clean, or full of bad ticks."
                 Distinguishes equity/index jumps (>40% single-bar = possible
                 unadjusted split/bonus) from futures jumps (GC=F/CL=F have no
                 corporate actions, so a large jump there is a candidate
                 continuous-contract roll artifact — NOT confirmed against an
                 authoritative roll calendar, flagged as "verify manually").
  3. CROSS-SOURCE — live-fetches a small recent sample from two independent
                 sources for a handful of symbols and reports how much they
                 disagree on the same bars. Hits real APIs; skip with
                 --skip-cross-source for a fast DB-only run.

Never silently drops or "fixes" flagged rows — matches CLAUDE.md's rule
(section 10): if data is suspect, raise a flag, do not interpolate silently.

Usage:
  python -X utf8 -m market_agent.scripts.data_audit
  python -X utf8 -m market_agent.scripts.data_audit --skip-cross-source
  python -X utf8 -m market_agent.scripts.data_audit --symbols RELIANCE.NS BTC-USD
"""
import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import argparse
import warnings
warnings.filterwarnings("ignore")

from datetime import datetime
import numpy as np
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from market_agent.data.storage.postgres import PostgresStorage
from market_agent.scripts.rebuild_db import fetch_clean_history, ALL_SYMBOLS

# Futures continuous contracts: no corporate actions possible; a large jump
# here is a candidate roll-artifact, not a split. Handled separately from
# equities in the outlier report.
FUTURES_SYMBOLS = {'GC=F', 'CL=F'}

# Magnitude thresholds
EQUITY_JUMP_THRESHOLD = 0.40   # >40% single-bar move on an equity/index = suspected unadjusted split
Z_SCORE_THRESHOLD      = 6.0   # rolling return z-score beyond this = statistical outlier
Z_SCORE_WINDOW         = 60    # bars used to compute the rolling mean/std of returns


def _load_symbol_frame(session, symbol: str, timeframe: str) -> pd.DataFrame:
    rows = session.execute(text("""
        SELECT timestamp, open_price, high_price, low_price, close_price, volume_val, data_source
        FROM market_data
        WHERE symbol = :symbol AND timeframe = :timeframe AND close_price IS NOT NULL
        ORDER BY timestamp ASC
    """), {"symbol": symbol, "timeframe": timeframe}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'source'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    for c in ['Open', 'High', 'Low', 'Close']:
        df[c] = df[c].astype(float)
    return df


def audit_coverage(session, symbols, timeframes) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("1. COVERAGE — row count, date range, gap count")
    print("=" * 78)
    out = []
    for symbol in symbols:
        for tf in timeframes:
            df = _load_symbol_frame(session, symbol, tf)
            if df.empty:
                out.append({'symbol': symbol, 'timeframe': tf, 'rows': 0, 'min': None, 'max': None,
                            'covid_covered': False, 'bear2022_covered': False, 'gap_days_gt_5': 0})
                continue
            min_d, max_d = df['timestamp'].min(), df['timestamp'].max()
            covid_covered = min_d.date() <= datetime(2020, 2, 1).date()
            bear2022_covered = min_d.date() <= datetime(2022, 1, 1).date()
            gap_days_gt_5 = 0
            if tf == '1d':
                deltas = df['timestamp'].diff().dt.days.dropna()
                gap_days_gt_5 = int((deltas > 5).sum())  # >5 calendar days between daily bars = suspect gap
            out.append({
                'symbol': symbol, 'timeframe': tf, 'rows': len(df),
                'min': min_d.date(), 'max': max_d.date(),
                'covid_covered': covid_covered, 'bear2022_covered': bear2022_covered,
                'gap_days_gt_5': gap_days_gt_5,
            })
    report = pd.DataFrame(out)
    with pd.option_context('display.max_rows', None, 'display.width', 200):
        print(report.to_string(index=False))
    return report


def audit_outliers(session, symbols, timeframes) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("2. OUTLIERS — statistical flags on data already stored")
    print("=" * 78)
    flagged = []
    for symbol in symbols:
        for tf in timeframes:
            df = _load_symbol_frame(session, symbol, tf)
            if len(df) < Z_SCORE_WINDOW + 5:
                continue
            df['ret'] = df['Close'].pct_change()
            roll_mean = df['ret'].rolling(Z_SCORE_WINDOW).mean()
            roll_std  = df['ret'].rolling(Z_SCORE_WINDOW).std()
            df['z'] = (df['ret'] - roll_mean) / roll_std.replace(0, np.nan)

            is_futures = symbol in FUTURES_SYMBOLS
            for _, row in df.iterrows():
                if pd.isna(row['z']):
                    continue
                big_move = abs(row['ret']) > EQUITY_JUMP_THRESHOLD
                stat_outlier = abs(row['z']) > Z_SCORE_THRESHOLD
                if not (big_move or stat_outlier):
                    continue
                if is_futures and big_move:
                    reason = 'candidate continuous-contract roll artifact (no corporate actions on futures; NOT checked against an authoritative roll calendar — verify manually)'
                elif big_move:
                    reason = 'possible unadjusted split/bonus (>40% single-bar move)'
                else:
                    reason = f'statistical outlier (return z-score={row["z"]:.1f})'
                flagged.append({
                    'symbol': symbol, 'timeframe': tf, 'date': row['timestamp'].date(),
                    'return_pct': round(row['ret'] * 100, 1), 'z_score': round(row['z'], 1),
                    'source': row['source'], 'reason': reason,
                })
    report = pd.DataFrame(flagged)
    if report.empty:
        print("No outliers flagged.")
    else:
        with pd.option_context('display.max_rows', None, 'display.width', 200):
            print(report.to_string(index=False))
    return report


def audit_cross_source(sample_symbols=('RELIANCE.NS', 'BTC-USD', 'AAPL')) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("3. CROSS-SOURCE — live comparison of independent sources (recent 60d, daily)")
    print("=" * 78)
    out = []
    for symbol in sample_symbols:
        primary = fetch_clean_history(symbol, '1d', '60d')
        if primary is None or primary.empty:
            out.append({'symbol': symbol, 'primary_source': None, 'note': 'primary fetch failed'})
            continue
        primary_source = primary.attrs.get('source')

        # Force yfinance explicitly as the independent cross-check source,
        # regardless of what fetch_clean_history picked as primary.
        import yfinance as yf
        try:
            yf_df = yf.Ticker(symbol).history(period='60d', interval='1d', auto_adjust=True)
            if hasattr(yf_df.index, 'tz') and yf_df.index.tz is not None:
                yf_df.index = yf_df.index.tz_convert(None)
        except Exception as e:
            out.append({'symbol': symbol, 'primary_source': primary_source, 'note': f'yfinance cross-check failed: {e}'})
            continue

        if primary_source == 'yfinance':
            out.append({'symbol': symbol, 'primary_source': primary_source,
                        'note': 'primary already yfinance — no independent second source available for this symbol'})
            continue

        merged = primary[['Close']].join(yf_df[['Close']], lsuffix='_primary', rsuffix='_yf', how='inner')
        if merged.empty:
            out.append({'symbol': symbol, 'primary_source': primary_source, 'note': 'no overlapping dates to compare'})
            continue
        merged['pct_diff'] = ((merged['Close_primary'] - merged['Close_yf']) / merged['Close_yf'] * 100).abs()
        out.append({
            'symbol': symbol, 'primary_source': primary_source, 'cross_source': 'yfinance',
            'overlapping_bars': len(merged),
            'mean_pct_diff': round(merged['pct_diff'].mean(), 3),
            'max_pct_diff': round(merged['pct_diff'].max(), 3),
        })
    report = pd.DataFrame(out)
    print(report.to_string(index=False))
    return report


def main():
    parser = argparse.ArgumentParser(description='Audit market_data coverage and cleanliness')
    parser.add_argument('--symbols', nargs='+', default=None, help='Limit to specific symbols (default: full watchlist)')
    parser.add_argument('--timeframes', nargs='+', default=['1d'], help='Timeframes to audit (default: 1d)')
    parser.add_argument('--skip-cross-source', action='store_true', help='Skip the live cross-source fetch (faster, DB-only)')
    args = parser.parse_args()

    symbols = args.symbols or ALL_SYMBOLS
    storage = PostgresStorage()
    session = storage.Session()
    try:
        coverage = audit_coverage(session, symbols, args.timeframes)
        outliers = audit_outliers(session, symbols, args.timeframes)
    finally:
        session.close()

    if not args.skip_cross_source:
        audit_cross_source()

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    n_covid = int(coverage['covid_covered'].sum()) if not coverage.empty else 0
    n_total = len(coverage)
    print(f"Symbols/timeframes reaching COVID window (pre-2020-02-01): {n_covid}/{n_total}")
    print(f"Rows flagged as possible outliers: {len(outliers)}")
    print("Flags are informational — nothing is auto-corrected. Review flagged rows before trusting them in training.")


if __name__ == '__main__':
    main()
