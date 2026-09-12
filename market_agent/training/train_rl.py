"""
RL Ensemble Training
====================
Trains the RLEnsembleWeighter to dynamically weight models based on market variance.
Uses a simplified Policy Gradient approach on historical data.

Output: rl_agent_v1.pth (The "Manager")

Usage: python -m market_agent.training.train_rl
"""

import os
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import pandas as pd
import yfinance as yf
import structlog
from market_agent.models.causal_rl_models import RLEnsembleWeighter

# Define locally to avoid circular imports or missing exports
TRAINING_SYMBOLS = ["BTC-USD", "ETH-USD", "NVDA", "AMD", "AAPL", "MSFT"]
MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "checkpoints")

logger = structlog.get_logger()

# Hyperparams
BATCH_SIZE = 32
EPOCHS = 50
LEARNING_RATE = 0.001
GAMMA = 0.99
CONTEXT_SIZE = 5  # [RSI, Volatility, Trend, Volume, ATR]

def get_market_features(df):
    """Extract 5-dim context vector for RL state."""
    # 1. RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 2. Volatility (ATR / Price)
    high_low = df['High'] - df['Low']
    atr = high_low.rolling(14).mean()
    volatility = atr / df['Close']
    
    # 3. Trend (SMA50 / Price) - 1.0
    sma50 = df['Close'].rolling(50).mean()
    trend = (df['Close'] / sma50) - 1.0
    
    # 4. Volume Ratio
    vol_ma = df['Volume'].rolling(20).mean()
    vol_ratio = df['Volume'] / vol_ma
    
    # 5. Returns
    returns = df['Close'].pct_change()
    
    features = pd.DataFrame({
        'rsi': rsi / 100.0, # Normalize
        'volatility': volatility * 10, # Scale up
        'trend': trend,
        'volume': vol_ratio.clip(0, 3) / 3.0,
        'returns': returns
    }).fillna(0)
    
    return features.values

def simulate_expert_predictions(df):
    """
    Simulate predictions from 3 expert models for training data.
    Model A: Trend Follower (Good in trends)
    Model B: Mean Reversion (Good in ranges)
    Model C: Random/Noise (Baseline)
    """
    n = len(df)
    predictions = np.zeros((n, 3, 3)) # (Time, Models, [Down, Flat, Up])
    
    closes = df['Close'].values
    
    for i in range(50, n):
        # Ground Truth Direction
        future_ret = (closes[min(i+1, n-1)] - closes[i]) / closes[i]
        
        # Model A: Trend (SMA 20 > SMA 50)
        sma20 = np.mean(closes[i-20:i])
        sma50 = np.mean(closes[i-50:i])
        if sma20 > sma50:
            predictions[i, 0] = [0.1, 0.2, 0.7] # Bullish
        else:
            predictions[i, 0] = [0.7, 0.2, 0.1] # Bearish
            
        # Model B: Mean Rev (RSI)
        rsi_proxy = 50 # Simplified
        if rsi_proxy > 70:
            predictions[i, 1] = [0.8, 0.1, 0.1] # Sell
        elif rsi_proxy < 30:
            predictions[i, 1] = [0.1, 0.1, 0.8] # Buy
        else:
            predictions[i, 1] = [0.1, 0.8, 0.1] # Flat
            
        # Model C: Noise
        predictions[i, 2] = [0.33, 0.33, 0.33]
        
    return torch.tensor(predictions, dtype=torch.float32)

def train_rl_agent():
    print("="*60)
    print("  TRAINING RL ENSEMBLE AGENT")
    print("="*60)
    
    # 1. Setup Model
    agent = RLEnsembleWeighter(num_models=3, context_size=CONTEXT_SIZE)
    optimizer = optim.Adam(agent.parameters(), lr=LEARNING_RATE)
    
    # 2. Prepare Training Data 
    # (Using BTC-USD as proxy for general market dynamics)
    print("Fetching training data (BTC-USD)...")
    ticker = yf.Ticker("BTC-USD")
    df = ticker.history(period="1y", interval="1h")
    
    if len(df) < 500:
        print("Not enough data.")
        return
        
    context = torch.tensor(get_market_features(df), dtype=torch.float32)
    model_preds = simulate_expert_predictions(df) # Simulated inputs
    
    returns = df['Close'].pct_change().fillna(0).values
    
    print(f"Training on {len(df)} bars over {EPOCHS} epochs...")
    
    # 3. Training Loop (REINFORCE)
    for epoch in range(EPOCHS):
        total_reward = 0
        optimizer.zero_grad()
        
        # Batch processing
        for t in range(50, len(df)-1, BATCH_SIZE):
            end_t = min(t + BATCH_SIZE, len(df)-1)
            
            batch_preds = model_preds[t:end_t]         # (B, 3, 3)
            batch_context = context[t:end_t]           # (B, 5)
            batch_returns = returns[t+1:end_t+1]       # (B,) - Next step return
            
            # Forward Pass
            # agent returns: (weighted_pred, weights)
            weighted_preds, weights = agent(batch_preds, batch_context)
            
            # Calculate Reward
            # If weighted_pred aligns with actual return -> Reward ++
            # weighted_pred is (B, 3) [Down, Flat, Up]
            # Map returns to class labels: < -0.001 (0), > 0.001 (2), else (1)
            
            target_dirs = []
            for r in batch_returns:
                if r > 0.0005: target_dirs.append(2) # Up
                elif r < -0.0005: target_dirs.append(0) # Down
                else: target_dirs.append(1) # Flat
            
            target_tensor = torch.tensor(target_dirs, dtype=torch.long)
            
            # Loss = CrossEntropy (Maximize probability of correct direction)
            loss = nn.CrossEntropyLoss()(weighted_preds, target_tensor)
            
            loss.backward()
            total_reward -= loss.item() # Loss minimization = Reward maximization
            
        optimizer.step()
        
        if epoch % 10 == 0:
            print(f"  Epoch {epoch}: Loss = {-total_reward:.4f}")

    # 4. Save
    os.makedirs(MODELS_DIR, exist_ok=True)
    output_path = os.path.join(MODELS_DIR, "rl_agent_v1.pth")
    torch.save(agent.state_dict(), output_path)
    
    print(f"\nSaved RL Agent to: {output_path}")
    
    # 5. Verify
    print("\nVerification Inference:")
    agent.eval()
    with torch.no_grad():
        test_ctx = torch.randn(1, CONTEXT_SIZE)
        test_preds = torch.tensor([[[0.1, 0.1, 0.8], [0.8, 0.1, 0.1], [0.3, 0.4, 0.3]]])
        out, w = agent(test_preds, test_ctx)
        print(f"  Input Models: [Bullish, Bearish, Neutral]")
        print(f"  Learned Weights: {w.numpy()[0]}")
        
if __name__ == "__main__":
    train_rl_agent()
