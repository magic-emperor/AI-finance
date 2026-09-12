"""
AEP Generator — one Gemini call that writes the structured diagnosis.

Triggered ONLY when aep_trigger.should_trigger_aep() returns a trigger.
Output: JSON dict with problem_summary, root_cause, suggested_change (parameter only).
NOT code. Never code. Parameter values only.
"""
# AEP GOVERNANCE RULE (do not remove this comment):
# This system generates OBSERVATIONS and PARAMETER SUGGESTIONS only.
# It NEVER generates code diffs. It NEVER auto-applies any change.
# suggested_change must contain only numbers or simple scalar values — never code.

import json
import re
import structlog
from datetime import datetime
from typing import Dict, Any, Optional

log = structlog.get_logger("aep_generator")

# Keywords that indicate an LLM accidentally put code into a parameter value.
# If detected, the suggested_change is stripped and flagged.
_CODE_INDICATORS = [
    "def ", "import ", "class ", "return ", "()", "{}",
    "self.", "lambda", "async ", "yield ", "raise ",
]


def generate_aep(
    trigger_info: Dict[str, Any],
    brain_name: str,
    aep_storage,
    gemini_client,
) -> Optional[Dict[str, Any]]:
    """
    Generate a structured AEP from trigger data.
    1 Gemini call. Returns a JSON proposal — NOT code.

    Args:
        trigger_info:  output of should_trigger_aep() — contains trigger type + evidence
        brain_name:    e.g. "AMV-LSTM"
        aep_storage:   AEPStorage instance
        gemini_client: GeminiClient instance

    Returns:
        Proposal dict, or None if generation failed entirely.
    """
    # ── Build evidence context from DB ───────────────────────────
    recent_trades   = aep_storage.get_brain_trades(brain_name, limit=20)
    regime          = trigger_info.get("regime", "ALL")
    regime_acc      = aep_storage.get_brain_accuracy_by_regime(brain_name, regime) if regime != "ALL" else None

    trade_summary = "\n".join([
        f"  Trade {i+1}: {t['direction']} on {t['symbol']} "
        f"| Regime: {t['regime']} | Outcome: {t['outcome']} "
        f"| Brain was: {'RIGHT' if t['was_correct'] else 'WRONG'} "
        f"| Score: {t['accuracy']:.0f}/100"
        for i, t in enumerate(recent_trades[:10])
    ]) or "  (no recent trades available)"

    regime_summary = (
        f"In regime {regime}: "
        f"{regime_acc['sample_size']} trades, "
        f"{regime_acc['win_rate']*100:.1f}% win rate, "
        f"avg score {regime_acc['avg_score']:.1f}/100"
        if regime_acc else "Regime data not available"
    )

    trigger_desc = _describe_trigger(trigger_info)

    prompt = f"""You are analyzing a trading brain called "{brain_name}".
A failure pattern has been detected: {trigger_desc}

Recent trade history:
{trade_summary}

Regime accuracy: {regime_summary}

Your task: Write a structured diagnosis and ONE parameter suggestion.
Respond ONLY in this exact JSON format. No text outside the JSON.

{{
  "brain": "{brain_name}",
  "problem_summary": "one sentence describing the failure pattern",
  "root_cause": "one sentence explaining why this brain fails in this condition",
  "severity": "HIGH or MEDIUM",
  "suggestion_type": "parameter",
  "suggested_change": {{
    "parameter_name": "name of the parameter to change",
    "current_value": "current value as string",
    "suggested_value": "suggested value as string",
    "applies_to_regime": "one of: SCANNING_INTRADAY, STABLE_TRADING, VOLATILE_CHAOS, HYBRID_SCAN, or ALL",
    "applies_to_symbol": "symbol ticker or ALL"
  }},
  "change_rationale": "one sentence explaining why this change would help",
  "requires_retrain": true or false,
  "confidence": "HIGH or MEDIUM or LOW",
  "evidence_summary": "one sentence summarizing the key evidence"
}}

Rules:
- suggestion_type must always be "parameter" — never suggest code changes
- suggested_change must contain only numbers or simple scalar values — NEVER code
- If you cannot identify a clear parameter change, set suggested_change to null
  and explain in change_rationale why the issue needs human investigation
- applies_to_regime must be one of the 4 known regime names above, or ALL"""

    try:
        raw   = gemini_client.call(prompt, max_tokens=600)
        if not raw:
            log.warning("aep_gemini_returned_empty", brain=brain_name)
            return _fallback_proposal(brain_name, trigger_info, "Gemini returned empty response")

        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            log.warning("aep_no_json_in_response", brain=brain_name, raw=raw[:200])
            return _fallback_proposal(brain_name, trigger_info, "Could not parse JSON from Gemini response")

        proposal = json.loads(match.group())

        # ── Safety check: reject if suggested_change contains code ─
        change = proposal.get("suggested_change")
        if change and isinstance(change, dict):
            for key, val in change.items():
                if isinstance(val, str) and any(
                    kw in val.lower() for kw in _CODE_INDICATORS
                ):
                    log.warning(
                        "aep_code_detected_in_parameter",
                        brain=brain_name,
                        key=key,
                        val=val[:80],
                    )
                    proposal["suggested_change"] = None
                    proposal["change_rationale"] = (
                        "Code detected in parameter suggestion — stripped for safety. "
                        "Human investigation required."
                    )
                    break

        # ── Attach metadata ───────────────────────────────────────
        proposal["trigger_info"]  = trigger_info
        proposal["generated_at"]  = datetime.utcnow().isoformat()
        proposal.setdefault("brain", brain_name)

        log.info(
            "aep_generated",
            brain=brain_name,
            trigger=trigger_info.get("trigger"),
            confidence=proposal.get("confidence"),
            has_suggestion=proposal.get("suggested_change") is not None,
        )
        return proposal

    except json.JSONDecodeError as e:
        log.warning("aep_json_parse_failed", brain=brain_name, error=str(e)[:80])
        return _fallback_proposal(brain_name, trigger_info, f"JSON parse error: {e}")
    except Exception as e:
        log.error("aep_generation_failed", brain=brain_name, error=str(e)[:120])
        return _fallback_proposal(brain_name, trigger_info, str(e))


def _describe_trigger(trigger_info: Dict[str, Any]) -> str:
    """Format trigger info as a human-readable sentence for the prompt."""
    t = trigger_info.get("trigger", "unknown")
    if t == "accuracy_drift":
        return (
            f"Accuracy dropped {trigger_info.get('drop', 0)*100:.1f}pp "
            f"from {trigger_info.get('prior_7d', 0)*100:.1f}% to "
            f"{trigger_info.get('recent_7d', 0)*100:.1f}% "
            f"over the past week. Severity: {trigger_info.get('severity', 'MEDIUM')}."
        )
    elif t == "regime_failure":
        return (
            f"Win rate in {trigger_info.get('regime', '?')} regime is "
            f"{trigger_info.get('win_rate', 0)*100:.1f}% "
            f"over {trigger_info.get('sample_size', 0)} trades. "
            f"Threshold is 45%. Severity: HIGH."
        )
    return str(trigger_info)


def _fallback_proposal(
    brain_name: str,
    trigger_info: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    """Returns a low-confidence AEP when Gemini fails, so the data still reaches the dashboard."""
    return {
        "brain":            brain_name,
        "problem_summary":  f"Pattern detected but Gemini diagnosis failed: {reason[:100]}",
        "root_cause":       "Manual investigation required.",
        "severity":         trigger_info.get("severity", "MEDIUM"),
        "suggestion_type":  "parameter",
        "suggested_change": None,
        "change_rationale": "Automatic diagnosis unavailable — review trade history manually.",
        "requires_retrain": False,
        "confidence":       "LOW",
        "evidence_summary": _describe_trigger(trigger_info),
        "trigger_info":     trigger_info,
        "generated_at":     datetime.utcnow().isoformat(),
    }
