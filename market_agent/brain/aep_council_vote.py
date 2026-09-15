"""
AEP Council Vote — peer review before a proposal reaches the human.

Pure Python: zero LLM calls.
Each brain checks its OWN accuracy records (from signal_predictions).
Votes YES if it also struggles in the same regime / experienced the same drift.
Requires 4/7 brains to vote YES for the proposal to proceed.
"""
# AEP GOVERNANCE RULE (do not remove this comment):
# This system generates OBSERVATIONS only. No code changes. No auto-apply.

import structlog
from typing import Dict, Any

log = structlog.get_logger("aep_council_vote")

# Consensus threshold
REQUIRED_YES  = 4
MIN_VOTERS    = 4   # Need at least 4 non-abstaining brains for a valid vote

# Voting thresholds — a brain votes YES if it also shows weakness
DRIFT_VOTE_THRESHOLD  = 0.05   # Brain also drifted ≥5pp → YES
REGIME_VOTE_THRESHOLD = 0.55   # Brain also below 55% in same regime → YES

# Import brain list from trigger (single source of truth)
from market_agent.brain.aep_trigger import ALL_BRAIN_NAMES


def council_vote_on_aep(
    proposal: Dict[str, Any],
    aep_storage,
) -> Dict[str, Any]:
    """
    Each brain votes YES if it has also experienced the same weakness.
    Pure Python — each brain checks its own accuracy records in signal_predictions.
    No LLM calls. No network calls beyond the DB query each brain already makes.

    Returns:
        {
          approved:    bool,
          votes:       {brain_name: "YES"|"NO"|"ABSTAIN"},
          yes_count:   int,
          total_voted: int,
          threshold:   "4/7",
        }
    """
    trigger    = proposal.get("trigger_info", {})
    trigger_t  = trigger.get("trigger")
    regime     = trigger.get("regime")        # present for regime_failure
    proposer   = proposal.get("brain", "")

    votes     = {}
    yes_count = 0

    for brain_name in ALL_BRAIN_NAMES:
        # The proposing brain doesn't vote on its own proposal
        if brain_name == proposer:
            votes[brain_name] = "PROPOSER"
            continue

        try:
            if trigger_t == "regime_failure" and regime:
                # Does this brain also struggle in the same regime?
                acc = aep_storage.get_brain_accuracy_by_regime(brain_name, regime)
                if acc and acc["sample_size"] >= 10:
                    also_fails = acc["win_rate"] < REGIME_VOTE_THRESHOLD
                    vote = "YES" if also_fails else "NO"
                else:
                    vote = "ABSTAIN"

            elif trigger_t == "accuracy_drift":
                # Did this brain also drift downward this week?
                recent = aep_storage.get_brain_accuracy(brain_name, days=7)
                prior  = aep_storage.get_brain_accuracy(brain_name, days=7, offset_days=7)
                if recent and prior and recent["sample_size"] >= 10:
                    drift = prior["win_rate"] - recent["win_rate"]
                    also_drifted = drift > DRIFT_VOTE_THRESHOLD
                    vote = "YES" if also_drifted else "NO"
                else:
                    vote = "ABSTAIN"

            else:
                # Unknown trigger type — abstain
                vote = "ABSTAIN"

        except Exception as e:
            log.warning("aep_brain_vote_failed",
                        brain=brain_name, error=str(e)[:80])
            vote = "ABSTAIN"

        votes[brain_name] = vote
        if vote == "YES":
            yes_count += 1

    # Count non-abstaining (and non-proposer) brains
    actual_voters = [v for v in votes.values() if v not in ("ABSTAIN", "PROPOSER")]
    total_voted   = len(actual_voters)
    approved      = yes_count >= REQUIRED_YES and total_voted >= MIN_VOTERS

    log.info(
        "aep_council_vote_complete",
        proposer=proposer,
        yes_count=yes_count,
        total_voted=total_voted,
        approved=approved,
        trigger=trigger_t,
    )

    return {
        "approved":    approved,
        "votes":       votes,
        "yes_count":   yes_count,
        "total_voted": total_voted,
        "threshold":   f"{REQUIRED_YES}/{len(ALL_BRAIN_NAMES)}",
    }
