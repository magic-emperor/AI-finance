"""
market_agent/runner/run_amv_grid.py

AMV-LSTM parameter grid backtest — TRENDING_DOWN SPECIALIST version.

STATUS: Brain confirmed as TRENDING_DOWN equity specialist after Run 4.
  Symbols: RELIANCE.NS, NVDA only (BTC/ETH excluded — structural no-edge confirmed).
  Regime:  TRENDING_DOWN only (TRENDING_UP blocked — 3 runs, WR=27%, EV negative).
  Locked params: gap=0.15, vol=1.2, t1=1.5, sl=0.75, min_cross_age=4.

USE OF THIS GRID going forward:
  - Re-run to validate on new data (e.g., extended period, new symbols)
  - Re-run after any gate changes to verify EV impact
  - Do NOT use this to re-test TRENDING_UP — that is amv_lstm_uptrend.py

Grid parameters (3×3×3 = 27 combos):
  gap_threshold : minimum SMA gap%
  vol_mult_min  : minimum vol_ratio for confirmation boost
  rr_t1_mult    : T1 distance in ATR units

FIXES in this version:
  - BTC-USD removed from SYMBOLS (structural no-edge, 3 runs confirmed)
  - AMV_ALLOWED restricted to TRENDING_DOWN only
  - Gate 5 ATR expansion now applies to SELL signals (was dead code for TU BUY only)
  - cross_age >= 3 bonus changed to >= 5 (was always true after Gate 1 raised to age>=4)
  - Vol boost comment clarified: vol_mult_min is the swept param, _VOL_BOOST_THRESHOLD=1.2 is locked

Run:
  python -X utf8 -m market_agent.runner.run_amv_grid
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import itertools

from market_agent.brain.amv_lstm        import (
    amv_lstm_signal,
    _MIN_SLOPE_PCT, _ATR_EXPANSION_FACTOR,
    # Gate constants — imported so grid and brain CANNOT drift apart
    _MIN_CROSS_AGE, _STALE_WINDOW_MINUTES, _MIN_GAP_PCT,
    _MIN_VOL_RATIO,
    # Confidence constants
    _GAP_TO_CONF_SCALE, _GAP_CONF_MAX, _VOL_BOOST_THRESHOLD,
    _FALLBACK_CONF_CAP,
)
from market_agent.brain.regime_ensemble  import regime_ensemble_signal
from market_agent.brain.brain_contract   import BrainSignal
from market_agent.brain.brain_utils      import calc_atr, calc_rsi_float
from market_agent.runner.backtester      import (
    load_ohlcv, aggregate_results,
    _evaluate_signal, _build_trade_row, MIN_HIST_BARS, MAX_BARS_IN_TRADE
)
from market_agent.data.storage.postgres  import PostgresStorage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('run_amv_grid')

# ── Settings ──────────────────────────────────────────────────────────────────
# BTC-USD removed permanently — 3 runs confirmed structural WR=24.9% (no edge on crypto).
# This brain is equity/forex/commodity only. Do not add BTC/ETH back.
SYMBOLS          = ['RELIANCE.NS', 'NVDA']
TIMEFRAME        = '1h'
TIMEFRAME_MINUTES = 60   # used for stale gate calc — change if switching TF
END              = datetime.utcnow()
START            = END - timedelta(days=90)

DATE_TAG   = END.strftime('%Y%m%d')
GRID_CSV   = f'amv_grid_results_{DATE_TAG}.csv'
TRADES_CSV = f'amv_trades_{DATE_TAG}.csv'

# ── Grid ──────────────────────────────────────────────────────────────────────
PARAM_GRID = {
    'gap_threshold': [0.15, 0.20, 0.30],
    'vol_mult_min':  [1.2,  1.5,  2.0],
    'rr_t1_mult':    [1.5,  2.0,  2.5],
}

# R:R per regime — matches new amv_lstm.py asymmetric design
RR_SL_BY_REGIME = {
    'TRENDING_DOWN': 0.75,
    'TRENDING_UP':   0.65,
}


# ── Full patched signal function (matches ALL gates in amv_lstm.py) ───────────
def _make_amv_fn(gap_threshold: float, vol_mult_min: float, rr_t1_mult: float):
    """
    Returns a patched amv_lstm_signal that applies the grid param values AND
    all gates that exist in amv_lstm.py. Previous version was missing Gate 4/5/6.

    IMPORTANT: The patched function MUST be a faithful replica of amv_lstm.py
    gate logic. Any gate added to amv_lstm.py must also be added here.
    These two must stay in sync.
    """

    def amv_signal_patched(hist: pd.DataFrame, regime: str = 'TRENDING_UP') -> BrainSignal:
        close = hist['Close']
        sma5  = close.rolling(5).mean()
        sma20 = close.rolling(20).mean()
        atr   = calc_atr(hist)
        price = float(close.iloc[-1])
        atr_val = float(atr)

        # Regime-aware R:R
        rr_sl = RR_SL_BY_REGIME.get(regime, 0.75)

        cross_bullish = sma5.iloc[-1] > sma20.iloc[-1]
        cross_age = 0
        for i in range(1, min(20, len(hist))):
            if (sma5.iloc[-i] > sma20.iloc[-i]) == cross_bullish:
                cross_age += 1
            else:
                break

        sma5_slope  = float((sma5.iloc[-1] - sma5.iloc[-3]) / sma5.iloc[-3] * 100) if sma5.iloc[-3] else 0.0
        sma20_slope = float((sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100) if sma20.iloc[-5] else 0.0
        sma_gap_pct = abs(float(sma5.iloc[-1]) - float(sma20.iloc[-1])) / price * 100 if price > 0 else 0

        vol_series = hist['Volume']
        vol_avg    = float(vol_series.iloc[-20:].mean()) if len(vol_series) >= 20 else float(vol_series.mean())
        vol_curr   = float(vol_series.iloc[-1])
        vol_ratio  = vol_curr / vol_avg if vol_avg > 0 else 1.0

        # ATR expansion (Gate 5)
        if len(hist) >= 25:
            atr_series = pd.Series([float(calc_atr(hist.iloc[:j])) for j in range(len(hist)-5, len(hist))])
            atr_avg_5  = float(atr_series.mean()) if not atr_series.empty else atr_val
        else:
            atr_avg_5 = atr_val
        atr_expanding = atr_val >= atr_avg_5 * _ATR_EXPANSION_FACTOR

        def _hold(reason_key, conf=0.41, decision_factor='GATE_HOLD'):
            return BrainSignal(
                brain_name='AMV-LSTM', specialization='Temporal Trend Memory',
                method=f'SMA5/20 grid(gap≥{gap_threshold} vol≥{vol_mult_min}x t1={rr_t1_mult}×ATR)',
                direction='HOLD', confidence=conf,
                signal_strength=0.0, signal_age_candles=cross_age,
                primary_evidence=reason_key,
                supporting_factors=[], contra_factors=[reason_key],
                method_confidence=0.15, regime_suitability='LOW',
                reliability_flags={reason_key: True},
                measurements={
                    'decision_factor': decision_factor,
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': len(hist), 'sma_gap_pct': sma_gap_pct,
                    'vol_ratio': round(vol_ratio, 3),
                    'sma5_slope': sma5_slope, 'sma20_slope': sma20_slope,
                    'atr_expanding': int(atr_expanding),
                },
                rr_t1_mult=rr_t1_mult, rr_sl_mult=rr_sl,
                recent_accuracy=None, regime_accuracy=None,
            )

        # Gate 1: Fresh cross — imported from brain to stay in sync
        if cross_age < _MIN_CROSS_AGE:
            return _hold('cross_too_fresh', 0.45, 'FRESH_GATE')

        # Gate 2: Stale cross — uses _STALE_WINDOW_MINUTES / TIMEFRAME_MINUTES (not hardcoded)
        stale_candles = max(4, int(_STALE_WINDOW_MINUTES / TIMEFRAME_MINUTES))
        if cross_age > stale_candles:
            return _hold('cross_too_old', 0.40, 'STALE_GATE')

        # Gate 3: Gap minimum (GRID PARAM)
        if sma_gap_pct < gap_threshold:
            return _hold('gap_too_small', 0.42, 'GAP_GATE')

        # Gate 4 (enhanced): Slope alignment + minimum threshold
        direction_check = 'BUY' if cross_bullish else 'SELL'
        if direction_check == 'BUY':
            sma20_ok = sma20_slope > _MIN_SLOPE_PCT
            sma5_ok  = sma5_slope  > 0
        else:
            sma20_ok = sma20_slope < -_MIN_SLOPE_PCT
            sma5_ok  = sma5_slope  < 0

        if not sma20_ok or not sma5_ok:
            return _hold('slope_conflict', 0.43, 'SLOPE_GATE')

        # Gate 5: ATR expansion — applied to ALL signals (not just TRENDING_UP BUY)
        # Brain is TRENDING_DOWN SELL specialist. ATR must be expanding for real momentum.
        # Contracting ATR on a SELL cross = no energy behind the move = low probability.
        if not atr_expanding:
            return _hold('atr_contracting', 0.43, 'ATR_EXPANSION_GATE')

        # Gate 6 (NEW): Low volume hard block — uses _MIN_VOL_RATIO from brain
        if vol_ratio < _MIN_VOL_RATIO:
            return _hold('low_volume_cross', 0.41, 'LOW_VOLUME_GATE')

        # Survived all gates — build signal
        direction = direction_check

        # Confidence (uses named constants from brain — stays in sync)
        base_conf = 0.50 + min(_GAP_CONF_MAX, sma_gap_pct * _GAP_TO_CONF_SCALE)
        slope_quality = (
            (sma5_slope > 0 and sma20_slope > _MIN_SLOPE_PCT) or
            (sma5_slope < 0 and sma20_slope < -_MIN_SLOPE_PCT)
        )
        if slope_quality:
            base_conf += 0.04
        # Mature cross bonus: age >= 5 is genuinely mature (post-retest AND established).
        # age 4 = just past gate threshold, no extra bonus.
        # Evidence: age 6-7 avg_R=+0.500, age 4-5 avg_R=+0.250 (Run 4).
        # Note: cross_age >= 3 was here previously but after raising _MIN_CROSS_AGE to 4,
        # cross_age >= 3 was ALWAYS true (dead conditional). Fixed to >= 5.
        if cross_age >= 5:
            base_conf += 0.02
        if atr_expanding:
            base_conf += 0.03

        # RSI
        rsi = calc_rsi_float(hist)
        if direction == 'BUY':
            if rsi > 70: base_conf -= 0.10
            elif rsi > 60: base_conf -= 0.05
        else:
            if rsi < 30: base_conf -= 0.10
            elif rsi < 40: base_conf -= 0.05

        base_conf  = max(0.0, base_conf)
        confidence = min(_FALLBACK_CONF_CAP, base_conf)

        # Vol boost AFTER cap.
        # NOTE: vol_mult_min is the GRID PARAMETER being swept.
        # Production brain uses _VOL_BOOST_THRESHOLD=1.2 (fixed).
        # vol_mult_min=1.2 combo is the only one that exactly matches production.
        # This is intentional: vol_mult_min sweeps the gate threshold itself,
        # not to test how many signals pass, but to measure which minimum
        # confirmation level produces the best EV. The accepted value (1.2)
        # is then locked into _VOL_BOOST_THRESHOLD in the brain.
        if vol_ratio >= vol_mult_min:
            confidence = min(_FALLBACK_CONF_CAP + 0.04, confidence + 0.04)

        # RSI favorable entry boost AFTER cap
        if direction == 'BUY' and rsi < 40:
            confidence = min(0.72, confidence + 0.04)
        elif direction == 'SELL' and rsi > 60:
            confidence = min(0.72, confidence + 0.04)

        # Trade levels — asymmetric per regime
        if direction == 'BUY':
            entry     = price
            target_1  = price + rr_t1_mult * atr_val
            stop_loss = price - rr_sl * atr_val
        else:
            entry     = price
            target_1  = price - rr_t1_mult * atr_val
            stop_loss = price + rr_sl * atr_val

        return BrainSignal(
            brain_name='AMV-LSTM', specialization='Temporal Trend Memory',
            method=f'SMA5/20 grid(gap≥{gap_threshold} vol≥{vol_mult_min}x t1={rr_t1_mult}×ATR)',
            direction=direction, confidence=confidence,
            signal_strength=sma_gap_pct / 2.0,
            signal_age_candles=cross_age,
            primary_evidence=(
                f'SMA cross age={cross_age} gap={sma_gap_pct:.3f}% '
                f'vol={vol_ratio:.2f}x ATR {"expanding" if atr_expanding else "flat"}'
            ),
            supporting_factors=[
                f'SMA5 slope={sma5_slope:+.3f}%',
                f'vol_ratio={vol_ratio:.2f}',
                f'RSI={rsi:.1f}',
            ],
            contra_factors=[],
            method_confidence=0.55,
            regime_suitability='HIGH',
            reliability_flags={'low_volume_cross': vol_ratio < _MIN_VOL_RATIO},
            measurements={
                'entry_price':        round(entry, 6),
                'target_1':           round(target_1, 6),
                'stop_loss':          round(stop_loss, 6),
                'decision_factor':    'SMA_CROSS',
                'price_at_signal':    round(price, 6),
                'atr_at_signal':      round(atr_val, 6),
                'atr_pct_at_signal':  round(atr_val / price * 100, 3) if price > 0 else 0.0,
                'bars_used':          len(hist),
                'sma_gap_pct':        round(sma_gap_pct, 4),
                'cross_age':          cross_age,
                'vol_ratio':          round(vol_ratio, 3),
                'rsi':                round(rsi, 1),
                'atr_expanding':      int(atr_expanding),
                'regime':             regime,
                'rr_sl':              rr_sl,
                'indicator_1_name':   'sma_gap_pct',
                'indicator_1_value':  round(sma_gap_pct, 4),
                'indicator_2_name':   'vol_ratio',
                'indicator_2_value':  round(vol_ratio, 3),
                'indicator_3_name':   'atr_expanding',
                'indicator_3_value':  int(atr_expanding),
            },
            rr_t1_mult=rr_t1_mult, rr_sl_mult=rr_sl,
            recent_accuracy=None, regime_accuracy=None,
        )

    return amv_signal_patched


# ── Grid runner ────────────────────────────────────────────────────────────────
def run_combo(params: dict, data_cache: dict) -> pd.DataFrame:
    brain_fn    = _make_amv_fn(**params)
    combo_trades = []

    for sym, df in data_cache.items():
        n = len(df)
        rows = []
        for i in range(MIN_HIST_BARS, n - MAX_BARS_IN_TRADE):
            hist        = df.iloc[:i]
            future_bars = df.iloc[i: i + MAX_BARS_IN_TRADE]

            # Regime gate — TRENDING_DOWN ONLY (confirmed specialist after Run 4)
            # TRENDING_UP blocked: 3 runs confirmed WR=27%, EV negative, structural.
            try:
                regime_sig = regime_ensemble_signal(hist)
            except Exception:
                continue
            regime = regime_sig.measurements.get('computed_regime', 'RANGING')
            AMV_ALLOWED = {'TRENDING_DOWN'}
            if regime not in AMV_ALLOWED:
                continue

            # Brain signal — pass regime so asymmetric R:R applies correctly
            try:
                brain_sig = brain_fn(hist, regime=regime)
            except Exception:
                continue
            if brain_sig.direction == 'HOLD':
                continue

            m     = brain_sig.measurements or {}
            entry = float(m.get('entry_price', 0) or 0)
            t1    = float(m.get('target_1',    0) or 0)
            sl    = float(m.get('stop_loss',   0) or 0)
            if entry <= 0 or t1 <= 0 or sl <= 0:
                continue

            eval_res = _evaluate_signal(entry, t1, sl, future_bars)
            bar_time = df.index[i]
            row = _build_trade_row(sym, bar_time, brain_sig, regime_sig,
                                   eval_res, extra_params=params)
            # Store additional detail for extended breakdown
            row['rsi']          = m.get('rsi', 0)
            row['atr_expanding']= m.get('atr_expanding', 0)
            row['vol_ratio']    = m.get('vol_ratio', 0)
            row['cross_age']    = m.get('cross_age', 0)
            row['rr_sl']        = m.get('rr_sl', 0.75)
            rows.append(row)

        if rows:
            combo_trades.extend(rows)

    return pd.DataFrame(combo_trades)


def _extended_breakdown(df: pd.DataFrame, baseline_ev: float) -> None:
    """Print regime, symbol, RSI, and ATR breakdowns for the best combo."""
    if df.empty:
        return

    print("\n  ── Regime Breakdown ──")
    for reg in df['regime_used'].unique():
        sub = df[df['regime_used'] == reg]
        wins = (sub['outcome'] == 'WIN').sum()
        losses = (sub['outcome'] == 'LOSS').sum()
        total = len(sub)
        wr = wins / total if total > 0 else 0
        avg_r = sub['r_achieved'].mean() if 'r_achieved' in sub.columns else 0
        ev_flag = "✅ EV+" if avg_r > 0 else "❌ EV-"
        print(f"    {reg:<20} n={total:4d}  WR={wr:.1%}  avg_R={avg_r:+.3f}  {ev_flag}")

    print("\n  ── Symbol Breakdown ──")
    for sym in df['symbol'].unique():
        sub = df[df['symbol'] == sym]
        wins = (sub['outcome'] == 'WIN').sum()
        total = len(sub)
        wr = wins / total if total > 0 else 0
        avg_r = sub['r_achieved'].mean() if 'r_achieved' in sub.columns else 0
        print(f"    {sym:<18} n={total:4d}  WR={wr:.1%}  avg_R={avg_r:+.3f}")

    if 'rsi' in df.columns:
        print("\n  ── RSI Bucket Breakdown ──")
        buckets = [
            ('< 40 (oversold)',  df['rsi'] < 40),
            ('40–60 (neutral)',  (df['rsi'] >= 40) & (df['rsi'] <= 60)),
            ('> 60 (overbought)',df['rsi'] > 60),
        ]
        for label, mask in buckets:
            sub = df[mask]
            if len(sub) == 0:
                continue
            wins  = (sub['outcome'] == 'WIN').sum()
            wr    = wins / len(sub)
            avg_r = sub['r_achieved'].mean() if 'r_achieved' in sub.columns else 0
            print(f"    RSI {label:<20} n={len(sub):4d}  WR={wr:.1%}  avg_R={avg_r:+.3f}")

    if 'atr_expanding' in df.columns:
        print("\n  ── ATR State Breakdown ──")
        for state, val in [('ATR Expanding', 1), ('ATR Contracting', 0)]:
            sub = df[df['atr_expanding'] == val]
            if len(sub) == 0:
                continue
            wins  = (sub['outcome'] == 'WIN').sum()
            wr    = wins / len(sub)
            avg_r = sub['r_achieved'].mean() if 'r_achieved' in sub.columns else 0
            print(f"    {state:<20} n={len(sub):4d}  WR={wr:.1%}  avg_R={avg_r:+.3f}")

    if 'cross_age' in df.columns:
        print("\n  ── Cross Age Breakdown ──")
        bins = [(2, 3, 'age 2-3'), (4, 5, 'age 4-5'), (6, 7, 'age 6-7')]
        for lo, hi, label in bins:
            sub = df[(df['cross_age'] >= lo) & (df['cross_age'] <= hi)]
            if len(sub) == 0:
                continue
            wins  = (sub['outcome'] == 'WIN').sum()
            wr    = wins / len(sub)
            avg_r = sub['r_achieved'].mean() if 'r_achieved' in sub.columns else 0
            print(f"    {label:<20} n={len(sub):4d}  WR={wr:.1%}  avg_R={avg_r:+.3f}")


def main():
    log.info("=" * 70)
    log.info("AMV-LSTM Parameter Grid Backtest (with Gate 4/5/6 + asymmetric R:R)")
    log.info(f"Symbols   : {SYMBOLS}")
    log.info(f"Timeframe : {TIMEFRAME}")
    log.info(f"Period    : {START.date()} → {END.date()} ({(END-START).days} days)")
    log.info(f"Grid size : {3*3*3} combos × {len(SYMBOLS)} symbols")
    log.info(f"Gates     : Fresh + Stale + Gap + Slope(min={_MIN_SLOPE_PCT}%) + ATR_Exp({_ATR_EXPANSION_FACTOR}x) + LowVol")
    log.info("=" * 70)

    storage    = PostgresStorage()
    grid_rows  = []
    all_trades = []

    log.info("Pre-loading OHLCV data...")
    data_cache = {}
    for sym in SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, START, END, storage)
        if df.empty:
            log.warning(f"No data for {sym} — skipping")
        else:
            data_cache[sym] = df
            log.info(f"  {sym}: {len(df)} bars")

    if not data_cache:
        log.error("No data loaded. Check DB and rebuild_db.py first.")
        return

    # Baseline
    baseline_params = dict(gap_threshold=0.20, vol_mult_min=1.5, rr_t1_mult=2.0)
    log.info("Computing baseline (gap=0.20, vol=1.5x, t1=2.0×ATR)...")
    baseline_df    = run_combo(baseline_params, data_cache)
    baseline_stats = aggregate_results(baseline_df)
    baseline_ev    = baseline_stats['ev']
    log.info(
        f"  Baseline: signals={baseline_stats['total']} "
        f"WR={baseline_stats['wr']:.1%} "
        f"EV={baseline_ev:.3f} "
        f"AvgR={baseline_stats['avg_r']:.3f}"
    )

    # Grid
    keys   = list(PARAM_GRID.keys())
    combos = list(itertools.product(*PARAM_GRID.values()))

    for combo in combos:
        params   = dict(zip(keys, combo))
        log.info(f"  Testing {params}")

        combo_df = run_combo(params, data_cache)
        stats    = aggregate_results(combo_df)

        # Check TRENDING_DOWN EV independently (all signals should be TD now)
        td_ev = 0.0
        if not combo_df.empty and 'regime_used' in combo_df.columns:
            td_sub = combo_df[combo_df['regime_used'] == 'TRENDING_DOWN']
            if len(td_sub) > 0 and 'r_achieved' in td_sub.columns:
                td_ev = float(td_sub['r_achieved'].mean())

        ev_delta = stats['ev'] - baseline_ev
        # Acceptance: TD-only EV > 0 AND sample >= 25 AND EV delta > 0.10R
        # Note: sample threshold 25 (not 30) — equity-only has fewer opportunities per 90d
        meets = (
            stats['total'] >= 25 and
            ev_delta > 0.10 and
            td_ev > 0.0
        )

        grid_rows.append({
            **params,
            'total_signals':  stats['total'],
            'wins':           stats['wins'],
            'losses':         stats['losses'],
            'expired':        stats['expired'],
            'win_rate':       stats['wr'],
            'avg_r':          stats['avg_r'],
            'ev':             stats['ev'],
            'ev_vs_baseline': round(ev_delta, 4),
            'td_ev':          round(td_ev, 4),
            'meets_criteria': meets,
        })
        log.info(
            f"    → signals={stats['total']} WR={stats['wr']:.1%} "
            f"EV={stats['ev']:.3f} Δ={ev_delta:+.3f} "
            f"TD_EV={td_ev:+.3f} {'✅' if meets else '❌'}"
        )

        if not combo_df.empty:
            all_trades.append(combo_df)

    # Save outputs
    grid_df = pd.DataFrame(grid_rows).sort_values('ev', ascending=False)
    grid_df.to_csv(GRID_CSV, index=False)
    log.info(f"Grid results → {GRID_CSV}")

    if all_trades:
        trades_df = pd.concat(all_trades, ignore_index=True)
        trades_df.to_csv(TRADES_CSV, index=False)
        log.info(f"Trades detail → {TRADES_CSV}  ({len(trades_df)} rows)")

    print("\n" + "=" * 75)
    print("AMV-LSTM TRENDING_DOWN SPECIALIST — Grid Results")
    print(f"Baseline EV: {baseline_ev:.3f}")
    print(f"Acceptance:  sample>=25  EV_delta>0.10R  TD_EV>0")
    print("=" * 75)
    cols = ['gap_threshold', 'vol_mult_min', 'rr_t1_mult',
            'total_signals', 'win_rate', 'avg_r', 'ev', 'ev_vs_baseline',
            'td_ev', 'meets_criteria']
    print(grid_df[cols].head(5).to_string(index=False))
    print("=" * 75)

    accepted = grid_df[grid_df['meets_criteria'] == True]
    if not accepted.empty:
        best = accepted.iloc[0]
        be_wr = 0.75 / (best['rr_t1_mult'] + 0.75)
        print(f"\n  ACCEPTED PARAMS:")
        print(f"  gap_threshold = {best['gap_threshold']}")
        print(f"  vol_mult_min  = {best['vol_mult_min']}")
        print(f"  rr_t1_mult    = {best['rr_t1_mult']}")
        print(f"  EV            = {best['ev']:.3f}  (baseline {baseline_ev:.3f}, delta +{best['ev_vs_baseline']:.3f})")
        print(f"  TD_EV         = {best['td_ev']:+.3f}")
        print(f"  signals       = {int(best['total_signals'])}  WR = {best['win_rate']:.1%}  break-even = {be_wr:.1%}")
    else:
        print("\n  No combo meets criteria (EV delta > 0.10R + sample >= 25 + TD_EV > 0)")
        best_row = grid_df.iloc[0]
        print(f"\n  Best EV combo (not accepted):")
        print(f"  gap={best_row['gap_threshold']} vol={best_row['vol_mult_min']} t1={best_row['rr_t1_mult']} "
              f"EV={best_row['ev']:.3f} TD_EV={best_row['td_ev']:+.3f} n={int(best_row['total_signals'])}")

    # Extended breakdowns for the best combo
    if all_trades:
        best_params = dict(zip(keys, [grid_df.iloc[0][k] for k in keys]))
        best_df     = run_combo(best_params, data_cache)
        if not best_df.empty:
            print(f"\n  Extended breakdown for best combo {best_params}:")
            _extended_breakdown(best_df, baseline_ev)

    print("\n  NEXT: Run 180-day confirmation (run_amv_confirm.py with days=180)")
    print("  Then: AMV-LSTM-Uptrend grid (run_amv_uptrend_grid.py)")


if __name__ == '__main__':
    main()