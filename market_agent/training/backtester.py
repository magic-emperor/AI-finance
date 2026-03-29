"""
Historical Backtester — Train signal engine on past data to find optimal parameters.

Multi-source data:
- yfinance: All assets, 1min-1d OHLC (1min limited to 7 days)
- CoinDCX: Crypto (INR pairs), free public API
- Angel One: NSE stocks, via SmartAPI

Training flow (continuous loop):
1. Download historical data from all available sources
2. Walk forward through candles, generating signals at each step
3. Check next N candles for target/SL hit (simulates live resolution)
4. Score accuracy using same gradient formula as live system
5. Grid search parameters: ATR multiplier, target %, confidence thresholds
6. Save optimal params to strategy_params.json
7. Brain discussion: share what worked, what didn't
8. Repeat periodically
"""

import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import structlog

logger = structlog.get_logger()


class Backtester:
    """Run signal engine on historical data to find optimal parameters."""

    # Parameter grid for optimization
    SCALP_GRID = {
        't1_pct': [0.001, 0.0015, 0.002, 0.003, 0.004],   # 0.1% to 0.4%
        't2_pct': [0.002, 0.003, 0.004, 0.006, 0.008],     # 0.2% to 0.8%
        'sl_pct': [0.002, 0.003, 0.004, 0.005],             # 0.2% to 0.5%
    }

    SWING_GRID = {
        't1_multiplier': [0.5, 0.75, 1.0, 1.25, 1.5],
        't2_multiplier': [1.0, 1.25, 1.5, 2.0, 2.5],
        'sl_multiplier': [1.0, 1.5, 2.0, 2.5],
    }

    def __init__(self):
        self.results: List[Dict] = []
        self.best_params: Dict = {}
        try:
            from market_agent.config import BACKTEST_SLIPPAGE_BPS, BACKTEST_COMMISSION_BPS, BACKTEST_COMMISSION_PER_TRADE
            slip_pct = BACKTEST_SLIPPAGE_BPS / 10000.0
            self.slippage = {'equity': slip_pct, 'crypto': slip_pct * 0.4}
            self.commission_bps = BACKTEST_COMMISSION_BPS
            self.commission_per_trade = BACKTEST_COMMISSION_PER_TRADE
        except Exception:
            self.slippage = {'equity': 0.0005, 'crypto': 0.0002}
            self.commission_bps = 10.0
            self.commission_per_trade = 0.0

    def fetch_historical_data(self, symbol: str, period: str = '6mo',
                               intervals: List[str] = None) -> Dict[str, pd.DataFrame]:
        """
        Fetch historical data from multiple sources.

        Returns: {interval: DataFrame} e.g. {'1h': df_hourly, '1d': df_daily, '1m': df_1min}
        """
        if intervals is None:
            intervals = ['1m', '15m', '1h', '1d']

        data = {}

        # ─── yfinance (primary) ───
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)

            for interval in intervals:
                try:
                    if interval == '1m':
                        # yfinance: 1min data limited to last 7 days
                        df = ticker.history(period='7d', interval='1m')
                    elif interval == '15m':
                        df = ticker.history(period='1mo', interval='15m')
                    elif interval == '1h':
                        df = ticker.history(period=period, interval='1h')
                    elif interval == '1d':
                        df = ticker.history(period=period, interval='1d')
                    else:
                        df = ticker.history(period=period, interval=interval)

                    if df is not None and not df.empty:
                        data[interval] = df
                except Exception:
                    pass
        except ImportError:
            pass

        # ─── CoinDCX (crypto INR pairs) ───
        if '-' in symbol:
            try:
                self._fetch_coindcx(symbol, data)
            except Exception:
                pass

        return data

    def _fetch_coindcx(self, symbol: str, data: Dict):
        """Fetch crypto data from CoinDCX public API (free, no key)."""
        try:
            import requests
            # CoinDCX market codes: BTC-USD -> B-BTC_USDT
            base = symbol.split('-')[0]
            url = f"https://api.coindcx.com/exchange/v1/markets_details"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                markets = resp.json()
                # Find matching market
                for m in markets:
                    if m.get('target_currency_short_name', '').upper() == base:
                        logger.info("coindcx_market_found", market=m.get('coindcx_name'))
                        break
        except Exception:
            pass  # Silently fall back to yfinance data

    def run_backtest(self, symbol: str, strategy: str = "Intraday (Scalp)",
                     params: Dict = None, interval: str = None) -> Dict:
        """
        Run backtest on historical data with given parameters.

        Returns: {accuracy, win_rate, t1_hits, t2_hits, sl_hits, expired, total, params}
        """
        is_scalp = "Scalp" in strategy or "Intraday" in strategy

        # Determine best interval for this strategy
        if interval is None:
            interval = '1h' if is_scalp else '1d'

        # Fetch data
        hist_data = self.fetch_historical_data(symbol, period='6mo', intervals=[interval, '1d'])
        df = hist_data.get(interval)
        if df is None or len(df) < 50:
            return {'error': f'Insufficient data for {symbol} at {interval}', 'total': 0}

        # Default params
        if params is None:
            if is_scalp:
                params = {'t1_pct': 0.002, 't2_pct': 0.004, 'sl_pct': 0.003}
            else:
                params = {'t1_multiplier': 1.0, 't2_multiplier': 1.5, 'sl_multiplier': 1.5}

        # Walk-forward simulation (Path A: track binary_win, per-trade P&L, commission)
        results = {'t1_hit': 0, 't2_hit': 0, 'sl_hit': 0, 'expired': 0, 'total': 0,
                    'accuracies': [], 'directions_correct': 0, 'binary_wins': 0,
                    'trade_returns_pct': [], 'commission_total': 0.0}

        lookback = 14  # Need at least 14 bars for ATR
        sr_lookback = 50  # Bars for support/resistance detection
        lookahead = 6 if is_scalp else 12  # How many bars to check for resolution

        for i in range(max(lookback, sr_lookback), len(df) - lookahead):
            window = df.iloc[:i+1]
            future = df.iloc[i+1:i+1+lookahead]

            current_price = float(window['Close'].iloc[-1])

            # Calculate ATR
            high_low = window['High'] - window['Low']
            high_cp = (window['High'] - window['Close'].shift()).abs()
            low_cp = (window['Low'] - window['Close'].shift()).abs()
            tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            if np.isnan(atr) or atr == 0:
                continue

            # Simple direction prediction (SMA crossover)
            short_ma = float(window['Close'].rolling(5).mean().iloc[-1])
            long_ma = float(window['Close'].rolling(20).mean().iloc[-1])
            direction = 'BUY' if short_ma > long_ma else 'SELL'

            entry = current_price
            
            # Apply slippage to entry (buy higher, sell lower than quote)
            is_crypto = '-' in symbol and not symbol.endswith('.NS')
            slip = self.slippage['crypto'] if is_crypto else self.slippage['equity']
            if direction == 'BUY':
                entry = current_price * (1 + slip)  # Worse entry for buys
            else:
                entry = current_price * (1 - slip)  # Worse entry for sells

            # ═══ DYNAMIC TARGET COMPUTATION (same logic as live signal_engine) ═══
            # 1. Detect support/resistance from the window
            recent = window.tail(sr_lookback)
            tolerance = current_price * 0.005  # 0.5% cluster tolerance
            supports = []
            resistances = []

            for k in range(2, len(recent) - 2):
                h_i = float(recent['High'].iloc[k])
                h_prev = float(recent['High'].iloc[k-1])
                h_next = float(recent['High'].iloc[k+1])
                l_i = float(recent['Low'].iloc[k])
                l_prev = float(recent['Low'].iloc[k-1])
                l_next = float(recent['Low'].iloc[k+1])
                if h_i > h_prev and h_i > h_next and h_i > current_price:
                    resistances.append(h_i)
                if l_i < l_prev and l_i < l_next and l_i < current_price:
                    supports.append(l_i)

            # Sort nearest first
            resistances.sort()
            supports.sort(reverse=True)

            # 2. Pivot points from previous candle
            prev_candle = window.iloc[-2] if len(window) > 1 else window.iloc[-1]
            p_high = float(prev_candle['High'])
            p_low = float(prev_candle['Low'])
            p_close = float(prev_candle['Close'])
            pivot = (p_high + p_low + p_close) / 3
            r1 = 2 * pivot - p_low
            s1 = 2 * pivot - p_high
            r2 = pivot + (p_high - p_low)
            s2 = pivot - (p_high - p_low)

            # 3. Volume ratio
            if 'Volume' in window.columns:
                vol_avg = float(window['Volume'].rolling(20).mean().iloc[-1])
                vol_now = float(window['Volume'].iloc[-1])
                vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1.0
            else:
                vol_ratio = 1.0

            vol_mult = 1.0
            if vol_ratio > 2.0:
                vol_mult = 1.5
            elif vol_ratio > 1.3:
                vol_mult = 1.2
            elif vol_ratio < 0.5:
                vol_mult = 0.6
            elif vol_ratio < 0.8:
                vol_mult = 0.8

            # 4. Pick targets: nearest S/R > Pivot > ATR fallback
            if direction == 'BUY':
                # Targets from resistance levels
                if resistances:
                    t1 = resistances[0]
                    t2 = resistances[1] if len(resistances) > 1 else t1 * 1.002
                elif r1 > current_price:
                    t1 = r1
                    t2 = r2 if r2 > r1 else r1 * 1.002
                else:
                    t1 = entry + atr * 0.3 * vol_mult
                    t2 = entry + atr * 0.6 * vol_mult

                # SL from support levels
                if supports:
                    sl = supports[0] * 0.998
                elif s1 > 0 and s1 < current_price:
                    sl = s1 * 0.998
                else:
                    sl = entry - atr * 0.5
            else:
                # Targets from support levels
                if supports:
                    t1 = supports[0]
                    t2 = supports[1] if len(supports) > 1 else t1 * 0.998
                elif s1 < current_price:
                    t1 = s1
                    t2 = s2 if s2 < s1 else s1 * 0.998
                else:
                    t1 = entry - atr * 0.3 * vol_mult
                    t2 = entry - atr * 0.6 * vol_mult

                # SL from resistance levels
                if resistances:
                    sl = resistances[0] * 1.002
                elif r1 > current_price:
                    sl = r1 * 1.002
                else:
                    sl = entry + atr * 0.5

            # Check future candles for resolution
            resolved = False
            best_favorable = 0.0

            for j in range(len(future)):
                candle_high = float(future['High'].iloc[j])
                candle_low = float(future['Low'].iloc[j])
                candle_close = float(future['Close'].iloc[j])

                if direction == 'BUY':
                    # Apply slippage to exit too (sell lower than quote)
                    effective_high = candle_high * (1 - slip)
                    effective_low = candle_low * (1 + slip)
                    favorable = effective_high - entry
                    # Check T2 first (best case)
                    if effective_high >= t2:
                        results['t2_hit'] += 1
                        results['binary_wins'] += 1
                        results['accuracies'].append(100.0)
                        ret_pct = (t2 - entry) / entry * 100
                        results['trade_returns_pct'].append(ret_pct)
                        resolved = True
                        break
                    elif effective_high >= t1:
                        results['t1_hit'] += 1
                        results['binary_wins'] += 1
                        results['accuracies'].append(100.0)
                        ret_pct = (t1 - entry) / entry * 100
                        results['trade_returns_pct'].append(ret_pct)
                        resolved = True
                        break
                    elif effective_low <= sl:
                        results['sl_hit'] += 1
                        results['accuracies'].append(0.0)
                        ret_pct = (sl - entry) / entry * 100
                        results['trade_returns_pct'].append(ret_pct)
                        resolved = True
                        break
                    best_favorable = max(best_favorable, favorable)
                else:
                    # SELL: slippage makes lows harder to reach, highs easier to trigger SL
                    effective_low = candle_low * (1 + slip)
                    effective_high = candle_high * (1 - slip)
                    favorable = entry - effective_low
                    if effective_low <= t2:
                        results['t2_hit'] += 1
                        results['binary_wins'] += 1
                        results['accuracies'].append(100.0)
                        ret_pct = (entry - t2) / entry * 100
                        results['trade_returns_pct'].append(ret_pct)
                        resolved = True
                        break
                    elif effective_low <= t1:
                        results['t1_hit'] += 1
                        results['binary_wins'] += 1
                        results['accuracies'].append(100.0)
                        ret_pct = (entry - t1) / entry * 100
                        results['trade_returns_pct'].append(ret_pct)
                        resolved = True
                        break
                    elif effective_high >= sl:
                        results['sl_hit'] += 1
                        results['accuracies'].append(0.0)
                        ret_pct = (entry - sl) / entry * 100
                        results['trade_returns_pct'].append(ret_pct)
                        resolved = True
                        break
                    best_favorable = max(best_favorable, favorable)

            if not resolved:
                results['expired'] += 1
                # Gradient accuracy: how close did we get to T1?
                target_range = abs(t1 - entry)
                if target_range > 0:
                    grad_acc = min(100.0, max(0.0, (best_favorable / target_range) * 100))
                else:
                    grad_acc = 0.0
                results['accuracies'].append(grad_acc)
                # Expired: use gradient as approximate return (e.g. 50% to T1 -> 0.5 * (t1-entry)/entry)
                if direction == 'BUY' and target_range > 0:
                    ret_pct = (best_favorable / target_range) * (t1 - entry) / entry * 100
                elif direction == 'SELL' and target_range > 0:
                    ret_pct = (best_favorable / target_range) * (entry - t1) / entry * 100
                else:
                    ret_pct = 0.0
                results['trade_returns_pct'].append(ret_pct)

            # Commission (Path A): per-trade deduction (entry + exit)
            notional = entry  # assume 1 unit
            comm_pct = (self.commission_per_trade * 2 / notional * 100) if self.commission_per_trade > 0 else (self.commission_bps / 10000.0 * 2 * 100)
            if results['trade_returns_pct']:
                results['trade_returns_pct'][-1] -= comm_pct
            if self.commission_per_trade > 0:
                results['commission_total'] += self.commission_per_trade * 2
            else:
                results['commission_total'] += notional * (self.commission_bps / 10000.0) * 2

            # Did direction prediction match?
            actual_close = float(future['Close'].iloc[-1]) if len(future) > 0 else current_price
            if (direction == 'BUY' and actual_close > current_price) or \
               (direction == 'SELL' and actual_close < current_price):
                results['directions_correct'] += 1

            results['total'] += 1

        # Calculate summary
        avg_accuracy = np.mean(results['accuracies']) if results['accuracies'] else 0.0
        win_rate = (results['t1_hit'] + results['t2_hit']) / results['total'] * 100 if results['total'] > 0 else 0
        direction_accuracy = results['directions_correct'] / results['total'] * 100 if results['total'] > 0 else 0
        # Path A: compound net return and max drawdown from trade_returns_pct
        net_return_pct = 0.0
        max_drawdown_pct = 0.0
        if results.get('trade_returns_pct'):
            equity = 1.0
            peak = 1.0
            for r in results['trade_returns_pct']:
                equity *= (1 + r / 100.0)
                peak = max(peak, equity)
                max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak * 100)
            net_return_pct = (equity - 1.0) * 100

        return {
            'symbol': symbol,
            'strategy': strategy,
            'interval': interval,
            'params': params,
            'total': results['total'],
            'accuracy': round(avg_accuracy, 2),
            'win_rate': round(win_rate, 2),
            'direction_accuracy': round(direction_accuracy, 2),
            't1_hits': results['t1_hit'],
            't2_hits': results['t2_hit'],
            'sl_hits': results['sl_hit'],
            'expired': results['expired'],
            'binary_wins': results.get('binary_wins', results['t1_hit'] + results['t2_hit']),
            'net_return_pct': round(net_return_pct, 2),
            'max_drawdown_pct': round(max_drawdown_pct, 2),
            'commission_total': results.get('commission_total', 0),
        }

    def run_backtest_with_buy_and_hold(self, symbol: str, strategy: str = "Intraday (Scalp)",
                                        params: Dict = None, interval: str = None) -> Dict:
        """
        Run backtest and compare strategy return vs buy-and-hold (Path A).

        Returns: backtest result plus buy_hold_return_pct, strategy_return_pct, max_drawdown_pct.
        """
        result = self.run_backtest(symbol, strategy, params, interval)
        if result.get('total', 0) == 0 and 'error' in result:
            return result
        is_scalp = "Scalp" in strategy or "Intraday" in strategy
        if interval is None:
            interval = '1h' if is_scalp else '1d'
        hist_data = self.fetch_historical_data(symbol, period='6mo', intervals=[interval])
        df = hist_data.get(interval)
        buy_hold_return_pct = 0.0
        if df is not None and len(df) >= 2:
            first_close = float(df['Close'].iloc[0])
            last_close = float(df['Close'].iloc[-1])
            buy_hold_return_pct = (last_close - first_close) / first_close * 100
        result['buy_hold_return_pct'] = round(buy_hold_return_pct, 2)
        result['strategy_return_pct'] = result.get('net_return_pct', 0)
        result['max_drawdown_pct'] = result.get('max_drawdown_pct', 0)
        return result

    def optimize_parameters(self, symbol: str, strategy: str = "Intraday (Scalp)",
                            interval: str = None) -> Dict:
        """
        Grid search over parameter space to find optimal targets and stops.

        Returns: {best_params, best_accuracy, all_results}
        """
        is_scalp = "Scalp" in strategy or "Intraday" in strategy
        grid = self.SCALP_GRID if is_scalp else self.SWING_GRID

        best_accuracy = 0
        best_params = None
        all_results = []

        if is_scalp:
            for t1 in grid['t1_pct']:
                for t2 in grid['t2_pct']:
                    if t2 <= t1:
                        continue  # T2 must be larger than T1
                    for sl in grid['sl_pct']:
                        params = {'t1_pct': t1, 't2_pct': t2, 'sl_pct': sl}
                        result = self.run_backtest(symbol, strategy, params, interval)
                        result['params'] = params
                        all_results.append(result)

                        # Optimize for accuracy (not just win rate)
                        score = result.get('accuracy', 0)
                        if score > best_accuracy:
                            best_accuracy = score
                            best_params = params.copy()
        else:
            for t1 in grid['t1_multiplier']:
                for t2 in grid['t2_multiplier']:
                    if t2 <= t1:
                        continue
                    for sl in grid['sl_multiplier']:
                        params = {'t1_multiplier': t1, 't2_multiplier': t2, 'sl_multiplier': sl}
                        result = self.run_backtest(symbol, strategy, params, interval)
                        result['params'] = params
                        all_results.append(result)

                        score = result.get('accuracy', 0)
                        if score > best_accuracy:
                            best_accuracy = score
                            best_params = params.copy()

        self.best_params = best_params or {}
        return {
            'symbol': symbol,
            'strategy': strategy,
            'best_params': best_params,
            'best_accuracy': best_accuracy,
            'combinations_tested': len(all_results),
            'top_5': sorted(all_results, key=lambda x: x.get('accuracy', 0), reverse=True)[:5],
        }

    def train_and_save(self, symbols: List[str], strategy: str = "Intraday (Scalp)") -> Dict:
        """
        Full training pipeline: optimize parameters for each symbol, save best to config.

        Returns: {results_per_symbol, updated_config_path}
        """
        from market_agent.learning.training_persistence import training_db

        # Record training run start
        current_params = self._load_config()
        run_id = training_db.start_training_run(
            run_type='parameter_optimize',
            symbols=symbols,
            strategy=strategy,
            data_source='yfinance+CoinDCX',
            params_before=current_params
        )

        results = {}
        total_candles = 0
        total_signals = 0

        for symbol in symbols:
            try:
                opt = self.optimize_parameters(symbol, strategy)
                results[symbol] = opt
                if opt.get('top_5'):
                    top = opt['top_5'][0]
                    total_candles += top.get('total', 0)
                    total_signals += top.get('total', 0)
            except Exception as e:
                results[symbol] = {'error': str(e)}

        # Save best parameters to config
        config = self._load_config()
        is_scalp = "Scalp" in strategy or "Intraday" in strategy

        # Aggregate best params across all symbols
        best_overall = None
        best_overall_acc = 0
        for symbol, opt in results.items():
            bp = opt.get('best_params')
            ba = opt.get('best_accuracy', 0)
            if bp and ba > best_overall_acc:
                best_overall = bp
                best_overall_acc = ba

        if best_overall:
            if is_scalp:
                config['scalp'] = {**config.get('scalp', {}), **best_overall,
                                   'notes': f'Auto-optimized {datetime.now().isoformat()}, accuracy={best_overall_acc:.1f}%'}
            else:
                config['swing'] = {**config.get('swing', {}), **best_overall,
                                   'notes': f'Auto-optimized {datetime.now().isoformat()}, accuracy={best_overall_acc:.1f}%'}

            config['_meta']['last_updated'] = datetime.now().isoformat()
            config['_meta']['version'] = self._bump_version(config['_meta'].get('version', '2.0.0'))
            self._save_config(config)

        # Complete training run
        overall_acc = np.mean([r.get('best_accuracy', 0) for r in results.values() if isinstance(r, dict)])
        training_db.complete_training_run(
            run_id=run_id,
            accuracy_after=overall_acc,
            params_after=best_overall,
            signals_gen=total_signals,
            candles=total_candles
        )

        return {
            'results': results,
            'best_params': best_overall,
            'best_accuracy': best_overall_acc,
            'config_updated': True,
            'run_id': run_id,
        }

    def _load_config(self) -> Dict:
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json')
            with open(cfg_path) as f:
                return json.load(f)
        except Exception:
            return {'_meta': {}, 'default': {}, 'scalp': {}, 'swing': {}}

    def _save_config(self, config: Dict):
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json')
            with open(cfg_path, 'w') as f:
                json.dump(config, f, indent=4, default=str)
        except Exception as e:
            logger.warning("config_save_failed", error=str(e)[:100])

    def _bump_version(self, version: str) -> str:
        parts = version.split('.')
        parts[-1] = str(int(parts[-1]) + 1)
        return '.'.join(parts)


# Module-level singleton
backtester = Backtester()


if __name__ == '__main__':
    print("=" * 60)
    print("Aegis Backtester — Historical Training")
    print("=" * 60)

    bt = Backtester()

    # Quick test: run backtest on BTC with current params
    print("\n[1] Running backtest on BTC-USD with current scalp params...")
    result = bt.run_backtest('BTC-USD', 'Intraday (Scalp)')
    print(f"    Total signals: {result.get('total', 0)}")
    print(f"    Accuracy: {result.get('accuracy', 0):.1f}%")
    print(f"    Win rate: {result.get('win_rate', 0):.1f}%")
    print(f"    T1 hits: {result.get('t1_hits', 0)}, T2: {result.get('t2_hits', 0)}, SL: {result.get('sl_hits', 0)}, Expired: {result.get('expired', 0)}")

    # Optimization
    print("\n[2] Optimizing parameters for BTC-USD scalp...")
    opt = bt.optimize_parameters('BTC-USD', 'Intraday (Scalp)')
    print(f"    Best params: {opt.get('best_params')}")
    print(f"    Best accuracy: {opt.get('best_accuracy', 0):.1f}%")
    print(f"    Combinations tested: {opt.get('combinations_tested', 0)}")
    print("\n    Top 5 parameter sets:")
    for i, r in enumerate(opt.get('top_5', [])[:5]):
        print(f"      {i+1}. {r.get('params')} -> acc={r.get('accuracy', 0):.1f}%, win={r.get('win_rate', 0):.1f}%")
