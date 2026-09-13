"""
Mechanism test for the liquidity-sweep hypothesis (plan Part "STOP TUNING.
TEST THE MECHANISM FIRST." — Step 1/2/3).

THIS IS NOT A STRATEGY BACKTEST. It has no gates, no confidence scoring, no
R:R, no P&L. It tests one narrow claim:

    "When price wicks past a prior swing level by some margin and closes
     back through it, does price subsequently move in the reversal
     direction MORE than it would from a random comparable bar?"

Two free parameters only:
    --depth-atr   how far the wick must pierce the level (in ATR units)
    --lookback    how many bars back a "prior swing level" can be found

Everything else is a MEASUREMENT, not a gate:
  - forward return in the predicted direction, in ATR units, at several
    horizons, for detected sweep events
  - the same measurement for a CONTROL sample of non-event bars, matched
    by ATR percentile (volatility) and assigned a random direction at the
    event sample's own UP/DOWN ratio
  - split by year, so a real mechanism must show up in more than one
    year/regime, not just a lucky window (COVID 2020 + 2022 bear are only
    reachable on daily bars — see plan's Step 0 note on vendor depth caps)
  - split by "level prominence" (genuine range extreme vs. a fractal pivot
    sitting mid-range) — tests the "false level" failure mode found by hand
  - split by whether the immediate post-signal gap is included or excluded
    — tests the "lucky gap" failure mode found by hand
  - MAE distribution among events whose direction was eventually correct
    — tests the "stop too tight" failure mode found by hand, by measuring
    how much heat a correct trade actually takes instead of assuming it

Pre-committed pass/fail threshold (stated BEFORE running, per
hypothesis-registry discipline): the mean forward return in the predicted
direction must differ from the control's at p < 0.05 (Mann-Whitney U),
with a non-trivial effect size, in a majority of individual years tested.
If it does not clear that bar, the mechanism is not real and no arrangement
of gates on top of it will make it real.

Usage:
  python -X utf8 -m market_agent.scripts.measure_sweep_mechanism
  python -X utf8 -m market_agent.scripts.measure_sweep_mechanism --depth-atr 0.25 --lookback 60
"""
import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import argparse
import warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
from scipy import stats

from market_agent.data.storage.postgres import PostgresStorage

HORIZONS = [1, 3, 5, 10, 20]
PIVOT_BARS = 5          # bars each side for a fractal pivot — structural, not tuned
MIN_LEVEL_AGE = 5        # level must predate the signal bar by this many bars
RANDOM_SEED = 42

SYMBOLS = [
    'ITC.NS', 'HDFCBANK.NS', 'RELIANCE.NS', 'TATASTEEL.NS', 'LT.NS',
    'M&M.NS', 'ADANIENT.NS', 'ADANIPORTS.NS', '^NSEBANK', '^NSEI',
    'NVDA', 'GOOGL', 'AAPL', 'AMD',
    'BTC-USD', 'ETH-USD', 'GC=F', 'CL=F', 'GBPJPY=X', 'USDJPY=X', 'INR=X',
]


def load_daily(symbol: str) -> pd.DataFrame:
    storage = PostgresStorage()
    rows = storage.get_latest_data(symbol, '1d', limit=5000)
    if not rows:
        return None
    df = pd.DataFrame([r['data'] for r in rows], index=[r['timestamp'] for r in rows]).sort_index()
    for c in ('Open', 'High', 'Low', 'Close'):
        if c not in df.columns:
            return None
    return df


def compute_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Vectorized Wilder's ATR over the whole series (not just the latest bar)."""
    high, low, prev_close = df['High'], df['Low'], df['Close'].shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def find_pivots(df: pd.DataFrame, pivot_bars: int = PIVOT_BARS):
    """
    Fractal pivots using ONLY bars up to and including each candidate bar's
    own neighborhood (no lookahead beyond what a walk-forward scan at bar i
    could have already seen once i >= pivot bar + pivot_bars).
    Returns two boolean Series: is_pivot_high, is_pivot_low.
    """
    high, low = df['High'], df['Low']
    n = len(df)
    is_high = pd.Series(False, index=df.index)
    is_low = pd.Series(False, index=df.index)
    h, l = high.values, low.values
    for i in range(pivot_bars, n - pivot_bars):
        window_h = h[i - pivot_bars:i + pivot_bars + 1]
        window_l = l[i - pivot_bars:i + pivot_bars + 1]
        if h[i] >= window_h.max():
            is_high.iloc[i] = True
        if l[i] <= window_l.min():
            is_low.iloc[i] = True
    return is_high, is_low


def detect_sweeps(df: pd.DataFrame, atr: pd.Series, is_pivot_high: pd.Series,
                   is_pivot_low: pd.Series, depth_atr: float, lookback: int,
                   min_level_age: int = MIN_LEVEL_AGE) -> pd.DataFrame:
    """
    Walk forward bar by bar (no lookahead: at bar i, only pivots from bars
    < i - min_level_age are eligible levels). Detect a bullish sweep (wick
    below a prior swing low by >= depth_atr, close back above it) or a
    bearish sweep (mirror). Returns one row per detected event.
    """
    n = len(df)
    events = []
    pivot_low_prices = df['Low'].where(is_pivot_low)
    pivot_high_prices = df['High'].where(is_pivot_high)

    for i in range(max(lookback, 20), n):
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        window_start = max(0, i - lookback)
        eligible_end = i - min_level_age  # levels must predate the signal bar

        c_low, c_high, c_close = df['Low'].iloc[i], df['High'].iloc[i], df['Close'].iloc[i]

        # Most recent qualifying swing low in [window_start, eligible_end)
        lows_slice = pivot_low_prices.iloc[window_start:max(window_start, eligible_end)]
        lows_slice = lows_slice.dropna()
        if not lows_slice.empty:
            level = lows_slice.iloc[-1]
            below_by = level - c_low
            if below_by >= depth_atr * a and c_close > level:
                events.append({
                    'symbol': None, 'idx': i, 'date': df.index[i], 'direction': 'BUY',
                    'level': level, 'depth_atr': below_by / a, 'atr': a,
                    'level_age_bars': int(i - df.index.get_loc(lows_slice.index[-1])),
                })

        # Most recent qualifying swing high
        highs_slice = pivot_high_prices.iloc[window_start:max(window_start, eligible_end)]
        highs_slice = highs_slice.dropna()
        if not highs_slice.empty:
            level = highs_slice.iloc[-1]
            above_by = c_high - level
            if above_by >= depth_atr * a and c_close < level:
                events.append({
                    'symbol': None, 'idx': i, 'date': df.index[i], 'direction': 'SELL',
                    'level': level, 'depth_atr': above_by / a, 'atr': a,
                    'level_age_bars': int(i - df.index.get_loc(highs_slice.index[-1])),
                })

    return pd.DataFrame(events)


def forward_metrics(df: pd.DataFrame, atr: pd.Series, idx: int, direction: str,
                    horizons=HORIZONS, include_gap: bool = True):
    """
    Forward return in the PREDICTED direction, in ATR units, at each horizon.
    include_gap=False measures from the NEXT bar's open (skips the immediate
    signal->next-bar gap) instead of from the signal bar's own close.
    Also returns MAE (max adverse excursion) up to the longest horizon.
    """
    n = len(df)
    a = atr.iloc[idx]
    sign = 1.0 if direction == 'BUY' else -1.0
    entry = df['Open'].iloc[idx + 1] if (not include_gap and idx + 1 < n) else df['Close'].iloc[idx]
    start_bar = idx + 1

    out = {}
    mae = 0.0
    for h in horizons:
        end_bar = idx + h
        if end_bar >= n:
            out[f'fwd_{h}'] = np.nan
            continue
        fut_close = df['Close'].iloc[end_bar]
        out[f'fwd_{h}'] = sign * (fut_close - entry) / a if a > 0 else np.nan

    mae_end = min(n - 1, idx + max(horizons))
    if start_bar <= mae_end and a > 0:
        path_low = df['Low'].iloc[start_bar:mae_end + 1]
        path_high = df['High'].iloc[start_bar:mae_end + 1]
        if direction == 'BUY':
            adverse = (entry - path_low).max()
        else:
            adverse = (path_high - entry).max()
        mae = max(0.0, adverse / a)
    out['mae_atr'] = mae
    return out


def build_control(df: pd.DataFrame, atr: pd.Series, event_idxs: set, event_atr_idxs: list,
                  control_multiple: int, up_ratio: float, rng: np.random.Generator):
    """
    Non-event bars matched to the event sample's ATR-percentile (volatility)
    decile, each assigned a random direction at the event sample's own
    UP/DOWN ratio. This operationalizes "volatility/regime matched" as an
    ATR-percentile decile match: for every event, draw `control_multiple`
    control bars from candidates sharing that event's decile, so the
    control set's volatility distribution mirrors the event set's rather
    than being uniform-random over the whole series.
    """
    n = len(df)
    valid_range = list(range(30, n - max(HORIZONS) - 1))
    candidates = [i for i in valid_range if i not in event_idxs]
    if not candidates or not event_atr_idxs:
        return []

    atr_full = atr.reindex(df.index)
    all_ranks = atr_full.rank(pct=True)
    candidate_deciles = {i: min(9, int(all_ranks.iloc[i] * 10)) for i in candidates
                        if not pd.isna(all_ranks.iloc[i])}
    by_decile: dict = {}
    for i, d in candidate_deciles.items():
        by_decile.setdefault(d, []).append(i)

    control_events = []
    for event_i in event_atr_idxs:
        rank = all_ranks.iloc[event_i]
        if pd.isna(rank):
            continue
        decile = min(9, int(rank * 10))
        pool = by_decile.get(decile) or candidates
        n_draw = min(control_multiple, len(pool))
        if n_draw == 0:
            continue
        picks = rng.choice(len(pool), size=n_draw, replace=(n_draw > len(pool)))
        for p in picks:
            i = pool[p]
            direction = 'BUY' if rng.random() < up_ratio else 'SELL'
            control_events.append({'idx': i, 'direction': direction})
    return control_events


def mann_whitney_report(event_vals, control_vals, label):
    event_vals = np.array([v for v in event_vals if not np.isnan(v)])
    control_vals = np.array([v for v in control_vals if not np.isnan(v)])
    if len(event_vals) < 5 or len(control_vals) < 5:
        return {'label': label, 'n_event': len(event_vals), 'n_control': len(control_vals),
               'event_mean': np.nan, 'control_mean': np.nan, 'diff': np.nan, 'p': np.nan}
    stat, p = stats.mannwhitneyu(event_vals, control_vals, alternative='greater')
    pooled_std = np.sqrt((event_vals.var() + control_vals.var()) / 2)
    effect_size = (event_vals.mean() - control_vals.mean()) / pooled_std if pooled_std > 0 else np.nan
    return {
        'label': label, 'n_event': len(event_vals), 'n_control': len(control_vals),
        'event_mean': event_vals.mean(), 'control_mean': control_vals.mean(),
        'diff': event_vals.mean() - control_vals.mean(), 'p': p, 'effect_size': effect_size,
    }


def main():
    parser = argparse.ArgumentParser(description='Liquidity-sweep MECHANISM test (not a strategy backtest)')
    parser.add_argument('--symbols', nargs='+', default=SYMBOLS)
    parser.add_argument('--depth-atr', type=float, default=0.30,
                       help='wick depth beyond the level, in ATR units (free parameter 1)')
    parser.add_argument('--lookback', type=int, default=60,
                       help='bars back a prior swing level may be found in (free parameter 2)')
    parser.add_argument('--control-multiple', type=int, default=5,
                       help='control sample size = this many times the event count')
    args = parser.parse_args()

    rng = np.random.default_rng(RANDOM_SEED)
    all_events = []
    all_control = []

    print('=' * 78)
    print('LIQUIDITY-SWEEP MECHANISM TEST — not a strategy backtest')
    print(f'depth_atr={args.depth_atr}  lookback={args.lookback}  pivot_bars={PIVOT_BARS}')
    print('=' * 78)

    for symbol in args.symbols:
        df = load_daily(symbol)
        if df is None or len(df) < 100:
            print(f'{symbol}: insufficient daily data — SKIP')
            continue

        atr = compute_atr_series(df)
        is_high, is_low = find_pivots(df)
        events = detect_sweeps(df, atr, is_high, is_low, args.depth_atr, args.lookback)
        if events.empty:
            print(f'{symbol}: 0 sweep events in {len(df)} bars')
            continue

        up_ratio = (events['direction'] == 'BUY').mean()
        event_idx_set = set(events['idx'].tolist())
        control = build_control(df, atr, event_idx_set, events['idx'].tolist(),
                                args.control_multiple, up_ratio, rng)

        for _, ev in events.iterrows():
            m_gap = forward_metrics(df, atr, ev['idx'], ev['direction'], include_gap=True)
            m_nogap = forward_metrics(df, atr, ev['idx'], ev['direction'], include_gap=False)
            row = {
                'symbol': symbol, 'date': ev['date'], 'direction': ev['direction'],
                'depth_atr': ev['depth_atr'], 'level_age_bars': ev['level_age_bars'],
                'year': ev['date'].year,
                **{f'gap_{k}': v for k, v in m_gap.items()},
                **{f'nogap_{k}': v for k, v in m_nogap.items() if k != 'mae_atr'},
            }
            all_events.append(row)

        for c in control:
            m_gap = forward_metrics(df, atr, c['idx'], c['direction'], include_gap=True)
            all_control.append({
                'symbol': symbol, 'date': df.index[c['idx']], 'direction': c['direction'],
                'year': df.index[c['idx']].year,
                **{f'gap_{k}': v for k, v in m_gap.items()},
            })

        print(f'{symbol}: {len(events)} events ({(events["direction"]=="BUY").sum()} BUY / '
              f'{(events["direction"]=="SELL").sum()} SELL), {len(df)} bars '
              f'({df.index[0].date()} -> {df.index[-1].date()})')

    ev_df = pd.DataFrame(all_events)
    ct_df = pd.DataFrame(all_control)

    if ev_df.empty:
        print('\nNo events detected at all — cannot test the mechanism with these parameters.')
        return 1

    ev_df.to_csv('sweep_mechanism_events.csv', index=False)
    ct_df.to_csv('sweep_mechanism_control.csv', index=False)

    print('\n' + '=' * 78)
    print(f'TOTAL: {len(ev_df)} events, {len(ct_df)} control bars')
    print('=' * 78)

    print('\n--- CORE TEST: forward return in predicted direction, event vs control ---')
    print(f'{"horizon":>10}{"n_event":>9}{"n_ctrl":>8}{"event_mean":>12}{"ctrl_mean":>11}'
          f'{"diff":>9}{"effect_d":>10}{"p-value":>10}')
    core_results = []
    for h in HORIZONS:
        r = mann_whitney_report(ev_df[f'gap_fwd_{h}'], ct_df[f'gap_fwd_{h}'], f'{h}bar')
        core_results.append(r)
        print(f'{h:>9}b{r["n_event"]:>9}{r["n_control"]:>8}{r["event_mean"]:>+12.4f}'
              f'{r["control_mean"]:>+11.4f}{r["diff"]:>+9.4f}{r.get("effect_size",float("nan")):>10.3f}'
              f'{r["p"]:>10.4f}')

    print('\n--- SPLIT BY YEAR (mechanism must hold across regimes, not one lucky window) ---')
    print(f'{"year":>6}{"n_event":>9}{"n_ctrl":>8}{"event_mean(10b)":>17}{"ctrl_mean(10b)":>16}{"p":>9}')
    years_passing = 0
    years_tested = 0
    for yr in sorted(ev_df['year'].unique()):
        ev_y = ev_df[ev_df['year'] == yr]
        ct_y = ct_df[ct_df['year'] == yr]
        if len(ev_y) < 5 or len(ct_y) < 5:
            print(f'{yr:>6}{len(ev_y):>9}{len(ct_y):>8}  (too few — skipped)')
            continue
        years_tested += 1
        r = mann_whitney_report(ev_y['gap_fwd_10'], ct_y['gap_fwd_10'], str(yr))
        passed = r['p'] < 0.05 and r['diff'] > 0
        years_passing += int(passed)
        flag = '  <- PASS' if passed else ''
        print(f'{yr:>6}{r["n_event"]:>9}{r["n_control"]:>8}{r["event_mean"]:>+17.4f}'
              f'{r["control_mean"]:>+16.4f}{r["p"]:>9.4f}{flag}')

    print('\n--- FAILURE MODE 2a: level prominence (real range extreme vs. mid-range fractal) ---')
    ev_df['prominent'] = ev_df['level_age_bars'] >= ev_df['level_age_bars'].median()
    for label, sub in [('older/more-tested levels', ev_df[ev_df['prominent']]),
                       ('younger/less-tested levels', ev_df[~ev_df['prominent']])]:
        r = mann_whitney_report(sub['gap_fwd_10'], ct_df['gap_fwd_10'], label)
        print(f'  {label:<30} n={r["n_event"]:<5} event_mean={r["event_mean"]:+.4f}  p={r["p"]:.4f}')

    print('\n--- FAILURE MODE 2b: gap-inclusive vs intraday-only forward return ---')
    for h in [1, 3, 5]:
        gap_mean = ev_df[f'gap_fwd_{h}'].mean()
        nogap_mean = ev_df[f'nogap_fwd_{h}'].mean()
        print(f'  {h}-bar: WITH immediate gap = {gap_mean:+.4f}   WITHOUT (from next open) = {nogap_mean:+.4f}'
              f'   gap contributes {gap_mean - nogap_mean:+.4f}')

    print('\n--- FAILURE MODE 2c: MAE distribution for eventually-correct trades (data-derived stop) ---')
    correct = ev_df[ev_df['gap_fwd_10'] > 0]
    if len(correct) >= 10:
        mae = correct['gap_mae_atr'].dropna()
        print(f'  n={len(mae)}  50th pct={mae.quantile(.50):.2f}xATR  75th={mae.quantile(.75):.2f}xATR  '
              f'90th={mae.quantile(.90):.2f}xATR  max={mae.max():.2f}xATR')
        print(f'  (a stop tighter than the 75th percentile would have been hit on 25% of eventual winners)')
    else:
        print(f'  only {len(correct)} eventually-correct trades — too few to report a distribution')

    print('\n' + '=' * 78)
    print('PRE-COMMITTED VERDICT (p<0.05 AND diff>0, in a MAJORITY of individually-tested years)')
    ten_bar = core_results[HORIZONS.index(10)]
    overall_pass = ten_bar['p'] < 0.05 and ten_bar['diff'] > 0
    majority_years = years_tested > 0 and years_passing > years_tested / 2
    print(f'  Overall (10-bar, all years pooled): {"PASS" if overall_pass else "FAIL"} '
          f'(p={ten_bar["p"]:.4f}, diff={ten_bar["diff"]:+.4f})')
    print(f'  Years passing individually: {years_passing}/{years_tested} '
          f'({"PASS" if majority_years else "FAIL"} majority threshold)')
    final = overall_pass and majority_years
    print(f'\n  FINAL: {"MECHANISM SHOWS REAL SIGNAL — proceed to Step 4" if final else "MECHANISM NOT DEMONSTRATED — do not build a strategy on this"}')
    print('=' * 78)
    return 0


if __name__ == '__main__':
    sys.exit(main())
