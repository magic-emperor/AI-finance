# Phase 1: grader v2 (shadow), what was built

| | |
|---|---|
| Written | 2026-10-02 by Claude Code |
| Plan | `prediction-agent-v2-plan.md` §7, scoped by the owner on 2026-10-02: start with grading every flag, B0, costs and the decision schema; Yahoo adjusted prices and `^NSEI` for now; sector benchmarks and the bhavcopy adjuster move to Phase 3 |
| Stacks on | `claude/runbook-holiday-sast` (holiday check, macro-view ledger, SAST period weighting) |
| Status | **Phase 1 stops here.** Grader v2 runs in shadow; the agent still reads v1's `scores/summary.md` until you switch it (§7.10). |

## What changed

| Piece | Files | Notes |
|---|---|---|
| Decision record | `intel/schema.py` (`decision.v1`), `intel/ledger.py` (`append-decision`, third chain `decisions/`), `intel/validate.py` | One per investigated opportunity: CALL (UP/DOWN) or NO_CALL with a reason from the §7.1 taxonomy. Carries `role`, `model`, `playbook_version`, `process_version`, `opportunity_id`, `flag_ids`, `rules_applied`, `evidence_pack_id`. The ledger stamps id, time and hash, and refuses caller-supplied ones. `append-call` stays valid for old records. |
| Opportunity ids | `intel/scouts/escalate.py`, `intel/agent_queue.py` | `opp:<symbol>:<sha1 of the sorted flag ids, 12 hex>`, printed by the queue with each group's flag ids. |
| RUNBOOK | `agent/RUNBOOK.md` step 5 | The agent now records a decision for every opportunity it investigates, declines included. |
| Grader v2 | `intel/grader_v2.py`, `intel/config/grader_v2.json` | See rules below. Writes `scores/{opportunities,decisions,macro_views}_scored.jsonl`, `stats/summary.md` and `stats/visible/*.json` on agent-data. |
| Statistics | `intel/stats/` | Port of `stats_reference.py`; costs come from config. Its self-tests are now `tests/test_intel_stats.py`. |
| v1 kept alive | `intel/grader.py` | Its loader also reads CALL decisions (rules unchanged), so v1 keeps grading new calls and the shadow comparison is possible. |
| CI | `intel/ci/agent_grade.sh`, `.github/workflows/agent_grader.yml` | The workflow calls the script on the code branch. A v2 failure is a warning and never blocks v1. The YAML change needs the `main` PR too (scheduled runs read main's copy); the fallback keeps it working in either merge order. |
| Look-ahead fix | `intel/scouts/run_scouts.py` (`seen_at`), `intel/grader_v2.py` (`flag_time`) | See "Finding" below. |

## Grading rules (all in config or code, none in the prompt)

- **Opportunities:** every archived flag, at 1/5/20 trading days.
- **Entry and exit:** entry is the close of the decision day if decided before 15:00 IST, else the next trading day's close. Exit is the close h sessions later. For an uninvestigated flag the decision time is the later of its publication (`observed_at`) and the scouts' first sighting (`seen_at`).
- **Excess:** vs `^NSEI` for `.NS` stocks; raw return for indices, FX and commodities.
- **Costs:** `.NS` only, from config.
  - Liquidity bucket comes from the 20-session median turnover before entry. Unknown turnover is costed as small.
  - Round trips at Rs 1 lakh: large ≈ 0.35%, mid ≈ 0.55%, small ≈ 0.85% (tested).
- **Payoff:** NO_CALL = 0.
- **B0:** each flag in its kind's default direction, per your decision Q5:
  - `sast_acquisition`, `bulk_deal_buy` and `block_deal_buy` are UP.
  - `volume_breakout` takes the breakout day's direction (the flag's own hint).
  - `macro_move` follows its hint, the sign of the move's z (owner decision 2026-10-02; flags archived before that carry no hint and stay out).
  - `news_multi_source`, `volume_spurt_notice`, `regulator_release` and untyped announcements are graded but kept out of B0.
  - Typed announcements follow the table approved on 2026-10-02: only "Bagging/Receiving of orders/contracts" has a default (UP).
- **Champion − B0:** paired on the same opportunity, the decision's own entry and horizon, and the same costs. B0's direction for an opportunity is the majority default of its flags; ties and no defaults give no pair.
- **REAFFIRM:** a CALL restating a still-open CALL (same role, instrument and direction) is left out of N.
- **Excluded calls:** `agent-20261002T034124Z-001` is shown under "Excluded" with the reason "forced by the old step 3b", and never counted (Q4). Its p = 0.56 is graded as one data point in the macro calibration track.
- **Macro views:** Brier score, and skill vs a 2-year climatology of up-5-day windows.
- **Visible and holdout weeks:**
  - Every statistic uses visible weeks only.
  - Holdout rows are written **sealed**: identity and status are kept, every outcome field is withheld.
  - So nothing on agent-data carries holdout results, which goes beyond the plan's "no per-family holdout stats".
  - Phase 4 gates will recompute from prices.

## Finding: look-ahead in flag timing (fixed)

The first live backfill graded 39 flags at 1 day, entering on 29–30 Sep. Those flags were filings *published* on 29–30 Sep, but the scouts first saw them on 1 Oct at 16:56 UTC. `observed_at` is the source's publication time, so grading from it entered before our system could have known.

- **Fix:** scouts now stamp `seen_at` on every archived flag, and grader v2 enters no earlier than it.
- **Older flags:** flags archived before the fix fall back to the end of their archive day, which never enters early. For the 1 Oct backlog this gives the same entry as the true sighting time: 16:56 UTC is after the 15:00 IST cutoff.
- **v1 is unaffected:** it grades only calls, which carry their own `created_at`.

## Acceptance (plan §7)

| Check | Status |
|---|---|
| Unit tests on synthetic ledgers with known answers: costs, NO_CALL = 0, baselines, paired differences, CI coverage | **Met** for B0, costs, NO_CALL, pairing, CI coverage, entry rule, sealing and exclusion (`tests/test_intel_grader_v2.py`, `test_intel_stats.py`, `test_intel_decisions.py`). B1 is not built. |
| A backfill over all existing history completes | **Met.** It ran against live agent-data and the ledger on 2026-10-02: 219 flags, prices for 137 of 137 instruments, about 11 s. Everything is PENDING, because the first post-flag NSE session is Mon 5 Oct. |
| `summary.md` v2 shows champion vs B0 with a CI | **Met in structure**, tested on synthetic data. There are no live numbers until decisions are graded, around 12 Oct for 5-day calls made on 5 Oct. |
| No agent-readable file contains holdout-week per-family stats | **Met by construction:** statistics are visible-only, holdout rows are sealed, and a test checks both. |

## Not in this PR (Phase 1 items left for later)

- B1 random-matched baseline.
- Calibration table and `calibration_map.json` (needs ≥ 100 graded calls).
- Harm monitor with `RobustEProcess` (needs weeks of data).
- `dashboard.json`.
- The F&O list: DOWN calls on stocks show `tradable = null`.
- Statistics cut by process flag (Phase 2).
- Sector and size benchmarks, and bhavcopy prices (Phase 3, by your decision).

## For your review

1. **Typed-announcement directions:** approved as proposed and active since 2026-10-02. "Auditor resignation → DOWN" still needs a scout change first, because that category isn't flagged today.
2. **`macro_move` in B0:** follows the move's direction since 2026-10-02 (owner decision).
3. **Switching the agent to summary v2** (§7.10). After about a week of shadow running, compare v1 and v2, then decide.
