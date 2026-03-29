"""
market_agent/runner/run_regime_grid.py

One-command runner: Regime-Ensemble parameter grid on 3 months of
BTC-USD + RELIANCE.NS + NVDA using the 1h timeframe stored in our DB.

What we test:
  volatile_mult: [2.0, 2.5, 3.0]   (× 60-bar median ATR% → VOLATILE threshold)
  chaos_mult:    [4.0, 5.0, 6.0]   (× 60-bar median ATR% → CHAOS threshold)

Acceptance rule: sample >= 30 signals AND EV > baseline by 0.10R.

Outputs:
  - regime_grid_results_YYYYMMDD.csv  : full grid stats per param combo
  - regime_trades_YYYYMMDD.csv        : every evaluated trade with full detail
  - prints the top 5 param combos to console

Run: python -m market_agent.runner.run_regime_grid
"""
import logging
import pandas as pd
from datetime import datetime, timedelta
from market_agent.runner.backtester import run_backtest, aggregate_results, run_param_grid
from market_agent.data.storage.postgres import PostgresStorage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('run_regime_grid')

# ── Settings ─────────────────────────────────────────────────────────────────
SYMBOLS   = ['BTC-USD', 'RELIANCE.NS', 'NVDA']
TIMEFRAME = '1h'
END       = datetime.utcnow()
START     = END - timedelta(days=90)

DATE_TAG  = END.strftime('%Y%m%d')
GRID_CSV  = f'regime_grid_results_{DATE_TAG}.csv'
TRADES_CSV= f'regime_trades_{DATE_TAG}.csv'

# ── Regime-Ensemble factory ───────────────────────────────────────────────────
# We monkey-patch the threshold multipliers into the brain at runtime.
# The brain's _ensure_connected() runs once per combo.
def _make_regime_fn(volatile_mult: float, chaos_mult: float):
    """Return a patched regime_ensemble_signal with custom multipliers."""
    import types
    from market_agent.brain import regime_ensemble as re_mod

    def patched_signal(hist):
        import pandas as pd
        from market_agent.brain.brain_contract import BrainSignal
        from market_agent.brain.brain_utils import calc_atr, calc_adx

        if len(hist) < 50:
            return re_mod.regime_ensemble_signal(hist)   # use orignal for shorthand

        close = hist['Close']
        high  = hist['High']
        low   = hist['Low']

        atr   = calc_atr(hist, 14)
        adx   = calc_adx(hist, 14)
        price = float(close.iloc[-1])
        atr_pct = atr / price if price > 0 else 0.01

        sma50 = float(close.rolling(50).mean().iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        std20 = float(close.rolling(20).std().iloc[-1])
        bb_w  = (sma20 + 2 * std20 - (sma20 - 2 * std20)) / sma20 if sma20 > 0 else 0.04

        upper_s    = close.rolling(20).mean() + 2 * close.rolling(20).std()
        lower_s    = close.rolling(20).mean() - 2 * close.rolling(20).std()
        bb_width_s = (upper_s - lower_s) / close.rolling(20).mean()
        avg_bb_w   = float(bb_width_s.rolling(20).mean().iloc[-1]) if len(bb_width_s) > 20 else bb_w

        # ATR% series for adaptive thresholds
        prev_c = close.shift(1)
        tr_s = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
        atr_pct_series = tr_s / close

        if len(hist) >= 60:
            median_atr_pct     = float(atr_pct_series.iloc[-60:].median())
            volatile_threshold = max(0.02, median_atr_pct * volatile_mult)   # grid param
            chaos_threshold    = max(0.05, median_atr_pct * chaos_mult)       # grid param
        else:
            median_atr_pct     = None
            volatile_threshold = 0.04
            chaos_threshold    = 0.08

        regime_suitability = 'HIGH'
        if atr_pct > chaos_threshold:
            regime, confidence = 'CHAOS', 0.90
        elif atr_pct > volatile_threshold:
            regime, confidence = 'VOLATILE', 0.80
        elif bb_w < avg_bb_w * 0.70:
            regime, confidence = 'SQUEEZE', 0.80
        elif adx > 25:
            regime = 'TRENDING_UP' if price > sma50 else 'TRENDING_DOWN'
            confidence = min(0.90, 0.65 + (adx - 25) / 100)
        elif adx > 22:
            regime = 'TRENDING_UP' if price > sma50 else 'TRENDING_DOWN'
            confidence = 0.55
            regime_suitability = 'MEDIUM'
        else:
            regime, confidence = 'RANGING', 0.72

        weights = re_mod.UNIFIED_REGIMES.get(regime, re_mod.UNIFIED_REGIMES['RANGING'])
        return BrainSignal(
            brain_name='Regime-Ensemble',
            specialization='Market Condition Classifier -- Meta Brain',
            method=f'Adaptive ATR (vol×{volatile_mult} chaos×{chaos_mult})',
            direction='HOLD',
            confidence=confidence,
            signal_strength=min(1.0, adx / 50.0),
            signal_age_candles=0,
            primary_evidence=f'Regime: {regime} | ATR%={atr_pct:.2%} | ADX={adx:.1f}',
            supporting_factors=[
                f'volatile_mult={volatile_mult}', f'chaos_mult={chaos_mult}',
                f'volatile_threshold={volatile_threshold:.3%}',
            ],
            contra_factors=[],
            method_confidence=0.85,
            regime_suitability=regime_suitability,
            reliability_flags={},
            measurements={
                'computed_regime':    regime,
                'atr_pct':            round(atr_pct, 4),
                'adx':                round(adx, 1),
                'bb_width':           round(bb_w, 4),
                'avg_bb_w':           round(avg_bb_w, 4),
                'price_vs_sma50_pct': round((price - sma50) / sma50 * 100, 2),
                'volatile_threshold': round(volatile_threshold, 4),
                'chaos_threshold':    round(chaos_threshold, 4),
                'median_atr_pct':     round(median_atr_pct, 4) if median_atr_pct else None,
                'decision_factor':    'REGIME_CLASSIFY',
                'price_at_signal':    round(price, 6),
                'atr_at_signal':      round(float(atr), 6),
                'atr_pct_at_signal':  round(float(atr) / price * 100, 3) if price > 0 else 0.0,
                'bars_used':          len(hist),
            },
            recent_accuracy=None,
            regime_accuracy=None,
        )
    return patched_signal


# For the brain_fn in a regime backtest we use a DUMMY directional brain.
# We're not testing a directional brain here — we're testing Regime-Ensemble itself.
# So we use a simple momentum-based directional brain as the "trigger"
# so we can actually see how many signals fire and survive (WIN/LOSS/EXPIRED).
def _make_momentum_brain():
    """
    Simple SMA5/SMA20 crossover brain used purely as the signal trigger
    when backtesting Regime-Ensemble. Not a production brain.
    """
    from market_agent.brain.brain_contract import BrainSignal

    def momentum_signal(hist):
        if len(hist) < 25:
            return BrainSignal(
                brain_name='Momentum-Trigger', specialization='Backtester trigger',
                method='SMA5/20', direction='HOLD', confidence=0.0,
                signal_strength=0.0, signal_age_candles=0,
                primary_evidence='insufficient data', supporting_factors=[],
                contra_factors=[], method_confidence=0.0,
                regime_suitability='HIGH', reliability_flags={}, measurements={},
                recent_accuracy=None, regime_accuracy=None,
            )
        close = hist['Close']
        sma5  = close.rolling(5).mean().iloc[-1]
        sma20 = close.rolling(20).mean().iloc[-1]
        price = float(close.iloc[-1])
        atr   = float((hist['High'] - hist['Low']).rolling(14).mean().iloc[-1])

        direction = 'BUY' if sma5 > sma20 else 'SELL'
        entry     = price
        target_1  = price * (1 + 2 * atr / price) if direction == 'BUY' else price * (1 - 2 * atr / price)
        stop_loss = price * (1 - 0.75 * atr / price) if direction == 'BUY' else price * (1 + 0.75 * atr / price)

        return BrainSignal(
            brain_name='Momentum-Trigger', specialization='Backtester trigger',
            method='SMA5/20', direction=direction, confidence=0.60,
            signal_strength=abs(sma5 - sma20) / sma20, signal_age_candles=1,
            primary_evidence=f'SMA5={sma5:.2f} vs SMA20={sma20:.2f}',
            supporting_factors=[], contra_factors=[],
            method_confidence=0.65, regime_suitability='HIGH',
            reliability_flags={},
            measurements={
                'entry_price':       round(entry, 4),
                'target_1':          round(target_1, 4),
                'stop_loss':         round(stop_loss, 4),
                'price_at_signal':   round(price, 4),
                'atr_at_signal':     round(atr, 4),
                'atr_pct_at_signal': round(atr / price * 100, 3),
                'decision_factor':   'SMA_CROSS',
                'bars_used':         len(hist),
            },
            recent_accuracy=None, regime_accuracy=None,
        )
    return momentum_signal


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("Regime-Ensemble Parameter Grid Backtest")
    log.info(f"Symbols   : {SYMBOLS}")
    log.info(f"Timeframe : {TIMEFRAME}")
    log.info(f"Period    : {START.date()} → {END.date()} ({(END-START).days} days)")
    log.info("=" * 60)

    storage = PostgresStorage()

    PARAM_GRID = {
        'volatile_mult': [2.0, 2.5, 3.0],
        'chaos_mult':    [4.0, 5.0, 6.0],
    }

    # We need both a brain_fn and a regime_fn for the backtester.
    # Here we hold the directional brain constant (Momentum-Trigger)
    # and vary only the regime parameters, so we can see how the regime
    # gate changes signal count and performance.
    momentum_brain = _make_momentum_brain()

    def brain_fn_factory(**params):
        return momentum_brain   # constant brain, only regime varies

    def regime_fn_factory(**params):
        return _make_regime_fn(
            volatile_mult=params['volatile_mult'],
            chaos_mult=params['chaos_mult'],
        )

    # ── Run grid ──────────────────────────────────────────────────────────────
    grid_df = run_param_grid(
        brain_fn_factory  = brain_fn_factory,
        regime_fn_factory = regime_fn_factory,
        param_grid        = PARAM_GRID,
        symbols           = SYMBOLS,
        timeframe         = TIMEFRAME,
        start             = START,
        end               = END,
        storage           = storage,
        output_csv        = GRID_CSV,
    )

    if grid_df.empty:
        log.warning("No grid results — check DB data coverage.")
        return

    # ── Also collect all trade detail rows ────────────────────────────────────
    all_trades = []
    best_params = grid_df.iloc[0]
    log.info(f"\nBest params: volatile_mult={best_params['volatile_mult']} "
             f"chaos_mult={best_params['chaos_mult']} EV={best_params['ev']}")

    # Re-run best params to get detailed trade log
    best_brain  = momentum_brain
    best_regime = _make_regime_fn(
        volatile_mult=float(best_params['volatile_mult']),
        chaos_mult=float(best_params['chaos_mult']),
    )
    for sym in SYMBOLS:
        trades = run_backtest(
            brain_fn     = best_brain,
            regime_fn    = best_regime,
            symbol       = sym,
            timeframe    = TIMEFRAME,
            start        = START,
            end          = END,
            storage      = storage,
            extra_params = {
                'volatile_mult': float(best_params['volatile_mult']),
                'chaos_mult':    float(best_params['chaos_mult']),
            },
        )
        if not trades.empty:
            all_trades.append(trades)

    if all_trades:
        trades_df = pd.concat(all_trades, ignore_index=True)
        trades_df.to_csv(TRADES_CSV, index=False)
        log.info(f"Trade detail saved → {TRADES_CSV}  ({len(trades_df)} rows)")

    # ── Print summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("TOP 5 REGIME PARAM COMBOS (sorted by EV)")
    print("=" * 70)
    cols = ['volatile_mult', 'chaos_mult', 'total_signals', 'win_rate', 'avg_r', 'ev']
    print(grid_df[cols].head(5).to_string(index=False))
    print("=" * 70)

    # Acceptance check (from EachBrain.md)
    baseline = grid_df.iloc[-1]   # worst combo as proxy baseline
    accepted = grid_df[
        (grid_df['total_signals'] >= 30) &
        (grid_df['ev'] > baseline['ev'] + 0.10)
    ]
    if not accepted.empty:
        best = accepted.iloc[0]
        print(f"\n✅ ACCEPTED: volatile_mult={best['volatile_mult']} "
              f"chaos_mult={best['chaos_mult']} | "
              f"EV={best['ev']:.3f} signals={best['total_signals']}")
    else:
        print("\n⚠️  No combo meets acceptance criteria (sample ≥ 30 AND EV > baseline + 0.10R)")
        print("    Current params may already be near-optimal. No change recommended.")


if __name__ == '__main__':
    main()
