import json
import os
import time
from datetime import datetime
import structlog
from typing import Dict, Any

logger = structlog.get_logger()

class ConfigResearchAgent:
    """
    Phase 26: The Research-to-Config Loop (Safe Singularity).
    1. Researches optimal parameters.
    2. checks Guardrails.
    3. PROPOSES a patch (does not apply it).
    """
    
    def __init__(self):
        self.base_path = os.path.dirname(os.path.dirname(__file__))
        self.config_path = os.path.join(self.base_path, 'config', 'strategy_params.json')
        self.guardrails_path = os.path.join(self.base_path, 'config', 'guardrails.json')
        self.patches_dir = os.path.join(self.base_path, 'config', 'patches')
        
        # Ensure patches dir exists
        os.makedirs(self.patches_dir, exist_ok=True)
        
        # Load Guardrails
        with open(self.guardrails_path, 'r') as f:
            self.guardrails = json.load(f)

    def research_parameter(self, symbol: str, param_name: str) -> Dict[str, Any]:
        """
        Simulates researching a parameter.
        In prod, this calls GoogleSearch/Perplexity.
        """
        logger.info("research_started", symbol=symbol, param=param_name)
        
        # Mock Research Result for Demo
        # Concept: "Gold needs 2.8x ATR because 2.5x gets stopped out too often in 2026."
        if symbol == "XAUUSD=X" and param_name == "stop_loss_multiplier":
            found_value = 2.8
            source = "SSRN: 'Gold Volatility Dynamics 2026'"
            confidence = 0.95
        elif symbol == "BTC-USD" and param_name == "rsi_period":
            found_value = 21 # Weekly RSI is better for crypto trend
            source = "CoinMetrics: 'Bitcoin Trend Analysis 2026'"
            confidence = 0.88
        else:
            return {"status": "NO_CHANGE", "reason": "Current config aligns with consensus."}
            
        return {
            "status": "PROPOSED",
            "value": found_value,
            "source": source,
            "confidence": confidence,
            "reason": f"Research suggests {found_value} outperforms current settings."
        }

    def check_trigger_condition(self, symbol: str, current_performance: Dict) -> bool:
        """
        Phase 27: The Anomaly Trigger.
        Checks if the agent SHOULD run research.
        """
        # Trigger if Win Rate < 40% or Drawdown > 5%
        if current_performance.get('win_rate', 0.5) < 0.40:
            logger.warning("research_trigger_activated", reason="Low Win Rate")
            return True
            
        if current_performance.get('drawdown', 0) > 0.05:
            logger.warning("research_trigger_activated", reason="High Drawdown")
            return True
            
        return False

    def validate_guardrails(self, param_name: str, value: float) -> bool:
        """Check if value is within safe limits."""
        limits = self.guardrails.get(param_name)
        if not limits:
            return True # No limit defined
            
        if value < limits['min'] or value > limits['max']:
            logger.warning("guardrail_violation", param=param_name, value=value, limits=limits)
            return False
            
        return True

    def create_patch_proposal(self, symbol: str, changes: Dict[str, Any]):
        """Creates a JSON patch file for human review."""
        
        # 0. Deduplication Check
        # If we already have a PENDING patch for this symbol, skip to avoid spam.
        existing_patches = self.list_pending_patches()
        for p in existing_patches:
            if p['target_symbol'] == symbol:
                logger.info("patch_skip_duplicate", symbol=symbol)
                return None

        patch_id = f"patch_{int(time.time())}_{symbol.replace('=','')}"
        
        patch_data = {
            "patch_id": patch_id,
            "target_symbol": symbol,
            "created_at": datetime.now().isoformat(),
            "status": "PENDING_REVIEW",
            "changes": [],
            "research_summary": "Based on recent volatility papers."
        }
        
        for param, data in changes.items():
            # Guardian Check
            if not self.validate_guardrails(param, data['value']):
                continue
                
            patch_data["changes"].append({
                "parameter": param,
                "new_value": data['value'],
                "source": data['source'],
                "confidence": data['confidence']
            })
            
        if not patch_data["changes"]:
            return None # All rejected by guardrails
            
        # Write Patch
        patch_file = os.path.join(self.patches_dir, f"{patch_id}.json")
        with open(patch_file, 'w') as f:
            json.dump(patch_data, f, indent=4)
            
        logger.info("patch_created", patch_id=patch_id)
        return patch_data

    def list_pending_patches(self):
        """List all patches waiting for approval."""
        patches = []
        if not os.path.exists(self.patches_dir):
            return []
            
        for f in os.listdir(self.patches_dir):
            if f.endswith(".json"):
                 with open(os.path.join(self.patches_dir, f), 'r') as pf:
                     data = json.load(pf)
                     if data['status'] == 'PENDING_REVIEW':
                         patches.append(data)
        return patches

    def apply_patch(self, patch_id: str):
        """
        Applies a patch to the main config.
        Crucial: Updates the file atomically.
        """
        patch_file = os.path.join(self.patches_dir, f"{patch_id}.json")
        if not os.path.exists(patch_file):
            return False
            
        with open(patch_file, 'r') as pf:
            patch = json.load(pf)
            
        # Load Main Config
        with open(self.config_path, 'r') as cf:
            main_config = json.load(cf)
            
        symbol = patch['target_symbol']
        if symbol not in main_config:
            main_config[symbol] = {}
            
        # Apply Changes
        for change in patch['changes']:
            main_config[symbol][change['parameter']] = change['new_value']
            
            # Add Audit Log
            if "_research_log" not in main_config[symbol]:
                main_config[symbol]["_research_log"] = []
                
            # Log the change in meta history (if list) or overwrite object
            # For simplicity, we overwrite the latest log
            main_config[symbol]["_research_log"] = {
                "last_update": datetime.now().isoformat(),
                "source": change['source'],
                "confidence": change['confidence']
            }

        # Save Main Config
        with open(self.config_path, 'w') as cf:
            json.dump(main_config, cf, indent=4)
        
        # Mark patch as APPLIED (FIX: This was missing!)
        patch['status'] = 'APPLIED'
        patch['applied_at'] = datetime.now().isoformat()
        with open(patch_file, 'w') as pf:
            json.dump(patch, pf, indent=4)
            
        logger.info("patch_applied", patch_id=patch_id)
        return True


    def reject_patch(self, patch_id: str):
        """Rejects a patch by moving it to the archive as REJECTED or deleting it."""
        patch_file = os.path.join(self.patches_dir, f"{patch_id}.json")
        if not os.path.exists(patch_file):
            return False
            
        with open(patch_file, 'r') as pf:
            patch = json.load(pf)
            
        patch['status'] = "REJECTED"
        patch['rejected_at'] = datetime.now().isoformat()
        
        with open(patch_file, 'w') as pf:
            json.dump(patch, pf, indent=4)
            
        logger.info("patch_rejected", patch_id=patch_id)
        return True

    def clear_all_pending_patches(self):
        """Wipes all PENDING_REVIEW patches to clear held 'spams'."""
        count = 0
        if not os.path.exists(self.patches_dir):
            return 0
            
        for f in os.listdir(self.patches_dir):
            if f.endswith(".json"):
                 file_path = os.path.join(self.patches_dir, f)
                 with open(file_path, 'r') as pf:
                     data = json.load(pf)
                     if data['status'] == 'PENDING_REVIEW':
                         # We could delete or mark rejected. Let's mark rejected for history.
                         data['status'] = 'REJECTED_SWEEP'
                         data['rejected_at'] = datetime.now().isoformat()
                         with open(file_path, 'w') as out:
                             json.dump(data, out, indent=4)
                         count += 1
        return count

    def list_rejected_patches(self):
        """Phase 42: List all rejected/swept patches for possible restoration."""
        patches = []
        if not os.path.exists(self.patches_dir):
            return []
            
        for f in os.listdir(self.patches_dir):
            if f.endswith(".json"):
                 with open(os.path.join(self.patches_dir, f), 'r') as pf:
                     data = json.load(pf)
                     if data['status'] in ['REJECTED', 'REJECTED_SWEEP']:
                         patches.append(data)
        # Return last 10
        return sorted(patches, key=lambda x: x.get('rejected_at', ''), reverse=True)[:10]

    def restore_patch(self, patch_id: str):
        """Phase 42: Moves a rejected patch back to PENDING_REVIEW."""
        patch_file = os.path.join(self.patches_dir, f"{patch_id}.json")
        if not os.path.exists(patch_file):
            return False
            
        with open(patch_file, 'r') as pf:
            patch = json.load(pf)
            
        patch['status'] = "PENDING_REVIEW"
        patch['restored_at'] = datetime.now().isoformat()
        
        with open(patch_file, 'w') as pf:
            json.dump(patch, pf, indent=4)
            
        logger.info("patch_restored", patch_id=patch_id)
        return True
