"""
AEP Storage Helper (Geminiiflow / AEPflow architecture)

Read-only access to signal_predictions table for per-brain accuracy analysis.
Write access to ai_code_proposals table (AEP records).
Wraps PostgresStorage — does NOT replace it.

If AEP is ever removed, delete this file. Nothing else is affected.
"""
# AEP GOVERNANCE RULE (do not remove this comment):
# This system generates OBSERVATIONS and PARAMETER SUGGESTIONS only.
# It NEVER generates code diffs. It NEVER auto-applies any change.
# proposed_diff in the DB schema is reserved for future use and is
# intentionally never populated by this system.

import json
import structlog
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy import text

log = structlog.get_logger("aep_storage")


class AEPStorage:
    """
    Read-only access to signal_predictions for brain accuracy analysis.
    Write access to ai_code_proposals for AEP records.

    Usage:
        from market_agent.data.storage.postgres import PostgresStorage
        storage = PostgresStorage()
        aep_storage = AEPStorage(storage)
    """

    # Accuracy score threshold: ≥ this = a "win" in binary terms
    WIN_THRESHOLD = 50.0

    def __init__(self, postgres_storage):
        self._storage = postgres_storage

    def _session(self):
        """Get a fresh session from the wrapped storage."""
        return self._storage.Session()

    # ─────────────────────────────────────────────────────────────
    # READ: Brain accuracy queries (from signal_predictions table)
    # ─────────────────────────────────────────────────────────────

    def get_brain_accuracy(
        self,
        brain_name: str,
        days: int = 7,
        offset_days: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Accuracy stats for a brain over a time window.

        Args:
            brain_name:  exact model_id string (e.g. "AMV-LSTM")
            days:        window size in days
            offset_days: shift window backward by this many days
                         (offset=7, days=7 → the 7 days before last week)

        Returns:
            {win_rate: 0-1 float, sample_size: int, avg_score: float}
            or None if no data.
        """
        session = self._session()
        try:
            end_dt   = datetime.utcnow() - timedelta(days=offset_days)
            start_dt = end_dt - timedelta(days=days)

            rows = session.execute(text("""
                SELECT accuracy_score, binary_win
                FROM   signal_predictions
                WHERE  model_id    = :brain
                AND    is_resolved = true
                AND    resolved_at >= :start
                AND    resolved_at <  :end
            """), {
                "brain": brain_name,
                "start": start_dt,
                "end":   end_dt,
            }).fetchall()

            if not rows:
                return None

            n     = len(rows)
            wins  = sum(1 for r in rows if (r[0] or 0) >= self.WIN_THRESHOLD)
            avg_s = sum((r[0] or 0) for r in rows) / n

            return {
                "win_rate":    round(wins / n, 3),
                "sample_size": n,
                "avg_score":   round(avg_s, 1),
                "wins":        wins,
            }

        except Exception as e:
            log.error("aep_get_brain_accuracy_failed",
                      brain=brain_name, error=str(e)[:100])
            return None
        finally:
            session.close()

    def get_brain_accuracy_by_regime(
        self,
        brain_name: str,
        regime: str,
        min_days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        Accuracy stats for a brain within a specific market regime.

        Args:
            brain_name: exact model_id
            regime:     one of KNOWN_REGIMES (real DB values)
            min_days:   look back at most this many days

        Returns:
            {win_rate: 0-1, sample_size: int, avg_score: float, regime: str}
            or None if no data.
        """
        session = self._session()
        try:
            since = datetime.utcnow() - timedelta(days=min_days)

            rows = session.execute(text("""
                SELECT accuracy_score, binary_win
                FROM   signal_predictions
                WHERE  model_id    = :brain
                AND    regime      = :regime
                AND    is_resolved = true
                AND    resolved_at >= :since
            """), {
                "brain":  brain_name,
                "regime": regime,
                "since":  since,
            }).fetchall()

            if not rows:
                return None

            n     = len(rows)
            wins  = sum(1 for r in rows if (r[0] or 0) >= self.WIN_THRESHOLD)
            avg_s = sum((r[0] or 0) for r in rows) / n

            return {
                "win_rate":    round(wins / n, 3),
                "sample_size": n,
                "avg_score":   round(avg_s, 1),
                "wins":        wins,
                "regime":      regime,
            }

        except Exception as e:
            log.error("aep_get_regime_accuracy_failed",
                      brain=brain_name, regime=regime, error=str(e)[:100])
            return None
        finally:
            session.close()

    def get_brain_trades(
        self,
        brain_name: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Recent resolved predictions for a brain (used for AEP diagnosis context).

        Returns list of dicts: {direction, symbol, regime, outcome, was_correct}
        """
        session = self._session()
        try:
            rows = session.execute(text("""
                SELECT direction, symbol, regime, resolution_type,
                       accuracy_score, binary_win, resolved_at
                FROM   signal_predictions
                WHERE  model_id    = :brain
                AND    is_resolved = true
                ORDER  BY resolved_at DESC
                LIMIT  :limit
            """), {"brain": brain_name, "limit": limit}).fetchall()

            return [
                {
                    "direction":   r[0],
                    "symbol":      r[1],
                    "regime":      r[2],
                    "outcome":     r[3],
                    "accuracy":    round(r[4] or 0, 1),
                    "was_correct": bool(r[5] == 1) if r[5] is not None else False,
                }
                for r in rows
            ]

        except Exception as e:
            log.error("aep_get_brain_trades_failed",
                      brain=brain_name, error=str(e)[:100])
            return []
        finally:
            session.close()

    def get_pending_aep(
        self,
        brain_name: str,
        trigger_type: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Check if an equivalent PENDING AEP already exists for this brain + trigger.
        Used to prevent duplicate proposals.

        Returns the existing AEP record dict, or None if clear to create a new one.
        """
        session = self._session()
        try:
            row = session.execute(text("""
                SELECT id, pr_id, created_at
                FROM   ai_code_proposals
                WHERE  proposing_brain = :brain
                AND    trigger_type    = :trigger
                AND    human_decision  = 'PENDING'
                ORDER  BY created_at DESC
                LIMIT  1
            """), {
                "brain":   brain_name,
                "trigger": trigger_type,
            }).fetchone()

            if not row:
                return None
            return {"id": row[0], "pr_id": row[1], "created_at": row[2]}

        except Exception as e:
            log.error("aep_get_pending_failed",
                      brain=brain_name, error=str(e)[:100])
            return None
        finally:
            session.close()

    def get_active_overrides(self, brain_name: str) -> List[Dict[str, Any]]:
        """
        Get active parameter overrides for a brain from brain_parameter_overrides.
        Called once per brain at initialization to load approved AEP changes.

        Returns list of {parameter_name, new_value, applies_to_regime, applies_to_symbol}
        """
        session = self._session()
        try:
            rows = session.execute(text("""
                SELECT parameter_name, new_value,
                       applies_to_regime, applies_to_symbol
                FROM   brain_parameter_overrides
                WHERE  brain_name = :brain
                AND    active     = true
                ORDER  BY created_at ASC
            """), {"brain": brain_name}).fetchall()

            return [
                {
                    "parameter_name":    r[0],
                    "new_value":         r[1],
                    "applies_to_regime": r[2],
                    "applies_to_symbol": r[3],
                }
                for r in rows
            ]
        except Exception as e:
            # Table may not exist yet — safe to return empty
            log.debug("aep_get_overrides_failed", brain=brain_name, error=str(e)[:80])
            return []
        finally:
            session.close()

    # ─────────────────────────────────────────────────────────────
    # WRITE: Save approved AEP to DB
    # ─────────────────────────────────────────────────────────────

    def save_aep(
        self,
        proposal: Dict[str, Any],
        vote_result: Dict[str, Any],
        mistral_review: str,
    ) -> int:
        """
        Write a council-approved AEP to ai_code_proposals.
        Returns the new AEP's integer ID.
        """
        session = self._session()
        try:
            pr_id = self._generate_pr_id(session)

            session.execute(text("""
                INSERT INTO ai_code_proposals (
                    pr_id, proposing_brain, trigger_type,
                    problem_summary, root_cause,
                    suggested_change, change_rationale,
                    requires_retrain, evidence_summary,
                    council_votes, mistral_review,
                    human_decision,
                    consensus, user_status
                ) VALUES (
                    :pr_id, :brain, :trigger,
                    :problem, :cause,
                    :change, :rationale,
                    :retrain, :evidence,
                    :votes, :review,
                    'PENDING',
                    'PENDING', 'PENDING'
                )
            """), {
                "pr_id":    pr_id,
                "brain":    proposal.get("brain", ""),
                "trigger":  proposal.get("trigger_info", {}).get("trigger", ""),
                "problem":  proposal.get("problem_summary", ""),
                "cause":    proposal.get("root_cause", ""),
                "change":   json.dumps(proposal.get("suggested_change")),
                "rationale":proposal.get("change_rationale", ""),
                "retrain":  bool(proposal.get("requires_retrain", False)),
                "evidence": proposal.get("evidence_summary", ""),
                "votes":    json.dumps(vote_result),
                "review":   mistral_review,
            })
            session.commit()

            row = session.execute(text(
                "SELECT id FROM ai_code_proposals WHERE pr_id = :pr_id"
            ), {"pr_id": pr_id}).fetchone()

            aep_id = row[0] if row else -1
            log.info("aep_saved", pr_id=pr_id, id=aep_id,
                     brain=proposal.get("brain"))
            return aep_id

        except Exception as e:
            session.rollback()
            log.error("aep_save_failed", error=str(e)[:150])
            return -1
        finally:
            session.close()

    def save_parameter_override(
        self,
        aep_id: int,
        brain_name: str,
        parameter_name: str,
        old_value: str,
        new_value: str,
        applies_to_regime: Optional[str] = None,
        applies_to_symbol: Optional[str] = None,
    ) -> bool:
        """
        Called when human approves an AEP. Writes to brain_parameter_overrides.
        Returns True on success.
        """
        session = self._session()
        try:
            session.execute(text("""
                INSERT INTO brain_parameter_overrides (
                    brain_name, parameter_name, old_value, new_value,
                    applies_to_regime, applies_to_symbol,
                    aep_id, approved_by, active
                ) VALUES (
                    :brain, :param, :old, :new,
                    :regime, :symbol,
                    :aep_id, 'human', true
                )
            """), {
                "brain":  brain_name,
                "param":  parameter_name,
                "old":    str(old_value),
                "new":    str(new_value),
                "regime": applies_to_regime,
                "symbol": applies_to_symbol,
                "aep_id": aep_id,
            })
            # Mark AEP as applied
            session.execute(text("""
                UPDATE ai_code_proposals
                SET    human_decision = 'APPROVED',
                       applied_at     = NOW()
                WHERE  id = :id
            """), {"id": aep_id})
            session.commit()
            log.info("override_saved", brain=brain_name,
                     param=parameter_name, new_value=new_value)
            return True
        except Exception as e:
            session.rollback()
            log.error("override_save_failed", error=str(e)[:120])
            return False
        finally:
            session.close()

    def reject_aep(self, aep_id: int) -> bool:
        """Mark an AEP as rejected by human."""
        session = self._session()
        try:
            session.execute(text("""
                UPDATE ai_code_proposals
                SET    human_decision = 'REJECTED'
                WHERE  id = :id
            """), {"id": aep_id})
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            log.error("aep_reject_failed", error=str(e)[:80])
            return False
        finally:
            session.close()

    # ─────────────────────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _generate_pr_id(session) -> str:
        """
        Generate a human-readable AEP ID: AEP-YYYYMMDD-NNN
        e.g. AEP-20260221-001, AEP-20260221-002, ...
        """
        today  = date.today().strftime("%Y%m%d")
        prefix = f"AEP-{today}-"
        count  = session.execute(text(
            "SELECT COUNT(*) FROM ai_code_proposals WHERE pr_id LIKE :prefix"
        ), {"prefix": prefix + "%"}).scalar() or 0
        return f"{prefix}{str(count + 1).zfill(3)}"

    def get_last_aep_run(self) -> Optional[Dict[str, Any]]:
        """
        Returns info about the most recent AEP runner execution.
        Returns None if the runner has never run.
        Used by the dashboard to distinguish
        STATE 4 (never run / stale) vs STATE 2 (healthy).
        """
        session = self._session()
        try:
            row = session.execute(text("""
                SELECT run_at, brains_checked, triggers_fired,
                       aeps_created, had_enough_data
                FROM   aep_run_logs
                ORDER  BY run_at DESC
                LIMIT  1
            """)).fetchone()
            if not row:
                return None
            return {
                "run_at":          row[0],
                "brains_checked":  row[1],
                "triggers_fired":  row[2],
                "aeps_created":    row[3],
                "had_enough_data": row[4],
            }
        except Exception:
            # Table may not exist yet — return None (= never run)
            return None
        finally:
            session.close()

    def get_data_sufficiency(self) -> Dict[str, Any]:
        """
        Check how many of the 7 AEP brains have ≥15 resolved trades
        in at least one regime. Used for STATE 1 (insufficient data).

        Returns:
            {
              sufficient:      bool  — True if ≥4 brains have enough data
              brains_ready:    int   — count of brains with ≥15 resolved trades
              resolved_trades: int   — total resolved rows in DB
            }
        """
        from market_agent.brain.aep_trigger import ALL_BRAIN_NAMES
        session = self._session()
        try:
            total = session.execute(text(
                "SELECT COUNT(*) FROM signal_predictions WHERE is_resolved = true"
            )).scalar() or 0

            brains_ready = 0
            for brain in ALL_BRAIN_NAMES:
                count = session.execute(text("""
                    SELECT COUNT(*) FROM signal_predictions
                    WHERE  model_id = :brain AND is_resolved = true
                """), {"brain": brain}).scalar() or 0
                if count >= 15:
                    brains_ready += 1

            return {
                "sufficient":      brains_ready >= 4,
                "brains_ready":    brains_ready,
                "resolved_trades": total,
            }
        except Exception as e:
            log.error("aep_data_sufficiency_failed", error=str(e)[:80])
            return {"sufficient": False, "brains_ready": 0, "resolved_trades": 0}
        finally:
            session.close()

    def save_aep_run_log(self, run_info: Dict[str, Any]) -> None:
        """
        Record that the AEP runner executed. Called at the end of run_daily_aep_check().
        Writes to aep_run_logs table.
        """
        session = self._session()
        try:
            session.execute(text("""
                INSERT INTO aep_run_logs
                    (run_at, brains_checked, triggers_fired, aeps_created, had_enough_data)
                VALUES
                    (:run_at, :brains_checked, :triggers_fired, :aeps_created, :had_enough_data)
            """), {
                "run_at":          run_info.get("run_at"),
                "brains_checked":  run_info.get("brains_checked", 0),
                "triggers_fired":  run_info.get("triggers_fired", 0),
                "aeps_created":    run_info.get("aeps_created", 0),
                "had_enough_data": bool(run_info.get("had_enough_data", False)),
            })
            session.commit()
        except Exception as e:
            session.rollback()
            log.error("aep_run_log_failed", error=str(e)[:80])
        finally:
            session.close()

    def get_pending_aeps_for_dashboard(self) -> List[Dict[str, Any]]:
        """
        Returns all PENDING AEPs for dashboard display.
        Includes proposal summary + Mistral review.
        """
        session = self._session()
        try:
            rows = session.execute(text("""
                SELECT id, pr_id, proposing_brain, trigger_type,
                       problem_summary, root_cause,
                       suggested_change, change_rationale,
                       requires_retrain, council_votes,
                       mistral_review, created_at
                FROM   ai_code_proposals
                WHERE  human_decision = 'PENDING'
                ORDER  BY created_at DESC
            """)).fetchall()

            result = []
            for r in rows:
                votes = {}
                try:
                    votes = json.loads(r[9] or "{}")
                except Exception:
                    pass
                change = None
                try:
                    change = json.loads(r[6] or "null")
                except Exception:
                    pass

                result.append({
                    "id":               r[0],
                    "pr_id":            r[1],
                    "brain":            r[2],
                    "trigger_type":     r[3],
                    "problem_summary":  r[4],
                    "root_cause":       r[5],
                    "suggested_change": change,
                    "change_rationale": r[7],
                    "requires_retrain": r[8],
                    "vote_summary":     f"{votes.get('yes_count',0)}/{votes.get('total_voted',0)} brains agreed",
                    "mistral_review":   r[10],
                    "created_at":       r[11].isoformat() if r[11] else "",
                })
            return result

        except Exception as e:
            log.error("aep_dashboard_fetch_failed", error=str(e)[:100])
            return []
        finally:
            session.close()
