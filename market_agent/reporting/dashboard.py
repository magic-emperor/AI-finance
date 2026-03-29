import streamlit as st
import pandas as pd
from market_agent.data.storage.postgres import PostgresStorage

st.set_page_config(page_title="Market AI Agent - Layer 0/1", layout="wide")

st.title("🚀 Market Research AI Agent - Observability")

storage = PostgresStorage()

symbol = st.sidebar.selectbox("Symbol", ["ITC.NS", "RELIANCE.NS", "AAPL"])
timeframe = st.sidebar.selectbox("Timeframe", ["1m", "5m", "1h"])

@st.cache_data(ttl=60)
def load_data(s, t):
    data = storage.get_latest_data(s, t, limit=500)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame([{"timestamp": d["timestamp"], **d["data"]} for d in data])
    df.set_index("timestamp", inplace=True)
    return df

df = load_data(symbol, timeframe)

if not df.empty:
    st.subheader(f"Price Chart: {symbol} ({timeframe})")
    df['SMA_20'] = df['close'].rolling(window=20).mean()
    
    st.line_chart(df[['close', 'SMA_20']])
    
    st.subheader("Raw Data (Binary Storage Replay)")
    st.dataframe(df.tail(10))
else:
    st.warning("No data found in database. Run ingestion script first.")
