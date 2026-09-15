"""
scorecard.py — Print an OOS scorecard and the pre-registered Go/No-Go verdict.

The verdict shows EACH criterion's pass/fail so we see nuance (e.g. a profitable
but low-win-rate system), not just a single yes/no.
"""
from __future__ import annotations

import os
import csv
from typing import Dict, List, Tuple


def _fmt(x: float) -> str:
    if x == float("inf"):
        return "inf"
    return f"{x:.3f}"


def format_metrics(title: str, m: Dict) -> str:
    lines = [f"── {title} ──"]
    lines.append(f"  trades        : {m['n_trades']}")
    lines.append(f"  win_rate      : {m['win_rate']*100:.1f}%")
    lines.append(f"  expectancy_R  : {_fmt(m['expectancy_r'])}   (headline: avg R per trade)")
    lines.append(f"  profit_factor : {_fmt(m['profit_factor'])}")
    lines.append(f"  avg_win_R     : {_fmt(m['avg_win_r'])}")
    lines.append(f"  avg_loss_R    : {_fmt(m['avg_loss_r'])}")
    lines.append(f"  reward_risk   : {_fmt(m['reward_risk'])}")
    lines.append(f"  max_dd_R      : {_fmt(m['max_drawdown_r'])}")
    lines.append(f"  total_R       : {_fmt(m['total_r'])}")
    return "\n".join(lines)


def evaluate_gate(m: Dict, gate: Dict) -> Tuple[bool, List[Tuple[str, str, bool]]]:
    """HARD gate — the honest profit criteria (win rate is NOT here on purpose)."""
    checks = []

    def add(name, value_str, ok):
        checks.append((name, value_str, ok))

    add("n_trades >= %d" % gate["min_trades"],
        str(m["n_trades"]), m["n_trades"] >= gate["min_trades"])
    add("expectancy_R >= %.2f" % gate["min_expectancy_r"],
        _fmt(m["expectancy_r"]), m["expectancy_r"] >= gate["min_expectancy_r"])
    add("profit_factor >= %.2f" % gate["min_profit_factor"],
        _fmt(m["profit_factor"]), m["profit_factor"] >= gate["min_profit_factor"])
    add("max_dd_R >= %.1f" % gate["max_drawdown_r"],
        _fmt(m["max_drawdown_r"]), m["max_drawdown_r"] >= gate["max_drawdown_r"])

    passed = all(ok for _, _, ok in checks)
    return passed, checks


def format_info(m: Dict, info: Dict) -> str:
    """Informational metrics — reported to inform judgement, never auto-fail."""
    wr_ok = info["win_rate_low"] <= m["win_rate"] <= info["win_rate_high"]
    rr_ok = m["reward_risk"] >= info["min_reward_risk"]
    lines = ["── Informational (not gated) ──"]
    lines.append(f"  win_rate    : {m['win_rate']*100:.1f}%  "
                 f"(typical band {info['win_rate_low']*100:.0f}-{info['win_rate_high']*100:.0f}%: "
                 f"{'in' if wr_ok else 'out — fine if expectancy is positive'})")
    lines.append(f"  reward_risk : {_fmt(m['reward_risk'])}  (>= {info['min_reward_risk']}: "
                 f"{'yes' if rr_ok else 'no'})")
    return "\n".join(lines)


def format_gate(passed: bool, checks: List[Tuple[str, str, bool]]) -> str:
    lines = ["── GO / NO-GO GATE (out-of-sample) ──"]
    for name, value, ok in checks:
        mark = "PASS" if ok else "FAIL"
        lines.append(f"  [{mark}] {name:<28} got {value}")
    lines.append("")
    lines.append("  VERDICT: %s" % ("GO — earns paper trading" if passed
                                    else "NO-GO — do not risk money; try fallback or stop"))
    return "\n".join(lines)


def save_equity_curve(equity: List[float], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trade_no", "cumulative_R"])
        for i, v in enumerate(equity, 1):
            w.writerow([i, f"{v:.4f}"])
