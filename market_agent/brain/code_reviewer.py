# ============================================================
# DEPRECATED - THIS FILE IS NO LONGER ACTIVE
#
# code_reviewer.py was part of the AEP (AI Enhancement Proposal)
# self-improvement pipeline. It allowed brains to draft code
# changes via Gemini AI and submit them for peer review.
#
# STATUS: Prototype - NOT wired into live trading loop.
# RISK: AI-generated code proposals executing unsupervised in
#       a trading system is dangerous. Keeping disabled until
#       safe sandboxed review process is in place.
#
# The AEP pipeline files (aep_trigger.py, aep_storage.py,
# aep_council_vote.py, aep_generator.py, aep_reviewer.py)
# ARE still active for data collection & brain health monitoring.
# ONLY this file (code drafting) and pr_system.py (code merging)
# are disabled.
# ============================================================
pass


# """
# Phase 3.5: AI Code Reviewer

# Brains discover patterns in their own performance and propose improvements.
# Each brain can analyze its own accuracy data, find patterns, and create
# enhancement proposals (AEPs) with diagnosis, proposed fix, and test code.

# This is the DISCOVERY and DRAFT step of the AI Enhancement Proposal pipeline.
# """

# import structlog
# import json
# from typing import Dict, List, Any, Optional
# from datetime import datetime

# logger = structlog.get_logger()

# # Brain specialties (what each brain knows about)
# BRAIN_SPECIALTIES = {
#     "AMV-LSTM": {
#         "expertise": ["time-series patterns", "momentum", "trend strength"],
#         "files": ["market_agent/brain/signal_engine.py"],
#     },
#     "Cross-Stock GNN": {
#         "expertise": ["sector correlations", "cross-stock patterns", "network effects"],
#         "files": ["market_agent/brain/signal_engine.py"],
#     },
#     "RL Weighter": {
#         "expertise": ["position sizing", "risk management", "weight optimization"],
#         "files": ["market_agent/learning/calibrator.py"],
#     },
#     "Multi-Timeframe": {
#         "expertise": ["timeframe confluence", "trend alignment", "multi-resolution"],
#         "files": ["market_agent/brain/signal_engine.py"],
#     },
#     "Regime Ensemble": {
#         "expertise": ["regime detection", "regime transitions", "volatility modeling"],
#         "files": ["market_agent/brain/signal_engine.py"],
#     },
#     "Multi-Modal Fusion": {
#         "expertise": ["news integration", "sentiment", "multi-source fusion"],
#         "files": ["market_agent/brain/gemini_client.py"],
#     },
#     "Causal Ensemble": {
#         "expertise": ["causal relationships", "lead-lag", "fundamental drivers"],
#         "files": ["market_agent/brain/signal_engine.py"],
#     },
# }


# class AICodeReviewer:
#     """
#     Enables brains to discover patterns and propose code improvements.
    
#     Flow:
#     1. Brain analyzes its own accuracy by regime
#     2. Identifies weakness patterns (e.g., "I fail in VOLATILE_CHAOS")
#     3. Generates a proposal via AI: diagnosis + fix + test
#     4. Submits to PR system for peer review
#     """
    
#     def __init__(self):
#         self._gemini = None
#         self._health_monitor = None
#         self._council_memory = None
    
#     @property
#     def gemini(self):
#         if self._gemini is None:
#             try:
#                 from market_agent.brain.gemini_client import gemini_client
#                 self._gemini = gemini_client
#             except Exception:
#                 pass
#         return self._gemini
    
#     @property
#     def health_monitor(self):
#         if self._health_monitor is None:
#             try:
#                 from market_agent.brain.health_monitor import get_health_monitor
#                 self._health_monitor = get_health_monitor()
#             except Exception:
#                 pass
#         return self._health_monitor
    
#     @property
#     def council_memory(self):
#         if self._council_memory is None:
#             try:
#                 from market_agent.brain.council_memory import get_council_memory
#                 self._council_memory = get_council_memory()
#             except Exception:
#                 pass
#         return self._council_memory
    
#     def discover_improvements(self, brain_name: str = None) -> List[Dict]:
#         """
#         Step 1: Discovery — brain analyzes its own performance and finds patterns.
#         Returns list of potential improvement areas.
#         """
#         discoveries = []
#         brains = [brain_name] if brain_name else list(BRAIN_SPECIALTIES.keys())
        
#         if not self.health_monitor:
#             return discoveries
        
#         report = self.health_monitor.full_health_check()
        
#         for brain in brains:
#             brain_data = report.get("brains", {}).get(brain, {})
#             if not brain_data or brain_data.get("total_predictions", 0) < 5:
#                 continue
            
#             overall_acc = brain_data.get("overall_accuracy", 0)
#             by_regime = brain_data.get("by_regime", {})
#             gaps = brain_data.get("gaps", [])
            
#             # Pattern 1: Regime-specific weakness
#             for regime, regime_data in by_regime.items():
#                 regime_acc = regime_data.get("accuracy", 0)
#                 regime_total = regime_data.get("total", 0)
                
#                 if regime_total >= 5 and regime_acc < 45:
#                     discoveries.append({
#                         "brain": brain,
#                         "type": "REGIME_WEAKNESS",
#                         "regime": regime,
#                         "accuracy": regime_acc,
#                         "total": regime_total,
#                         "description": (
#                             f"{brain} is {regime_acc:.0f}% accurate in {regime} "
#                             f"({regime_total} predictions). "
#                             f"Needs strategy adaptation for this regime."
#                         ),
#                         "priority": "HIGH" if regime_acc < 35 else "MEDIUM"
#                     })
            
#             # Pattern 2: Overall decline
#             if brain_data.get("trend") == "DECLINING" and overall_acc > 0:
#                 discoveries.append({
#                     "brain": brain,
#                     "type": "DECLINING_TREND",
#                     "accuracy": overall_acc,
#                     "description": (
#                         f"{brain} accuracy is DECLINING (currently {overall_acc:.0f}%). "
#                         f"May need parameter recalibration or feature update."
#                     ),
#                     "priority": "HIGH"
#                 })
            
#             # Pattern 3: Training gaps
#             for gap in gaps:
#                 discoveries.append({
#                     "brain": brain,
#                     "type": "TRAINING_GAP",
#                     "regime": gap.get("regime", "Unknown"),
#                     "description": gap.get("message", "Training gap detected"),
#                     "priority": "MEDIUM"
#                 })
        
#         return discoveries
    
#     def draft_proposal(self, discovery: Dict) -> Optional[Dict]:
#         """
#         Step 2: Draft — brain creates a detailed enhancement proposal.
#         Uses AI to generate diagnosis, proposed fix, expected impact, and test code.
#         """
#         brain = discovery.get("brain", "Unknown")
#         specialty = BRAIN_SPECIALTIES.get(brain, {})
#         expertise = specialty.get("expertise", [])
#         target_files = specialty.get("files", [])
        
#         # FAISS recall of similar past proposals
#         similar_context = ""
#         if self.council_memory:
#             try:
#                 similar = self.council_memory.recall_similar(
#                     query=f"{brain} improvement proposal for {discovery.get('type', '')}",
#                     memory_type="DEBATE", k=3
#                 )
#                 if similar:
#                     similar_context = "Past similar proposals: " + "; ".join(
#                         s.get("text", "")[:100] for s in similar
#                     )
#             except Exception:
#                 pass
        
#         # Try AI-generated proposal
#         if self.gemini and self.gemini.is_available:
#             try:
#                 prompt = (
#                     f"You are {brain}, an AI trading brain specializing in {', '.join(expertise)}.\n"
#                     f"Discovery: {discovery.get('description', '')}\n"
#                     f"Type: {discovery.get('type', '')}\n"
#                     f"Target files: {', '.join(target_files)}\n"
#                     f"{similar_context}\n\n"
#                     f"Create an enhancement proposal with EXACTLY this format:\n"
#                     f"DIAGNOSIS: [What's wrong and why]\n"
#                     f"PROPOSED_FIX: [Specific code/config change]\n"
#                     f"EXPECTED_IMPACT: [Expected accuracy improvement]\n"
#                     f"Keep each section to 2-3 sentences max."
#                 )
                
#                 ai_response = self.gemini._call_ai(prompt, f"proposal_{brain}")
                
#                 if ai_response:
#                     # Parse the AI response
#                     diagnosis = self._extract_section(ai_response, "DIAGNOSIS")
#                     proposed_fix = self._extract_section(ai_response, "PROPOSED_FIX")
#                     expected_impact = self._extract_section(ai_response, "EXPECTED_IMPACT")
                    
#                     return {
#                         "brain": brain,
#                         "discovery": discovery,
#                         "diagnosis": diagnosis or ai_response[:200],
#                         "proposed_diff": proposed_fix or "See diagnosis for details",
#                         "expected_impact": expected_impact or "Improve regime-specific accuracy",
#                         "target_files": target_files,
#                         "test_code": self._generate_test_stub(brain, discovery),
#                         "timestamp": datetime.now().isoformat()
#                     }
#             except Exception:
#                 pass
        
#         # Data-only fallback
#         return {
#             "brain": brain,
#             "discovery": discovery,
#             "diagnosis": discovery.get("description", "Performance issue detected"),
#             "proposed_diff": f"Adjust {brain} parameters for {discovery.get('regime', 'current')} regime",
#             "expected_impact": f"Target: improve from {discovery.get('accuracy', 0):.0f}% accuracy",
#             "target_files": target_files,
#             "test_code": self._generate_test_stub(brain, discovery),
#             "timestamp": datetime.now().isoformat()
#         }
    
#     def _extract_section(self, text: str, section: str) -> str:
#         """Extract a labeled section from AI response."""
#         try:
#             lines = text.split('\n')
#             capture = False
#             result = []
#             for line in lines:
#                 if section + ":" in line.upper():
#                     capture = True
#                     # Get content after the label
#                     after_label = line.split(":", 1)[1].strip() if ":" in line else ""
#                     if after_label:
#                         result.append(after_label)
#                     continue
#                 if capture:
#                     if any(label in line.upper() for label in ["DIAGNOSIS:", "PROPOSED_FIX:", "EXPECTED_IMPACT:"]):
#                         break
#                     result.append(line.strip())
#             return " ".join(result).strip()
#         except Exception:
#             return ""
    
#     def _generate_test_stub(self, brain: str, discovery: Dict) -> str:
#         """Generate a basic test stub for the proposal."""
#         regime = discovery.get("regime", "UNKNOWN")
#         acc = discovery.get("accuracy", 0)
        
#         return (
#             f"def test_{brain.lower().replace(' ', '_').replace('-', '_')}_regime_{regime.lower()}():\n"
#             f"    '''Test that {brain} accuracy improves in {regime} regime after fix.'''\n"
#             f"    from market_agent.brain.health_monitor import get_health_monitor\n"
#             f"    monitor = get_health_monitor()\n"
#             f"    report = monitor.full_health_check()\n"
#             f"    brain_data = report['brains'].get('{brain}', {{}})\n"
#             f"    regime_data = brain_data.get('by_regime', {{}}).get('{regime}', {{}})\n"
#             f"    accuracy = regime_data.get('accuracy', 0)\n"
#             f"    assert accuracy > {acc + 5}, f'Expected >{acc + 5}%, got {{accuracy}}%'\n"
#         )
    
#     def run_discovery_cycle(self) -> List[Dict]:
#         """
#         Full discovery cycle: find issues across all brains and draft proposals.
#         Returns list of ready-to-submit proposals.
#         """
#         discoveries = self.discover_improvements()
        
#         if not discoveries:
#             return []
        
#         # Sort by priority
#         priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
#         discoveries.sort(key=lambda d: priority_order.get(d.get("priority", "LOW"), 2))
        
#         # Draft proposals for top 3 discoveries
#         proposals = []
#         for discovery in discoveries[:3]:
#             proposal = self.draft_proposal(discovery)
#             if proposal:
#                 proposals.append(proposal)
        
#         return proposals


# # Singleton
# _reviewer = None

# def get_code_reviewer() -> AICodeReviewer:
#     global _reviewer
#     if _reviewer is None:
#         _reviewer = AICo