"""
Automated Retraining Loop (Self-Healing)
========================================
Orchestrates the weekly retraining of:
1. Causal Graph (train_causal.py)
2. RL Ensemble Agent (train_rl.py)

Usage:
    python -m market_agent.training.retrain_loop --force
    Or scheduled via tasks.json / autonomous_scout.py
"""

import os
import time
import shutil
import logging
import subprocess
from datetime import datetime
import structlog

logger = structlog.get_logger()

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models", "checkpoints")
BACKUP_DIR = os.path.join(MODELS_DIR, "backups")

def backup_current_models():
    """Backup existing models before retraining."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    
    files = ["causal_graph.json", "rl_agent_v1.pth"]
    
    for f in files:
        src = os.path.join(MODELS_DIR, f)
        if os.path.exists(src):
            dst = os.path.join(BACKUP_DIR, f"{f}_{timestamp}.bak")
            shutil.copy2(src, dst)
            print(f"  Backed up {f} to {dst}")

def run_training_script(module_name):
    """Run a training module as a subprocess."""
    print(f"\n>>> Running {module_name}...")
    try:
        # sys.executable ensures we use the same python interpreter
        result = subprocess.run(
            ["python", "-m", module_name],
            cwd=os.path.dirname(BASE_DIR), # Run from project root
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print(f"  SUCCESS: {module_name}")
            print(result.stdout[-500:]) # Print last 500 chars of output
            return True
        else:
            print(f"  FAILED: {module_name}")
            print(result.stderr)
            return False
            
    except Exception as e:
        print(f"  ERROR executing {module_name}: {e}")
        return False

def run_retraining_loop(force=False):
    print("="*60)
    print("  SELF-HEALING BRAIN LOOP")
    print("="*60)
    
    # Check schedule (Sunday or Force)
    is_sunday = datetime.now().weekday() == 6
    if not is_sunday and not force:
        print("  Skipping: Not Sunday (use --force to override)")
        return

    print("  Starting Automated Retraining...")
    
    # 1. Backup
    backup_current_models()
    
    # 2. Train Causal Graph
    causal_success = run_training_script("market_agent.training.train_causal")
    
    # 3. Train RL Agent
    rl_success = run_training_script("market_agent.training.train_rl")
    
    # 4. Summary
    print("\n" + "="*60)
    if causal_success and rl_success:
        print("  RETRAINING COMPLETE: All Brains Updated Successfully 🧠")
        logger.info("brain_retraining_complete", status="SUCCESS")
    else:
        print("  RETRAINING FAILED: Check logs above ⚠️")
        logger.error("brain_retraining_failed", causal=causal_success, rl=rl_success)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force run now")
    args = parser.parse_args()
    
    run_retraining_loop(force=args.force)
