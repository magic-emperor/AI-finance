"""
Path A – Data audit: how much data per symbol/timeframe, last 2 years?, new data till when?
Run: python -m market_agent.runner.data_audit
"""
from sqlalchemy import text
from market_agent.data.storage.postgres import PostgresStorage


def run_audit():
    storage = PostgresStorage()
    out = []

    with storage.engine.connect() as conn:
        # market_data: symbol, timeframe, count, min_ts, max_ts
        out.append("=== market_data (OHLC) ===")
        try:
            r = conn.execute(text("""
                SELECT symbol, timeframe, COUNT(*) AS cnt,
                       MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts
                FROM market_data
                GROUP BY symbol, timeframe
                ORDER BY symbol, timeframe
            """))
            rows = r.fetchall()
            if not rows:
                out.append("  No rows in market_data.")
            else:
                for row in rows:
                    symbol, tf, cnt, min_ts, max_ts = row
                    min_s = min_ts.strftime("%Y-%m-%d %H:%M") if min_ts else "N/A"
                    max_s = max_ts.strftime("%Y-%m-%d %H:%M") if max_ts else "N/A"
                    out.append(f"  {symbol} | {tf} | count={cnt} | from {min_s} to {max_s}")
        except Exception as e:
            out.append(f"  Error: {e}")

        # signal_predictions (resolved): symbol, model_id, count, min_created, max_created
        out.append("\n=== signal_predictions (resolved) ===")
        try:
            r = conn.execute(text("""
                SELECT symbol, model_id, COUNT(*) AS cnt,
                       MIN(created_at) AS min_created, MAX(created_at) AS max_created
                FROM signal_predictions
                WHERE is_resolved = true
                GROUP BY symbol, model_id
                ORDER BY symbol, model_id
            """))
            rows = r.fetchall()
            if not rows:
                out.append("  No resolved predictions.")
            else:
                for row in rows:
                    symbol, model_id, cnt, min_c, max_c = row
                    min_s = min_c.strftime("%Y-%m-%d %H:%M") if min_c else "N/A"
                    max_s = max_c.strftime("%Y-%m-%d %H:%M") if max_c else "N/A"
                    out.append(f"  {symbol} | {model_id} | count={cnt} | from {min_s} to {max_s}")
        except Exception as e:
            out.append(f"  Error: {e}")

        # Summary: do we have ~2 years of data?
        out.append("\n=== Summary ===")
        try:
            r = conn.execute(text("""
                SELECT timeframe, MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts,
                       COUNT(DISTINCT symbol) AS symbols
                FROM market_data
                GROUP BY timeframe
            """))
            for row in r.fetchall():
                tf, min_ts, max_ts, symbols = row
                if min_ts and max_ts:
                    delta = (max_ts - min_ts).days
                    out.append(f"  {tf}: {delta} days range, {symbols} symbols. New data till: {max_ts}")
                else:
                    out.append(f"  {tf}: no data")
        except Exception as e:
            out.append(f"  Error: {e}")

    return "\n".join(out)


if __name__ == "__main__":
    print(run_audit())
