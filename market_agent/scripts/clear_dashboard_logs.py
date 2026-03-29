import os
import sys
sys.path.insert(0, os.path.abspath('.'))
from market_agent.data.storage.postgres import PostgresStorage
from sqlalchemy import text

db = PostgresStorage()
session = db.Session()
try:
    print("Clearing remaining historic logs...")
    session.execute(text("TRUNCATE TABLE council_debates CASCADE;"))
    session.execute(text("TRUNCATE TABLE signal_predictions CASCADE;"))
    session.execute(text("TRUNCATE TABLE brain_analyst_logs CASCADE;"))
    session.commit()
    print("Dashboard log cache successfully cleared!")
except Exception as e:
    session.rollback()
    print(f"Error: {e}")
finally:
    session.close()
