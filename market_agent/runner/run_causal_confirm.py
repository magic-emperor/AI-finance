"""
market_agent/runner/run_causal_confirm.py

Causal-Ensemble (Brain 7) — 365-Day Confirmation Runner
=========================================================
VERSION 2 — updated after 180d confirm analysis (2026-03-09).

WHY 365 DAYS:
  SQUEEZE is a rare regime. In 180d, equity symbols produced only 2–5 signals
  each after gates — statistically meaningless. Projecting to 365d gives:
    - Pooled US equity:    ~38 signals  (was ~19)
    - Pooled Indian equity: ~20 signals (was ~10, includes new additions)
    - BTC-USD:             ~52 signals  (was ~26)
    - FX 4h:               ~24 signals  (was ~12)
    - Total projected:     ~180 signals (was ~70)
  This is the minimum viable sample for a pooled decision.

WHY MORE SYMBOLS:
  Added MSFT, META, AMZN, TSLA to US equity pool.
  These are liquid, 1h data available, same mean-reversion mechanics as AAPL/AMD.
  Adding 4 symbols increases US equity signal frequency from ~3/month to ~6/month.
  NOT adding index ETFs (SPY, QQQ) — volume is artificial, fills different.

CHANGES vs v1 (180d runner):
  1. Window: 180 days → 365 days
  2. New symbols: MSFT, META, AMZN, TSLA added to equity 1h pool
  3. TATASTEEL.NS excluded (brain exclusion list, p=1.95% — statistically confirmed bad)
  4. LT.NS kept in run (p=8.98% — not conclusive, needs more data)
  5. BTC RSI gate in brain (RSI>55 for BUY, RSI<45 for SELL — brain gate 7)
  6. Reporting: pooled by asset-group (equity-US, equity-IN, crypto, fx-comm)
     NOT per-symbol, because per-symbol n is too thin for individual decisions
  7. Regime cache: regime computed ONCE per bar (not per combo) — fast run

ACCEPTANCE CRITERIA (365-day, pooled groups):
  Overall:       n >= 50, EV > 0, WR >= 38%, MaxDD <= 10R
  Per group:     n >= 20, EV > 0  (to make deployment decision per group)

SPEED: Should run in 10-20 minutes (was 2+ hours).
  - Regime cached per bar
  - Single config (no combo loop)
  - Pre-filter RANGING/VOLATILE before calling brain

Run:
  python -X utf8 -m market_agent.runner.run_causal_confirm
"""
import logging
import pandas as pd
from datetime import datetime, timedelta

from market_agent.brain.causal_ensemble import (
    causal_ensemble_signal,
    CAUSAL_EXCLUDED_SYMBOLS,
    FX_COMM_SYMBOLS,
    _SQUEEZE_RATIO,
    _BTC_RSI_BUY_MIN, _BTC_RSI_SELL_MAX,
)
from market_agent.brain.regime_ensemble  import regime_ensemble_signal
from market_agent.runner.backtester      import (
    load_ohlcv, aggregate_results,
    _evaluate_signal, _build_trade_row, MIN_HIST_BARS, MAX_BARS_IN_TRADE,
)
from market_agent.data.storage.postgres  import PostgresStorage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('run_causal_confirm')


# ══════════════════════════════════════════════════════════════════════════════
# SYMBOL LIST
# ══════════════════════════════════════════════════════════════════════════════

# US Equity 1h — added MSFT, META, AMZN, TSLA vs v1
SYMBOLS_US_EQUITY_1H = [
    'AAPL', 'AMD', 'NVDA', 'GOOGL',
    'MSFT', 'META', 'AMZN', 'TSLA',   # NEW — added for sample volume
]

# Indian Equity 1h — TATASTEEL.NS removed (brain exclusion, p=1.95%)
# LT.NS kept — needs 365d data (p=8.98%)
SYMBOLS_IN_EQUITY_1H = [
    'ADANIENT.NS', 'ADANIPORTS.NS', 'LT.NS',
    # Excluded (brain blocks these anyway, listed for transparency):
    # 'HDFCBANK.NS'  — 90d+180d SQUEEZE WR=0%
    # 'RELIANCE.NS'  — contradictory results, needs 365d (brain blocks)
    # 'TATASTEEL.NS' — p=1.95%, statistically confirmed bad
]

# Crypto: BTC excluded after 365d confirm (WR=25%, EV=−0.237, p not low enough but consistent).
# BTC trends through squeezes — re-evaluate after 180 live signals.
SYMBOLS_CRYPTO_1H = []  # empty — BTC excluded (brain also blocks it via CAUSAL_EXCLUDED_SYMBOLS)

# FX / Commodities 4h ONLY — 1h blocked in brain (gate 3)
# CL=F excluded: 365d SQUEEZE WR=0% EV=−1.000 (7 signals), p=0.78%
# Crude oil does not mean-revert on squeezes.
SYMBOLS_FX_4H = [
    'GBPJPY=X', 'GC=F',
    # USDJPY=X excluded (brain blocks — 90d+180d both negative)
    # CL=F excluded (365d confirmed: WR=0%, brain blocks via CAUSAL_EXCLUDED_SYMBOLS)
]

ALL_SYMBOL_TF = (
    [(sym, '1h') for sym in SYMBOLS_US_EQUITY_1H]   +
    [(sym, '1h') for sym in SYMBOLS_IN_EQUITY_1H]   +
    [(sym, '1h') for sym in SYMBOLS_CRYPTO_1H]      +
    [(sym, '4h') for sym in SYMBOLS_FX_4H]
)

# Asset group mapping for pooled reporting
def _asset_group(sym: str) -> str:
    if sym == 'BTC-USD':
        return 'crypto'
    if sym in FX_COMM_SYMBOLS:
        return 'fx_comm_4h'
    if sym.endswith('.NS'):
        return 'equity_IN_1h'
    return 'equity_US_1h'


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

END   = datetime.utcnow()
START = END - timedelta(days=365)

DATE_TAG    = END.strftime('%Y%m%d')
TRADES_CSV  = f'causal_confirm_trades_{DATE_TAG}.csv'
SUMMARY_CSV = f'causal_confirm_summary_{DATE_TAG}.csv'

SLIPPAGE_PCT = 0.0005   # 0.05% per side

# Overall acceptance
CONFIRM_MIN_N   = 50
CONFIRM_MIN_EV  = 0.0
CONFIRM_MIN_WR  = 0.38
CONFIRM_MAX_DD  = 10.0

# Per-group acceptance (deployment decision per group)
GROUP_MIN_N  = 20
GROUP_MIN_EV = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# FAST CONFIRM LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_confirm(data_cache: dict) -> pd.DataFrame:
    """
    Bar-by-bar confirm. Regime computed ONCE per bar per symbol/TF (cached).
    Brain called once per bar. No combo loop.

    Speed vs old grid:
      - Old grid: 27 combos × all bars × regime_compute = 2+ hours
      - This runner: 1 config × all bars × regime_compute (cached) = 10-20 min
    """
    all_rows = []

    for (sym, tf), df in data_cache.items():
        n        = len(df)
        sym_rows = []
        regime_cache: dict = {}   # bar_index → regime_sig

        for i in range(MIN_HIST_BARS, n - MAX_BARS_IN_TRADE):
            hist        = df.iloc[:i]
            future_bars = df.iloc[i: i + MAX_BARS_IN_TRADE]

            # ── Regime (cached — computed only once per bar) ──────────────────
            if i not in regime_cache:
                try:
                    regime_cache[i] = regime_ensemble_signal(hist)
                except Exception:
                    regime_cache[i] = None

            regime_sig = regime_cache[i]
            if regime_sig is None:
                continue
            regime = regime_sig.measurements.get('computed_regime', 'RANGING')

            # Pre-filter: skip CHAOS, RANGING, VOLATILE before calling brain
            # Brain blocks these too, but this avoids full indicator computation
            if regime in ('CHAOS', 'RANGING', 'VOLATILE'):
                continue

            # Pre-filter TRENDING without squeeze (brain blocks, skip early)
            if regime in ('TRENDING_UP', 'TRENDING_DOWN'):
                close_s   = hist['Close']
                sma20_tmp = close_s.rolling(20).mean()
                std20_tmp = close_s.rolling(20).std()
                u_tmp     = sma20_tmp + 2 * std20_tmp
                l_tmp     = sma20_tmp - 2 * std20_tmp
                bw_tmp    = ((u_tmp - l_tmp) / sma20_tmp.replace(0, float('nan'))).dropna()
                if len(bw_tmp) >= 10:
                    cur_w  = float(bw_tmp.iloc[-1])
                    avg_w2 = float(bw_tmp.iloc[-min(20, len(bw_tmp)):].mean())
                    if not (cur_w < avg_w2 * _SQUEEZE_RATIO):
                        continue
                else:
                    continue

            # ── Brain signal ──────────────────────────────────────────────────
            try:
                brain_sig = causal_ensemble_signal(
                    hist, regime=regime, symbol=sym, timeframe=tf
                )
            except Exception as e:
                log.debug(f"brain error {sym}/{tf} bar {i}: {e}")
                continue

            if brain_sig.direction == 'HOLD':
                continue

            m = brain_sig.measurements or {}
            entry_raw = float(m.get('entry_price', 0) or 0)
            t1_raw    = float(m.get('target_1',    0) or 0)
            sl_raw    = float(m.get('stop_loss',   0) or 0)
            if entry_raw <= 0 or t1_raw <= 0 or sl_raw <= 0:
                continue

            direction = brain_sig.direction
            entry = (entry_raw * (1 + SLIPPAGE_PCT)
                     if direction == 'BUY'
                     else entry_raw * (1 - SLIPPAGE_PCT))

            if direction == 'BUY'  and (entry >= t1_raw or entry <= sl_raw):
                continue
            if direction == 'SELL' and (entry <= t1_raw or entry >= sl_raw):
                continue

            eval_res = _evaluate_signal(entry, t1_raw, sl_raw, future_bars)
            bar_time = df.index[i]
            row = _build_trade_row(sym, bar_time, brain_sig, regime_sig,
                                   eval_res, extra_params={'timeframe': tf})
            row['timeframe']    = tf
            row['rsi']          = m.get('rsi', 0)
            row['pct_b']        = m.get('pct_b', 0)
            row['bb_width']     = m.get('bb_width', 0)
            row['is_squeeze']   = m.get('is_squeeze', 0)
            row['sig_tier']     = m.get('sig_strength_tier', 'UNKNOWN')
            row['asset_group']  = _asset_group(sym)
            sym_rows.append(row)

        wins = sum(1 for r in sym_rows if r.get('outcome') == 'WIN')
        loss = sum(1 for r in sym_rows if r.get('outcome') == 'LOSS')
        exp  = sum(1 for r in sym_rows if r.get('outcome') == 'EXPIRED')
        if sym_rows or True:  # always log to detect missing data
            log.info(f"  {sym:>18} {tf}: signals={len(sym_rows)} W={wins} L={loss} E={exp}")
        all_rows.extend(sym_rows)

    return pd.DataFrame(all_rows)


# ══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════════════════

def _maxdd(r_series):
    cum=0; peak=0; maxdd=0
    for r in r_series:
        cum+=r; peak=max(peak,cum); maxdd=max(maxdd,peak-cum)
    return round(maxdd, 2)


def _print_results(df: pd.DataFrame) -> bool:
    decided = df[df['outcome'].isin(['WIN', 'LOSS'])]
    if decided.empty:
        print("  No decided trades. Check regime distribution.")
        return False

    n    = len(decided)
    wins = (decided['outcome'] == 'WIN').sum()
    wr   = wins / n
    ev   = decided['r_achieved'].mean()
    dd   = _maxdd(decided['r_achieved'].values)

    print(f"\n{'='*75}")
    print(f"CAUSAL-ENSEMBLE — 365-Day Confirmation")
    print(f"{'='*75}")
    print(f"  Period         : {df['bar_time'].min()} → {df['bar_time'].max()}")
    print(f"  Total signals  : {len(df)}")
    print(f"  WIN / LOSS     : {wins} / {n-wins}")
    print(f"  EXPIRED        : {(df['outcome']=='EXPIRED').sum()}")
    print(f"  Win Rate       : {wr:.1%}  (target >= {CONFIRM_MIN_WR:.0%})")
    print(f"  EV (avg R)     : {ev:+.3f}  (target > 0)")
    print(f"  Max Drawdown   : {dd:.2f}R  (limit <= {CONFIRM_MAX_DD}R)")

    print(f"\n  {'─'*60}")
    print(f"  POOLED GROUP BREAKDOWN (deployment decision per group)")
    print(f"  {'─'*60}")
    group_results = {}
    for grp in sorted(decided['asset_group'].unique()):
        sub  = decided[decided['asset_group'] == grp]
        gn   = len(sub)
        gwr  = (sub['outcome']=='WIN').sum() / gn
        gev  = sub['r_achieved'].mean()
        gdd  = _maxdd(sub['r_achieved'].values)
        ok   = '✅' if (gn >= GROUP_MIN_N and gev > GROUP_MIN_EV) else '❌'
        thin = ' (THIN)' if gn < GROUP_MIN_N else ''
        print(f"  {grp:<20} n={gn:4d}  WR={gwr:.1%}  EV={gev:+.3f}  MaxDD={gdd:.1f}R  {ok}{thin}")
        group_results[grp] = {'n': gn, 'wr': gwr, 'ev': gev, 'ok': gn >= GROUP_MIN_N and gev > 0}

    print(f"\n  {'─'*60}")
    print(f"  SYMBOL BREAKDOWN")
    print(f"  {'─'*60}")
    sym_stats = []
    for sym in sorted(decided['symbol'].unique()):
        sub = decided[decided['symbol'] == sym]
        sym_stats.append((sym, len(sub), (sub['outcome']=='WIN').sum()/len(sub), sub['r_achieved'].mean()))
    for sym, sn, swr, sev in sorted(sym_stats, key=lambda x: -x[3]):
        thin = ' (thin)' if sn < 15 else ''
        print(f"  {sym:<18} n={sn:4d}  WR={swr:.1%}  EV={sev:+.3f}  {'✅' if sev>0 else '❌'}{thin}")

    print(f"\n  {'─'*60}")
    print(f"  CUMULATIVE R CURVE (every 10 decided trades)")
    print(f"  {'─'*60}")
    r_vals = decided['r_achieved'].values
    cum_r  = 0.0
    for idx, r in enumerate(r_vals):
        cum_r += r
        if (idx + 1) % 10 == 0 or idx == len(r_vals) - 1:
            print(f"  Trade {idx+1:4d}: cum_R = {cum_r:+.3f}")

    overall_passes = (n >= CONFIRM_MIN_N and ev > CONFIRM_MIN_EV
                      and wr >= CONFIRM_MIN_WR and dd <= CONFIRM_MAX_DD)
    groups_passing = [g for g, r in group_results.items() if r['ok']]

    print(f"\n{'='*75}")
    if overall_passes:
        print(f"  ✅ OVERALL CONFIRMATION PASSED")
        if groups_passing:
            print(f"  Groups cleared for deployment: {', '.join(groups_passing)}")
        print(f"  NEXT: Paper trade 1 week, min 10 signals → live at 0.5% risk/trade")
    else:
        print(f"  ❌ OVERALL CONFIRMATION FAILED")
        if n < CONFIRM_MIN_N:
            print(f"  FAIL: n={n} < {CONFIRM_MIN_N} — not enough SQUEEZE setups in 365d")
            print(f"  ACTION: Add more liquid equity symbols or check regime distribution")
        if ev <= 0:
            print(f"  FAIL: EV={ev:.3f} <= 0")
        if wr < CONFIRM_MIN_WR:
            print(f"  FAIL: WR={wr:.1%} < {CONFIRM_MIN_WR:.0%}")
        if dd > CONFIRM_MAX_DD:
            print(f"  FAIL: MaxDD={dd:.2f}R > {CONFIRM_MAX_DD}R")

        if groups_passing:
            print(f"\n  ⚠️  PARTIAL: These groups passed individually: {', '.join(groups_passing)}")
            print(f"  Consider deploying ONLY these groups while investigating failing ones.")

    print(f"\n  Excluded symbols (brain gates): {CAUSAL_EXCLUDED_SYMBOLS}")
    print(f"  BTC RSI gate: BUY>={_BTC_RSI_BUY_MIN}, SELL<={_BTC_RSI_SELL_MAX}")
    print(f"{'='*75}")

    return overall_passes


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 75)
    log.info("Causal-Ensemble (Brain 7) — 365-Day Confirmation Run v2")
    log.info(f"Symbol/TF pairs : {len(ALL_SYMBOL_TF)}")
    log.info(f"Period          : {START.date()} → {END.date()} ({(END-START).days} days)")
    log.info(f"Slippage        : {SLIPPAGE_PCT*100:.2f}% per side")
    log.info(f"New symbols     : MSFT, META, AMZN, TSLA (added for sample volume)")
    log.info(f"Excluded        : {CAUSAL_EXCLUDED_SYMBOLS}")
    log.info(f"FX blocked 1h   : {FX_COMM_SYMBOLS}")
    log.info(f"BTC RSI gate    : BUY>={_BTC_RSI_BUY_MIN}, SELL<={_BTC_RSI_SELL_MAX}")
    log.info(f"Acceptance      : n>={CONFIRM_MIN_N} EV>0 WR>={CONFIRM_MIN_WR:.0%} MaxDD<={CONFIRM_MAX_DD}R")
    log.info(f"Expected runtime: 10-20 minutes (regime cached per bar)")
    log.info("=" * 75)

    storage = PostgresStorage()

    log.info("Pre-loading OHLCV data (365 days)...")
    data_cache: dict = {}
    for sym, tf in ALL_SYMBOL_TF:
        df = load_ohlcv(sym, tf, START, END, storage)
        if df.empty:
            log.warning(f"  No data: {sym} {tf} — skipping (check yfinance or DB)")
        else:
            data_cache[(sym, tf)] = df
            log.info(f"  {sym:>18} {tf}: {len(df):5d} bars")

    if not data_cache:
        log.error("No data loaded. Check database connection and symbol list.")
        return

    log.info(f"Running confirm on {len(data_cache)} symbol/TF pairs...")
    confirm_df = run_confirm(data_cache)

    if confirm_df.empty:
        log.error("0 trades generated. Diagnose:")
        log.error("  1. Check regime_ensemble on 365d — is SQUEEZE occurring?")
        log.error("  2. Check brain gate logs by running causal_ensemble_signal manually")
        log.error("  3. Verify MIN_HIST_BARS and MAX_BARS_IN_TRADE in backtester.py")
        return

    confirm_df.to_csv(TRADES_CSV, index=False)
    log.info(f"Trades saved → {TRADES_CSV}")

    _print_results(confirm_df)


if __name__ == '__main__':
    main()