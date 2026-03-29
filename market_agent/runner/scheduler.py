import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from market_agent.runner.signal_resolver import resolve_signals
from market_agent.data.storage.postgres import PostgresStorage

logger = structlog.get_logger()

_scheduler = None

def start_scheduler(storage: PostgresStorage) -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return  # already running

    _scheduler = BackgroundScheduler(timezone='UTC')

    # Resolve stranded predictions every 10 minutes
    # Completely independent of scanner or dashboard state
    _scheduler.add_job(
        func     = lambda: _safe_resolve(storage),
        trigger  = 'interval',
        minutes  = 10,
        id       = 'resolve_signals',
        name     = 'Resolve stranded predictions',
        max_instances = 1,          # prevents overlap with Gap 6 fix
        coalesce      = True,       # if missed, run once not multiple times
    )

    # AEP check daily at 15:00 IST = 09:30 UTC
    _scheduler.add_job(
        func    = lambda: _safe_aep_check(storage),
        trigger = 'cron',
        hour    = 9,
        minute  = 30,
        id      = 'aep_daily_check',
        name    = 'Daily AEP analysis',
        max_instances = 1,
    )

    # Accuracy cache refresh daily at Midnight IST = 18:30 UTC
    _scheduler.add_job(
        func    = _safe_cache_refresh,
        trigger = 'cron',
        hour    = 18,
        minute  = 30,
        id      = 'accuracy_cache_refresh',
        name    = 'Midnight Cache Refresh',
        max_instances = 1,
    )

    _scheduler.start()
    logger.info('scheduler_started',
                jobs=['resolve_signals (10min)', 'aep_daily_check (09:30 UTC)', 'accuracy_cache_refresh (18:30 UTC)'])


def _safe_resolve(storage: PostgresStorage) -> None:
    """Wrapped resolve with error handling so scheduler never crashes."""
    try:
        resolved = resolve_signals(storage)
        if resolved and resolved > 0:
            logger.info('scheduled_resolve_complete', resolved=resolved)
    except Exception as e:
        logger.error('scheduled_resolve_failed', error=str(e))


def _safe_aep_check(storage: PostgresStorage) -> None:
    try:
        from market_agent.runner.aep_runner import schedule_aep_check
        from market_agent.brain.gemini_client import gemini_client
        schedule_aep_check(storage, gemini_client)
    except Exception as e:
        logger.error('scheduled_aep_failed', error=str(e))


def _safe_cache_refresh() -> None:
    try:
        from market_agent.brain.health_monitor import refresh_accuracy_cache
        from market_agent.data.storage.postgres import PostgresStorage
        storage = PostgresStorage()
        refresh_accuracy_cache(storage)
        logger.info('scheduled_cache_refresh_complete')
    except Exception as e:
        logger.error('scheduled_cache_refresh_failed', error=str(e))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
