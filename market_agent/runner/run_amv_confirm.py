"""
market_agent/runner/run_amv_confirm.py

AMV-LSTM Confirmation Runner — calls amv_lstm_signal() directly.

ARCHITECTURE (fixed from previous version):
  Previous version had its own copy of the signal logic (_amv_confirmed_signal).
  That copy drifted from amv_lstm.py repeatedly — Gate 7 added to brain but not runner.
  This version calls amv_lstm_signal() directly.
  The brain file is the single source of truth. No drift possible.

FIXES in this version vs prior:
  1. Slippage added (was missing). Grid runner had slippage; confirm did not.
     Without slippage, EV results between grid and confirm are on different bases.
     0.05% per side = 0.10% round trip. Conservative for liquid large-cap equities.
  2. atr_avg_14 tracked in row (renamed from atr_avg_5 in brain).
  3. AMD watch note added to symbol breakdown output.

CURRENT PARAMS (from amv_lstm.py constants — not hardcoded here):
  min_cross_age = 6
  gap_threshold = 0.20%
  vol_min       = 0.6× (hard block)
  rr_t1         = 2.0×  (TRENDING_DOWN)
  rr_sl         = 0.75× (TRENDING_DOWN)
  gates active  = 1+2+3+4+5+6+7 (HTF D1 alignment active)

Confirmed results (180d, 12 equity symbols, Gates 1-7):
  n=49, WR=42.9%, EV=+0.573R, MaxDD=5.0R

Run:
  python -X utf8 -m market_agent.runner.run_amv_confirm
"""
import logging
import math
import pandas as pd
from datetime import datetime, timedelta

from market_agent.brain.amv_lstm import (
    amv_lstm_signal,
    _MIN_CROSS_AGE,
    _STALE_WINDOW_MINUTES,
    _MIN_GAP_PCT,
    _MIN_VOL_RATIO,
    _RR_T1_TRENDING_DOWN,
    _RR_SL_TRENDING_DOWN,
    AMV_LSTM_EXCLUDED_SYMBOLS,
)
from market_agent.brain.regime_ensemble import regime_ensemble_signal
from market_agent.runner.backtester import (
    load_ohlcv, aggregate_results,
    _evaluate_signal, _build_trade_row, MIN_HIST_BARS, MAX_BARS_IN_TRADE,
)
from market_agent.data.storage.postgres import PostgresStorage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('run_amv_confirm')

# ── Settings ──────────────────────────────────────────────────────────────────
# NVDA excluded: 4-run evidence, WR=14.3%, structural (see amv_lstm.py).
# AMD under watch: n=3, WR=0% in 180d. Not yet excluded — need n>=15 to confirm.
# BTC/ETH excluded: structural no-edge on 24/7 algo markets (3 runs).
SYMBOLS = [
    'NVDA',          # EXCLUDED — listed here intentionally to be skipped via AMV_LSTM_EXCLUDED_SYMBOLS
    'AAPL', 'AMD', 'GOOGL',
    'RELIANCE.NS', 'ITC.NS', 'HDFCBANK.NS', 'TATASTEEL.NS',
    'LT.NS', 'M&M.NS', 'ADANIENT.NS', 'ADANIPORTS.NS',
]
# Filter out excluded symbols at runtime using the brain's authoritative list
ACTIVE_SYMBOLS = [s for s in SYMBOLS if s not in AMV_LSTM_EXCLUDED_SYMBOLS]

TIMEFRAME = '1h'
END       = datetime.utcnow()
START     = END - timedelta(days=180)

AMV_ALLOWED_REGIMES = {'TRENDING_DOWN'}

# Slippage — must match grid runner (FIXED: was 0 in prior version)
_SLIPPAGE_PER_SIDE = 0.0005   # 0.05% per side = 0.10% round trip


def main():
    DATE_TAG   = END.strftime('%Y%m%d')
    TRADES_CSV = f'amv_confirmed_trades_{DATE_TAG}.csv'

    be_wr = _RR_SL_TRENDING_DOWN / (_RR_T1_TRENDING_DOWN + _RR_SL_TRENDING_DOWN)

    log.info("=" * 65)
    log.info("AMV-LSTM Confirmation Run  (direct brain call — no drift possible)")
    log.info(f"All symbols   : {SYMBOLS}")
    log.info(f"Active symbols: {ACTIVE_SYMBOLS}  (excluded: {list(AMV_LSTM_EXCLUDED_SYMBOLS)})")
    log.info(f"Regime        : {AMV_ALLOWED_REGIMES}")
    log.info(f"min_cross_age : {_MIN_CROSS_AGE}  (from brain)")
    log.info(f"gap_min       : {_MIN_GAP_PCT}%  (from brain)")
    log.info(f"vol_min       : {_MIN_VOL_RATIO}×  (from brain)")
    log.info(f"R:R           : T1={_RR_T1_TRENDING_DOWN}×  SL={_RR_SL_TRENDING_DOWN}×  (from brain)")
    log.info(f"Break-even WR : {be_wr:.1%}")
    log.info(f"Slippage      : {_SLIPPAGE_PER_SIDE*100:.2f}% per side (FIXED — now matches grid)")
    log.info(f"Gates         : 1(fresh) 2(stale) 3(gap) 4(slope) 5(ATR) 6(vol) 7(HTF-D1)")
    log.info(f"Period        : {START.date()} -> {END.date()}  ({(END-START).days} days)")
    log.info(f"MIN_HIST_BARS : {MIN_HIST_BARS}  (need >= 200 for Gate 7)")
    log.info("=" * 65)

    if MIN_HIST_BARS < 200:
        log.warning(
            f"MIN_HIST_BARS={MIN_HIST_BARS} < 200. Gate 7 needs ~140 H1 bars for 20 D1 bars. "
            f"Some Gate 7 checks will be skipped. Fix in backtester.py."
        )

    storage  = PostgresStorage()
    all_rows = []

    for sym in ACTIVE_SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, START, END, storage)
        if df.empty:
            log.warning(f"{sym}: no data loaded")
            continue
        log.info(f"  {sym}: {len(df)} bars loaded")

        n = len(df)
        for i in range(MIN_HIST_BARS, n - MAX_BARS_IN_TRADE):
            hist        = df.iloc[:i]
            future_bars = df.iloc[i: i + MAX_BARS_IN_TRADE]

            try:
                regime_sig = regime_ensemble_signal(hist)
            except Exception:
                continue
            regime = regime_sig.measurements.get('computed_regime', 'RANGING')
            if regime not in AMV_ALLOWED_REGIMES:
                continue

            try:
                brain_sig = amv_lstm_signal(hist, regime=regime)
            except Exception as e:
                log.debug(f"amv_lstm_signal failed bar {i}: {e}")
                continue

            if brain_sig.direction == 'HOLD':
                continue

            m     = brain_sig.measurements or {}
            entry = float(m.get('entry_price', 0) or 0)
            t1    = float(m.get('target_1',    0) or 0)
            sl    = float(m.get('stop_loss',   0) or 0)
            if entry <= 0 or t1 <= 0 or sl <= 0:
                continue

            # Apply slippage (FIXED: was missing — grid had it, confirm did not)
            # For SELL signals: entry gets slipped down (receive less), T1 slipped down,
            # SL slipped up (pay more to close).
            direction = brain_sig.direction
            if direction == 'SELL':
                entry_s = entry * (1 - _SLIPPAGE_PER_SIDE)   # SELL: receive less at entry
                t1_s    = t1    * (1 - _SLIPPAGE_PER_SIDE)   # cover at T1: pay more (lower)
                sl_s    = sl    * (1 + _SLIPPAGE_PER_SIDE)   # cover at SL: pay more (higher)
            else:   # BUY
                entry_s = entry * (1 + _SLIPPAGE_PER_SIDE)
                t1_s    = t1    * (1 - _SLIPPAGE_PER_SIDE)
                sl_s    = sl    * (1 - _SLIPPAGE_PER_SIDE)

            eval_res = _evaluate_signal(entry_s, t1_s, sl_s, future_bars)
            row = _build_trade_row(
                sym, df.index[i], brain_sig, regime_sig, eval_res,
                extra_params={
                    'min_cross_age': _MIN_CROSS_AGE,
                    'gap_threshold': _MIN_GAP_PCT,
                    'vol_min':       _MIN_VOL_RATIO,
                },
            )
            row['rsi']           = m.get('rsi', 0)
            row['cross_age']     = m.get('cross_age', 0)
            row['vol_ratio']     = m.get('vol_ratio', 0)
            row['atr_expanding'] = m.get('atr_expanding', 0)
            row['atr_avg_14']    = m.get('atr_avg_14', 0)   # renamed from atr_avg_5
            all_rows.append(row)

    if not all_rows:
        log.error("No trades generated. Check data, regime labeling, and MIN_HIST_BARS.")
        return

    df_trades = pd.DataFrame(all_rows)
    stats     = aggregate_results(df_trades)

    df_trades.to_csv(TRADES_CSV, index=False)
    log.info(f"Trades saved -> {TRADES_CSV}")

    ev = stats['ev']

    print("\n" + "=" * 65)
    print("AMV-LSTM CONFIRMATION RUN — RESULT")
    print(
        f"  Brain: amv_lstm_signal() direct | min_age={_MIN_CROSS_AGE} | "
        f"gap={_MIN_GAP_PCT}% | vol={_MIN_VOL_RATIO}× | "
        f"T1={_RR_T1_TRENDING_DOWN}× SL={_RR_SL_TRENDING_DOWN}×"
    )
    print(f"  Slippage: {_SLIPPAGE_PER_SIDE*100:.2f}% per side (FIXED — now consistent with grid)")
    print(f"  Break-even WR = {be_wr:.1%}")
    print("=" * 65)
    print(f"  Total signals : {stats['total']}")
    print(f"  Wins (T1 hit) : {stats['wins']}")
    print(f"  Losses (SL)   : {stats['losses']}")
    print(f"  Expired       : {stats['expired']}")
    print(f"  Win Rate      : {stats['wr']:.1%}  (break-even: {be_wr:.1%})")
    print(f"  avg_R         : {stats['avg_r']:+.3f}R")
    print(f"  EV            : {ev:+.3f}R per trade")
    print(f"  Max Drawdown  : {stats['max_dd_r']:.3f}R")
    print("=" * 65)

    # Symbol breakdown
    print("\n  -- Symbol --")
    amd_note = "  ⚠ AMD under watch (n=3 in prior run, WR=0%)" if 'AMD' in ACTIVE_SYMBOLS else ''
    for sym in df_trades['symbol'].unique():
        sub   = df_trades[df_trades['symbol'] == sym]
        sub_s = aggregate_results(sub)
        flag  = 'OK' if sub_s['ev'] > 0 else '--'
        watch = ' [WATCH]' if sym == 'AMD' and sub_s['wr'] < 0.25 else ''
        print(
            f"  {sym:<18} n={sub_s['total']:4d}  WR={sub_s['wr']:.1%}  "
            f"avg_R={sub_s['avg_r']:+.3f}  EV={sub_s['ev']:+.3f}  {flag}{watch}"
        )
    if amd_note:
        print(amd_note)

    # Cross age breakdown
    if 'cross_age' in df_trades.columns:
        print("\n  -- Cross Age --")
        for lo, hi in [(4, 5), (6, 7), (8, 10), (11, 20)]:
            sub = df_trades[(df_trades['cross_age'] >= lo) & (df_trades['cross_age'] <= hi)]
            if len(sub) == 0:
                continue
            sub_s    = aggregate_results(sub)
            age_note = ' [gated out]' if hi <= 5 and _MIN_CROSS_AGE >= 6 else ''
            print(
                f"  age {lo}-{hi}{age_note:<12} n={sub_s['total']:4d}  WR={sub_s['wr']:.1%}  "
                f"avg_R={sub_s['avg_r']:+.3f}  EV={sub_s['ev']:+.3f}  "
                f"{'OK' if sub_s['ev'] > 0 else '--'}"
            )

    # RSI breakdown
    if 'rsi' in df_trades.columns:
        print("\n  -- RSI at Entry (SELL signals: low RSI = overbought context) --")
        for label, mask in [
            ('< 40',  df_trades['rsi'] < 40),
            ('40-60', (df_trades['rsi'] >= 40) & (df_trades['rsi'] < 60)),
            ('60-70', (df_trades['rsi'] >= 60) & (df_trades['rsi'] < 70)),
            ('> 70',  df_trades['rsi'] >= 70),
        ]:
            sub = df_trades[mask]
            if len(sub) == 0:
                continue
            sub_s = aggregate_results(sub)
            print(
                f"  RSI {label:<8}  n={sub_s['total']:4d}  WR={sub_s['wr']:.1%}  "
                f"avg_R={sub_s['avg_r']:+.3f}  EV={sub_s['ev']:+.3f}"
            )

    # ATR breakdown
    if 'atr_expanding' in df_trades.columns:
        print("\n  -- ATR State --")
        for label, val in [('Expanding', 1), ('Contracting', 0)]:
            sub = df_trades[df_trades['atr_expanding'] == val]
            if len(sub) == 0:
                continue
            sub_s = aggregate_results(sub)
            print(
                f"  ATR {label:<12} n={sub_s['total']:4d}  WR={sub_s['wr']:.1%}  "
                f"avg_R={sub_s['avg_r']:+.3f}  EV={sub_s['ev']:+.3f}"
            )

    # ── VERDICT ───────────────────────────────────────────────────────────────
    min_sample = 25
    print()
    print("=" * 65)
    if ev > 0 and stats['total'] >= min_sample:
        se    = math.sqrt(stats['wr'] * (1 - stats['wr']) / stats['total'])
        ci_lo = stats['wr'] - 1.96 * se
        ev_lo = ci_lo * _RR_T1_TRENDING_DOWN - (1 - ci_lo) * _RR_SL_TRENDING_DOWN
        print(f"  CONFIRMED — EV={ev:+.3f}R, n={stats['total']} >= {min_sample}")
        print(f"  95% CI on EV: lower bound = {ev_lo:+.3f}R  "
              f"({'above' if ev_lo > 0 else 'BELOW'} zero)")
        print()
        print("  LOCKED PARAMS (all sourced from amv_lstm.py):")
        print(f"    min_cross_age = {_MIN_CROSS_AGE}")
        print(f"    gap_threshold = {_MIN_GAP_PCT}%")
        print(f"    vol_min       = {_MIN_VOL_RATIO}×")
        print(f"    rr_t1         = {_RR_T1_TRENDING_DOWN}×ATR")
        print(f"    rr_sl         = {_RR_SL_TRENDING_DOWN}×ATR below entry")
        print(f"    excluded      = {sorted(AMV_LSTM_EXCLUDED_SYMBOLS)}")
        print(f"    regime        = TRENDING_DOWN only")
        print(f"    gates         = 1+2+3+4+5+6+7 (HTF D1 active)")
        print()
        print("  NEXT: AMV-LSTM-Uptrend grid v2")
        print("  Command: python -X utf8 -m market_agent.runner.run_amv_uptrend_grid")
    elif ev > 0 and stats['total'] < min_sample:
        print(f"  EV positive ({ev:+.3f}R) but n={stats['total']} < {min_sample} (insufficient).")
        print("  Options:")
        print("    A) Add equity symbols: INFY.NS, TCS.NS")
        print("    B) Extend to 270 days: START = END - timedelta(days=270)")
    else:
        print(f"  NOT CONFIRMED — EV={ev:+.3f}R, n={stats['total']}")
        print()
        print("  Diagnosis:")
        print("  1. Is Gate 7 (HTF) blocking more signals than in last run?")
        print("     Check D1 slope direction for each symbol.")
        print("  2. Is AMD now n>=10 with WR<25%? Consider permanent exclusion.")
        print("  3. Is cross_age 6-7 bucket still losing? Was previously at break-even.")
        print("     If so, raise _MIN_CROSS_AGE to 8.")
    print("=" * 65)

    # AMD monitoring output
    if 'AMD' in [s for s in df_trades['symbol'].unique()]:
        amd_sub = df_trades[df_trades['symbol'] == 'AMD']
        amd_s   = aggregate_results(amd_sub)
        print()
        print(f"  ⚠ AMD MONITOR: n={amd_s['total']}  WR={amd_s['wr']:.1%}  EV={amd_s['ev']:+.3f}R")
        if amd_s['total'] >= 15 and amd_s['wr'] < 0.25:
            print("  ❌ AMD: n>=15 and WR<25%. Add to AMV_LSTM_EXCLUDED_SYMBOLS in amv_lstm.py.")
        elif amd_s['total'] >= 15:
            print("  ✅ AMD: n>=15. WR acceptable — do not exclude.")
        else:
            print(f"  AMD: n={amd_s['total']} < 15. Continue monitoring.")


if __name__ == '__main__':
    main()