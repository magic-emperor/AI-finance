"""
Phase 3, Step 3.3 — Data Quality Validator.

Run after rebuild_db.py to confirm every symbol has clean data.
Exit code 0 = all PASS. Exit code 1 = one or more FAIL/WARN.

Run: python -m market_agent.scripts.validate_db
"""
import logging
import sys
from market_agent.data.storage.postgres import PostgresStorage

log = logging.getLogger('validate_db')


def validate_symbol(symbol: str, timeframe: str, storage: PostgresStorage) -> dict:
    records = storage.get_latest_data(symbol, timeframe, limit=10000)

    if not records:
        return {'symbol': symbol, 'timeframe': timeframe,
                'status': 'FAIL', 'reason': 'No data found'}

    timestamps = [r['timestamp'] for r in records]
    closes     = [r['data']['Close'] for r in records]

    # ── Gap detection — market-aware thresholds ────────────────────────
    # NSE and US stocks do NOT trade 24h — they have ~18h overnight gaps
    # and 48h weekend gaps. A real data gap is >3 missing TRADING sessions.
    #   1h  data: normal overnight = 18h, weekend = 64h  → flag >80h
    #   15m data: same calendar logic                     → flag >80h
    #   1d  data: normal weekend   = 72h                  → flag >96h
    gap_thresholds = {
        '1h':  96,    # covers 3-day NSE weekends (Fri close->Mon open ~90h); >96h = real gap
        '15m': 96,    # same calendar logic
        '1d':  120,   # >5 calendar days = truly missing daily data
    }
    threshold_h = gap_thresholds.get(timeframe, 80)

    gaps = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i-1]).total_seconds() / 3600
        if delta > threshold_h:
            gaps.append({'at': str(timestamps[i]), 'gap_h': round(delta, 1)})

    # Price sanity — no >50% jump between consecutive candles
    jumps = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            chg = abs(closes[i] - closes[i-1]) / closes[i-1]
            if chg > 0.50:
                jumps.append({'at': str(timestamps[i]), 'pct': round(chg * 100, 1)})

    status = 'PASS' if not jumps and len(gaps) < 5 else 'WARN'

    return {
        'symbol':       symbol,
        'timeframe':    timeframe,
        'candles':      len(records),
        'from':         str(timestamps[0]),
        'to':           str(timestamps[-1]),
        'gaps':         len(gaps),
        'bad_jumps':    len(jumps),
        'status':       status,
        'gap_samples':  gaps[:3],
        'jump_samples': jumps[:3],
    }


def run():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
    )
    storage = PostgresStorage()

    # Import watchlist from rebuild script
    from market_agent.scripts.rebuild_db import ALL_SYMBOLS, FETCH_SPEC

    results = []
    for sym in ALL_SYMBOLS:
        for spec in FETCH_SPEC:
            r = validate_symbol(sym, spec['interval'], storage)
            results.append(r)
            icon = '✅' if r['status'] == 'PASS' else ('⚠️ ' if r['status'] == 'WARN' else '❌')
            log.info(
                f"{icon} {sym:20s} {spec['interval']:4s} | "
                f"{r.get('candles', 0):6,d} candles | "
                f"gaps={r.get('gaps', 'N/A')} | "
                f"jumps={r.get('bad_jumps', 'N/A')} | "
                f"{r['status']}"
            )

    passed  = [r for r in results if r['status'] == 'PASS']
    warned  = [r for r in results if r['status'] == 'WARN']
    failed  = [r for r in results if r['status'] == 'FAIL']

    log.info('')
    log.info(f'Validation complete: {len(passed)} PASS | {len(warned)} WARN | {len(failed)} FAIL')

    if warned:
        log.warning('--- WARN details ---')
        for r in warned:
            log.warning(f"  {r['symbol']} {r['timeframe']}: "
                        f"gaps={r['gaps']} | gaps_sample={r['gap_samples'][:1]}")

    if failed:
        log.error('--- FAIL details ---')
        for r in failed:
            log.error(f"  {r['symbol']} {r['timeframe']}: {r.get('reason','unknown')}")

    if failed:
        log.error('\n❌ FAIL — do NOT start retrain. Re-run rebuild for failed symbols.')
        sys.exit(1)
    elif warned:
        log.warning('\n⚠️  WARN — review gaps above. If acceptable, proceed to retrain.')
        sys.exit(0)
    else:
        log.info('\n✅ ALL PASS — safe to proceed to Phase 5 (retrain).')
        sys.exit(0)


if __name__ == '__main__':
    run()
