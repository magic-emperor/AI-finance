"""
brain_logger.py — Per IMPL-PLAN-V3 Fix 6
Structured JSONL logger for every brain council cycle.
Records: timestamp, symbol, regime, each brain's vote + confidence,
         Boss Brain final verdict + adjusted confidence, agreement_factor.

Usage (wired into cortex.py conduct_grand_council):
    from market_agent.brain.brain_logger import log_cycle
    log_cycle(symbol, regime, brain_positions, verdict, verdict_confidence)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import structlog

logger = structlog.get_logger()

# ── Log file location ─────────────────────────────────────────────────────────
_LOG_DIR  = Path(__file__).parent.parent.parent / "logs" / "brain_decisions"
_LOG_FILE = _LOG_DIR / "decisions.jsonl"


def _ensure_log_dir() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_cycle(
    symbol:             str,
    regime:             str,
    brain_positions:    List[Dict[str, Any]],
    verdict:            str,
    verdict_confidence: float,
    debate_duration_sec: float = 0.0,
    council_session_id: Optional[str] = None,
) -> None:
    """
    Append one JSON line to logs/brain_decisions/decisions.jsonl.
    Each line is a complete, standalone record of one council cycle.
    Non-blocking — any error is logged but never propagated.
    """
    try:
        _ensure_log_dir()

        brains_summary = []
        for bp in brain_positions:
            brains_summary.append({
                "brain":      bp.get("brain", "?"),
                "direction":  bp.get("position", "HOLD"),
                "confidence": round(float(bp.get("confidence", 0.5)), 3),
            })

        # Compute agreement_rate for the log record
        total = len(brains_summary)
        agreers = sum(1 for b in brains_summary if b["direction"] == verdict)
        agreement_rate = round(agreers / total, 3) if total > 0 else 0.0

        record = {
            "ts":                  datetime.utcnow().isoformat() + "Z",
            "symbol":              symbol,
            "regime":              regime,
            "verdict":             verdict,
            "verdict_confidence":  round(float(verdict_confidence), 3),
            "agreement_rate":      agreement_rate,
            "brains":              brains_summary,
            "debate_sec":          round(debate_duration_sec, 1),
            "session_id":          council_session_id or "",
        }

        with open(_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug(
            "brain_cycle_logged",
            symbol=symbol, regime=regime,
            verdict=verdict, conf=record["verdict_confidence"],
            agreement=agreement_rate,
        )

    except Exception as e:
        logger.debug("brain_logger_failed", error=str(e)[:80])


def get_recent_cycles(n: int = 20) -> List[Dict[str, Any]]:
    """Read last N cycle records from the JSONL log. Returns [] if log not found."""
    try:
        if not _LOG_FILE.exists():
            return []
        lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()
        recent = lines[-n:]
        return [json.loads(ln) for ln in recent if ln.strip()]
    except Exception as e:
        logger.debug("brain_logger_read_failed", error=str(e)[:80])
        return []
