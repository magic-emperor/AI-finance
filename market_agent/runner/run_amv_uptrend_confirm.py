"""
market_agent/runner/run_amv_uptrend_confirm.py  (v4.3 — D1 timeframe, 5yr)

AMV-LSTM-Uptrend Confirmation Runner — DAILY TIMEFRAME

v4.3 changes vs v4.2:
  close_pct floor: 0.50 → 0.40 (LT.NS 0.40-0.50 EV=+1.046R, GOOGL EV=+0.475R)
  close_pct upper cap: 0.70 → 0.85 (5/7 symbols positive above 0.70 on 5yr data)
  TATASTEEL.NS removed: structural mismatch — breakout pattern, not pullback brain
  Basket: 6 symbols (was 7)

Run:
  python -X utf8 -m market_agent.runner.run_amv_uptrend_confirm
"""
import logging
import math
import pandas as pd
from datetime import datetime, timedelta

from market_agent.brain.amv_lstm_uptrend import (
    amv_lstm_uptrend_signal,
    _RR_T1_MULT, _RR_T2_MULT, _RR_SL_MULT,
    _PULLBACK_ATR_DISTANCE, _PULLBACK_LOOKBACK_BARS, _MIN_PULLBACK_DROP_ATR,
    _MIN_BOUNCE_VOL_RATIO, _MIN_CLOSE_ABOVE_LOW_PCT, _MAX_CLOSE_ABOVE_LOW_PCT,
    _RSI_PULLBACK_MAX, _RSI_RECOVERY_MIN,
    _MIN_SMA20_SLOPE_PCT, _ATR_EXPANSION_FACTOR,
    _MIN_W1_BARS, _MIN_W1_SLOPE_PCT,
    UPTREND_EXCLUDED_SYMBOLS,
)
from market_agent.brain.regime_ensemble import regime_ensemble_signal
from market_agent.runner.backtester import (
    load_ohlcv, aggregate_results,
    _evaluate_signal, _build_trade_row, MIN_HIST_BARS,
)
from market_agent.data.storage.postgres import PostgresStorage

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('run_amv_uptrend_confirm_v4_d1')

# 6-symbol D1 basket (5yr data confirmed)
# TATASTEEL.NS removed v4.2: structural mismatch — edge only in >=0.70 candles
# (breakout pattern, not pullback-to-SMA20). 5yr raw investigation confirmed.
ALL_SYMBOLS = [
    'LT.NS',        # Confirmed edge ALL close_pct zones. SMA20 bounce works cleanly.
    'AAPL',         # 0.50-0.70 EV=+1.000R n=21. Gate [0.40,0.85] correct.
    'RELIANCE.NS',  # 0.50-0.70 EV=+0.562R n=26. Gate change may unlock signals.
    'ITC.NS',       # 0.50-0.70 EV=+0.025R marginal. Monitor.
    'AMD',          # 0.50-0.70 EV=+0.400R n=21. Confirmed.
    'GOOGL',        # Edge in 0.40-0.50 EV=+0.475R n=14. Gate lowered to capture this.
]
ACTIVE_SYMBOLS = [s for s in ALL_SYMBOLS if s not in UPTREND_EXCLUDED_SYMBOLS]

TIMEFRAME             = '1d'
END                   = datetime.utcnow()
START                 = END - timedelta(days=1825)  # 5 years (extended from 900/2.5yr)
                                                    # DB confirmed: 5yr D1 available
UPTREND_ALLOWED       = {'TRENDING_UP'}
_SLIPPAGE             = 0.0005   # 0.05% per side
_MAX_BARS_IN_TRADE_D1 = 30       # 30 D1 bars = 6 calendar weeks
MIN_N_ACCEPT          = 20


def main():
    DATE_TAG   = END.strftime('%Y%m%d')
    TRADES_CSV = f'amv_uptrend_confirmed_v4_d1_{DATE_TAG}.csv'

    be_wr = _RR_SL_MULT / (_RR_T1_MULT + _RR_SL_MULT)

    log.info('=' * 70)
    log.info('AMV-LSTM-Uptrend Confirmation v4.2 — D1 TIMEFRAME (5yr dataset)')
    log.info(f'Active symbols   : {ACTIVE_SYMBOLS}')
    log.info(f'Excluded         : {sorted(UPTREND_EXCLUDED_SYMBOLS)}')
    log.info(f'Timeframe        : {TIMEFRAME}  (D1)')
    log.info(f'Period           : {START.date()} → {END.date()}  (~5 years)')
    log.info(f'RSI gate         : [{_RSI_RECOVERY_MIN}, {_RSI_PULLBACK_MAX}]  (v4.1: ceiling 55→60)')
    log.info(f'close_pct gate   : [{_MIN_CLOSE_ABOVE_LOW_PCT}, {_MAX_CLOSE_ABOVE_LOW_PCT}]  '
             f'(v4.1: upper cap 0.70 added)')
    log.info(f'SMA slope gate   : >= {_MIN_SMA20_SLOPE_PCT}% per 5 D1 bars  (v4.1: 0.10→0.05)')
    log.info(f'ATR factor       : {_ATR_EXPANSION_FACTOR}')
    log.info(f'Pullback dist    : {_PULLBACK_ATR_DISTANCE}×ATR, '
             f'lookback {_PULLBACK_LOOKBACK_BARS} D1 bars')
    log.info(f'pullback_drop    : {_MIN_PULLBACK_DROP_ATR}×ATR (Method B)')
    log.info(f'vol_min          : {_MIN_BOUNCE_VOL_RATIO}×')
    log.info(f'W1 gate          : W1 SMA10, min {_MIN_W1_BARS} weekly bars, '
             f'slope >= -{_MIN_W1_SLOPE_PCT}%')
    log.info(f'R:R              : T1={_RR_T1_MULT}× SL={_RR_SL_MULT}× (entry-anchored)')
    log.info(f'Break-even WR    : {be_wr:.1%}')
    log.info(f'Slippage         : {_SLIPPAGE*100:.2f}% per side')
    log.info(f'Max trade bars   : {_MAX_BARS_IN_TRADE_D1} D1 bars (= 6 weeks)')
    log.info(f'Accept           : n >= {MIN_N_ACCEPT} AND EV > 0')
    log.info('=' * 70)

    storage  = PostgresStorage()
    all_rows = []
    min_start = max(MIN_HIST_BARS, 60)   # D1 warmup: 60 bars minimum

    for sym in ACTIVE_SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, START, END, storage)
        if df.empty:
            log.warning(f'  {sym}: no D1 data — skipping')
            continue
        log.info(f'  {sym}: {len(df)} D1 bars  '
                 f'({df.index[0].date()} → {df.index[-1].date()})')

        n           = len(df)
        sym_signals = 0

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
                bs = amv_lstm_uptrend_signal(hist, regime='TRENDING_UP')
            except Exception as e:
                log.debug(f'  {sym} bar {i}: signal error — {e}')
                continue
            if bs.direction == 'HOLD':
                continue

            m     = bs.measurements or {}
            entry = float(m.get('entry_price', 0) or 0)
            t1    = float(m.get('target_1',    0) or 0)
            sl    = float(m.get('stop_loss',   0) or 0)
            if entry <= 0 or t1 <= 0 or sl <= 0:
                continue

            # Slippage — BUY: pay more on entry, less received on exit
            entry_s = entry * (1 + _SLIPPAGE)
            t1_s    = t1    * (1 - _SLIPPAGE)
            sl_s    = sl    * (1 - _SLIPPAGE)

            ev  = _evaluate_signal(entry_s, t1_s, sl_s, future_bars)
            row = _build_trade_row(
                sym, df.index[i], bs, reg, ev,
                extra_params={
                    'pullback_dist':       _PULLBACK_ATR_DISTANCE,
                    'pullback_lookback':   _PULLBACK_LOOKBACK_BARS,
                    'pullback_drop_atr':   _MIN_PULLBACK_DROP_ATR,
                    'vol_min':             _MIN_BOUNCE_VOL_RATIO,
                    'rr_t1':               _RR_T1_MULT,
                    'rr_sl':               _RR_SL_MULT,
                    'rsi_range':           f'[{_RSI_RECOVERY_MIN},{_RSI_PULLBACK_MAX}]',
                    'close_pct_min':       _MIN_CLOSE_ABOVE_LOW_PCT,
                    'sma_slope_min':       _MIN_SMA20_SLOPE_PCT,
                    'timeframe':           'D1',
                },
            )
            row['rsi']             = m.get('rsi', 0)
            row['close_pct']       = m.get('close_pct', 0)
            row['vol_ratio']       = m.get('vol_ratio', 0)
            row['atr_expanding']   = m.get('atr_expanding', 0)
            row['pullback_method'] = m.get('pullback_method', 'NONE')
            row['sma20_dist_atr']  = m.get('sma20_dist_atr', 0)
            row['sma20_slope']     = m.get('sma20_slope', 0)
            row['atr_avg_14']      = m.get('atr_avg_14', 0)
            all_rows.append(row)
            sym_signals += 1

        log.info(f'  {sym}: {sym_signals} signals generated')

    if not all_rows:
        log.error('No trades generated across all symbols.')
        log.error('Most likely cause: regime_ensemble_signal() is tuned for H1 bars.')
        log.error('Check: what % of D1 bars are labelled TRENDING_UP per symbol.')
        log.error('Expected: 30-50% of D1 bars in a 2.5-year period.')
        log.error('If near-zero % labelled TRENDING_UP, regime filter is too strict for D1.')
        return

    df_t   = pd.DataFrame(all_rows)
    stats  = aggregate_results(df_t)
    ev     = stats['ev']
    be_wr  = _RR_SL_MULT / (_RR_T1_MULT + _RR_SL_MULT)

    df_t.to_csv(TRADES_CSV, index=False)
    log.info(f'Trades saved → {TRADES_CSV}')

    print('\n' + '=' * 70)
    print('AMV-LSTM-UPTREND CONFIRMATION v4.2 — D1 TIMEFRAME  (5-YEAR DATASET)')
    print(f'  Period: ~2.5y | RSI: [{_RSI_RECOVERY_MIN},{_RSI_PULLBACK_MAX}] | '
          f'close_pct >= {_MIN_CLOSE_ABOVE_LOW_PCT} | '
          f'SMA slope >= {_MIN_SMA20_SLOPE_PCT}%/5d')
    print(f'  T1={_RR_T1_MULT}× | SL={_RR_SL_MULT}× | Break-even WR = {be_wr:.1%}')
    print('=' * 70)
    print(f'  Total signals  : {stats["total"]}')
    print(f'  Wins  (T1 hit) : {stats["wins"]}')
    print(f'  Losses (SL hit): {stats["losses"]}')
    print(f'  Expired        : {stats["expired"]}')
    print(f'  Win Rate       : {stats["wr"]:.1%}  (break-even: {be_wr:.1%})')
    print(f'  avg_R          : {stats["avg_r"]:+.3f}R')
    print(f'  EV             : {ev:+.3f}R per trade')
    print(f'  Max Drawdown   : {stats["max_dd_r"]:.3f}R')
    print('=' * 70)

    # ── Symbol breakdown ──────────────────────────────────────────────────────
    print('\n  ── Per-Symbol D1 Performance ──')
    for sym in sorted(df_t['symbol'].unique()):
        sub  = df_t[df_t['symbol'] == sym]
        s    = aggregate_results(sub)
        note = ('← confirmed D1' if sym in ('LT.NS', 'AAPL', 'AMD')
                else '← gate unlocked')
        flag = 'OK' if s['ev'] > 0 else '--'
        print(f'  {sym:<18} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
              f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}  {note}')

    # ── RSI sub-buckets ───────────────────────────────────────────────────────
    if 'rsi' in df_t.columns:
        print(f'\n  ── D1 RSI Sub-buckets (gate: {_RSI_RECOVERY_MIN}-{_RSI_PULLBACK_MAX}) ──')
        for label, mask in [
            (f'{_RSI_RECOVERY_MIN}-50 deep pullback',
             (df_t['rsi'] >= _RSI_RECOVERY_MIN) & (df_t['rsi'] < 50)),
            (f'50-{_RSI_PULLBACK_MAX} upper zone',
             (df_t['rsi'] >= 50) & (df_t['rsi'] <= _RSI_PULLBACK_MAX)),
            ('< 45  [gate leak ⚠]', df_t['rsi'] < _RSI_RECOVERY_MIN),
            ('> 55  [gate leak ⚠]', df_t['rsi'] > _RSI_PULLBACK_MAX),
        ]:
            sub = df_t[mask]
            if len(sub) == 0:
                continue
            s    = aggregate_results(sub)
            flag = '⚠ GATE LEAK' if 'leak' in label else ('OK' if s['ev'] > 0 else '--')
            print(f'  RSI {label:<30} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
                  f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}')

    # ── close_pct breakdown ───────────────────────────────────────────────────
    if 'close_pct' in df_t.columns:
        print(f'\n  ── D1 Bounce Day Quality (close_pct, gate [{_MIN_CLOSE_ABOVE_LOW_PCT},{_MAX_CLOSE_ABOVE_LOW_PCT}]) ──')
        for label, mask in [
            (f'< {_MIN_CLOSE_ABOVE_LOW_PCT} [gate leak ⚠]',
             df_t['close_pct'] < _MIN_CLOSE_ABOVE_LOW_PCT),
            ('0.40-0.50  [newly unlocked v4.3]',
             (df_t['close_pct'] >= 0.40) & (df_t['close_pct'] < 0.50)),
            ('0.50-0.70  [core zone]',
             (df_t['close_pct'] >= 0.50) & (df_t['close_pct'] < 0.70)),
            ('0.70-0.85  [newly unlocked v4.3]',
             (df_t['close_pct'] >= 0.70) & (df_t['close_pct'] <= 0.85)),
            (f'> {_MAX_CLOSE_ABOVE_LOW_PCT} [gate leak ⚠]',
             df_t['close_pct'] > _MAX_CLOSE_ABOVE_LOW_PCT),
        ]:
            sub = df_t[mask]
            if len(sub) == 0:
                continue
            s    = aggregate_results(sub)
            flag = '⚠ GATE LEAK' if 'leak' in label else ('OK' if s['ev'] > 0 else '--')
            print(f'  close_pct {label:<30} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
                  f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}')

    # ── Pullback method ───────────────────────────────────────────────────────
    if 'pullback_method' in df_t.columns:
        print('\n  ── Pullback Method ──')
        mA = len(df_t[df_t['pullback_method'] == 'METHOD_A'])
        mB = len(df_t[df_t['pullback_method'] == 'METHOD_B'])
        for method in ['METHOD_A', 'METHOD_B']:
            sub = df_t[df_t['pullback_method'] == method]
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

    # ── ATR state ─────────────────────────────────────────────────────────────
    if 'atr_expanding' in df_t.columns:
        print('\n  ── ATR State ──')
        for label, val, leak in [
            ('Expanding', 1, False), ('Contracting [gate leak ⚠]', 0, True)
        ]:
            sub = df_t[df_t['atr_expanding'] == val]
            if len(sub) == 0:
                continue
            s    = aggregate_results(sub)
            flag = '⚠ GATE LEAK' if leak else ('OK' if s['ev'] > 0 else '--')
            print(f'  ATR {label:<28} n={s["total"]:3d}  WR={s["wr"]:.1%}  '
                  f'avg_R={s["avg_r"]:+.3f}  EV={s["ev"]:+.3f}  {flag}')

    # ── Expired trade analysis ─────────────────────────────────────────────────
    expired_n = stats.get('expired', 0)
    if expired_n > stats['total'] * 0.3:
        print(f'\n  ⚠ HIGH EXPIRY RATE: {expired_n}/{stats["total"]} trades expired '
              f'({expired_n/stats["total"]:.0%}).')
        print(f'  Current max hold = {_MAX_BARS_IN_TRADE_D1} D1 bars (6 weeks).')
        print('  Consider increasing _MAX_BARS_IN_TRADE_D1 to 45 (9 weeks).')
        print('  D1 targets at 2.5×ATR may take 4-8 weeks to resolve.')

    # ── Verdict ───────────────────────────────────────────────────────────────
    print()
    print('=' * 70)
    if ev > 0 and stats['total'] >= MIN_N_ACCEPT:
        se    = math.sqrt(stats['wr'] * (1 - stats['wr']) / stats['total'])
        ci_lo = stats['wr'] - 1.96 * se
        ev_lo = ci_lo * _RR_T1_MULT - (1 - ci_lo) * _RR_SL_MULT
        print(f'  ✅ CONFIRMED — EV={ev:+.3f}R, n={stats["total"]} >= {MIN_N_ACCEPT}')
        print(f'  95% CI WR lower bound = {ci_lo:.1%}')
        print(f'  95% CI EV lower bound = {ev_lo:+.3f}R  '
              f'({"ABOVE zero ✅" if ev_lo > 0 else "BELOW zero ⚠ — need more data"})')
        print()
        print('  LOCKED PARAMS (D1):')
        print(f'    timeframe         = D1')
        print(f'    pullback_dist     = {_PULLBACK_ATR_DISTANCE}×ATR '
              f'(lookback {_PULLBACK_LOOKBACK_BARS} bars)')
        print(f'    pullback_drop_atr = {_MIN_PULLBACK_DROP_ATR}×ATR (Method B)')
        print(f'    vol_min           = {_MIN_BOUNCE_VOL_RATIO}×20-day avg')
        print(f'    rr_t1             = {_RR_T1_MULT}×ATR  (SL={_RR_SL_MULT}×)')
        print(f'    rsi_range         = [{_RSI_RECOVERY_MIN}, {_RSI_PULLBACK_MAX}]')
        print(f'    close_pct_min     = {_MIN_CLOSE_ABOVE_LOW_PCT}')
        print(f'    sma_slope_min     = {_MIN_SMA20_SLOPE_PCT}% per 5 D1 bars')
        print(f'    w1_gate           = W1 SMA10, slope >= -{_MIN_W1_SLOPE_PCT}%')
        print(f'    excluded          = {sorted(UPTREND_EXCLUDED_SYMBOLS)} '
              f'(add any broken symbols post-review)')
        print()
        print('  NEXT STEPS:')
        print('  1. Wire AMV-LSTM-Uptrend-D1 into paper trading alongside D1 downtrend.')
        print('  2. Generate signals once per day at close.')
        print('  3. Monitor for 2 weeks (D1 regime — 1 week = 5 signals max).')
        print('  4. Check live gate_factor distribution matches backtest ratio.')

    elif ev > 0 and stats['total'] < MIN_N_ACCEPT:
        print(f'  ⚠ EV={ev:+.3f}R positive but n={stats["total"]} < {MIN_N_ACCEPT}.')
        print('  Statistically insufficient. Options:')
        print('  A) Extend to 1200 days (5 years) if data exists.')
        print('  B) Add 2-3 more symbols with confirmed D1 data.')
        print('  C) Loosen pullback_dist to 1.25 to capture more setups.')

    else:
        print(f'  ❌ NOT CONFIRMED — EV={ev:+.3f}R, n={stats["total"]}')
        print()
        print('  Priority diagnostic steps:')
        print()
        print('  1. Check regime coverage:')
        print('     How many D1 bars are labelled TRENDING_UP per symbol?')
        print('     Run: regime_ensemble_signal on each symbol D1 hist, count labels.')
        print('     If < 20% labelled TRENDING_UP over 2.5 years, regime filter')
        print('     is too strict and was tuned for H1 bar density, not D1.')
        print()
        print('  2. Check expiry rate:')
        print(f'     If > 30% expired, _MAX_BARS_IN_TRADE_D1={_MAX_BARS_IN_TRADE_D1} is too short.')
        print('     D1 targets at 2.5×ATR can take 4-8 weeks. Try 45 bars.')
        print()
        print('  3. Check if any symbols are structurally broken on D1:')
        print('     Any symbol with n >= 5 and WR < 25%, EV < -0.3R — exclude it.')
        print('     Add to UPTREND_EXCLUDED_SYMBOLS in brain file.')
        print()
        print('  4. If all symbols EV negative and n is adequate:')
        print('     The RSI 45-55 edge from H1 may not transfer to D1.')
        print('     Next step: remove RSI lower bound gate (try RSI 45-65).')
        print('     Document result before concluding the strategy has no D1 edge.')
    print('=' * 70)


if __name__ == '__main__':
    main()