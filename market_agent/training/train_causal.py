"""
Causal Graph Training
=====================
Learns "Lead-Lag" relationships between assets using Granger Causality.
Output: causal_graph.json (The "Knowledge")

Usage: python -m market_agent.training.train_causal
"""

import os
import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
import structlog
from market_agent.models.causal_ensemble import GrangerCausalityAnalyzer

# Training symbols from train_all_brains.py or define here
TRAINING_SYMBOLS = [
    # India / NSE
    "ITC.NS", "HDFCBANK.NS", "RELIANCE.NS", "TATASTEEL.NS",
    "LT.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    # US tech / AI
    "NVDA", "GOOGL", "AAPL", "AMD",
    # Crypto / FX / Commodities
    "BTC-USD", "GC=F", "GBPJPY=X", "USDJPY=X", "CL=F",
]

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "checkpoints")
logger = structlog.get_logger()

try:
    from statsmodels.tsa.stattools import grangercausalitytests
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False
    print("Warning: statsmodels not installed. Granger Causality will not work.")

def fetch_data(symbols):
    """Fetch 1h candle data for all symbols."""
    print("Fetching historical data (2y, 1h)...")
    data = {}
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            # Fetch long history for robust stats
            df = ticker.history(period="2y", interval="1h")
            if not df.empty and len(df) > 500:
                # Use Close price for causality
                # Important: Use returns to ensure stationarity (or let the analyzer handle it)
                # GrangerCausalityAnalyzer handles differencing if data is non-stationary
                series = df['Close'].dropna().values
                data[symbol] = series
                print(f"  {symbol}: {len(series)} points")
        except Exception as e:
            print(f"  Error fetching {symbol}: {e}")
    return data

def train_causal_graph():
    print("="*60)
    print("  TRAINING CAUSAL GRAPH (Granger Causality)")
    print("="*60)
    
    if not HAS_STATSMODELS:
        print("Error: statsmodels is required. pip install statsmodels")
        return

    # 1. Get Data
    data_dict = fetch_data(TRAINING_SYMBOLS)
    if len(data_dict) < 2:
        print("Not enough data to train causality.")
        return

    # 2. Analyze
    analyzer = GrangerCausalityAnalyzer(max_lag=5, significance_level=0.05)
    
    # Analyze raw data (build_causal_graph handles stationarity checks internally)
    edges = analyzer.build_causal_graph(data_dict)
    
    # 3. Format Output
    graph = {}
    print("\nSignificant Relationships (p < 0.05):")
    
    # Group by source
    for edge in edges:
        s, t = edge.source, edge.target
        if s not in graph:
            graph[s] = {}
        
        graph[s][t] = {
            "lag": int(edge.lag),
            "p_value": float(round(edge.p_value, 4)),
            "strength": float(round(edge.strength, 3))
        }
        print(f"  {s} -> {t} (Lag: {edge.lag}h, Strength: {edge.strength:.2f})")

    if not edges:
        print("  No significant causal relationships found.")

    # 4. Save
    os.makedirs(MODELS_DIR, exist_ok=True)
    output_path = os.path.join(MODELS_DIR, "causal_graph.json")
    
    with open(output_path, 'w') as f:
        json.dump(graph, f, indent=4)
        
    print(f"\nSaved causal graph to: {output_path}")
    print(f"Total Edges: {len(edges)}")
    
if __name__ == "__main__":
    train_causal_graph()
