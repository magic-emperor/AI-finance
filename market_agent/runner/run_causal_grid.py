"""
market_agent/runner/run_amv_uptrend_grid.py  (v4 — D1 timeframe)

AMV-LSTM-Uptrend Parameter Grid Search — DAILY TIMEFRAME

Context:
  H1 v3 grid produced best combo n=4-9 on 270d — signal starvation.
  H1 SMA20 (3-day MA) is not a respected support level. TRENDING_UP regime
  on H1 is noisy and labels flip frequently. RSI 45-55 + SMA20 proximity
  on H1 is an extremely rare 4-star alignment.

  D1 version uses 2.5 years of daily data per symbol. SMA20 on D1 = 1-month
  moving average — a genuine institutional support level. RSI 45-55 on D1
  = a real multi-day correction (5-15 trading days). Expected n: 4-8 per
  symbol × 7 symbols = 28-56 signals — sufficient for statistical confirmation.

D1-specific changes vs H1 v3 runner:
  TIMEFRAME:     '1h' → '1d'
  START:         270d → 900d (full 2.5-year dataset)
  MAX_BARS_IN_TRADE: explicit override to 30 D1 bars (= 6 weeks max hold)
  Gate 8:        D1 resample removed. Now W1 resample + W1 SMA10 slope check.
  make_brain_fn: pullback lookback 3 → 5 bars, SMA slope threshold 0.015 → 0.10
  All other gate logic: mirrors amv_lstm_uptrend.py exactly (imported constants)

WHAT IS SEARCHED:
  pullback_dist     : Method A proximity threshold (ATR multiples from SMA20)
  pullback_drop_atr : Method B min close-to-close drop
  vol_min           : Minimum volume ratio on bounce day
  rr_t1             : T1 target distance (ATR multiples)
  (SL, RSI gates, close_pct, ATR factor, SMA slope are fixed — evidence-driven)

ACCEPTANCE: n >= 20 AND EV > 0

Run:
  python -X utf8 -m market_agent.runner.run_amv_uptrend_grid
"""
import logging
import itertools
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
import os

from market_agent.brain.amv_lstm_uptrend import (
    _RR_T2_MULT, _RR_SL_MULT,
    _MIN_SMA20_SLOPE_PCT, _MIN_CLOSE_ABOVE_LOW_PCT,
    _RSI_PULLBACK_MAX, _RSI_RECOVERY_MIN,
    _ATR_EXPANSION_FACTOR,
    _PULLBACK_LOOKBACK_BARS,
    _MIN_W1_BARS, _MIN_W1_SLOPE_PCT,
    UPTREND_EXCLUDED_SYMBOLS,
)
from market_agent.brain.regime_ensemble import regime_ensemble_signal
from market_agent.brain.brain_contract  import BrainSignal
from market_agent.brain.brain_utils     import calc_atr, calc_rsi_float
from market_agent.runner.backtester     import (
    load_ohlcv, aggregate_results,
    _evaluate_signal, _build_trade_row, MIN_HIST_BARS,
)
from market_agent.data.storage.postgres import PostgresStorage

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('run_amv_uptrend_grid_v4_d1')

# ── Universe ──────────────────────────────────────────────────────────────────
# 7 symbols with 600+ D1 bars confirmed available.
# H1 exclusions cleared — D1 evidence starts fresh.
ALL_SYMBOLS = [
    'LT.NS',        # Proven H1 edge (WR=42.9%)
    'TATASTEEL.NS', # Proven H1 edge (WR=27.3%)
    'AAPL',         # Proven H1 edge (WR=50.0%, small n)
    'RELIANCE.NS',  # Natural D1 candidate
    'ITC.NS',       # Natural D1 candidate
    'AMD',          # Checking if D1 fixes H1 noise
    'GOOGL',        # H1 broken (SMA20 not H1 support) — testing if D1 fixes this
]
SYMBOLS = [s for s in ALL_SYMBOLS if s not in UPTREND_EXCLUDED_SYMBOLS]

TIMEFRAME           = '1d'                      # D1 — key change from H1
END                 = datetime.utcnow()
START               = END - timedelta(days=900)  # 2.5 years (full available history)
UPTREND_ALLOWED     = {'TRENDING_UP'}
_SLIPPAGE           = 0.0005                    # 0.05% per side
MIN_N_ACCEPT        = 20
# D1 trades can take several weeks to resolve. Override to 30 D1 bars = 6 weeks.
# This prevents almost every trade from expiring before T1 or SL is hit.
_MAX_BARS_IN_TRADE_D1 = 30

# Grid v4 (D1) — RSI gates and close_pct fixed (evidence-driven from H1 v2/v3).
# pullback_dist: Method A. Start with 1.0 (H1 best), also test 0.75/1.25.
#   On D1 ATR is larger; 1.0×D1 ATR from SMA20 is a tight but real proximity.
# pullback_drop_atr: Method B. 0.3-0.6×D1 ATR over 3 closes is a real drop.
# vol_min: 1.2 was H1 best. D1 volume is more consistent — search 1.0-1.5.
# rr_t1: 2.5 was H1 best. D1 targets take longer — also test 2.0, 3.0.
PARAM_GRID = {
    'pullback_dist':     [0.75, 1.0, 1.25],
    'pullback_drop_atr': [0.3,  0.4,  0.6],
    'vol_min':           [1.0,  1.2,  1.5],
    'rr_t1':             [2.0,  2.5,  3.0],
}


def make_brain_fn(
    pullback_dist:     float,
    pullback_drop_atr: float,
    vol_min:           float,
    rr_t1:             float,
):
    """
    Factory: returns a patched D1 brain function using grid params.
    Non-grid constants imported from brain file — single source of truth.
    Gate logic MUST exactly mirror amv_lstm_uptrend.py (D1 version).

    D1-specific notes in this factory:
      - pullback lookback uses _PULLBACK_LOOKBACK_BARS (= 5, imported from brain)
      - SMA slope threshold uses _MIN_SMA20_SLOPE_PCT (= 0.10, imported from brain)
      - ATR expansion uses _ATR_EXPANSION_FACTOR (= 0.85, imported from brain)
      - Gate 8 is W1 resample + W1 SMA10, NOT D1 resample
    """
    def _brain(hist: pd.DataFrame, regime: str = 'TRENDING_UP') -> BrainSignal:
        close   = hist['Close']
        high    = hist['High']
        low     = hist['Low']
        sma20   = close.rolling(20).mean()
        sma5    = close.rolling(5).mean()
        atr     = calc_atr(hist)
        price   = float(close.iloc[-1])
        atr_val = float(atr)
        rsi     = calc_rsi_float(hist)

        sma20_curr  = float(sma20.iloc[-1])
        sma5_curr   = float(sma5.iloc[-1])
        sma20_slope = (
            float((sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100)
            if len(sma20) >= 5 and sma20.iloc[-5] else 0.0
        )
        sma5_slope = (
            float((sma5.iloc[-1] - sma5.iloc[-3]) / sma5.iloc[-3] * 100)
            if len(sma5) >= 3 and sma5.iloc[-3] else 0.0
        )

        vol_series = hist['Volume']
        vol_avg    = (float(vol_series.iloc[-20:].mean())
                      if len(vol_series) >= 20 else float(vol_series.mean()))
        vol_ratio  = float(vol_series.iloc[-1]) / vol_avg if vol_avg > 0 else 1.0

        # ATR expansion — 14-bar simple TR mean
        if len(hist) >= 15:
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low  - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr_avg_14 = float(tr.iloc[-14:].mean())
        else:
            atr_avg_14 = atr_val
        # Use _ATR_EXPANSION_FACTOR from brain import (0.85 for D1)
        atr_expanding = atr_val >= atr_avg_14 * _ATR_EXPANSION_FACTOR

        # ── Pullback detection ────────────────────────────────────────────────
        pullback_method  = 'NONE'
        pullback_touched = False

        # Method A — D1: look back _PULLBACK_LOOKBACK_BARS (=5) bars
        for j in range(1, min(_PULLBACK_LOOKBACK_BARS + 1, len(hist))):
            if abs(float(low.iloc[-j]) - sma20_curr) <= pullback_dist * atr_val:
                pullback_touched = True
                pullback_method  = 'METHOD_A'
                break

        # Method B — directional close drop
        if not pullback_touched and len(hist) >= 5:
            recent_closes = close.iloc[-4:]
            bar_changes   = recent_closes.diff().dropna()
            down_bars     = int((bar_changes < 0).sum())
            total_drop    = float(recent_closes.max() - recent_closes.iloc[-1])
            if down_bars >= 2 and total_drop >= pullback_drop_atr * atr_val:
                pullback_touched = True
                pullback_method  = 'METHOD_B'

        candle_low   = float(low.iloc[-1])
        candle_high  = float(high.iloc[-1])
        candle_range = candle_high - candle_low if candle_high > candle_low else atr_val * 0.1
        close_pct    = (price - candle_low) / candle_range

        def _h(reason: str, conf: float = 0.42, factor: str = 'GATE_HOLD') -> BrainSignal:
            return BrainSignal(
                brain_name='AMV-LSTM-Uptrend-D1',
                specialization='Pullback-to-SMA20 D1 Entry',
                method=f'D1-Grid-v4(pd={pullback_dist} drop={pullback_drop_atr} '
                       f'vol={vol_min} t1={rr_t1})',
                direction='HOLD', confidence=conf, signal_strength=0.0,
                signal_age_candles=0, primary_evidence=reason,
                supporting_factors=[], contra_factors=[reason],
                method_confidence=0.15, regime_suitability='LOW',
                reliability_flags={},
                measurements={
                    'decision_factor':  factor,
                    'timeframe':        'D1',
                    'price_at_signal':  round(price, 6),
                    'atr_at_signal':    round(atr_val, 6),
                    'sma20':            round(sma20_curr, 6),
                    'sma20_slope':      round(sma20_slope, 4),
                    'rsi':              round(rsi, 1),
                    'vol_ratio':        round(vol_ratio, 3),
                    'atr_expanding':    int(atr_expanding),
                    'atr_avg_14':       round(atr_avg_14, 6),
                    'pullback_touched': int(pullback_touched),
                    'pullback_method':  pullback_method,
                    'close_pct':        round(close_pct, 3),
                    'bars_used':        len(hist),
                },
                rr_t1_mult=rr_t1, rr_t2_mult=_RR_T2_MULT, rr_sl_mult=_RR_SL_MULT,
                recent_accuracy=None, regime_accuracy=None,
            )

        # ── Gates — mirrors amv_lstm_uptrend.py (D1) exactly ─────────────────
        # _MIN_SMA20_SLOPE_PCT = 0.10 for D1 (imported from brain)
        if sma20_slope < _MIN_SMA20_SLOPE_PCT:
            return _h('D1 SMA20 flat', factor='SMA20_SLOPE_GATE')
        if not pullback_touched:
            return _h('No D1 pullback', factor='NO_PULLBACK_GATE')
        if price <= sma20_curr:
            return _h('Below D1 SMA20', factor='BELOW_SMA20_GATE')
        if close_pct < _MIN_CLOSE_ABOVE_LOW_PCT:    # 0.50 from brain
            return _h('Weak D1 bounce', factor='WEAK_BOUNCE_GATE')
        if rsi > 70:
            return _h('RSI overbought', factor='RSI_OVERBOUGHT_GATE')
        if rsi < _RSI_RECOVERY_MIN:                 # 45 from brain
            return _h('RSI too low', factor='RSI_TOO_LOW_GATE')
        if rsi > _RSI_PULLBACK_MAX:                 # 55 from brain
            return _h('RSI not dipped enough', factor='RSI_NOT_PULLED_BACK_GATE')
        if vol_ratio < vol_min:
            return _h('Low volume', factor='LOW_BOUNCE_VOLUME_GATE')
        if not atr_expanding:
            return _h('ATR contracting', factor='ATR_CONTRACTION_GATE')

        # Gate 8: W1 alignment — D1 brain uses W1 resample, NOT D1 resample
        try:
            weekly = hist.resample('W').agg(
                Open=('Open',   'first'),
                High=('High',   'max'),
                Low=('Low',     'min'),
                Close=('Close', 'last'),
                bar_count=('Close', 'count'),
            ).dropna(subset=['Close'])
            weekly = weekly[weekly['bar_count'] >= 3]
            if len(weekly) > 1:
                weekly = weekly.iloc[:-1]
            if len(weekly) >= _MIN_W1_BARS:
                w1_sma10 = weekly['Close'].rolling(10).mean()
                if w1_sma10.iloc[-5] and w1_sma10.iloc[-5] != 0:
                    w1_slope = float(
                        (w1_sma10.iloc[-1] - w1_sma10.iloc[-5])
                        / w1_sma10.iloc[-5] * 100
                    )
                    if w1_slope < -_MIN_W1_SLOPE_PCT:
                        return _h('W1 trend falling', factor='HTF_W1_ALIGNMENT_GATE')
        except Exception:
            pass   # Gate fails open

        # ── Signal ───────────────────────────────────────────────────────────
        entry     = price
        target_1  = price + rr_t1 * atr_val
        target_2  = price + _RR_T2_MULT * atr_val
        stop_loss = price - _RR_SL_MULT * atr_val

        return BrainSignal(
            brain_name='AMV-LSTM-Uptrend-D1',
            specialization='Pullback-to-SMA20 D1 Entry',
            method=f'D1-Grid-v4(pd={pullback_dist} drop={pullback_drop_atr} '
                   f'vol={vol_min} t1={rr_t1})',
            direction='BUY', confidence=0.60, signal_strength=0.60,
            signal_age_candles=0,
            primary_evidence=(
                f'D1 pullback [{pullback_method}] RSI={rsi:.1f} '
                f'close_pct={close_pct:.0%} vol={vol_ratio:.2f}×'
            ),
            supporting_factors=[
                f'D1 SMA20 slope={sma20_slope:+.4f}%',
                f'pullback_method={pullback_method}',
                f'atr_expanding={atr_expanding}',
            ],
            contra_factors=[],
            method_confidence=0.60, regime_suitability='HIGH',
            reliability_flags={'no_lstm_model': True, 'timeframe_d1': True},
            measurements={
                'timeframe':          'D1',
                'entry_price':        round(entry, 6),
                'target_1':           round(target_1, 6),
                'target_2':           round(target_2, 6),
                'stop_loss':          round(stop_loss, 6),
                'sma20':              round(sma20_curr, 6),
                'sma5':               round(sma5_curr, 6),
                'sma20_slope':        round(sma20_slope, 4),
                'sma5_slope':         round(sma5_slope, 4),
                'close_pct':          round(close_pct, 3),
                'pullback_touched':   int(pullback_touched),
                'pullback_method':    pullback_method,
                'rsi':                round(rsi, 1),
                'vol_ratio':          round(vol_ratio, 3),
                'atr_expanding':      int(atr_expanding),
                'atr_val':            round(atr_val, 6),
                'atr_avg_14':         round(atr_avg_14, 6),
                'sma20_dist_atr':     round(abs(price - sma20_curr) / atr_val, 3)
                                      if atr_val > 0 else 0,
                'decision_factor':    'D1_PULLBACK_BOUNCE',
                'price_at_signal':    round(price, 6),
                'atr_at_signal':      round(atr_val, 6),
                'atr_pct_at_signal':  round(atr_val / price * 100, 3) if price > 0 else 0.0,
                'bars_used':          len(hist),
                'indicator_1_name':   'rsi',
                'indicator_1_value':  round(rsi, 1),
                'indicator_2_name':   'close_pct',
                'indicator_2_value':  round(close_pct, 3),
                'indicator_3_name':   'vol_ratio',
                'indicator_3_value':  round(vol_ratio, 3),
            },
            rr_t1_mult=rr_t1, rr_t2_mult=_RR_T2_MULT, rr_sl_mult=_RR_SL_MULT,
            recent_accuracy=None, regime_accuracy=None,
        )

    return _brain


def _run_symbol(sym: str, params: dict) -> pd.DataFrame:
    storage  = PostgresStorage()
    brain_fn = make_brain_fn(**params)
    df       = load_ohlcv(sym, TIMEFRAME, START, END, storage)
    if df.empty:
        log.warning(f'  [{sym}] No D1 data returned — skipping')
        return pd.DataFrame()

    log.debug(f'  [{sym}] {len(df)} D1 bars loaded')
    rows = []
    n    = len(df)

    # Use max(MIN_HIST_BARS, 60) for D1 warmup — need at least 60 D1 bars
    # for reliable SMA20 (20) + RSI (14) + pullback window (5) + buffer
    min_start = max(MIN_HIST_BARS, 60)

    for i in range(min_start, n - _MAX_BARS_IN_TRADE_D1):
        hist        = df.iloc[:i]
        future_bars = df.iloc[i: i + _MAX_BARS_IN_TRADE_D1]

        try:
            reg = regime_ensemble_signal(hist)
        except Exception:
            continue
        if reg.measurements.get('computed_regime', 'RANGING') not in UPTREND_ALLOWED:
            continue

        try:
            bs = brain_fn(hist, regime='TRENDING_UP')
        except Exception:
            continue
        if bs.direction == 'HOLD':
            continue

        m     = bs.measurements or {}
        entry = float(m.get('entry_price', 0) or 0)
        t1    = float(m.get('target_1',    0) or 0)
        sl    = float(m.get('stop_loss',   0) or 0)
        if entry <= 0 or t1 <= 0 or sl <= 0:
            continue

        # Slippage — BUY: pay more on entry, receive less on exit
        entry_s = entry * (1 + _SLIPPAGE)
        t1_s    = t1    * (1 - _SLIPPAGE)
        sl_s    = sl    * (1 - _SLIPPAGE)

        ev  = _evaluate_signal(entry_s, t1_s, sl_s, future_bars)
        row = _build_trade_row(sym, df.index[i], bs, reg, ev, extra_params=params)

        row['rsi']             = m.get('rsi', 0)
        row['close_pct']       = m.get('close_pct', 0)
        row['vol_ratio']       = m.get('vol_ratio', 0)
        row['atr_expanding']   = m.get('atr_expanding', 0)
        row['pullback_method'] = m.get('pullback_method', 'NONE')
        row['sma20_dist_atr']  = m.get('sma20_dist_atr', 0)
        row['sma20_slope']     = m.get('sma20_slope', 0)
        rows.append(row)

    return pd.DataFrame(rows)


def _run_combo_parallel(params: dict, n_workers: int) -> pd.DataFrame:
    work  = [(sym, params) for sym in SYMBOLS]
    parts = []
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futs = {ex.submit(_run_symbol, sym, p): sym for sym, p in work}
        for fut in as_completed(futs):
            try:
                bt = fut.result()
                if not bt.empty:
                    parts.append(bt)
            except Exception as e:
                log.warning(f'Worker error [{futs[fut]}]: {e}')
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _extended_breakdown(df: pd.DataFrame) -> None:
    if df.empty:
        return

    print('\n  ── Symbol ──')
    for sym in sorted(df['symbol'].unique()):
        sub  = df[df['symbol'] == sym]
        s    = aggregate_results(sub)
        flag = 'OK' if s['ev'] > 0 else '--'
        print(f'  {sym:<18} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
              f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}')

    print('\n  ── RSI Sub-buckets (gate: 45-55) ──')
    for label, mask in [
        ('45-50 (deep pullback)',  (df['rsi'] >= 45) & (df['rsi'] < 50)),
        ('50-55 (upper zone)',     (df['rsi'] >= 50) & (df['rsi'] <= 55)),
        ('< 45  [gate leak ⚠]',  df['rsi'] < 45),
        ('> 55  [gate leak ⚠]',  df['rsi'] > 55),
    ]:
        sub = df[mask]
        if len(sub) == 0:
            continue
        s    = aggregate_results(sub)
        flag = '⚠ GATE LEAK' if 'leak' in label else ('OK' if s['ev'] > 0 else '--')
        print(f'  RSI {label:<30} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
              f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}')

    print('\n  ── Bounce Day Quality (close_pct, gate >= 0.50) ──')
    for label, mask in [
        ('< 0.50  [gate leak ⚠]', df['close_pct'] < 0.50),
        ('0.50-0.70',              (df['close_pct'] >= 0.50) & (df['close_pct'] < 0.70)),
        ('>= 0.70',                df['close_pct'] >= 0.70),
    ]:
        sub = df[mask]
        if len(sub) == 0:
            continue
        s    = aggregate_results(sub)
        flag = '⚠ GATE LEAK' if 'leak' in label else ('OK' if s['ev'] > 0 else '--')
        print(f'  close_pct {label:<25} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
              f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}')

    print('\n  ── Pullback Method ──')
    mA = len(df[df['pullback_method'] == 'METHOD_A'])
    mB = len(df[df['pullback_method'] == 'METHOD_B'])
    for method in ['METHOD_A', 'METHOD_B']:
        sub = df[df['pullback_method'] == method]
        if len(sub) == 0:
            print(f'  {method}: n=0')
            continue
        s = aggregate_results(sub)
        print(f'  {method:<10} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
              f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  '
              f'{"OK" if s["ev"] > 0 else "--"}')
    if mB > mA * 2:
        print(f'  ⚠ Method B ({mB}) >> Method A ({mA}). '
              f'Consider tightening _MIN_PULLBACK_DROP_ATR.')

    print('\n  ── ATR State ──')
    for label, val in [('Expanding', 1), ('Contracting [leak ⚠]', 0)]:
        sub = df[df['atr_expanding'] == val]
        if len(sub) == 0:
            continue
        s    = aggregate_results(sub)
        flag = '⚠ GATE LEAK' if 'leak' in label else ('OK' if s['ev'] > 0 else '--')
        print(f'  ATR {label:<25} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
              f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}')


def main():
    DATE_TAG  = END.strftime('%Y%m%d')
    GRID_CSV  = f'amv_uptrend_grid_v4_d1_{DATE_TAG}.csv'
    N_WORKERS = min(os.cpu_count() or 4, 8)

    keys   = list(PARAM_GRID.keys())
    combos = list(itertools.product(*PARAM_GRID.values()))

    log.info('=' * 70)
    log.info('AMV-LSTM-Uptrend Grid v4 — D1 TIMEFRAME')
    log.info(f'Symbols         : {SYMBOLS}')
    log.info(f'Timeframe       : {TIMEFRAME}  (D1)')
    log.info(f'Period          : {START.date()} → {END.date()}  (~2.5 years)')
    log.info(f'RSI gate        : [{_RSI_RECOVERY_MIN}, {_RSI_PULLBACK_MAX}]')
    log.info(f'close_pct gate  : >= {_MIN_CLOSE_ABOVE_LOW_PCT}')
    log.info(f'SMA slope gate  : >= {_MIN_SMA20_SLOPE_PCT}% per 5 D1 bars')
    log.info(f'ATR factor      : {_ATR_EXPANSION_FACTOR}  (D1-calibrated)')
    log.info(f'Pullback window : {_PULLBACK_LOOKBACK_BARS} D1 bars (Method A)')
    log.info(f'Max trade bars  : {_MAX_BARS_IN_TRADE_D1} D1 bars (= 6 weeks)')
    log.info(f'Slippage        : {_SLIPPAGE*100:.2f}% per side')
    log.info(f'Grid size       : {len(combos)} combos × {len(SYMBOLS)} symbols')
    log.info(f'Workers         : {N_WORKERS}')
    log.info(f'Accept          : n >= {MIN_N_ACCEPT} AND EV > 0')
    log.info('=' * 70)

    grid_rows  = []
    best_ev    = -999.0
    best_combo = None

    for ci, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        log.info(
            f'  [{ci:3d}/{len(combos)}] '
            f'pd={params["pullback_dist"]} drop={params["pullback_drop_atr"]} '
            f'vol={params["vol_min"]} t1={params["rr_t1"]}'
        )

        combined = _run_combo_parallel(params, N_WORKERS)
        if combined.empty:
            log.info('           → n=0 (no signals)')
            continue

        stats    = aggregate_results(combined)
        be_wr    = _RR_SL_MULT / (params['rr_t1'] + _RR_SL_MULT)
        accepted = stats['total'] >= MIN_N_ACCEPT and stats['ev'] > 0

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
            'break_even_wr': round(be_wr, 3),
            'accepted':      accepted,
        })

        status = ('ACCEPTED ✅' if accepted else
                  (f'n<{MIN_N_ACCEPT}' if stats['total'] < MIN_N_ACCEPT else 'EV<=0'))
        log.info(
            f'           → n={stats["total"]:3d}  WR={stats["wr"]:.1%}  '
            f'EV={stats["ev"]:+.3f}R  {status}'
        )

        if accepted and stats['ev'] > best_ev:
            best_ev    = stats['ev']
            best_combo = {**params, 'stats': stats, 'df': combined}

    if not grid_rows:
        log.error('Grid produced zero results.')
        log.error('Check that load_ohlcv() returns D1 data for these symbols.')
        log.error(f'TIMEFRAME used: {TIMEFRAME!r}')
        return

    grid_df = pd.DataFrame(grid_rows).sort_values('ev', ascending=False)
    grid_df.to_csv(GRID_CSV, index=False)
    log.info(f'Grid results → {GRID_CSV}')

    print('\n' + '=' * 70)
    print('AMV-LSTM-UPTREND GRID v4 D1 COMPLETE')
    print(f'Period: ~2.5y | Timeframe: D1 | RSI: [{_RSI_RECOVERY_MIN},{_RSI_PULLBACK_MAX}] | '
          f'close_pct >= {_MIN_CLOSE_ABOVE_LOW_PCT}')
    print('=' * 70)
    print('\n  Top 5 by EV:')
    print(f'  {"pd":>5} {"drop":>5} {"vol":>5} {"t1":>4} '
          f'{"n":>4} {"WR":>6} {"EV":>8}  Status')
    for _, r in grid_df.head(5).iterrows():
        status = ('ACCEPTED' if r['accepted']
                  else f'n<{MIN_N_ACCEPT}' if r['total_signals'] < MIN_N_ACCEPT
                  else 'EV<=0')
        print(f'  {r["pullback_dist"]:>5.2f} {r["pullback_drop_atr"]:>5.1f} '
              f'{r["vol_min"]:>5.1f} {r["rr_t1"]:>4.1f} '
              f'{int(r["total_signals"]):>4} {r["win_rate"]:>6.1%} '
              f'{r["ev"]:>+8.3f}  {status}')

    print()
    if best_combo:
        s  = best_combo['stats']
        be = _RR_SL_MULT / (best_combo['rr_t1'] + _RR_SL_MULT)
        print(f'  BEST ACCEPTED COMBO:')
        print(f'    pullback_dist     = {best_combo["pullback_dist"]}')
        print(f'    pullback_drop_atr = {best_combo["pullback_drop_atr"]}')
        print(f'    vol_min           = {best_combo["vol_min"]}')
        print(f'    rr_t1             = {best_combo["rr_t1"]}')
        print(f'    n={s["total"]}  WR={s["wr"]:.1%}  EV={s["ev"]:+.3f}R  '
              f'MaxDD={s["max_dd_r"]:.2f}R')
        print(f'    Break-even WR = {be:.1%}')
        print()
        print('  Extended breakdown for best combo:')
        _extended_breakdown(best_combo['df'])
        print()
        print('  NEXT: Update amv_lstm_uptrend.py constants with best combo values.')
        print('  Then: python -X utf8 -m market_agent.runner.run_amv_uptrend_confirm')
    else:
        top = grid_df.iloc[0]
        n   = int(top['total_signals'])
        print(f'  No combo met criteria (n>={MIN_N_ACCEPT} AND EV>0).')
        print(f'  Best EV: pd={top["pullback_dist"]} drop={top["pullback_drop_atr"]} '
              f'vol={top["vol_min"]} t1={top["rr_t1"]} '
              f'n={n} EV={top["ev"]:+.3f}R')
        print()
        if n < MIN_N_ACCEPT:
            print(f'  Signal count ({n}) is below minimum ({MIN_N_ACCEPT}).')
            print('  Possible causes and actions:')
            print()
            print('  A) Regime-Ensemble labels too few D1 bars as TRENDING_UP.')
            print('     → Check: what % of D1 bars are labelled TRENDING_UP per symbol?')
            print('     → Expected: 30-50% of bars in a 2.5-year bull-leaning period.')
            print('     → If < 15%, the regime filter is too restrictive for D1.')
            print('     → Fix: check regime_ensemble_signal() on D1 hist — it may')
            print('       have been tuned for H1 bar counts and its indicators')
            print('       (crossover age, slope thresholds) may not translate to D1.')
            print()
            print('  B) RSI 45-55 zone is genuinely rare even on D1 for these assets.')
            print('     → Check: how often does D1 RSI touch 45-55 per symbol per year?')
            print('     → Expected: 3-6 times per symbol per year during uptrends.')
            print('     → If < 2 times per year, the issue is the regime filter (see A).')
            print()
            print('  C) Gate 1 (SMA slope 0.10%) is too aggressive.')
            print('     → Try loosening _MIN_SMA20_SLOPE_PCT to 0.05% and re-run.')
        else:
            print(f'  n={n} >= {MIN_N_ACCEPT} but EV negative across all combos.')
            print('  Check extended breakdown above for which symbol or RSI zone is broken.')
    print('=' * 70)


if __name__ == '__main__':
    main()