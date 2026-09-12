# ============================================================
# DEPRECATED - THIS FILE IS NO LONGER ACTIVE
#
# pr_system.py was the AEP (AI Enhancement Proposal) code merge
# pipeline: submit -> peer review -> consensus -> test -> user approval.
#
# STATUS: Prototype - NOT wired into live trading loop.
# RISK: Auto-merging AI-generated code into a live trading system
#       without proper sandboxing is dangerous. Disabled until safe.
#
# The monitoring side of AEP (aep_trigger.py, aep_storage.py,
# aep_council_vote.py, aep_generator.py, aep_reviewer.py) IS active.
# Only the code-drafting (code_reviewer.py) and code-merging (this
# file) are disabled.
# ============================================================
pass


# """
# Phase 3.5: AI Enhancement Proposal (AEP) System

# Manages the full lifecycle of brain-proposed improvements:
# 1. Submit — Brain submits proposal from code_reviewer
# 2. Peer Review — Other brains vote via AI
# 3. Consensus — 4/7 agree → AI_APPROVED
# 4. Test — Run pytest on brain's test code
# 5. User Review — Dashboard shows proposal for human approval
# 6. Apply & Monitor — Track accuracy pre/post change
# 7. Rollback — Auto-flag if accuracy drops >10%

# Merge conflict prevention: checks registry of pending proposals
# before allowing new ones targeting the same file.
# """

# import structlog
# import json
# import subprocess
# import tempfile
# import os
# from typing import Dict, List, Any, Optional
# from datetime import datetime

# logger = structlog.get_logger()

# # Consensus threshold
# APPROVAL_THRESHOLD = 4  # 4/7 brains must agree
# BRAINS = [
#     "AMV-LSTM", "Cross-Stock GNN", "RL Weighter",
#     "Multi-Timeframe", "Regime Ensemble", "Multi-Modal Fusion",
#     "Causal Ensemble",
# ]


# class PRSystem:
#     """
#     Manages AI Enhancement Proposals (AEPs) through the full review pipeline.
#     """
    
#     def __init__(self, storage=None):
#         self._storage = storage
#         self._gemini = None
#         self._next_pr_id = None
    
#     @property
#     def storage(self):
#         if self._storage is None:
#             try:
#                 from market_agent.data.storage.postgres import PostgresStorage
#                 self._storage = PostgresStorage()
#             except Exception:
#                 pass
#         return self._storage
    
#     @property
#     def gemini(self):
#         if self._gemini is None:
#             try:
#                 from market_agent.brain.gemini_client import gemini_client
#                 self._gemini = gemini_client
#             except Exception:
#                 pass
#         return self._gemini
    
#     # ═══════════════════════════════════════
#     # STEP 1: SUBMIT PROPOSAL
#     # ═══════════════════════════════════════
    
#     def submit_proposal(self, proposal: Dict) -> Optional[str]:
#         """
#         Submit a new enhancement proposal to the system.
#         Returns pr_id or None if rejected (conflict/duplicate).
#         """
#         brain = proposal.get("brain", "Unknown")
#         target_files = proposal.get("target_files", [])
        
#         # Check for conflicts with pending proposals
#         if self._has_conflict(target_files):
#             logger.info("proposal_conflict", brain=brain,
#                          files=target_files)
#             return None
        
#         # Generate PR ID
#         pr_id = self._generate_pr_id()
        
#         if not self.storage:
#             return pr_id  # Return ID but skip DB storage
        
#         try:
#             from market_agent.data.storage.postgres import AICodeProposal
#             session = self.storage.Session()
            
#             record = AICodeProposal(
#                 pr_id=pr_id,
#                 proposing_brain=brain,
#                 file_path=json.dumps(target_files),
#                 diagnosis=proposal.get("diagnosis", ""),
#                 proposed_diff=proposal.get("proposed_diff", ""),
#                 expected_impact=proposal.get("expected_impact", ""),
#                 test_code=proposal.get("test_code", ""),
#                 test_result="PENDING",
#                 peer_reviews_json="[]",
#                 consensus="PENDING",
#                 user_status="PENDING",
#             )
#             session.add(record)
#             session.commit()
#             session.close()
            
#             logger.info("proposal_submitted", pr_id=pr_id, brain=brain)
#             return pr_id
            
#         except Exception as e:
#             logger.debug("proposal_submit_failed", error=str(e)[:60])
#             return pr_id
    
#     # ═══════════════════════════════════════
#     # STEP 2: PEER REVIEW
#     # ═══════════════════════════════════════
    
#     def conduct_peer_review(self, pr_id: str) -> List[Dict]:
#         """
#         Each brain (except proposer) reviews the proposal via AI.
#         Returns list of reviews: [{brain, vote, reasoning}]
#         """
#         proposal = self._get_proposal(pr_id)
#         if not proposal:
#             return []
        
#         proposer = proposal.get("proposing_brain", "Unknown")
#         reviewers = [b for b in BRAINS if b != proposer]
#         reviews = []
        
#         for reviewer in reviewers:
#             review = self._get_brain_review(reviewer, proposal)
#             reviews.append(review)
        
#         # Store reviews
#         self._update_reviews(pr_id, reviews)
        
#         return reviews
    
#     def _get_brain_review(self, reviewer: str, proposal: Dict) -> Dict:
#         """Get a single brain's review via AI."""
#         if self.gemini and self.gemini.is_available:
#             try:
#                 prompt = (
#                     f"You are {reviewer}, reviewing an enhancement proposal.\n"
#                     f"Proposer: {proposal.get('proposing_brain', 'Unknown')}\n"
#                     f"Diagnosis: {proposal.get('diagnosis', '')[:200]}\n"
#                     f"Proposed Fix: {proposal.get('proposed_diff', '')[:200]}\n"
#                     f"Expected Impact: {proposal.get('expected_impact', '')[:100]}\n\n"
#                     f"Vote APPROVE or REJECT with 1-2 sentence reasoning.\n"
#                     f"Format: VOTE: [APPROVE/REJECT] REASON: [your reasoning]"
#                 )
                
#                 response = self.gemini._call_ai(prompt, f"review_{reviewer}_{proposal.get('pr_id', '')}")
                
#                 if response:
#                     vote = "APPROVE" if "APPROVE" in response.upper() else "REJECT"
#                     return {
#                         "brain": reviewer,
#                         "vote": vote,
#                         "reasoning": response[:300]
#                     }
#             except Exception:
#                 pass
        
#         # Fallback: abstain
#         return {
#             "brain": reviewer,
#             "vote": "ABSTAIN",
#             "reasoning": "AI unavailable — abstaining from review"
#         }
    
#     # ═══════════════════════════════════════
#     # STEP 3: CONSENSUS CHECK
#     # ═══════════════════════════════════════
    
#     def check_consensus(self, pr_id: str) -> str:
#         """
#         Check if proposal has enough approval votes.
#         Returns: AI_APPROVED, REJECTED, NEEDS_DISCUSSION
#         """
#         proposal = self._get_proposal(pr_id)
#         if not proposal:
#             return "UNKNOWN"
        
#         reviews = json.loads(proposal.get("peer_reviews_json", "[]"))
#         if not isinstance(reviews, list):
#             reviews = []
        
#         approvals = sum(1 for r in reviews if r.get("vote") == "APPROVE")
#         rejections = sum(1 for r in reviews if r.get("vote") == "REJECT")
        
#         if approvals >= APPROVAL_THRESHOLD:
#             consensus = "AI_APPROVED"
#         elif rejections >= APPROVAL_THRESHOLD:
#             consensus = "REJECTED"
#         else:
#             consensus = "NEEDS_DISCUSSION"
        
#         self._update_consensus(pr_id, consensus)
#         return consensus
    
#     # ═══════════════════════════════════════
#     # STEP 4: TEST EXECUTION
#     # ═══════════════════════════════════════
    
#     def run_tests(self, pr_id: str) -> Dict[str, Any]:
#         """
#         Run the brain's proposed test code via pytest.
#         Returns: {result: PASSED/FAILED, output: ...}
#         """
#         proposal = self._get_proposal(pr_id)
#         if not proposal:
#             return {"result": "SKIPPED", "output": "Proposal not found"}
        
#         test_code = proposal.get("test_code", "")
#         if not test_code:
#             return {"result": "SKIPPED", "output": "No test code provided"}
        
#         # Write test to temp file and run
#         try:
#             with tempfile.NamedTemporaryFile(
#                 mode='w', suffix='_test.py', 
#                 dir=tempfile.gettempdir(),
#                 delete=False, encoding='utf-8'
#             ) as f:
#                 f.write(test_code)
#                 test_file = f.name
            
#             result = subprocess.run(
#                 ["python", "-m", "pytest", test_file, "-v", "--timeout=30"],
#                 capture_output=True, text=True, timeout=60,
#                 cwd=os.path.dirname(os.path.dirname(os.path.dirname(
#                     os.path.abspath(__file__)
#                 )))
#             )
            
#             test_result = "PASSED" if result.returncode == 0 else "FAILED"
#             output = result.stdout[-500:] if result.stdout else result.stderr[-500:]
            
#             # Clean up
#             try:
#                 os.unlink(test_file)
#             except Exception:
#                 pass
            
#             self._update_test_result(pr_id, test_result)
            
#             return {"result": test_result, "output": output}
            
#         except subprocess.TimeoutExpired:
#             self._update_test_result(pr_id, "FAILED")
#             return {"result": "FAILED", "output": "Test timed out after 60s"}
#         except Exception as e:
#             return {"result": "FAILED", "output": str(e)[:200]}
    
#     # ═══════════════════════════════════════
#     # STEP 5: USER APPROVAL
#     # ═══════════════════════════════════════
    
#     def get_pending_proposals(self) -> List[Dict]:
#         """Get all proposals waiting for user review."""
#         if not self.storage:
#             return []
        
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
            
#             rows = session.execute(text("""
#                 SELECT pr_id, proposing_brain, diagnosis, proposed_diff,
#                        expected_impact, test_result, consensus, user_status,
#                        peer_reviews_json, created_at
#                 FROM ai_code_proposals
#                 WHERE user_status = 'PENDING'
#                 ORDER BY created_at DESC
#                 LIMIT 20
#             """)).fetchall()
            
#             session.close()
            
#             proposals = []
#             for row in rows:
#                 proposals.append({
#                     "pr_id": row[0],
#                     "proposing_brain": row[1],
#                     "diagnosis": row[2],
#                     "proposed_diff": row[3],
#                     "expected_impact": row[4],
#                     "test_result": row[5],
#                     "consensus": row[6],
#                     "user_status": row[7],
#                     "peer_reviews": json.loads(row[8]) if row[8] else [],
#                     "created_at": row[9].isoformat() if row[9] else None,
#                 })
            
#             return proposals
#         except Exception:
#             return []
    
#     def user_approve(self, pr_id: str) -> bool:
#         """User approves a proposal."""
#         return self._set_user_status(pr_id, "APPROVED")
    
#     def user_reject(self, pr_id: str) -> bool:
#         """User rejects a proposal."""
#         return self._set_user_status(pr_id, "REJECTED")
    
#     # ═══════════════════════════════════════
#     # STEP 6: ACCURACY MONITORING
#     # ═══════════════════════════════════════
    
#     def record_accuracy_snapshot(self, pr_id: str, phase: str = "before"):
#         """
#         Record accuracy before/after applying a proposal.
#         phase: 'before' or 'after'
#         """
#         if not self.storage:
#             return
        
#         try:
#             from market_agent.brain.health_monitor import get_health_monitor
#             monitor = get_health_monitor()
#             report = monitor.full_health_check()
#             system_accuracy = report.get("system_accuracy", 0)
            
#             from sqlalchemy import text
#             session = self.storage.Session()
            
#             if phase == "before":
#                 session.execute(text("""
#                     UPDATE ai_code_proposals
#                     SET accuracy_before = :acc
#                     WHERE pr_id = :pr_id
#                 """), {"acc": system_accuracy, "pr_id": pr_id})
#             else:
#                 session.execute(text("""
#                     UPDATE ai_code_proposals
#                     SET accuracy_after = :acc, applied_at = NOW()
#                     WHERE pr_id = :pr_id
#                 """), {"acc": system_accuracy, "pr_id": pr_id})
            
#             session.commit()
#             session.close()
#         except Exception:
#             pass
    
#     def check_for_rollback(self) -> List[Dict]:
#         """
#         Check if any applied proposals caused >10% accuracy drop.
#         Returns list of proposals that should be flagged for rollback.
#         """
#         if not self.storage:
#             return []
        
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
            
#             rows = session.execute(text("""
#                 SELECT pr_id, proposing_brain, accuracy_before, accuracy_after,
#                        diagnosis
#                 FROM ai_code_proposals
#                 WHERE user_status = 'APPROVED'
#                   AND accuracy_before IS NOT NULL
#                   AND accuracy_after IS NOT NULL
#                   AND (accuracy_before - accuracy_after) > 10
#             """)).fetchall()
            
#             session.close()
            
#             rollbacks = []
#             for row in rows:
#                 rollbacks.append({
#                     "pr_id": row[0],
#                     "brain": row[1],
#                     "accuracy_before": row[2],
#                     "accuracy_after": row[3],
#                     "drop": row[2] - row[3],
#                     "diagnosis": row[4],
#                 })
            
#             return rollbacks
#         except Exception:
#             return []
    
#     # ═══════════════════════════════════════
#     # FULL PIPELINE
#     # ═══════════════════════════════════════
    
#     def run_full_pipeline(self, proposal: Dict) -> Dict[str, Any]:
#         """
#         Run the full AEP pipeline: submit → review → consensus → test.
#         Returns status at each stage.
#         """
#         result = {
#             "pr_id": None,
#             "peer_review": [],
#             "consensus": "PENDING",
#             "test_result": "PENDING",
#             "status": "SUBMITTED"
#         }
        
#         # Step 1: Submit
#         pr_id = self.submit_proposal(proposal)
#         if not pr_id:
#             result["status"] = "CONFLICT"
#             return result
#         result["pr_id"] = pr_id
        
#         # Step 2: Peer review
#         reviews = self.conduct_peer_review(pr_id)
#         result["peer_review"] = reviews
        
#         # Step 3: Consensus
#         consensus = self.check_consensus(pr_id)
#         result["consensus"] = consensus
        
#         if consensus == "REJECTED":
#             result["status"] = "REJECTED_BY_PEERS"
#             return result
        
#         # Step 4: Test (only if AI-approved)
#         if consensus == "AI_APPROVED":
#             test = self.run_tests(pr_id)
#             result["test_result"] = test.get("result", "FAILED")
            
#             if test.get("result") == "FAILED":
#                 result["status"] = "TEST_FAILED"
#                 return result
        
#         result["status"] = "AWAITING_USER_REVIEW"
#         return result
    
#     # ═══════════════════════════════════════
#     # HELPERS
#     # ═══════════════════════════════════════
    
#     def _has_conflict(self, target_files: List[str]) -> bool:
#         """Check if any pending proposal targets the same files."""
#         if not self.storage or not target_files:
#             return False
        
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
            
#             rows = session.execute(text("""
#                 SELECT file_path FROM ai_code_proposals
#                 WHERE user_status = 'PENDING' AND consensus != 'REJECTED'
#             """)).fetchall()
            
#             session.close()
            
#             for row in rows:
#                 existing_files = json.loads(row[0]) if row[0] else []
#                 if set(target_files) & set(existing_files):
#                     return True
            
#             return False
#         except Exception:
#             return False
    
#     def _generate_pr_id(self) -> str:
#         """Generate next AEP ID."""
#         if not self.storage:
#             return f"AEP-{datetime.now().strftime('%H%M%S')}"
        
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
            
#             result = session.execute(text(
#                 "SELECT COUNT(*) FROM ai_code_proposals"
#             )).scalar()
            
#             session.close()
#             return f"AEP-{(result or 0) + 1:03d}"
#         except Exception:
#             return f"AEP-{datetime.now().strftime('%H%M%S')}"
    
#     def _get_proposal(self, pr_id: str) -> Optional[Dict]:
#         """Get proposal from DB."""
#         if not self.storage:
#             return None
        
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
            
#             row = session.execute(text("""
#                 SELECT pr_id, proposing_brain, file_path, diagnosis,
#                        proposed_diff, expected_impact, test_code,
#                        test_result, peer_reviews_json, consensus,
#                        user_status
#                 FROM ai_code_proposals WHERE pr_id = :pr_id
#             """), {"pr_id": pr_id}).fetchone()
            
#             session.close()
            
#             if not row:
#                 return None
            
#             return {
#                 "pr_id": row[0], "proposing_brain": row[1],
#                 "file_path": row[2], "diagnosis": row[3],
#                 "proposed_diff": row[4], "expected_impact": row[5],
#                 "test_code": row[6], "test_result": row[7],
#                 "peer_reviews_json": row[8], "consensus": row[9],
#                 "user_status": row[10],
#             }
#         except Exception:
#             return None
    
#     def _update_reviews(self, pr_id: str, reviews: List[Dict]):
#         """Store peer reviews."""
#         if not self.storage:
#             return
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
#             session.execute(text("""
#                 UPDATE ai_code_proposals
#                 SET peer_reviews_json = :reviews
#                 WHERE pr_id = :pr_id
#             """), {"reviews": json.dumps(reviews), "pr_id": pr_id})
#             session.commit()
#             session.close()
#         except Exception:
#             pass
    
#     def _update_consensus(self, pr_id: str, consensus: str):
#         """Update consensus status."""
#         if not self.storage:
#             return
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
#             session.execute(text("""
#                 UPDATE ai_code_proposals SET consensus = :c WHERE pr_id = :pr_id
#             """), {"c": consensus, "pr_id": pr_id})
#             session.commit()
#             session.close()
#         except Exception:
#             pass
    
#     def _update_test_result(self, pr_id: str, result: str):
#         """Update test result."""
#         if not self.storage:
#             return
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
#             session.execute(text("""
#                 UPDATE ai_code_proposals SET test_result = :r WHERE pr_id = :pr_id
#             """), {"r": result, "pr_id": pr_id})
#             session.commit()
#             session.close()
#         except Exception:
#             pass
    
#     def _set_user_status(self, pr_id: str, status: str) -> bool:
#         """Set user approval status."""
#         if not self.storage:
#             return False
#         try:
#             from sqlalchemy import text
#             session = self.storage.Session()
#             session.execute(text("""
#                 UPDATE ai_code_proposals
#                 SET user_status = :s
#                 WHERE pr_id = :pr_id
#             """), {"s": status, "pr_id": pr_id})
#             session.commit()
#             session.close()
#             return True
#         except Exception:
#             return False


# # Singleton
# _pr_system = None

# def get_pr_system(storage=None) -> PRSystem:
#     global _pr_system
#     if _pr_system is None:
#         _pr_system = PRSystem(storage=storage)
#     return _pr_system