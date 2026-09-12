"""
Brain 6: RL-Weighter — Adaptive Capital Allocation (Risk Sizing Brain)
======================================================================
Runs AFTER all other brains. Computes a risk_multiplier based on half-Kelly
criterion using historical win rate. Adjusts confidence (sizing) — does NOT
generate a new directional vote.

IMPORTANT — This brain output is handled differently in signal_generators.py:
  - Added to brain_results[] (for council debate context)
  - NOT added to signals[] (which drives trade execution)
  - risk_multiplier is stored separately and passed to execution sizing

Why: RL-Weighter was double-counting the majority vote. If 3 brains say BUY,
RL-Weighter also says BUY based on majority — adding it to signals gives a
false impression of 4 brains agreeing when it's really 3.

The brain is correctly used as a SIZER: how large should the position be given
the current performance trend? Not: is the current market direction correct?

Note: The name "RL-Weighter" is aspirational — this is half-Kelly sizing,
not reinforcement learning. The naming sets correct expectations.

Returns: BrainSignal (brain_contract.py)
"""
from __future__ import annotations

from market_agent.brain.brain_contract import BrainSignal


def rl_weighter_signal(
    base_signal: dict,
    performance_history: list,
    current_regime: str = 'RANGING',
    macro_context: dict = None,
) -> BrainSignal:
    """
    Brain 6: Adaptive Capital Allocation via Half-Kelly Criterion.
    Runs AFTER other brains. Uses their historical win rate to size position.

    performance_history: list of {'outcome': 'TARGET'|'SL'|'EXPIRED', 'regime': str}

    macro_context (optional): dict from MacroCircuitBreaker
      {'state': 'CLEAR'|'WATCH'|'HALT', 'crisis_type': str, ...}
      Adjusts risk_multiplier downward in WATCH/HALT states.
      crisis-aligned trades in HALT get 0.25x (small but not zero).
      Backward compatible: None = no macro adjustment.

    Returns BrainSignal with:
      - direction: mirrors majority direction (for council context only)
      - confidence: adjusted by risk_multiplier (for display only)
      - measurements['risk_multiplier']: the actual sizing output
                                         (consumed by execution layer)

    CRITICAL: signal_generators.py MUST add this to brain_results[] only,
              NOT to signals[]. See generate_brain_signals() for the correct usage.
    """
    if not performance_history:
        risk_multiplier = 1.0
        evidence        = 'No history — default 1x sizing'
    else:
        recent   = performance_history[-20:]
        wins     = sum(1 for t in recent if t.get('outcome') == 'TARGET')
        win_rate = wins / len(recent)

        from market_agent.signal_params import BASE_ATR_T1_MULT, BASE_ATR_SL_MULT
        b          = BASE_ATR_T1_MULT / BASE_ATR_SL_MULT   # base R:R
        kelly      = (b * win_rate - (1 - win_rate)) / b
        half_kelly = max(0.2, min(1.5, kelly / 2))

        regime_trades = [t for t in recent if t.get('regime') == current_regime]
        regime_wins   = sum(1 for t in regime_trades if t.get('outcome') == 'TARGET')
        regime_rate   = regime_wins / len(regime_trades) if regime_trades else win_rate

        # Slight boost when this regime outperforms overall, slight cut when underperforms
        risk_multiplier = half_kelly * (1.1 if regime_rate > win_rate else 0.9)
        evidence = (
            f'WR={win_rate:.1%} | Half-Kelly={half_kelly:.2f}x | '
            f'Regime {current_regime} WR={regime_rate:.1%}'
        )

    # N3a: Apply macro context adjustment to risk_multiplier
    macro_note = ""
    if macro_context:
        macro_state = macro_context.get('state', 'CLEAR')
        if macro_state == 'HALT':
            # Crisis-aligned trades still allowed but at 0.25x sizing
            risk_multiplier *= 0.25
            macro_note = f' | MACRO HALT: 0.25x (crisis={macro_context.get("crisis_type","?")})'
        elif macro_state == 'WATCH':
            risk_multiplier *= 0.75
            macro_note = ' | MACRO WATCH: 0.75x'

    if macro_note:
        evidence = evidence + macro_note

    base_dir  = base_signal.get('direction', 'HOLD')
    base_conf = float(base_signal.get('confidence', 0.5))

    return BrainSignal(
        brain_name='RL-Weighter',
        specialization='Adaptive Capital Allocation — Risk Sizing Brain',
        method='Half-Kelly criterion + regime-adjusted position sizing',
        direction=base_dir,                                  # mirrors majority — for context only
        confidence=base_conf * min(risk_multiplier, 1.0),   # adjusted display confidence
        signal_strength=min(1.0, risk_multiplier),
        signal_age_candles=0,
        primary_evidence=evidence,
        supporting_factors=[f'Risk multiplier={risk_multiplier:.2f}x base position'],
        contra_factors=['Reduce size — losing streak'] if risk_multiplier < 0.7 else [],
        method_confidence=0.85,
        regime_suitability='HIGH',
        reliability_flags={'insufficient_history': len(performance_history) < 10},
        measurements={
            'risk_multiplier': round(risk_multiplier, 3),   # ← execution layer reads this
            'win_rate':        wins / len(performance_history[-20:]) if performance_history else 0.0,
        },
        recent_accuracy=None,
        regime_accuracy=None,
    )
