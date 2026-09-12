"""
AEP Trigger — decides when a brain has failed enough to warrant a proposal.

Pure Python: zero LLM calls.
Only fires when data is statistically significant (min_samples enforced).

V2 NOTE: pattern_repeat trigger removed from V1.
Requires a 'pattern_description' field in signal_predictions and
minimum 3 months of trade history to be statistically meaningful.
Re-evaluate after 90 days of live data.
"""
# AEP GOVERNANCE RULE (do not remove this comment):
# This system generates OBSERVATIONS and PARAMETER SUGGESTIONS only.
# It NEVER generates code diffs. It NEVER auto-applies any change.

import structlog
from typing import Optional, Dict, Any, List
from sqlalchemy import text

log = structlog.get_logger("aep_trigger")


# ─── Brain roster ────────────────────────────────────────────────
# Hardcoded — intentionally NOT imported from signal_generators.
# If BRAIN_SPECS changes, we want AEP to break loudly rather than
# silently track wrong brains.
ALL_BRAIN_NAMES: List[str] = [
    "AMV-LSTM",
    "Cross-Stock GNN",
    "RL Weighter",
    "Multi-Timeframe",
    "Regime Ensemble",
    "Multi-Modal Fusion",
    "Causal Ensemble",
]

# ─── Real DB regime names ────────────────────────────────────────
# Use ONLY values that actually exist in signal_predictions.regime.
# The spec names (RANGING, VOLATILE_BREAKOUT, etc.) describe the
# Brain 2 redesign target — they do NOT exist in the current DB.
# When Brain 2 is redesigned and regime names change, validate_regimes()
# will warn on startup so this list can be updated immediately.
KNOWN_REGIMES: List[str] = [
    "SCANNING_INTRADAY",
    "STABLE_TRADING",
    "VOLATILE_CHAOS",
    "HYBRID_SCAN",
]

# ─── Trigger thresholds ──────────────────────────────────────────
AEP_TRIGGER_CONDITIONS: Dict[str, Any] = {
    # Condition 1: Accuracy drops significantly week-over-week
    "accuracy_drift": {
        "threshold":   0.10,  # 10pp drop in win_rate
        "window_days": 7,     # compared to previous 7-day window
        "min_samples": 15,    # need ≥15 resolved trades in each window
    },
    # Condition 2: Brain fails badly in a specific regime
    "regime_failure": {
        "threshold":   0.45,  # below 45% win_rate in that regime
        "min_samples": 15,    # need ≥15 resolved trades in that regime
    },
    # V2: pattern_repeat trigger — see module docstring above
}


def validate_regimes(aep_storage) -> List[str]:
    """
    On AEP startup, check what regime values actually exist in the DB.
    Logs warnings if KNOWN_REGIMES diverges from actual DB data.
    Call once when aep_runner.py initializes.

    Returns the list of actual regime names found in signal_predictions.
    """
    session = aep_storage._session()
    try:
        rows = session.execute(text(
            "SELECT DISTINCT regime "
            "FROM signal_predictions "
            "WHERE regime IS NOT NULL"
        )).fetchall()
        actual_names = {row[0] for row in rows}
    except Exception as e:
        log.error("aep_regime_validation_failed", error=str(e)[:80])
        return list(KNOWN_REGIMES)
    finally:
        session.close()

    known_names = set(KNOWN_REGIMES)
    unknown_in_db   = actual_names - known_names
    missing_from_db = known_names  - actual_names

    if unknown_in_db:
        log.warning(
            "aep_new_regimes_in_db",
            regimes=list(unknown_in_db),
            action="These regimes exist in DB but are not in KNOWN_REGIMES. "
                   "Add them to aep_trigger.KNOWN_REGIMES if they are real.",
        )
    if missing_from_db:
        log.warning(
            "aep_regimes_not_yet_in_db",
            regimes=list(missing_from_db),
            action="These KNOWN_REGIMES have no data yet — "
                   "regime_failure trigger will skip them naturally.",
        )

    log.info("aep_regime_validation_done",
             actual_in_db=list(actual_names),
             known_list=KNOWN_REGIMES,
             discrepancies=bool(unknown_in_db or missing_from_db))

    return list(actual_names)


def should_trigger_aep(
    brain_name: str,
    aep_storage,
) -> Optional[Dict[str, Any]]:
    """
    Check all trigger conditions for one brain.
    Pure Python — zero LLM calls.

    Returns a trigger info dict if any condition is met.
    Returns None if the brain appears healthy or has insufficient data.
    """
    # ── Condition 1: Accuracy drift ─────────────────────────────
    try:
        cfg     = AEP_TRIGGER_CONDITIONS["accuracy_drift"]
        recent  = aep_storage.get_brain_accuracy(brain_name, days=cfg["window_days"])
        prior   = aep_storage.get_brain_accuracy(
            brain_name,
            days=cfg["window_days"],
            offset_days=cfg["window_days"],
        )

        if (
            recent
            and prior
            and recent["sample_size"] >= cfg["min_samples"]
            and prior["sample_size"]  >= cfg["min_samples"]
        ):
            drop = prior["win_rate"] - recent["win_rate"]
            if drop >= cfg["threshold"]:
                severity = "HIGH" if drop > 0.15 else "MEDIUM"
                log.info(
                    "aep_trigger_accuracy_drift",
                    brain=brain_name,
                    prior_rate=prior["win_rate"],
                    recent_rate=recent["win_rate"],
                    drop=round(drop, 3),
                    severity=severity,
                )
                return {
                    "trigger":   "accuracy_drift",
                    "brain":     brain_name,
                    "prior_7d":  round(prior["win_rate"], 3),
                    "recent_7d": round(recent["win_rate"], 3),
                    "drop":      round(drop, 3),
                    "severity":  severity,
                }
    except Exception as e:
        log.error("aep_drift_check_failed", brain=brain_name, error=str(e)[:80])

    # ── Condition 2: Regime-specific failure ─────────────────────
    try:
        cfg = AEP_TRIGGER_CONDITIONS["regime_failure"]
        for regime in KNOWN_REGIMES:
            regime_acc = aep_storage.get_brain_accuracy_by_regime(brain_name, regime)
            if not regime_acc:
                continue
            if (
                regime_acc["sample_size"] >= cfg["min_samples"]
                and regime_acc["win_rate"] < cfg["threshold"]
            ):
                log.info(
                    "aep_trigger_regime_failure",
                    brain=brain_name,
                    regime=regime,
                    win_rate=regime_acc["win_rate"],
                    samples=regime_acc["sample_size"],
                )
                return {
                    "trigger":    "regime_failure",
                    "brain":      brain_name,
                    "regime":     regime,
                    "win_rate":   round(regime_acc["win_rate"], 3),
                    "sample_size":regime_acc["sample_size"],
                    "avg_score":  regime_acc.get("avg_score", 0),
                    "severity":   "HIGH",
                }
    except Exception as e:
        log.error("aep_regime_check_failed", brain=brain_name, error=str(e)[:80])

    # Brain is healthy (or insufficient data to tell)
    return None
