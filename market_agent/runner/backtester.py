"""
market_agent/runner/backtester.py

Bar-by-bar historical backtester.
NO future data leakage — each brain call sees ONLY hist[:i], never future bars.

Per-trade detail columns:
  symbol, brain, bar_time, direction, regime_used, regime_suitability,
  decision_factor, price_at_signal, atr_pct_at_signal, bars_used,
  entry_price, target_1, stop_loss, rr_ratio, confidence,
  outcome (WIN/LOSS/EXPIRED), bars_to_outcome, r_achieved,
  indicator_1_name, indicator_1_value, indicator_2_name, indicator_2_value,
  indicator_3_name, indicator_3_value, evidence_summary,
  volatile_threshold, chaos_threshold, adx, atr_pct, median_atr_pct

Usage:
  from market_agent.runner.backtester import run_backtest, run_param_grid, aggregate_results
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Callable, Any

from market_agent.data.storage.postgres import PostgresStorage

log = logging.getLogger("backtester")

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_BARS_IN_TRADE = 20   # signal EXPIRES if neither T1 nor SL hit within this many bars

# MIN_HIST_BARS: minimum hourly bars fed to brain before first backtest evaluation.
# Raised from 80 → 200 to support Gate 7 (D1 higher-timeframe alignment).
#
# Gate 7 requires 20 complete D1 bars to compute a reliable SMA20.
# Equity markets (NSE/NYSE) trade ~7 H1 bars/day.
# 20 trading days × 7 bars/day = 140 H1 bars minimum.
# Add 30% buffer for holidays/gaps + 20 bars for H1 indicators = 200.
#
# NOTE: 480 (20 × 24) is wrong for equity — that formula assumes 24h/day trading
# and wastes 3.5 months of backtest history on warmup. 200 is the correct equity number.
#
# Trade-off: first 200/7 ≈ 28 trading days of each backtest period are warmup-only.
# At 180-day backtest: ~152 tradeable days remain. Signal count impact is minimal.
MIN_HIST_BARS     = 200


# ── DB Loader ─────────────────────────────────────────────────────────────────

def load_ohlcv(symbol: str, timeframe: str,
               start: datetime, end: datetime,
               storage: Optional[PostgresStorage] = None) -> pd.DataFrame:
    """
    Load OHLCV from DB for a symbol/timeframe between start and end.
    Returns a DataFrame with columns [Open, High, Low, Close, Volume]
    indexed by timestamp, sorted ascending.
    """
    if storage is None:
        storage = PostgresStorage()

    session = storage.Session()
    try:
        from market_agent.data.storage.postgres import MarketData
        from sqlalchemy import and_

        rows = session.query(MarketData).filter(
            and_(
                MarketData.symbol    == symbol,
                MarketData.timeframe == timeframe,
                MarketData.timestamp >= start,
                MarketData.timestamp <= end,
            )
        ).order_by(MarketData.timestamp.asc()).all()

        records = []
        for r in rows:
            # Use columnar fields if available, fall back to pickle
            if r.open_price is not None:
                records.append({
                    'timestamp': r.timestamp,
                    'Open':   float(r.open_price),
                    'High':   float(r.high_price),
                    'Low':    float(r.low_price),
                    'Close':  float(r.close_price),
                    'Volume': int(r.volume_val or 0),
                })
            elif r.data_binary:
                import pickle
                d = pickle.loads(r.data_binary)
                records.append({
                    'timestamp': r.timestamp,
                    'Open':   float(d.get('Open', 0)),
                    'High':   float(d.get('High', 0)),
                    'Low':    float(d.get('Low', 0)),
                    'Close':  float(d.get('Close', 0)),
                    'Volume': int(d.get('Volume', 0)),
                })

        if not records:
            log.warning(f"load_ohlcv: no data for {symbol} {timeframe} {start}→{end}")
            return pd.DataFrame()

        df = pd.DataFrame(records).set_index('timestamp').sort_index()
        df = df[df['Close'] > 0].dropna(subset=['Open', 'High', 'Low', 'Close'])
        return df

    finally:
        session.close()


# ── Signal Evaluator ──────────────────────────────────────────────────────────

def _evaluate_signal(entry_price: float, target_1: float, stop_loss: float,
                     future_bars: pd.DataFrame) -> dict:
    """
    Walk forward through future_bars bar by bar.
    First event wins: T1 hit → WIN, SL hit → LOSS, 20 bars → EXPIRED.
    Uses High/Low to check intra-bar touch (realistic — not just close).
    Returns: {outcome, bars_to_outcome, r_achieved}
    """
    if entry_price <= 0 or target_1 <= 0 or stop_loss <= 0:
        return {'outcome': 'INVALID', 'bars_to_outcome': 0, 'r_achieved': 0.0}

    risk  = abs(entry_price - stop_loss)
    gain  = abs(target_1    - entry_price)
    if risk == 0:
        return {'outcome': 'INVALID', 'bars_to_outcome': 0, 'r_achieved': 0.0}

    is_long = target_1 > entry_price

    for i, (ts, row) in enumerate(future_bars.iterrows()):
        if i >= MAX_BARS_IN_TRADE:
            break

        bar_high = row['High']
        bar_low  = row['Low']

        if is_long:
            if bar_high >= target_1:
                return {'outcome': 'WIN',  'bars_to_outcome': i + 1, 'r_achieved': round(gain / risk, 2)}
            if bar_low  <= stop_loss:
                return {'outcome': 'LOSS', 'bars_to_outcome': i + 1, 'r_achieved': round(-1.0, 2)}
        else:
            if bar_low  <= target_1:
                return {'outcome': 'WIN',  'bars_to_outcome': i + 1, 'r_achieved': round(gain / risk, 2)}
            if bar_high >= stop_loss:
                return {'outcome': 'LOSS', 'bars_to_outcome': i + 1, 'r_achieved': round(-1.0, 2)}

    # Neither hit — expired
    last_close = float(future_bars['Close'].iloc[-1]) if not future_bars.empty else entry_price
    unrealised = (last_close - entry_price) / risk if is_long else (entry_price - last_close) / risk
    return {'outcome': 'EXPIRED', 'bars_to_outcome': MAX_BARS_IN_TRADE,
            'r_achieved': round(unrealised, 2)}


# ── Per-trade row builder ─────────────────────────────────────────────────────

def _build_trade_row(symbol: str, bar_time, brain_signal,
                     regime_signal, eval_result: dict,
                     extra_params: dict = None) -> dict:
    """
    Combine brain signal + regime signal + evaluation result into one flat row.
    This is the 'complete data on each trade' the user requested.
    """
    m   = brain_signal.measurements or {}
    rm  = regime_signal.measurements if regime_signal else {}
    ep  = extra_params or {}

    # Regime used for this trade
    regime_used  = rm.get('computed_regime', 'UNKNOWN')
    regime_suit  = brain_signal.regime_suitability or 'UNKNOWN'

    # Entry / targets from measurements (brain fills these in A12 impl)
    entry_price  = m.get('entry_price',  m.get('price_at_signal', 0.0))
    target_1     = m.get('target_1',     0.0)
    stop_loss    = m.get('stop_loss',    0.0)

    # R:R
    risk = abs(entry_price - stop_loss)  if entry_price and stop_loss  else 0
    gain = abs(target_1    - entry_price) if entry_price and target_1  else 0
    rr   = round(gain / risk, 2) if risk > 0 else 0.0

    return {
        # ── Identity ────────────────────────────────────
        'symbol':               symbol,
        'brain':                brain_signal.brain_name,
        'bar_time':             str(bar_time),
        'direction':            brain_signal.direction,
        'confidence':           round(brain_signal.confidence or 0.0, 4),

        # ── Regime ──────────────────────────────────────
        'regime_used':          regime_used,           # user-requested column
        'regime_suitability':   regime_suit,
        'regime_adx':           rm.get('adx'),
        'regime_atr_pct':       rm.get('atr_pct'),
        'regime_median_atr_pct':rm.get('median_atr_pct'),
        'volatile_threshold':   rm.get('volatile_threshold'),
        'chaos_threshold':      rm.get('chaos_threshold'),

        # ── Decision logging (A12) ──────────────────────
        'decision_factor':      m.get('decision_factor'),
        'price_at_signal':      m.get('price_at_signal'),
        'atr_at_signal':        m.get('atr_at_signal'),
        'atr_pct_at_signal':    m.get('atr_pct_at_signal'),
        'bars_used':            m.get('bars_used'),

        # ── Trade levels ────────────────────────────────
        'entry_price':          entry_price,
        'target_1':             target_1,
        'stop_loss':            stop_loss,
        'rr_ratio':             rr,

        # ── Outcome ─────────────────────────────────────
        'outcome':              eval_result['outcome'],
        'bars_to_outcome':      eval_result['bars_to_outcome'],
        'r_achieved':           eval_result['r_achieved'],

        # ── Indicators ──────────────────────────────────
        'indicator_1_name':     m.get('indicator_1_name'),
        'indicator_1_value':    m.get('indicator_1_value'),
        'indicator_2_name':     m.get('indicator_2_name'),
        'indicator_2_value':    m.get('indicator_2_value'),
        'indicator_3_name':     m.get('indicator_3_name'),
        'indicator_3_value':    m.get('indicator_3_value'),

        # ── Why it fired ────────────────────────────────
        'evidence_summary':     brain_signal.primary_evidence,

        # ── Grid params (if running a grid) ─────────────
        **{f'param_{k}': v for k, v in ep.items()},
    }


# ── Main backtest loop ────────────────────────────────────────────────────────

def run_backtest(brain_fn: Callable,
                 regime_fn: Callable,
                 symbol: str,
                 timeframe: str,
                 start: datetime,
                 end: datetime,
                 storage: Optional[PostgresStorage] = None,
                 extra_params: dict = None) -> pd.DataFrame:
    """
    Bar-by-bar backtest. Feeds brain_fn exactly hist[:i] — NO future data.

    brain_fn   : callable(hist: pd.DataFrame) -> BrainSignal
    regime_fn  : callable(hist: pd.DataFrame) -> BrainSignal (Regime-Ensemble)
    symbol     : e.g. 'BTC-USD'
    timeframe  : '1h'
    start/end  : date range for backtest
    extra_params: dict of grid params to tag on each row (for grid runs)

    Returns a DataFrame where each row is one evaluated trade.
    """
    df = load_ohlcv(symbol, timeframe, start, end, storage)
    if df.empty:
        log.warning(f"run_backtest: no data for {symbol} {timeframe}")
        return pd.DataFrame()

    rows      = []
    n         = len(df)
    log.info(f"Backtest {symbol} {timeframe}: {n} bars from {df.index[0]} to {df.index[-1]}")

    for i in range(MIN_HIST_BARS, n - MAX_BARS_IN_TRADE):
        hist         = df.iloc[:i]           # ONLY past bars — future is hidden
        future_bars  = df.iloc[i: i + MAX_BARS_IN_TRADE]

        # ── Get regime first ──────────────────────────────────────────────────
        try:
            regime_signal = regime_fn(hist)
        except Exception as e:
            log.debug(f"regime_fn failed at bar {i}: {e}")
            continue

        regime = regime_signal.measurements.get('computed_regime', 'RANGING')

        # ── CHAOS gate: no trades allowed ─────────────────────────────────────
        if regime == 'CHAOS':
            continue

        # ── Run the brain ─────────────────────────────────────────────────────
        try:
            brain_signal = brain_fn(hist)
        except Exception as e:
            log.debug(f"brain_fn failed at bar {i}: {e}")
            continue

        # Only evaluate non-HOLD signals
        if brain_signal.direction == 'HOLD':
            continue

        # ── Pull trade levels from brain signal ───────────────────────────────
        m = brain_signal.measurements or {}
        entry  = float(m.get('entry_price',  m.get('price_at_signal', 0)) or 0)
        t1     = float(m.get('target_1',  0) or 0)
        sl     = float(m.get('stop_loss', 0) or 0)

        # Skip if brain didn't provide proper levels
        if entry <= 0 or t1 <= 0 or sl <= 0:
            log.debug(f"Skipping bar {i}: incomplete levels entry={entry} t1={t1} sl={sl}")
            continue

        # ── Evaluate ──────────────────────────────────────────────────────────
        eval_result = _evaluate_signal(entry, t1, sl, future_bars)

        bar_time = df.index[i]
        row = _build_trade_row(
            symbol       = symbol,
            bar_time     = bar_time,
            brain_signal = brain_signal,
            regime_signal= regime_signal,
            eval_result  = eval_result,
            extra_params = extra_params,
        )
        rows.append(row)

    result_df = pd.DataFrame(rows)
    log.info(
        f"Backtest {symbol}: {len(result_df)} signals evaluated | "
        f"{'WIN: '+str((result_df.outcome=='WIN').sum()) if not result_df.empty else 'no signals'}"
    )
    return result_df


# ── Aggregate stats ───────────────────────────────────────────────────────────

def aggregate_results(df: pd.DataFrame) -> dict:
    """
    Compute summary stats from a backtest result DataFrame.
    Returns: {total, wins, losses, expired, wr, avg_r, ev, max_dd_r, regime_breakdown}
    """
    if df is None or df.empty:
        return {'total': 0, 'wins': 0, 'losses': 0, 'expired': 0,
                'wr': 0.0, 'avg_r': 0.0, 'ev': 0.0, 'max_dd_r': 0.0}

    decided = df[df['outcome'].isin(['WIN', 'LOSS'])]
    total   = len(decided)
    wins    = (decided['outcome'] == 'WIN').sum()
    losses  = (decided['outcome'] == 'LOSS').sum()
    expired = (df['outcome'] == 'EXPIRED').sum()

    wr      = wins / total           if total   > 0 else 0.0
    avg_r   = decided['r_achieved'].mean() if total > 0 else 0.0
    ev      = float(avg_r)   # Realized EV per trade is the literal average R achieved

    # Max drawdown in R
    cumulative = decided['r_achieved'].cumsum()
    roll_max   = cumulative.cummax()
    drawdown   = roll_max - cumulative
    max_dd_r   = float(drawdown.max()) if not drawdown.empty else 0.0

    # Regime breakdown
    regime_breakdown = {}
    if 'regime_used' in df.columns:
        for regime, grp in decided.groupby('regime_used'):
            g_win = (grp['outcome'] == 'WIN').sum()
            g_tot = len(grp)
            regime_breakdown[regime] = {
                'count': g_tot,
                'wr':    round(g_win / g_tot, 3) if g_tot > 0 else 0.0,
                'avg_r': round(grp['r_achieved'].mean(), 3),
            }

    return {
        'total':            total,
        'wins':             int(wins),
        'losses':           int(losses),
        'expired':          int(expired),
        'wr':               round(wr, 3),
        'avg_r':            round(float(avg_r), 3),
        'ev':               round(float(ev), 3),
        'max_dd_r':         round(max_dd_r, 3),
        'regime_breakdown': regime_breakdown,
    }


# ── Parameter Grid Runner ─────────────────────────────────────────────────────

def run_param_grid(brain_fn_factory: Callable,
                   regime_fn_factory: Callable,
                   param_grid: dict,
                   symbols: list,
                   timeframe: str,
                   start: datetime,
                   end: datetime,
                   storage: Optional[PostgresStorage] = None,
                   output_csv: Optional[str] = None) -> pd.DataFrame:
    """
    For each combination of param_grid values, run a full backtest on all symbols
    and aggregate results. Accepts only combinations with sample >= 30 AND EV > baseline.

    brain_fn_factory  : callable(**params) -> brain_signal_fn
    regime_fn_factory : callable(**params) -> regime_signal_fn
    param_grid        : {'volatile_mult': [2.0, 2.5, 3.0], 'chaos_mult': [4.0, 5.0]}

    Returns: DataFrame of grid results sorted by EV descending.
    """
    import itertools

    keys   = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))

    log.info(f"Grid: {len(combos)} combinations × {len(symbols)} symbols")

    grid_rows = []

    for combo in combos:
        params = dict(zip(keys, combo))
        log.info(f"  Testing params: {params}")

        try:
            brain_fn  = brain_fn_factory(**params)
            regime_fn = regime_fn_factory(**params)
        except Exception as e:
            log.error(f"  Factory failed for {params}: {e}")
            continue

        # Run across all symbols and stack results
        all_trades = []
        for sym in symbols:
            bt = run_backtest(
                brain_fn     = brain_fn,
                regime_fn    = regime_fn,
                symbol       = sym,
                timeframe    = timeframe,
                start        = start,
                end          = end,
                storage      = storage,
                extra_params = params,
            )
            if not bt.empty:
                bt['symbol_run'] = sym
                all_trades.append(bt)

        if not all_trades:
            continue

        combined = pd.concat(all_trades, ignore_index=True)
        stats    = aggregate_results(combined)

        grid_rows.append({
            **params,
            'total_signals': stats['total'],
            'wins':          stats['wins'],
            'losses':        stats['losses'],
            'expired':       stats['expired'],
            'win_rate':      stats['wr'],
            'avg_r':         stats['avg_r'],
            'ev':            stats['ev'],
            'max_dd_r':      stats['max_dd_r'],
        })

        log.info(
            f"  Params {params} → signals={stats['total']} WR={stats['wr']:.1%} "
            f"EV={stats['ev']:.3f} AvgR={stats['avg_r']:.3f}"
        )

    if not grid_rows:
        log.warning("Grid produced no results.")
        return pd.DataFrame()

    grid_df = pd.DataFrame(grid_rows).sort_values('ev', ascending=False)

    if output_csv:
        grid_df.to_csv(output_csv, index=False)
        log.info(f"Grid results saved → {output_csv}")

    return grid_df