"""
Step 2.4 — Backfill existing Pickle rows to columnar numeric fields.

Reads every market_data row where close_price IS NULL (legacy Pickle-only row),
unpickles data_binary, and writes the OHLCV values into the new numeric columns.

Safe to run multiple times — skips rows that already have close_price set.
Run: python -m market_agent.scripts.backfill_columnar
"""
import pickle
import logging
from sqlalchemy import text
from market_agent.data.storage.postgres import PostgresStorage

log = logging.getLogger('backfill_columnar')


def backfill(batch_size: int = 500) -> bool:
    storage = PostgresStorage()
    session = storage.Session()

    # Count pending rows
    total = session.execute(
        text("SELECT COUNT(*) FROM market_data WHERE close_price IS NULL")
    ).scalar()
    log.info(f"Rows needing backfill: {total}")

    if total == 0:
        log.info("Nothing to backfill — all rows already columnar. ✅")
        session.close()
        return True

    fixed  = 0
    errors = 0
    offset = 0

    while True:
        rows = session.execute(text("""
            SELECT id, data_binary
            FROM market_data
            WHERE close_price IS NULL
              AND data_binary IS NOT NULL
            ORDER BY id
            LIMIT :lim
        """), {'lim': batch_size}).fetchall()

        if not rows:
            break

        for row in rows:
            try:
                data = pickle.loads(row.data_binary)
                session.execute(text("""
                    UPDATE market_data SET
                        open_price  = :o,
                        high_price  = :h,
                        low_price   = :l,
                        close_price = :c,
                        volume_val  = :v,
                        data_source = 'legacy_pickle'
                    WHERE id = :id
                """), {
                    'o':  float(data.get('Open',   0) or 0),
                    'h':  float(data.get('High',   0) or 0),
                    'l':  float(data.get('Low',    0) or 0),
                    'c':  float(data.get('Close',  0) or 0),
                    'v':  int(data.get('Volume',   0) or 0),
                    'id': row.id,
                })
                fixed += 1
            except Exception as e:
                log.warning(f"  Failed row id={row.id}: {e}")
                errors += 1

        session.commit()
        log.info(f"  Progress: {fixed} migrated, {errors} errors ...")

    # Final verification
    remaining = session.execute(
        text("SELECT COUNT(*) FROM market_data WHERE close_price IS NULL")
    ).scalar()
    session.close()

    log.info(f"\nBackfill complete: {fixed} migrated, {errors} errors")
    if remaining == 0:
        log.info("✅ Verification PASSED — close_price IS NULL count = 0")
        log.info("   Safe to proceed to Phase 3 (data rebuild).")
        return True
    else:
        log.error(f"❌ Verification FAILED — {remaining} rows still have close_price IS NULL")
        log.error("   Re-run this script or inspect those rows manually.")
        return False


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    backfill()
