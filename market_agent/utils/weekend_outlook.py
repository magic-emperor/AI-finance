from market_agent.data.storage.postgres import PostgresStorage
import pandas as pd
import structlog
from datetime import datetime

logger = structlog.get_logger()

def generate_weekend_outlook(symbol="ITC.NS"):
    storage = PostgresStorage()
    
    # Fetch latest 100 1m records to see Friday's close
    raw_1m = storage.get_latest_data(symbol, "1m", limit=100)
    # Fetch 1H data to see the trend
    raw_1h = storage.get_latest_data(symbol, "1h", limit=100)
    
    if not raw_1m or not raw_1h:
        print("No data found in database for outlook calculation.")
        return

    df_1m = pd.DataFrame([{"t": d["timestamp"], **d["data"]} for d in raw_1m])
    df_1h = pd.DataFrame([{"t": d["timestamp"], **d["data"]} for d in raw_1h])
    
    last_price = df_1m['close'].iloc[-1]
    friday_open = df_1m['open'].iloc[0]
    daily_change = (last_price - friday_open) / friday_open
    
    # Trend Analysis
    sma_20_1h = df_1h['close'].rolling(20).mean().iloc[-1]
    regime = "BULLISH_CONSOLIDATION" if last_price > sma_20_1h else "BEARISH_PRESSURE"
    
    print(f"\n--- WEEKEND OUTLOOK: {symbol} ---")
    print(f"Friday Close: {last_price:.2f}")
    print(f"Daily Net Change: {daily_change*100:.2f}%")
    print(f"1H Trend (SMA 20): {sma_20_1h:.2f}")
    print(f"Regime Context: {regime}")
    print(f"Target for Monday Morning Brief: Analyze news over Sat/Sun to see if catalyst overrides technicals.")
    print("---------------------------------\n")

if __name__ == "__main__":
    generate_weekend_outlook()
