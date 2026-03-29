"""
AEP Runner — daily background job that orchestrates the full AEP pipeline.

Schedule: once per day, ideally 30 minutes before market close
  (e.g., 15:00 IST for NSE, or post-16:30 UTC for US market coverage).

Flow per brain:
  Step 1. should_trigger_aep()       → pure Python, 0 LLM calls
  Step 2. generate_aep()             → 1 Gemini call (only if triggered)
  Step 3. council_vote_on_aep()      → pure Python, 0 LLM calls
  Step 4. mistral_review_aep()       → 1 Mistral call (only if council approved)
  Step 5. aep_storage.save_aep()     → write to DB, appears on dashboard

LLM budget per day: 0 on healthy days, 2 calls per triggered brain on bad days.
"""
# AEP GOVERNANCE RULE (do not remove this comment):
# This system generates OBSERVATIONS and PARAMETER SUGGESTIONS only.
# It NEVER generates code diffs. It NEVER auto-applies any change.
# See AEPflow.md for full design rationale.

import structlog
from typing import Optional

log = structlog.get_logger("aep_runner")


def run_daily_aep_check(
    postgres_storage,
    gemini_client,
    mistral_client,
) -> int:
    """
    Daily AEP check for all 7 brains.

    Args:
        postgres_storage: PostgresStorage instance (already initialised)
        gemini_client:    GeminiClient singleton (from market_agent.brain.gemini_client)
        mistral_client:   _MistralBackend instance or GeminiClient (has .mistral attr)

    Returns:
        Number of AEPs generated and saved to DB.
    """
    from market_agent.brain.aep_storage       import AEPStorage
    from market_agent.brain.aep_trigger       import ALL_BRAIN_NAMES, validate_regimes, should_trigger_aep
    from market_agent.brain.aep_generator     import generate_aep
    from market_agent.brain.aep_council_vote  import council_vote_on_aep
    from market_agent.brain.aep_reviewer      import mistral_review_aep

    # Allow passing full GeminiClient (has .mistral attribute) or raw backend
    if hasattr(mistral_client, "mistral"):
        mistral_client = mistral_client.mistral

    # ── Init AEPStorage ──────────────────────────────────────────
    aep_storage = AEPStorage(postgres_storage)

    # ── Startup: validate regime names ──────────────────────────
    # Warns if DB regime names have drifted from KNOWN_REGIMES
    actual_regimes = validate_regimes(aep_storage)
    log.info("aep_runner_starting",
             brains=ALL_BRAIN_NAMES,
             actual_regimes=actual_regimes)

    generated = 0

    for brain_name in ALL_BRAIN_NAMES:

        # ── Step 1: Check trigger conditions (pure Python) ───────
        trigger = should_trigger_aep(brain_name, aep_storage)
        if not trigger:
            log.debug("aep_no_trigger", brain=brain_name)
            continue

        log.info("aep_triggered",
                 brain=brain_name,
                 trigger=trigger["trigger"],
                 severity=trigger["severity"])

        # ── Dedup guard: skip if this trigger+brain is already PENDING ─
        existing = aep_storage.get_pending_aep(brain_name, trigger["trigger"])
        if existing:
            log.info("aep_duplicate_skipped",
                     brain=brain_name,
                     existing_pr_id=existing["pr_id"])
            continue

        # ── Step 2: Generate structured proposal (1 Gemini call) ─
        proposal = generate_aep(trigger, brain_name, aep_storage, gemini_client)
        if not proposal:
            log.warning("aep_generation_returned_none", brain=brain_name)
            continue

        # ── Step 3: Council vote (pure Python, 0 LLM calls) ─────
        vote_result = council_vote_on_aep(proposal, aep_storage)
        log.info("aep_council_vote",
                 brain=brain_name,
                 approved=vote_result["approved"],
                 yes_count=vote_result["yes_count"],
                 total_voted=vote_result["total_voted"])

        if not vote_result["approved"]:
            log.info("aep_rejected_by_council",
                     brain=brain_name,
                     yes_count=vote_result["yes_count"],
                     needed=4)
            continue

        # ── Step 4: Mistral review (1 Mistral call) ─────────────
        review = "Mistral review skipped (client unavailable)."
        if mistral_client:
            review = mistral_review_aep(proposal, vote_result, mistral_client)

        # ── Step 5: Save to DB — appears on dashboard ────────────
        aep_id = aep_storage.save_aep(proposal, vote_result, review)
        if aep_id > 0:
            log.info("aep_saved_to_db",
                     brain=brain_name,
                     aep_id=aep_id,
                     trigger=trigger["trigger"])
            generated += 1
        else:
            log.error("aep_save_failed_no_id", brain=brain_name)

    # ── Final: Log the run itself for the dashboard ──────────────
    from datetime import datetime
    data_check = aep_storage.get_data_sufficiency()
    aep_storage.save_aep_run_log({
        'run_at':          datetime.utcnow(),
        'brains_checked':  len(ALL_BRAIN_NAMES),
        'triggers_fired':  generated,
        'aeps_created':    generated,
        'had_enough_data': data_check.get("sufficient", False),
    })

    log.info("aep_daily_check_complete",
             generated=generated,
             brains_checked=len(ALL_BRAIN_NAMES))
    return generated


def schedule_aep_check(postgres_storage, gemini_client, mistral_client=None) -> None:
    """
    Run a single AEP check immediately (for manual CLI invocation or testing).
    Does NOT set up a recurring schedule — use your own scheduler (APScheduler,
    cron, or a one-shot background thread in app.py).

    Usage:
        from market_agent.runner.aep_runner import schedule_aep_check
        from market_agent.brain.gemini_client import gemini_client
        from market_agent.data.storage.postgres import PostgresStorage
        storage = PostgresStorage()
        schedule_aep_check(storage, gemini_client)
    """
    n = run_daily_aep_check(postgres_storage, gemini_client, mistral_client)
    print(f"AEP check complete — {n} proposal(s) generated.")
