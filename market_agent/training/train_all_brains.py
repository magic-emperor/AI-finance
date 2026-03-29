"""
Historical Brain Training — Walk-Forward Training Pipeline

HOW IT WORKS (exactly as user described):
=========================================
Given 1000 candles of data:

  Candle #1 ─ #2 ─ #3 ─ ... ─ #500 ─ #501 ─ ... ─ #506
                                ↑                      ↑
                          Brain sees            Brain CANNOT see
                          ONLY this             (the "future")

At candle #500:
  - Each brain analyzes candles 1-500 (RSI, MACD, SMA, Bollinger, Volume)
  - Each brain predicts: BUY / SELL / HOLD
  - Backtester checks: Did candles 501-506 hit the target?
  - Score: correct or wrong per brain

Then shifts to candle #501, repeats. Brain NEVER sees future data.

WHAT EACH BRAIN DOES:
=====================
Brain 1 — SMA Crossover:     Short MA(5) vs Long MA(20)
Brain 2 — RSI Momentum:      RSI(14) oversold < 30 = BUY, overbought > 70 = SELL
Brain 3 — MACD Signal:       MACD line crossing signal line
Brain 4 — Bollinger Bounce:  Price near lower band = BUY, upper band = SELL
Brain 5 — Volume Breakout:   Volume spike + direction confirmation
Brain 6 — Combined (Boss):   Majority vote of all 5 brains

DATA:
=====
- yfinance 1h candles: 730 days (~2 years) for all 17 symbols
- Walk-forward on every candle from bar 50 to end

REPORT:
=======
After training, produces:
  - Per-brain accuracy table (which brain is best?)
  - Per-symbol breakdown (which stock is most predictable?)
  - Total correct/wrong/accuracy for each brain
  - Confidence recommendations based on actual hit rates

Usage: python -m market_agent.training.train_all_brains
"""

import os
import sys
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any
import structlog

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════
# BRAIN DEFINITIONS — Each brain has a different analysis method
# ═══════════════════════════════════════════════════════════

class BrainAnalyzer:
    """Individual brain analysis methods. Each returns (direction, confidence)."""

    @staticmethod
    def sma_crossover(window: pd.DataFrame) -> Tuple[str, float]:
        """Brain 1: SMA 5/20 crossover"""
        close = window['Close']
        if len(close) < 20:
            return 'HOLD', 0.0
        sma5 = float(close.rolling(5).mean().iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma5_prev = float(close.rolling(5).mean().iloc[-2])
        sma20_prev = float(close.rolling(20).mean().iloc[-2])

        if sma5 > sma20 and sma5_prev <= sma20_prev:
            return 'BUY', 0.75  # Fresh crossover = higher confidence
        elif sma5 > sma20:
            return 'BUY', 0.55
        elif sma5 < sma20 and sma5_prev >= sma20_prev:
            return 'SELL', 0.75
        elif sma5 < sma20:
            return 'SELL', 0.55
        return 'HOLD', 0.3

    @staticmethod
    def rsi_momentum(window: pd.DataFrame) -> Tuple[str, float]:
        """Brain 2: RSI(14) oversold/overbought"""
        close = window['Close']
        if len(close) < 15:
            return 'HOLD', 0.0
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1])

        if np.isnan(rsi_val):
            return 'HOLD', 0.0
        if rsi_val < 25:
            return 'BUY', 0.80
        elif rsi_val < 35:
            return 'BUY', 0.60
        elif rsi_val > 75:
            return 'SELL', 0.80
        elif rsi_val > 65:
            return 'SELL', 0.60
        return 'HOLD', 0.3

    @staticmethod
    def macd_signal(window: pd.DataFrame) -> Tuple[str, float]:
        """Brain 3: MACD line crossing signal line"""
        close = window['Close']
        if len(close) < 35:
            return 'HOLD', 0.0
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        hist = macd - signal

        hist_now = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2])

        if hist_now > 0 and hist_prev <= 0:
            return 'BUY', 0.70
        elif hist_now > 0:
            return 'BUY', 0.55
        elif hist_now < 0 and hist_prev >= 0:
            return 'SELL', 0.70
        elif hist_now < 0:
            return 'SELL', 0.55
        return 'HOLD', 0.3

    @staticmethod
    def bollinger_bounce(window: pd.DataFrame) -> Tuple[str, float]:
        """Brain 4: Bollinger Bands — price near bands"""
        close = window['Close']
        if len(close) < 20:
            return 'HOLD', 0.0
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20

        price = float(close.iloc[-1])
        upper_val = float(upper.iloc[-1])
        lower_val = float(lower.iloc[-1])
        mid = float(sma20.iloc[-1])

        if np.isnan(upper_val) or np.isnan(lower_val):
            return 'HOLD', 0.0

        band_width = upper_val - lower_val
        if band_width == 0:
            return 'HOLD', 0.0

        # How close to lower band (0=at lower, 1=at upper)
        position = (price - lower_val) / band_width

        if position < 0.1:
            return 'BUY', 0.75
        elif position < 0.25:
            return 'BUY', 0.60
        elif position > 0.9:
            return 'SELL', 0.75
        elif position > 0.75:
            return 'SELL', 0.60
        return 'HOLD', 0.35

    @staticmethod
    def volume_breakout(window: pd.DataFrame) -> Tuple[str, float]:
        """Brain 5: Volume spike + direction"""
        close = window['Close']
        if 'Volume' not in window.columns or len(close) < 20:
            return 'HOLD', 0.0

        vol = window['Volume']
        vol_avg = float(vol.rolling(20).mean().iloc[-1])
        vol_now = float(vol.iloc[-1])

        if vol_avg == 0 or np.isnan(vol_avg):
            return 'HOLD', 0.0

        vol_ratio = vol_now / vol_avg

        if vol_ratio < 1.5:
            return 'HOLD', 0.3  # No significant volume

        # Volume spike detected — check direction
        price_change = float(close.iloc[-1]) - float(close.iloc[-2])
        if price_change > 0:
            conf = min(0.85, 0.5 + vol_ratio * 0.1)
            return 'BUY', conf
        elif price_change < 0:
            conf = min(0.85, 0.5 + vol_ratio * 0.1)
            return 'SELL', conf
        return 'HOLD', 0.3

    @staticmethod
    def boss_brain(votes: Dict[str, str]) -> Tuple[str, float]:
        """Brain 6: Majority vote of all brains"""
        buy_count = sum(1 for v in votes.values() if v == 'BUY')
        sell_count = sum(1 for v in votes.values() if v == 'SELL')
        total = len(votes)

        if buy_count > sell_count and buy_count >= 3:
            return 'BUY', min(0.90, 0.5 + buy_count / total * 0.5)
        elif sell_count > buy_count and sell_count >= 3:
            return 'SELL', min(0.90, 0.5 + sell_count / total * 0.5)
        return 'HOLD', 0.3


# Brain registry
BRAINS = {
    'SMA-Crossover': BrainAnalyzer.sma_crossover,
    'RSI-Momentum': BrainAnalyzer.rsi_momentum,
    'MACD-Signal': BrainAnalyzer.macd_signal,
    'Bollinger-Bounce': BrainAnalyzer.bollinger_bounce,
    'Volume-Breakout': BrainAnalyzer.volume_breakout,
}


# ═══════════════════════════════════════════════════════════
# SYMBOLS
# ═══════════════════════════════════════════════════════════

TRAINING_SYMBOLS = [
    # India / NSE
    "ITC.NS", "HDFCBANK.NS", "RELIANCE.NS", "TATASTEEL.NS",
    "LT.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    # US tech / AI
    "NVDA", "GOOGL", "AAPL", "AMD",
    # Crypto / FX / Commodities
    "BTC-USD", "GC=F", "GBPJPY=X", "USDJPY=X", "CL=F",
]


# ═══════════════════════════════════════════════════════════
# WALK-FORWARD TRAINING ENGINE
# ═══════════════════════════════════════════════════════════

def run_walk_forward(symbol: str, df: pd.DataFrame, lookahead: int = 6) -> Dict[str, Any]:
    """
    Walk-forward test for ALL brains on one symbol.

    For each candle from bar 50 onwards:
      1. Each brain analyzes candles 0..i (never sees future)
      2. Each brain predicts direction
      3. Check next `lookahead` candles for target hit
      4. Score each brain

    Returns per-brain results.
    """
    sr_lookback = 50
    results = {}

    for brain_name in list(BRAINS.keys()) + ['Boss-Brain']:
        results[brain_name] = {
            'correct': 0, 'wrong': 0, 'hold': 0, 'total': 0,
            'buy_correct': 0, 'buy_wrong': 0,
            'sell_correct': 0, 'sell_wrong': 0,
        }

    candle_count = 0

    for i in range(sr_lookback, len(df) - lookahead):
        window = df.iloc[:i+1]
        future = df.iloc[i+1:i+1+lookahead]
        entry = float(window['Close'].iloc[-1])

        if entry <= 0:
            continue

        # Calculate ATR for target/SL
        high_low = window['High'] - window['Low']
        high_cp = (window['High'] - window['Close'].shift()).abs()
        low_cp = (window['Low'] - window['Close'].shift()).abs()
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        if np.isnan(atr) or atr <= 0:
            continue

        # FIXED R:R (Order 8 — Section 1 of Implementation Guide)
        # OLD: T1=ATR*0.3, SL=ATR*0.5  →  R:R = 0.6:1  (LOSING MONEY at live 62%)
        # NEW: T1=ATR*0.75, T2=ATR*1.50, SL=ATR*0.50  →  R:R T1=1.5:1, T2=3.0:1
        # EV at 62% live accuracy: (0.62*0.75)-(0.38*0.50) = +0.275 (profitable)
        t1_offset = atr * 0.75    # Intraday T1 — 1.5:1 R:R vs SL
        t2_offset = atr * 1.50    # Extended runner target — 3.0:1 R:R vs SL


        # FIXED R:R (Gap 3 Option A — tighter target to match real volatility)
        # Prevents unrealizable targets causing 18% accuracy
        # t1_offset = atr * 0.45    # Intraday T1 — reachable 0.9:1 R:R
        # t2_offset = atr * 1.00    # Extended runner target

        sl_offset = atr * 0.50    # Stop loss (unchanged)

        # ── Each brain votes ──
        votes = {}
        for brain_name, brain_fn in BRAINS.items():
            direction, confidence = brain_fn(window)
            votes[brain_name] = direction

            if direction == 'HOLD':
                results[brain_name]['hold'] += 1
                continue

            results[brain_name]['total'] += 1

            # Check future: did this direction work?
            hit = _check_future(direction, entry, future, t1_offset, sl_offset)

            if hit == 'TARGET':
                results[brain_name]['correct'] += 1
                if direction == 'BUY':
                    results[brain_name]['buy_correct'] += 1
                else:
                    results[brain_name]['sell_correct'] += 1
            elif hit == 'SL':
                results[brain_name]['wrong'] += 1
                if direction == 'BUY':
                    results[brain_name]['buy_wrong'] += 1
                else:
                    results[brain_name]['sell_wrong'] += 1
            else:
                # Expired — count as neutral (not correct, not wrong)
                results[brain_name]['hold'] += 1

        # Boss brain — majority vote
        boss_dir, boss_conf = BrainAnalyzer.boss_brain(votes)
        if boss_dir != 'HOLD':
            results['Boss-Brain']['total'] += 1
            hit = _check_future(boss_dir, entry, future, t1_offset, sl_offset)
            if hit == 'TARGET':
                results['Boss-Brain']['correct'] += 1
                if boss_dir == 'BUY':
                    results['Boss-Brain']['buy_correct'] += 1
                else:
                    results['Boss-Brain']['sell_correct'] += 1
            elif hit == 'SL':
                results['Boss-Brain']['wrong'] += 1
                if boss_dir == 'BUY':
                    results['Boss-Brain']['buy_wrong'] += 1
                else:
                    results['Boss-Brain']['sell_wrong'] += 1
            else:
                results['Boss-Brain']['hold'] += 1
        else:
            results['Boss-Brain']['hold'] += 1

        candle_count += 1

    # Compute accuracy per brain
    for brain_name, r in results.items():
        total = r['correct'] + r['wrong']
        r['accuracy'] = round(r['correct'] / total * 100, 1) if total > 0 else 0.0
        r['win_rate'] = r['accuracy']  # Same thing for walk-forward
        r['total_decisions'] = total

    return {'brains': results, 'candles_processed': candle_count, 'symbol': symbol}


def _check_future(direction: str, entry: float, future: pd.DataFrame,
                  t1_offset: float, sl_offset: float) -> str:
    """Check if target or SL was hit in future candles."""
    for j in range(len(future)):
        high = float(future['High'].iloc[j])
        low = float(future['Low'].iloc[j])

        if direction == 'BUY':
            target = entry + t1_offset
            stop = entry - sl_offset
            if high >= target:
                return 'TARGET'
            if low <= stop:
                return 'SL'
        else:
            target = entry - t1_offset
            stop = entry + sl_offset
            if low <= target:
                return 'TARGET'
            if high >= stop:
                return 'SL'

    return 'EXPIRED'


# ═══════════════════════════════════════════════════════════
# REPORT GENERATOR
# ═══════════════════════════════════════════════════════════

def print_report(all_results: Dict[str, Dict], elapsed_sec: float):
    """Print rich terminal report after training."""

    print("\n" + "=" * 80)
    print("  HISTORICAL TRAINING REPORT")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Duration: {elapsed_sec / 60:.1f} minutes")
    print("=" * 80)

    # ── 1. PER-BRAIN ACCURACY (aggregated across all symbols) ──
    brain_totals = {}
    for symbol, result in all_results.items():
        for brain_name, stats in result['brains'].items():
            if brain_name not in brain_totals:
                brain_totals[brain_name] = {'correct': 0, 'wrong': 0, 'hold': 0,
                                            'buy_correct': 0, 'buy_wrong': 0,
                                            'sell_correct': 0, 'sell_wrong': 0}
            for key in brain_totals[brain_name]:
                brain_totals[brain_name][key] += stats.get(key, 0)

    print("\n" + "-" * 80)
    print("  BRAIN ACCURACY REPORT (All Symbols Combined)")
    print("-" * 80)
    print(f"  {'Brain':<20} {'Correct':>8} {'Wrong':>8} {'Accuracy':>10} {'BUY W/L':>10} {'SELL W/L':>10}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")

    brain_sorted = sorted(brain_totals.items(),
                          key=lambda x: x[1]['correct'] / max(1, x[1]['correct'] + x[1]['wrong']),
                          reverse=True)

    for brain_name, t in brain_sorted:
        total = t['correct'] + t['wrong']
        acc = t['correct'] / total * 100 if total > 0 else 0
        buy_wl = f"{t['buy_correct']}/{t['buy_wrong']}"
        sell_wl = f"{t['sell_correct']}/{t['sell_wrong']}"
        marker = " ***" if acc >= 60 else " *" if acc >= 50 else ""
        print(f"  {brain_name:<20} {t['correct']:>8} {t['wrong']:>8} {acc:>9.1f}% {buy_wl:>10} {sell_wl:>10}{marker}")

    print(f"\n  *** = Good (>60%)   * = Decent (>50%)")

    # ── 2. PER-SYMBOL BREAKDOWN ──
    print("\n" + "-" * 80)
    print("  PER-SYMBOL BREAKDOWN (Boss Brain)")
    print("-" * 80)
    print(f"  {'Symbol':<18} {'Candles':>8} {'Correct':>8} {'Wrong':>8} {'Accuracy':>10} {'Hold':>8}")
    print(f"  {'-'*18} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*8}")

    for symbol in sorted(all_results.keys()):
        result = all_results[symbol]
        boss = result['brains'].get('Boss-Brain', {})
        total = boss.get('correct', 0) + boss.get('wrong', 0)
        acc = boss.get('correct', 0) / total * 100 if total > 0 else 0
        print(f"  {symbol:<18} {result.get('candles_processed', 0):>8} "
              f"{boss.get('correct', 0):>8} {boss.get('wrong', 0):>8} "
              f"{acc:>9.1f}% {boss.get('hold', 0):>8}")

    # ── 3. CONFIDENCE RECOMMENDATIONS ──
    print("\n" + "-" * 80)
    print("  CONFIDENCE RECOMMENDATIONS")
    print("-" * 80)
    print("  Based on actual hit rates, recommended confidence levels:")
    print()

    for brain_name, t in brain_sorted:
        total = t['correct'] + t['wrong']
        if total < 10:
            continue
        actual_rate = t['correct'] / total
        recommended_conf = round(actual_rate, 2)
        status = "STRONG" if actual_rate >= 0.6 else "DECENT" if actual_rate >= 0.5 else "WEAK"
        print(f"  {brain_name:<20} -> Confidence: {recommended_conf:.0%}  [{status}]")

    print("\n" + "=" * 80)
    print("  TRAINING COMPLETE")
    print("=" * 80)


def save_html_report(all_results: Dict[str, Dict], elapsed_sec: float, filepath: str):
    """Save detailed HTML report."""
    brain_totals = {}
    for symbol, result in all_results.items():
        for brain_name, stats in result['brains'].items():
            if brain_name not in brain_totals:
                brain_totals[brain_name] = {'correct': 0, 'wrong': 0, 'hold': 0,
                                            'buy_correct': 0, 'buy_wrong': 0,
                                            'sell_correct': 0, 'sell_wrong': 0}
            for key in brain_totals[brain_name]:
                brain_totals[brain_name][key] += stats.get(key, 0)

    brain_sorted = sorted(brain_totals.items(),
                          key=lambda x: x[1]['correct'] / max(1, x[1]['correct'] + x[1]['wrong']),
                          reverse=True)

    html = f"""<!DOCTYPE html>
<html><head><title>Brain Training Report - {datetime.now().strftime('%Y-%m-%d')}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 40px; }}
  h1 {{ color: #818cf8; }} h2 {{ color: #6ee7b7; margin-top: 40px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
  th {{ background: #1e293b; color: #818cf8; padding: 12px; text-align: left; }}
  td {{ padding: 10px; border-bottom: 1px solid #334155; }}
  tr:hover {{ background: #1e293b; }}
  .good {{ color: #6ee7b7; font-weight: bold; }}
  .bad {{ color: #f87171; }}
  .neutral {{ color: #94a3b8; }}
  .banner {{ background: linear-gradient(135deg, #1e293b, #312e81); padding: 30px; border-radius: 12px; margin-bottom: 30px; }}
  .stat {{ display: inline-block; margin: 0 30px; text-align: center; }}
  .stat-val {{ font-size: 32px; font-weight: bold; color: #818cf8; }}
  .stat-label {{ font-size: 14px; color: #94a3b8; }}
</style></head><body>
<div class="banner">
  <h1>Brain Training Report</h1>
  <p>Walk-Forward Historical Training | {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  <div class="stat"><div class="stat-val">{len(all_results)}</div><div class="stat-label">Symbols</div></div>
  <div class="stat"><div class="stat-val">{len(BRAINS)+1}</div><div class="stat-label">Brains</div></div>
  <div class="stat"><div class="stat-val">{elapsed_sec/60:.1f}m</div><div class="stat-label">Duration</div></div>
  <div class="stat"><div class="stat-val">{sum(r.get('candles_processed',0) for r in all_results.values()):,}</div><div class="stat-label">Candles</div></div>
</div>

<h2>Brain Accuracy (All Symbols)</h2>
<table>
<tr><th>Brain</th><th>Correct</th><th>Wrong</th><th>Accuracy</th><th>BUY W/L</th><th>SELL W/L</th><th>Rating</th></tr>"""

    for brain_name, t in brain_sorted:
        total = t['correct'] + t['wrong']
        acc = t['correct'] / total * 100 if total > 0 else 0
        css = 'good' if acc >= 60 else 'neutral' if acc >= 50 else 'bad'
        rating = 'STRONG' if acc >= 60 else 'DECENT' if acc >= 50 else 'WEAK'
        html += f"""<tr>
  <td>{brain_name}</td><td>{t['correct']}</td><td>{t['wrong']}</td>
  <td class="{css}">{acc:.1f}%</td>
  <td>{t['buy_correct']}/{t['buy_wrong']}</td><td>{t['sell_correct']}/{t['sell_wrong']}</td>
  <td class="{css}">{rating}</td></tr>"""

    html += """</table><h2>Per-Symbol Breakdown (Boss Brain)</h2>
<table><tr><th>Symbol</th><th>Candles</th><th>Correct</th><th>Wrong</th><th>Accuracy</th><th>Hold</th></tr>"""

    for symbol in sorted(all_results.keys()):
        result = all_results[symbol]
        boss = result['brains'].get('Boss-Brain', {})
        total = boss.get('correct', 0) + boss.get('wrong', 0)
        acc = boss.get('correct', 0) / total * 100 if total > 0 else 0
        css = 'good' if acc >= 60 else 'neutral' if acc >= 50 else 'bad'
        html += f"""<tr><td>{symbol}</td><td>{result.get('candles_processed',0)}</td>
  <td>{boss.get('correct',0)}</td><td>{boss.get('wrong',0)}</td>
  <td class="{css}">{acc:.1f}%</td><td>{boss.get('hold',0)}</td></tr>"""

    html += """</table>

<h2>Per-Symbol × Per-Brain Detail</h2>
<table><tr><th>Symbol</th>"""

    all_brains = list(BRAINS.keys()) + ['Boss-Brain']
    for b in all_brains:
        html += f"<th>{b}</th>"
    html += "</tr>"

    for symbol in sorted(all_results.keys()):
        html += f"<tr><td><b>{symbol}</b></td>"
        for brain_name in all_brains:
            stats = all_results[symbol]['brains'].get(brain_name, {})
            total = stats.get('correct', 0) + stats.get('wrong', 0)
            acc = stats.get('correct', 0) / total * 100 if total > 0 else 0
            css = 'good' if acc >= 60 else 'neutral' if acc >= 50 else 'bad'
            html += f'<td class="{css}">{acc:.0f}% ({stats.get("correct",0)}/{total})</td>'
        html += "</tr>"

    html += """</table>

<h2>Confidence Recommendations</h2>
<p>Based on actual walk-forward hit rates, here is the recommended confidence multiplier for each brain:</p>
<table><tr><th>Brain</th><th>Actual Hit Rate</th><th>Recommended Confidence</th><th>Status</th></tr>"""

    for brain_name, t in brain_sorted:
        total = t['correct'] + t['wrong']
        if total < 10:
            continue
        actual = t['correct'] / total
        status = 'STRONG' if actual >= 0.6 else 'DECENT' if actual >= 0.5 else 'WEAK'
        css = 'good' if actual >= 0.6 else 'neutral' if actual >= 0.5 else 'bad'
        html += f'<tr><td>{brain_name}</td><td>{actual:.1%}</td><td class="{css}">{actual:.0%}</td><td class="{css}">{status}</td></tr>'

    html += "</table></body></html>"

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n  HTML report saved: {filepath}")


def save_confidence_to_config(all_results: Dict[str, Dict]):
    """Save learned confidence levels to strategy_params.json."""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json')
    try:
        with open(config_path) as f:
            config = json.load(f)
    except Exception:
        config = {"default": {}}

    # Aggregate per-brain accuracy
    brain_totals = {}
    for symbol, result in all_results.items():
        for brain_name, stats in result['brains'].items():
            if brain_name not in brain_totals:
                brain_totals[brain_name] = {'correct': 0, 'wrong': 0}
            brain_totals[brain_name]['correct'] += stats.get('correct', 0)
            brain_totals[brain_name]['wrong'] += stats.get('wrong', 0)

    # Save confidence levels
    config['brain_confidence'] = {}
    for brain_name, t in brain_totals.items():
        total = t['correct'] + t['wrong']
        if total > 0:
            config['brain_confidence'][brain_name] = round(t['correct'] / total, 3)

    # Save per-symbol accuracy
    config['symbol_accuracy'] = {}
    for symbol, result in all_results.items():
        boss = result['brains'].get('Boss-Brain', {})
        total = boss.get('correct', 0) + boss.get('wrong', 0)
        config['symbol_accuracy'][symbol] = round(boss.get('correct', 0) / total * 100, 1) if total > 0 else 0

    config['last_training'] = datetime.now().isoformat()
    config['training_candles'] = sum(r.get('candles_processed', 0) for r in all_results.values())

    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4, default=str)
    print(f"  Confidence levels saved to strategy_params.json")


# ═══════════════════════════════════════════════════════════
# MAIN — Run this: python -m market_agent.training.train_all_brains
# ═══════════════════════════════════════════════════════════

def main():
    import yfinance as yf

    print("=" * 80)
    print("  AEGIS BRAIN TRAINING — Walk-Forward Historical Analysis")
    print("=" * 80)
    print()
    print("  HOW EACH BRAIN ANALYZES:")
    print("  " + "-" * 50)
    print("  SMA-Crossover    : Short MA(5) vs Long MA(20)")
    print("  RSI-Momentum     : RSI(14) oversold/overbought")
    print("  MACD-Signal      : MACD line crossing signal line")
    print("  Bollinger-Bounce : Price position in Bollinger Bands")
    print("  Volume-Breakout  : Volume spike + direction")
    print("  Boss-Brain       : Majority vote of all 5 brains")
    print()
    print(f"  Symbols: {len(TRAINING_SYMBOLS)}")
    print(f"  Data: ~2 years of 1-hour candles (yfinance)")
    print(f"  Method: Walk-forward (brain never sees future)")
    print("=" * 80)

    all_results = {}
    start_time = time.time()

    for idx, symbol in enumerate(TRAINING_SYMBOLS, 1):
        print(f"\n[{idx}/{len(TRAINING_SYMBOLS)}] Training on {symbol}...")
        sys.stdout.flush()

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period='2y', interval='1h')

            if df is None or len(df) < 100:
                # Fallback to daily if hourly not available
                print(f"  Hourly data limited ({len(df) if df is not None else 0} bars), trying daily...")
                df = ticker.history(period='2y', interval='1d')

            if df is None or len(df) < 60:
                print(f"  SKIP: Not enough data for {symbol}")
                continue

            print(f"  Data: {len(df)} candles ({df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')})")

            result = run_walk_forward(symbol, df)
            all_results[symbol] = result

            # Print quick summary for this symbol
            boss = result['brains']['Boss-Brain']
            total = boss['correct'] + boss['wrong']
            acc = boss['correct'] / total * 100 if total > 0 else 0
            print(f"  Boss-Brain: {boss['correct']}/{total} correct ({acc:.1f}%), "
                  f"processed {result['candles_processed']} candles")

        except Exception as e:
            print(f"  ERROR: {str(e)[:100]}")
            continue

    elapsed = time.time() - start_time

    if not all_results:
        print("\nNo results! Check internet connection and yfinance installation.")
        return

    # Print terminal report
    print_report(all_results, elapsed)

    # Save HTML report
    report_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports')
    html_path = os.path.join(report_dir, f"training_report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.html")
    save_html_report(all_results, elapsed, html_path)

    # Save confidence levels
    save_confidence_to_config(all_results)

    # Log to training persistence
    try:
        from market_agent.learning.training_persistence import training_db
        for symbol, result in all_results.items():
            boss = result['brains']['Boss-Brain']
            total = boss['correct'] + boss['wrong']
            training_db.log_brain_training_run(
                model_id=f"Aegis-{symbol.replace('.', '-')}",
                mode="historical_walk_forward",
                bars_learned=result.get('candles_processed', 0),
                epochs=1,
                final_loss=0.0,
                notes=f"Correct: {boss['correct']}, Wrong: {boss['wrong']}, "
                      f"Accuracy: {boss.get('accuracy', 0):.1f}%"
            )
    except Exception:
        pass

    print(f"\n  Total time: {elapsed/60:.1f} minutes")
    print(f"  Report: {html_path}")
    print(f"  Run dashboard to see results on Brain Monitor")


if __name__ == '__main__':
    main()
