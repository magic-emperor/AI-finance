import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
import pickle
import os

class DataPreprocessor:
    """
    Handles scaling and sequence generation for Neural Network training.
    """
    def __init__(self, scaler_path="market_agent/models/scaler.pkl"):
        self.scaler_path = scaler_path
        self.scaler = StandardScaler()
        if os.path.exists(scaler_path):
            with open(scaler_path, 'rb') as f:
                self.scaler = pickle.load(f)

    def fit_transform(self, df: pd.DataFrame):
        features = self._extract_features(df)
        scaled_features = self.scaler.fit_transform(features)
        with open(self.scaler_path, 'wb') as f:
            pickle.dump(self.scaler, f)
        return scaled_features

    def transform(self, df: pd.DataFrame):
        features = self._extract_features(df)
        return self.scaler.transform(features)

    def _extract_features(self, df: pd.DataFrame):
        """Extracts OHLCV + Layer 1 metrics as features."""
        # Ensure we have the necessary columns
        # Features: Open, High, Low, Close, Volume, ATR, Volatility, VWAP
        # (Assuming Layer 1 metrics are already calculated or passed)
        
        # For now, let's use standard OHLCV + some technicals
        required = ['open', 'high', 'low', 'close', 'volume']
        for col in required:
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")

        # CHANGED: Handle NaNs with ffill + 0 (research: Best for time series, preserves trends)
        df = df.fillna(method='ffill').fillna(0)  # Forward fill, then 0 for any remaining (e.g., first rows)

        # Calculate Layer 1 metrics
        from market_agent.patterns.perception import PerceptionEngine
        engine = PerceptionEngine()
        atr = engine.calculate_atr(df)
        volatility = engine.calculate_volatility(df)
        
        features = pd.DataFrame(index=df.index)
        features['open'] = df['open']
        features['high'] = df['high']
        features['low'] = df['low']
        features['close'] = df['close']
        features['volume'] = df['volume']
        features['atr'] = atr
        features['volatility'] = volatility
        
        return features.values

    def create_sequences(self, data, seq_length=10):
        """Prepares sliding windows for LSTM."""
        xs = []
        ys_direction = []
        ys_range = []
        
        # for i in range(len(data) - seq_length):
        #     x = data[i : (i + seq_length)]
            
        #     # Label: Next candle direction (relative to current close)
        #     # 2: Up, 1: Flat, 0: Down
        #     current_close = data[i + seq_length - 1][3] # Index 3 is Close
        #     next_close = data[i + seq_length][3]
            
        #     diff = next_close - current_close
        #     if diff > 0.001: # Threshold for 'Up'
        #         y_dir = 2
        #     elif diff < -0.001: # Threshold for 'Down'
        #         y_dir = 0
        #     else:
        #         y_dir = 1
                
        #     y_range = abs(diff)
            
        #     xs.append(x)
        #     ys_direction.append(y_dir)
        #     ys_range.append(y_range)
            
        # return np.array(xs), np.array(ys_direction), np.array(ys_range)

        # CHANGED: Added adaptive threshold using ATR (research: Best for labels, = atr * 0.5)
        atr_mean = np.nanmean(features[:, 5])  # ATR col index 5, ignore NaNs
        threshold = atr_mean * 0.5 if atr_mean > 0 else 0.001  # Fallback if no ATR
        
        # CHANGED: Added sentiment placeholder (col 7=0.0—research: Improves fusion models)
        features = np.c_[features, np.zeros(len(features))]  # Add sentiment col=0

        for i in range(len(features) - seq_length - horizon):
            x = features[i:(i + seq_length)]
            future_close = features[i + seq_length + horizon - 1, 3]  # Close price
            current_close = x[-1, 3]  # Last close in sequence
            
            diff = (future_close - current_close) / current_close
            if diff > threshold:  # CHANGED: Adaptive > threshold (was fixed 0.001)
                y_dir = 2
            elif diff < -threshold:
                y_dir = 0
            else:
                y_dir = 1
            
            y_range = abs(diff)
            
            xs.append(x)
            ys_direction.append(y_dir)
            ys_range.append(y_range)
        
        return np.array(xs), np.array(ys_direction), np.array(ys_range)

if __name__ == "__main__":
    # Test preprocessing
    dummy_df = pd.DataFrame({
        'open': np.random.randn(50) + 100,
        'high': np.random.randn(50) + 101,
        'low': np.random.randn(50) + 99,
        'close': np.random.randn(50) + 100,
        'volume': np.random.randint(100, 1000, 50)
    })
    
    preprocessor = DataPreprocessor()
    scaled = preprocessor.fit_transform(dummy_df)
    X, Y_dir, Y_range = preprocessor.create_sequences(scaled)
    
    print(f"Input shape: {X.shape}") # (Batch, Seq, Features)
    print(f"Target direction shape: {Y_dir.shape}")
