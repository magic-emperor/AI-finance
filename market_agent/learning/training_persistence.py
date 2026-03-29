"""
Training Persistence — Database models for brain weights, training decisions, and pattern tracking.

Persists:
- Training approval decisions (so they survive page refresh)
- Brain weights (RL-updated, not just session memory)
- Training run history with before/after accuracy metrics
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional
import structlog

logger = structlog.get_logger()

# SQLAlchemy models
try:
    from sqlalchemy import (
        Column, String, Float, Integer, Boolean, DateTime, Text,
        create_engine, JSON
    )
    from sqlalchemy.orm import declarative_base, Session, sessionmaker
    SA_AVAILABLE = True
except ImportError:
    SA_AVAILABLE = False

# ─── Database Configuration ───
if SA_AVAILABLE:
    def get_db_url():
        """Construct DB URL from env vars, matching postgres.py logic."""
        user = os.getenv("DB_USER", "agent_user")
        password = os.getenv("DB_PASSWORD", "agent_password")
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5433") # Docker maps 5433:5432 (see docker-compose.yml)
        db_name = os.getenv("DB_NAME", "market_data")
        
        # Override with full URL if provided
        return os.getenv('DATABASE_URL', f"postgresql://{user}:{password}@{host}:{port}/{db_name}")

    DB_URL = get_db_url()
else:
    DB_URL = None


if SA_AVAILABLE:
    Base = declarative_base()

    class BrainWeight(Base):
        """Persisted brain weight — survives restarts."""
        __tablename__ = 'brain_weights'

        id = Column(Integer, primary_key=True, autoincrement=True)
        brain_id = Column(String, unique=True, nullable=False)  # e.g. 'AMV-LSTM'
        weight = Column(Float, default=1.0)
        accuracy = Column(Float, default=0.0)  # rolling accuracy
        total_signals = Column(Integer, default=0)
        wins = Column(Integer, default=0)
        losses = Column(Integer, default=0)
        last_updated = Column(DateTime, default=datetime.now)
        metadata_json = Column(Text, default='{}')  # extra info as JSON

    class TrainingDecision(Base):
        """Records training approval/rejection decisions."""
        __tablename__ = 'training_decisions'

        id = Column(Integer, primary_key=True, autoincrement=True)
        brain_id = Column(String, nullable=False)
        action = Column(String, nullable=False)  # 'APPROVED', 'REJECTED', 'DEFERRED'
        old_weight = Column(Float)
        new_weight = Column(Float)
        old_accuracy = Column(Float)
        new_accuracy = Column(Float)
        reason = Column(Text, default='')
        decided_at = Column(DateTime, default=datetime.now)
        decided_by = Column(String, default='user')  # 'user' or 'auto'

    class TrainingRun(Base):
        """Records a training/backtest run with results."""
        __tablename__ = 'training_runs'

        id = Column(Integer, primary_key=True, autoincrement=True)
        run_type = Column(String)  # 'backtest', 'rl_update', 'parameter_optimize'
        symbols = Column(Text)  # comma-separated symbols
        strategy = Column(String)  # 'Intraday (Scalp)' or 'Swing (Hold)'
        data_source = Column(String)  # 'yfinance', 'CoinDCX', etc.
        candles_processed = Column(Integer, default=0)
        signals_generated = Column(Integer, default=0)
        accuracy_before = Column(Float)
        accuracy_after = Column(Float)
        params_before = Column(Text)  # JSON
        params_after = Column(Text)   # JSON
        started_at = Column(DateTime, default=datetime.now)
        completed_at = Column(DateTime)
        status = Column(String, default='RUNNING')  # RUNNING, COMPLETED, FAILED

else:
    BrainWeight = None
    TrainingDecision = None
    TrainingRun = None


class TrainingPersistence:
    """Manage persistent training state in PostgreSQL."""

    DEFAULT_BRAINS = [
        'AMV-LSTM', 'Regime-Ensemble', 'MM-Fusion',
        'Multi-TF', 'GNN-Sector', 'RL-Weighter'
    ]

    def __init__(self):
        self.engine = None
        self._session_factory = None
        if SA_AVAILABLE:
            try:
                self.engine = create_engine(DB_URL, pool_pre_ping=True)
                Base.metadata.create_all(self.engine)
                self._session_factory = sessionmaker(bind=self.engine)
            except Exception as e:
                logger.warning("training_db_init_failed", error=str(e)[:100])

    def _session(self) -> Optional[Session]:
        if self._session_factory:
            return self._session_factory()
        return None

    # ─── Brain Weights ───

    def get_brain_weights(self) -> Dict[str, Dict]:
        """Get all brain weights from DB. Returns {brain_id: {weight, accuracy, ...}}."""
        session = self._session()
        if not session:
            return {b: {'weight': 1.0, 'accuracy': 0.0, 'total_signals': 0} for b in self.DEFAULT_BRAINS}
        try:
            rows = session.query(BrainWeight).all()
            result = {}
            for row in rows:
                result[row.brain_id] = {
                    'weight': row.weight,
                    'accuracy': row.accuracy,
                    'total_signals': row.total_signals,
                    'wins': row.wins,
                    'losses': row.losses,
                    'last_updated': row.last_updated.isoformat() if row.last_updated else None,
                }
            # Add defaults for any missing brains
            for b in self.DEFAULT_BRAINS:
                if b not in result:
                    result[b] = {'weight': 1.0, 'accuracy': 0.0, 'total_signals': 0, 'wins': 0, 'losses': 0}
            return result
        except Exception as e:
            logger.warning("get_brain_weights_failed", error=str(e)[:100])
            return {b: {'weight': 1.0, 'accuracy': 0.0} for b in self.DEFAULT_BRAINS}
        finally:
            session.close()

    def update_brain_weight(self, brain_id: str, weight: float = None,
                            accuracy: float = None, wins: int = None, losses: int = None):
        """Update a brain's weight and/or accuracy in DB."""
        session = self._session()
        if not session:
            return False
        try:
            row = session.query(BrainWeight).filter_by(brain_id=brain_id).first()
            if not row:
                row = BrainWeight(brain_id=brain_id, weight=weight or 1.0)
                session.add(row)
            if weight is not None:
                row.weight = weight
            if accuracy is not None:
                row.accuracy = accuracy
            if wins is not None:
                row.wins = wins
                row.total_signals = (wins or 0) + (losses or row.losses or 0)
            if losses is not None:
                row.losses = losses
                row.total_signals = (wins or row.wins or 0) + (losses or 0)
            row.last_updated = datetime.now()
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.warning("update_weight_failed", brain=brain_id, error=str(e)[:100])
            return False
        finally:
            session.close()

    def recalculate_weights(self, accuracy_stats: Dict[str, float]) -> Dict[str, float]:
        """
        Recalculate brain weights based on rolling accuracy.
        Higher accuracy = higher weight. Minimum weight = 0.1.

        Args:
            accuracy_stats: {brain_id: accuracy_pct}

        Returns:
            {brain_id: new_weight}
        """
        if not accuracy_stats:
            return {}

        total_acc = sum(accuracy_stats.values()) or 1.0
        new_weights = {}
        for brain_id, acc in accuracy_stats.items():
            # Weight proportional to accuracy, min 0.1
            w = max(0.1, acc / total_acc * len(accuracy_stats))
            new_weights[brain_id] = round(w, 3)
            self.update_brain_weight(brain_id, weight=w, accuracy=acc)

        return new_weights

    # ─── Training Decisions ───

    def approve_training(self, brain_id: str, reason: str = '',
                         old_accuracy: float = None) -> int:
        """Record a training approval. Returns decision ID."""
        session = self._session()
        if not session:
            return -1
        try:
            # Get current weight
            row = session.query(BrainWeight).filter_by(brain_id=brain_id).first()
            old_weight = row.weight if row else 1.0

            decision = TrainingDecision(
                brain_id=brain_id,
                action='APPROVED',
                old_weight=old_weight,
                old_accuracy=old_accuracy or (row.accuracy if row else 0),
                reason=reason or f'User approved training for {brain_id}',
                decided_at=datetime.now(),
                decided_by='user'
            )
            session.add(decision)
            session.commit()
            return decision.id
        except Exception as e:
            session.rollback()
            logger.warning("approve_training_failed", error=str(e)[:100])
            return -1
        finally:
            session.close()

    def is_training_approved(self, brain_id: str) -> bool:
        """Check if a brain's training was already approved (to prevent repeats)."""
        session = self._session()
        if not session:
            return False
        try:
            latest = session.query(TrainingDecision).filter_by(
                brain_id=brain_id, action='APPROVED'
            ).order_by(TrainingDecision.decided_at.desc()).first()
            if not latest:
                return False
            # Approved within last 24h?
            age = (datetime.now() - latest.decided_at).total_seconds()
            return age < 86400  # 24 hours
        except Exception:
            return False
        finally:
            session.close()

    def get_pending_training(self, accuracy_threshold: float = None) -> List[Dict]:
        """Get brains needing training that haven't been approved yet."""
        if accuracy_threshold is None:
            # Load from config
            try:
                import json
                cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json')
                with open(cfg_path) as f:
                    cfg = json.load(f)
                accuracy_threshold = cfg.get('default', {}).get('accuracy_threshold', 55.0)
            except Exception:
                accuracy_threshold = 55.0

        weights = self.get_brain_weights()
        pending = []
        for brain_id, info in weights.items():
            if info.get('accuracy', 0) < accuracy_threshold and info.get('total_signals', 0) > 0:
                if not self.is_training_approved(brain_id):
                    pending.append({
                        'brain_id': brain_id,
                        'accuracy': info['accuracy'],
                        'total_signals': info['total_signals'],
                        'weight': info['weight'],
                    })
        return pending

    # ─── Training Runs ───

    def start_training_run(self, run_type: str, symbols: List[str],
                           strategy: str, data_source: str,
                           accuracy_before: float = None, params_before: Dict = None) -> int:
        """Start a training run. Returns run ID."""
        session = self._session()
        if not session:
            return -1
        try:
            run = TrainingRun(
                run_type=run_type,
                symbols=','.join(symbols),
                strategy=strategy,
                data_source=data_source,
                accuracy_before=accuracy_before,
                params_before=json.dumps(params_before) if params_before else None,
                started_at=datetime.now(),
                status='RUNNING'
            )
            session.add(run)
            session.commit()
            return run.id
        except Exception as e:
            session.rollback()
            logger.warning("start_run_failed", error=str(e)[:100])
            return -1
        finally:
            session.close()

    def complete_training_run(self, run_id: int, accuracy_after: float,
                              params_after: Dict = None, signals_gen: int = 0,
                              candles: int = 0):
        """Record completion of a training run."""
        session = self._session()
        if not session:
            return
        try:
            run = session.query(TrainingRun).get(run_id)
            if run:
                run.accuracy_after = accuracy_after
                run.params_after = json.dumps(params_after) if params_after else None
                run.signals_generated = signals_gen
                run.candles_processed = candles
                run.completed_at = datetime.now()
                run.status = 'COMPLETED'
                session.commit()
        except Exception as e:
            session.rollback()
            logger.warning("complete_run_failed", error=str(e)[:100])
        finally:
            session.close()


# Module singleton
training_db = TrainingPersistence()
