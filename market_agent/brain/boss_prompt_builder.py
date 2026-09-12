"""
Phase 4 Step 4.10 — Boss Brain Prompt Builder

Builds the grand_council prompt that consumes rich BrainSignal packets.
This replaces the old debate_topic-based prompt in cortex.py.

Usage (from cortex.py or wherever grand_council runs):
    from market_agent.brain.boss_prompt_builder import build_boss_prompt
    prompt = build_boss_prompt(symbol, brain_signals, regime_signal, market_context)
"""
from typing import List, Optional, Dict, Any
from market_agent.brain.brain_contract import BrainSignal


def build_boss_prompt(
    symbol: str,
    brain_signals: List[BrainSignal],
    regime_signal: BrainSignal,
    market_context: Dict[str, Any] = None,
    strategy_mode: str = 'Intraday (Scalp)',
) -> str:
    """
    Step 4.10 — Build the Boss Brain prompt from rich BrainSignal packets.

    Brain 2 (Regime Ensemble) is passed separately as it sets the meta-context.
    All other 6 brains are passed in brain_signals.

    The prompt instructs the Boss Brain to:
    1. Weight each brain by regime_suitability and method_confidence
    2. Discount brains with reliability_flags raised
    3. Use Brain 2 weights to balance trend vs mean-reversion brains
    4. Respect Brain 6 (RL) position sizing guidance

    Returns a complete, ready-to-send LLM prompt string.
    """
    # ── Build regime context ─────────────────────────────────────────────
    regime_weights = regime_signal.measurements if regime_signal else {}
    trend_trust    = regime_weights.get('trend_brain_weight', 0.7)
    mean_rev_trust = regime_weights.get('mean_rev_weight',   0.5)
    regime_ev      = regime_signal.primary_evidence if regime_signal else 'Unknown regime'

    # ── Build brain context blocks ───────────────────────────────────────
    brain_blocks = []
    for bs in brain_signals:
        brain_blocks.append(f'--- {bs.to_debate_context()}')

    brain_contexts = '\n\n'.join(brain_blocks) if brain_blocks else '(no brain signals available)'

    # ── Market context section ───────────────────────────────────────────
    ctx_lines = []
    if market_context:
        if market_context.get('fii_dii'):
            ctx_lines.append(f"FII/DII: {market_context['fii_dii'].get('evidence', 'N/A')}")
        if market_context.get('pcr'):
            ctx_lines.append(f"PCR: {market_context['pcr'].get('evidence', 'N/A')}")
        if market_context.get('sentiment'):
            sent = market_context['sentiment']
            ctx_lines.append(
                f"Sentiment: {sent.get('direction', 'HOLD')} "
                f"(composite={sent.get('composite', 0):+.2f}, "
                f"confidence={sent.get('confidence', 0):.0%})"
            )
    market_ctx_str = '\n'.join(ctx_lines) if ctx_lines else '(no external context)'

    # ── Mean-reversion brain identification ─────────────────────────────
    trend_brains    = ['AMV-LSTM', 'Multi-Timeframe']
    mean_rev_brains = ['Causal-Ensemble', 'Multi-Modal-Fusion']

    prompt = f"""You are the Boss Brain for {symbol} — the final decision maker.
You are judging a council of {len(brain_signals)} specialised AI brains.
Strategy mode: {strategy_mode}

YOUR TASK: Synthesize their evidence into ONE final trading verdict.

====================================================
MARKET REGIME CONTEXT (set by Brain 2 — read this FIRST)
====================================================
{regime_ev}
Regime trust weights:
  Trend brains ({', '.join(trend_brains)}): {trend_trust:.0%} trust
  Mean-reversion brains ({', '.join(mean_rev_brains)}): {mean_rev_trust:.0%} trust

====================================================
EXTERNAL MARKET SIGNALS
====================================================
{market_ctx_str}

====================================================
COUNCIL BRAIN REPORTS
====================================================
{brain_contexts}

====================================================
DECISION RULES (apply in order)
====================================================
1. Regime first: Weight each brain by its 'regime_suitability' field AND the regime trust weights above.
   A LOW-suitability brain should be discounted 50-70% regardless of its stated confidence.
2. Reliability flags: Each raised flag = -10% effective confidence on that brain. A brain with 3+ flags should be largely ignored.
3. RL Weighter (Brain 6) has already adjusted confidence for recent performance — treat its confidence as final for sizing.
4. Brains with recent_accuracy < 50% should be halved in weight.
5. When trend brains and mean-reversion brains strongly disagree, prefer the direction supported by brain 2's regime type.
6. Do NOT round-trip to 'HOLD' just to be safe — make the best call based on available evidence.

Respond ONLY in this exact JSON format (no other text, no markdown):
{{"verdict": "BUY", "confidence": 0.72, "reasoning": "One sentence — the single most decisive factor", "most_trusted_brain": "brain name", "overruled_brains": ["brain name if any"]}}

verdict must be EXACTLY one of: BUY, SELL, HOLD
confidence must be between 0.0 and 1.0"""

    return prompt


def parse_boss_response(raw_response: str) -> Optional[Dict[str, Any]]:
    """
    Parse the Boss Brain JSON response safely.
    Returns None if parsing fails (caller should fall back to majority vote).
    """
    import json, re
    try:
        # Try direct parse first
        return json.loads(raw_response.strip())
    except Exception:
        pass

    # Try regex extraction
    try:
        m = re.search(r'\{.*?\}', raw_response, re.DOTALL)
        if m:
            result = json.loads(m.group())
            # Validate mandatory fields
            if result.get('verdict') in ('BUY', 'SELL', 'HOLD'):
                return result
    except Exception:
        pass

    return None