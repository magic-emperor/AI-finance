"""
Cross-validate stored NSE data against Breeze (ICICI broker feed).

Breeze returns RAW (unadjusted) prices; our stored data is yfinance with
auto_adjust=True (dividend/split adjusted). So absolute prices legitimately
diverge whenever a corporate action falls inside the window. Three checks
are run, from weakest to strongest evidence:

  1. RECENT absolute prices -- after the latest corporate action, adjusted
     and raw should nearly coincide. Large divergence here = real problem.
  2. DAILY RETURNS across the whole overlap -- returns are invariant to a
     constant adjustment factor, so they should match on every day except
     an ex-dividend/split day itself.
  3. ADJUSTMENT-RATIO DRIFT -- the strongest check. raw/adjusted should be
     PIECEWISE CONSTANT: one fixed ratio between corporate actions, then a
     clean step at each one. If this ratio wanders randomly instead of
     sitting on flat steps, that means prices and/or dates don't actually
     line up between the two sources -- a real defect, not adjustment.

Requires a live Breeze session token in .env (BREEZE_API_KEY, BREEZE_SECRET,
BREEZE_SESSION_TOKEN) -- these expire roughly daily. Only covers NSE daily
bars within Breeze's ~1000-row/no-pagination cap (~2.7 years back).

Usage:
  python -X utf8 -m market_agent.scripts.cross_validate_breeze
  python -X utf8 -m market_agent.scripts.cross_validate_breeze --symbols ITC.NS RELIANCE.NS --days 200
"""
import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import argparse
import warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv(override=True)

import pandas as pd
from datetime import datetime, timedelta
from market_agent.data.ingestion.breeze_client import breeze_client
from market_agent.data.storage.postgres import PostgresStorage, MarketData

DEFAULT_SYMBOLS = ['ITC.NS', 'HDFCBANK.NS', 'RELIANCE.NS', 'TATASTEEL.NS',
                   'LT.NS', 'M&M.NS', 'ADANIENT.NS', 'ADANIPORTS.NS']


def validate_symbol(sess, symbol: str, days: int) -> dict:
    nse = symbol.replace('.NS', '')
    frm = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00.000Z')
    to  = datetime.utcnow().strftime('%Y-%m-%dT23:59:59.000Z')
    raw = breeze_client.get_historical_data(symbol=nse, interval='1day',
                                            from_date=frm, to_date=to)
    if not raw:
        return {'symbol': symbol, 'status': 'BREEZE_EMPTY'}

    b = pd.DataFrame([{
        'date': pd.to_datetime(r['datetime']).date(),
        'close': float(r['close']),
    } for r in raw]).set_index('date').sort_index()

    db = sess.query(MarketData).filter(
        MarketData.symbol == symbol, MarketData.timeframe == '1d').all()
    y = pd.DataFrame([{'date': r.timestamp.date(),
                       'close': float(r.close_price)} for r in db]
                     ).set_index('date').sort_index()

    j = b.join(y, lsuffix='_breeze', rsuffix='_yf', how='inner').dropna()
    if j.empty:
        return {'symbol': symbol, 'status': 'NO_OVERLAP'}

    # 1. Recent absolute price agreement
    recent = j.tail(30)
    rel = ((recent['close_breeze'] - recent['close_yf']).abs() / recent['close_breeze'])

    # 2. Daily return agreement
    r_b = j['close_breeze'].pct_change().dropna()
    r_y = j['close_yf'].pct_change().dropna()
    rd = (r_b - r_y).abs()

    # 3. Adjustment-ratio drift -- the decisive check. Round to 4dp to get
    # discrete "steps"; within each step, measure how much the UNROUNDED
    # ratio actually wanders. Near-zero wander = dates and prices both
    # line up exactly; any real drift means something doesn't match.
    ratio = j['close_breeze'] / j['close_yf']
    ratio_rounded = ratio.round(4)
    n_steps = ratio_rounded.nunique()
    max_wander = ratio.groupby(ratio_rounded).apply(lambda s: s.max() - s.min()).max()

    return {
        'symbol': symbol, 'status': 'OK', 'n_days': len(j),
        'first': j.index[0], 'last': j.index[-1],
        'recent_price_mean_pct': rel.mean() * 100, 'recent_price_max_pct': rel.max() * 100,
        'return_mean_pct': rd.mean() * 100, 'return_max_pct': rd.max() * 100,
        'n_steps': n_steps, 'max_wander': max_wander,
    }


def main():
    parser = argparse.ArgumentParser(description='Cross-validate stored NSE data against Breeze')
    parser.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS)
    parser.add_argument('--days', type=int, default=400,
                       help='Lookback window (Breeze caps at ~1000 rows/call with no pagination)')
    args = parser.parse_args()

    if not breeze_client._ensure_connected():
        print('Breeze is not connected -- session token likely expired or missing in .env. '
              'Refresh BREEZE_SESSION_TOKEN and retry.')
        return 1

    storage = PostgresStorage()
    sess = storage.Session()

    print('=' * 78)
    print('CROSS-VALIDATION: stored data vs Breeze (ICICI broker feed)')
    print('=' * 78)

    all_clean = True
    for symbol in args.symbols:
        r = validate_symbol(sess, symbol, args.days)
        if r['status'] != 'OK':
            print(f"\n{symbol}: {r['status']}")
            continue
        print(f"\n{r['symbol']}:  {r['n_days']} overlapping trading days "
              f"({r['first']} -> {r['last']})")
        print(f"  recent-30 abs price diff: mean {r['recent_price_mean_pct']:.4f}%  "
              f"max {r['recent_price_max_pct']:.4f}%")
        print(f"  daily return diff:        mean {r['return_mean_pct']:.4f}%  "
              f"max {r['return_max_pct']:.4f}%")
        print(f"  adjustment-ratio steps:   {r['n_steps']} distinct step(s), "
              f"max wander within a step = {r['max_wander']:.6f}")
        if r['max_wander'] > 0.001:
            all_clean = False
            print(f"  *** WANDER ABOVE 0.001 -- investigate: prices or dates may not "
                  f"line up between sources ***")

    sess.close()
    print('\n' + '=' * 78)
    print('All checked symbols show flat, piecewise-constant adjustment ratios '
          '(clean cross-validation).' if all_clean else
          'One or more symbols show ratio drift -- see *** flags above.')
    print('=' * 78)
    return 0 if all_clean else 1


if __name__ == '__main__':
    sys.exit(main())
