import os
import sys
sys.path.insert(0, os.path.abspath('.'))
from market_agent.data.storage.postgres import PostgresStorage
from sqlalchemy import text

db = PostgresStorage()
session = db.Session()

try:
    print("--- Executing Final DB Clean ---")
    
    # 1. Truncate signal_predictions (these were 256 from today's active tests)
    session.execute(text("TRUNCATE TABLE signal_predictions CASCADE;"))
    print("Truncated signal_predictions (from today's trial run)")
    
    # 2. Council Debates
    res = session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'council_debates';"))
    cd_cols = [r[0] for r in res]
    date_col = 'created_at' if 'created_at' in cd_cols else 'timestamp' if 'timestamp' in cd_cols else None
    
    if date_col:
        session.execute(text(f"DELETE FROM council_debates WHERE {date_col} < '2026-02-20';"))
        print(f"Deleted old council_debates using {date_col}")
    else:
        session.execute(text("TRUNCATE TABLE council_debates CASCADE;"))
        print("Truncated council_debates")

    # 3. Brain Analyst Logs
    res = session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'brain_analyst_logs';"))
    bal_cols = [r[0] for r in res]
    date_col = 'created_at' if 'created_at' in bal_cols else 'timestamp' if 'timestamp' in bal_cols else None
    
    if date_col:
        session.execute(text(f"DELETE FROM brain_analyst_logs WHERE {date_col} < '2026-02-20';"))
        print(f"Deleted old brain_analyst_logs using {date_col}")
    else:
        session.execute(text("TRUNCATE TABLE brain_analyst_logs CASCADE;"))
        print("Truncated brain_analyst_logs")
        
    # 4. Brain Weights 
    # The weights were generated on Feb 14, which means they are trained on corrupt OLD market_data.
    session.execute(text("TRUNCATE TABLE brain_weights CASCADE;"))
    session.execute(text("TRUNCATE TABLE training_runs CASCADE;"))
    print("Truncated brain_weights and training_runs (Corrupt Feb 14 models wiped. Needs retraining!)")

    session.commit()
    print("\nClean slate applied!")
except Exception as e:
    session.rollback()
    print(f"Error: {e}")
finally:
    session.close()
