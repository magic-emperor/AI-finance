import structlog
from datetime import datetime
from sqlalchemy import text
from market_agent.data.storage.postgres import PostgresStorage

logger = structlog.get_logger()

def _check_verdict_outcome(verdict, storage: PostgresStorage) -> dict:
    """
    Check if a council verdict Hit T1, Hit SL, or Expired.
    Returns None if still open.
    """
    session = storage.Session()
    try:
        rows = session.execute(text("""
            SELECT timestamp, data->>'High' as high, data->>'Low' as low, data->>'Close' as close
            FROM market_data
            WHERE symbol = :symbol
            AND timeframe IN ('1m', '5m', '1h')
            AND timestamp >= :start
            ORDER BY timestamp ASC
        """), {"symbol": verdict.symbol, "start": verdict.created_at}).fetchall()
        
        if not rows:
            return None
            
        entry = verdict.entry_price
        target = verdict.target_1
        sl = verdict.stop_loss
        direction = verdict.direction
        
        if entry is None or target is None or sl is None:
            return None
            
        candles_held = 0
        for r in rows:
            candles_held += 1
            high = float(r.high) if r.high else 0
            low = float(r.low) if r.low else 0
            close = float(r.close) if r.close else 0
            
            if high == 0 or low == 0: continue
            
            hit_target = False
            hit_sl = False
            
            if direction == "BUY":
                if low <= sl: hit_sl = True
                elif high >= target: hit_target = True
            elif direction == "SELL":
                if high >= sl: hit_sl = True
                elif low <= target: hit_target = True
                
            if hit_target:
                pnl = abs((target - entry) / entry * 100)
                return {'outcome': 'TARGET', 'exit_price': target, 'pnl_pct': pnl, 'holding_candles': candles_held}
            if hit_sl:
                pnl = -abs((sl - entry) / entry * 100)
                return {'outcome': 'SL', 'exit_price': sl, 'pnl_pct': pnl, 'holding_candles': candles_held}
                
        # Check expiry
        timeframe_min = verdict.timeframe_min or 15
        elapsed_mins = (datetime.utcnow() - verdict.created_at).total_seconds() / 60.0
        if elapsed_mins >= timeframe_min:
            final_close = float(rows[-1].close) if rows else entry
            pnl = ((final_close - entry) / entry * 100) if direction == "BUY" else ((entry - final_close) / entry * 100)
            return {'outcome': 'EXPIRED', 'exit_price': final_close, 'pnl_pct': pnl, 'holding_candles': candles_held}
            
        return None
    except Exception as e:
        logger.error("check_verdict_failed", error=str(e))
        return None
    finally:
        session.close()

def resolve_verdict(verdict_id: int, outcome: str, actual_direction: str, storage: PostgresStorage) -> None:
    """Gap 5: Update all individual brain predictions tied to this verdict."""
    session = storage.Session()
    try:
        from market_agent.data.storage.postgres import CouncilVerdict, BrainPrediction
        v = session.query(CouncilVerdict).get(verdict_id)
        if not v or not v.council_session_id:
            return
            
        brain_preds = session.query(BrainPrediction).filter(
            BrainPrediction.council_session_id == v.council_session_id
        ).all()
        
        for pred in brain_preds:
            was_correct = (pred.direction == actual_direction)
            if pred.direction == "HOLD" and actual_direction in ("FLAT", "HOLD"):
                was_correct = True
                
            pred.outcome = outcome
            pred.actual_direction = actual_direction
            pred.was_correct = was_correct
            pred.resolved_at = datetime.utcnow()
            
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error('resolve_verdict_failed', error=str(e))
    finally:
        session.close()

def resolve_signals(storage: PostgresStorage) -> int:
    """
    Gap 6: Resolve open verdicts with row-level locking.
    SKIP LOCKED prevents race conditions.
    """
    session = storage.Session()
    resolved = 0

    try:
        # Gap 6 Database-level lock
        open_verdicts = session.execute(text("""
            SELECT id, symbol, direction, entry_price, target_1, stop_loss, created_at, timeframe_min
            FROM council_verdicts
            WHERE outcome IS NULL
            AND created_at < NOW() - INTERVAL '5 minutes'
            ORDER BY created_at ASC
            LIMIT 50
            FOR UPDATE SKIP LOCKED
        """)).fetchall()

        for verdict in open_verdicts:
            try:
                outcome_data = _check_verdict_outcome(verdict, storage)
                if outcome_data:
                    storage.record_verdict_outcome(
                        verdict_id=verdict.id,
                        exit_price=outcome_data['exit_price'],
                        outcome=outcome_data['outcome'],
                        pnl_pct=outcome_data['pnl_pct'],
                        exit_ts=datetime.utcnow(),
                        holding_candles=outcome_data['holding_candles'],
                    )
                    
                    actual_dir = "FLAT"
                    if outcome_data['exit_price'] > verdict.entry_price:
                        actual_dir = "BUY"
                    elif outcome_data['exit_price'] < verdict.entry_price:
                        actual_dir = "SELL"
                        
                    resolve_verdict(verdict.id, outcome_data['outcome'], actual_dir, storage)
                    resolved += 1
            except Exception as e:
                logger.error('resolve_single_verdict_failed', verdict_id=verdict.id, error=str(e))

        session.commit()

    except Exception as e:
        session.rollback()
        logger.error('resolve_signals_failed', error=str(e))
    finally:
        session.close()

    return resolved
