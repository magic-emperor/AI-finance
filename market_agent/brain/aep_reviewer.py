"""
AEP Reviewer — Mistral sanity-checks the AEP before the human sees it.

1 Mistral call per council-approved AEP.
Not rewriting the proposal. Checking it.
Human sees BOTH the proposal AND this review.
"""
# AEP GOVERNANCE RULE (do not remove this comment):
# This system generates OBSERVATIONS only. No code changes. No auto-apply.

import structlog
from typing import Dict, Any

log = structlog.get_logger("aep_reviewer")


def mistral_review_aep(
    proposal: Dict[str, Any],
    vote_result: Dict[str, Any],
    mistral_client,
) -> str:
    """
    Mistral reads the AEP and returns a brief review (3-4 sentences).
    1 Mistral call per AEP.

    Args:
        proposal:       output of generate_aep()
        vote_result:    output of council_vote_on_aep()
        mistral_client: _MistralBackend or any object with .call(prompt, max_tokens)

    Returns:
        Review string. Always returns something (fallback on error).
    """
    change = proposal.get("suggested_change")
    if change and isinstance(change, dict):
        change_text = (
            f"Change '{change.get('parameter_name', '?')}' "
            f"from {change.get('current_value', '?')} to {change.get('suggested_value', '?')} "
            f"(applies to regime: {change.get('applies_to_regime', 'ALL')}, "
            f"symbol: {change.get('applies_to_symbol', 'ALL')})"
        )
    else:
        change_text = "No specific parameter change suggested — human investigation required."

    prompt = f"""Review this AI-generated trading system improvement proposal.

PROPOSAL:
Brain:            {proposal.get('brain', '?')}
Problem:          {proposal.get('problem_summary', '?')}
Root cause:       {proposal.get('root_cause', '?')}
Suggested change: {change_text}
Rationale:        {proposal.get('change_rationale', 'none')}
Requires retrain: {proposal.get('requires_retrain', False)}
Gemini confidence:{proposal.get('confidence', 'UNKNOWN')}
Council vote:     {vote_result.get('yes_count', 0)}/{vote_result.get('total_voted', 0)} brains agreed

Write a brief review covering these 4 points in 3-4 sentences total:
1. Is the diagnosis plausible given the evidence?
2. Is the suggested parameter change safe and logical?
3. What is the main risk if this change is applied?
4. Your overall verdict: APPROVE_FOR_REVIEW / NEEDS_MORE_DATA / REJECT

End your review with exactly one of these verdict tags on its own line:
VERDICT: APPROVE_FOR_REVIEW
VERDICT: NEEDS_MORE_DATA
VERDICT: REJECT

Be direct and critical. The human will make the final decision."""

    try:
        review = mistral_client.call(prompt, max_tokens=350)
        if review:
            log.info("aep_mistral_review_done",
                     brain=proposal.get("brain"),
                     length=len(review))
            return review.strip()
        else:
            return _fallback_review(proposal, "Mistral returned empty response")
    except Exception as e:
        log.error("aep_mistral_review_failed",
                  brain=proposal.get("brain"),
                  error=str(e)[:100])
        return _fallback_review(proposal, str(e))


def extract_verdict(mistral_review: str) -> str:
    """
    Extract the VERDICT tag from the Mistral review string.
    Returns one of: APPROVE_FOR_REVIEW, NEEDS_MORE_DATA, REJECT, UNKNOWN.
    """
    for line in mistral_review.splitlines():
        line = line.strip()
        if line.startswith("VERDICT:"):
            verdict = line.replace("VERDICT:", "").strip()
            if verdict in ("APPROVE_FOR_REVIEW", "NEEDS_MORE_DATA", "REJECT"):
                return verdict
    return "UNKNOWN"


def _fallback_review(proposal: Dict[str, Any], reason: str) -> str:
    brain = proposal.get("brain", "?")
    return (
        f"Mistral review unavailable ({reason}). "
        f"AEP for {brain} was generated from real DB data and passed council vote — "
        f"human review is still recommended. "
        f"Check trade history manually before applying any parameter change.\n"
        f"VERDICT: NEEDS_MORE_DATA"
    )
