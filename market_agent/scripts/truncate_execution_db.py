"""
Helper script to truncate all execution and prediction history tables.
Run this before starting a fresh Live Measurement phase to ensure 
old statistics don't contaminate the new system evaluation.
"""

from market_agent.data.storage.postgres import PostgresStorage
from sqlalchemy import text

def clean_execution_tables():
    db = PostgresStorage()
    session = db.Session()
    try:
        print("Truncating execution history tables...")
        session.execute(text('TRUNCATE TABLE council_verdicts CASCADE;'))
        session.execute(text('TRUNCATE TABLE signal_predictions CASCADE;'))
        session.execute(text('TRUNCATE TABLE brain_predictions CASCADE;'))
        session.execute(text('TRUNCATE TABLE ai_code_proposals CASCADE;'))
        session.execute(text('TRUNCATE TABLE brain_parameter_overrides CASCADE;'))
        session.commit()
        print("✅ Successfully cleared all previous execution traces.")
        print("The database is now a clean slate for Phase 8 Live Tracking.")
    except Exception as e:
        session.rollback()
        print(f"❌ Error truncating tables: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    clean_execution_tables()
