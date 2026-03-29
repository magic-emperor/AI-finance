import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import Dict, List, Optional
import structlog

logger = structlog.get_logger()


class TimeframeEncoder(nn.Module):
    """
    Encodes a single timeframe's data using LSTM + Self-Attention.
    Each timeframe gets its own encoder to capture timeframe-specific patterns.
    """
    def __init__(self, input_size: int = 7, hidden_size: int = 64, num_layers: int = 2):
        super(TimeframeEncoder, self).__init__()
        
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=0.2 if num_layers > 1 else 0,
            bidirectional=True
        )
        
        # Self-attention for temporal focus
        self.attention = nn.MultiheadAttention(
            embed_dim=hidden_size * 2,
            num_heads=4,
            batch_first=True,
            dropout=0.1
        )
        
        self.layer_norm = nn.LayerNorm(hidden_size * 2)
        self.output_size = hidden_size * 2
        
    def forward(self, x):
        # x: (batch, seq_len, features)
        lstm_out, _ = self.lstm(x)
        
        # Self-attention
        attn_out, attn_weights = self.attention(lstm_out, lstm_out, lstm_out)
        
        # Residual + LayerNorm
        out = self.layer_norm(lstm_out + attn_out)
        
        # Take last timestep as representation
        representation = out[:, -1, :]
        
        return representation, attn_weights


class CrossTimeframeAttention(nn.Module):
    """
    Learns which timeframe to pay attention to based on current market context.
    
    Key Insight:
    - In trending markets → Higher timeframes (1D, 1W) matter more
    - In ranging markets → Lower timeframes (1M, 15M) matter more
    - Near support/resistance → All timeframes align
    """
    def __init__(self, embed_dim: int = 128, num_timeframes: int = 4):
        super(CrossTimeframeAttention, self).__init__()
        
        self.num_timeframes = num_timeframes
        
        # Query: "What should I focus on?"
        self.query_proj = nn.Linear(embed_dim, embed_dim)
        # Key: "What does each timeframe offer?"
        self.key_proj = nn.Linear(embed_dim, embed_dim)
        # Value: "What information does each timeframe contain?"
        self.value_proj = nn.Linear(embed_dim, embed_dim)
        
        # Learnable timeframe importance prior
        self.timeframe_bias = nn.Parameter(torch.zeros(num_timeframes))
        
        # Output projection
        self.output_proj = nn.Linear(embed_dim, embed_dim)
        
        self.scale = np.sqrt(embed_dim)
        
    def forward(self, timeframe_embeddings: torch.Tensor):
        """
        Args:
            timeframe_embeddings: (batch, num_timeframes, embed_dim)
        Returns:
            fused_representation: (batch, embed_dim)
            attention_weights: (batch, num_timeframes)
        """
        batch_size = timeframe_embeddings.size(0)
        
        # Use mean of all timeframes as query (context)
        context = timeframe_embeddings.mean(dim=1)  # (batch, embed_dim)
        
        Q = self.query_proj(context).unsqueeze(1)  # (batch, 1, embed_dim)
        K = self.key_proj(timeframe_embeddings)     # (batch, num_tf, embed_dim)
        V = self.value_proj(timeframe_embeddings)   # (batch, num_tf, embed_dim)
        
        # Scaled dot-product attention
        scores = torch.bmm(Q, K.transpose(1, 2)) / self.scale  # (batch, 1, num_tf)
        
        # Add learnable bias
        scores = scores + self.timeframe_bias.unsqueeze(0).unsqueeze(0)
        
        attention_weights = F.softmax(scores, dim=-1)  # (batch, 1, num_tf)
        
        # Weighted sum
        attended = torch.bmm(attention_weights, V)  # (batch, 1, embed_dim)
        
        output = self.output_proj(attended.squeeze(1))
        
        return output, attention_weights.squeeze(1)


class MultiTimeframeModel(nn.Module):
    """
    Phase 14A: Multi-Timeframe Attention Network
    
    Processes 1M, 15M, 1H, 1D data simultaneously with cross-timeframe attention.
    
    Architecture:
    1M Data  → LSTM+Attn → Embed_1M  ─┐
    15M Data → LSTM+Attn → Embed_15M ─┼→ Cross-TF Attention → Dense → Prediction
    1H Data  → LSTM+Attn → Embed_1H  ─┤
    1D Data  → LSTM+Attn → Embed_1D  ─┘
    
    Expected Gain: +10-15% accuracy
    Why: Captures micro-momentum (1M) + intraday (1H) + trend (1D) simultaneously
    """
    
    TIMEFRAMES = ['1m', '15m', '1h', '1d']
    
    def __init__(self, input_size: int = 7, hidden_size: int = 64, dropout: float = 0.3):
        super(MultiTimeframeModel, self).__init__()
        
        self.hidden_size = hidden_size
        self.num_timeframes = len(self.TIMEFRAMES)
        
        # One encoder per timeframe
        self.encoders = nn.ModuleDict({
            tf: TimeframeEncoder(input_size, hidden_size)
            for tf in self.TIMEFRAMES
        })
        
        embed_dim = hidden_size * 2  # Bidirectional LSTM output
        
        # Cross-timeframe attention
        self.cross_attn = CrossTimeframeAttention(embed_dim, self.num_timeframes)
        
        # Feature extraction
        self.fc1 = nn.Linear(embed_dim, hidden_size)
        self.bn1 = nn.BatchNorm1d(hidden_size)
        self.fc2 = nn.Linear(hidden_size, 64)
        self.bn2 = nn.BatchNorm1d(64)
        
        self.activation = nn.GELU()
        self.dropout = nn.Dropout(dropout)
        
        # Output heads
        self.direction_head = nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 3)  # DOWN, FLAT, UP
        )
        
        self.range_head = nn.Sequential(
            nn.Linear(64, 16),
            nn.ReLU(),
            nn.Linear(16, 1)
        )
        
        self.confidence_head = nn.Sequential(
            nn.Linear(64, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
            nn.Sigmoid()
        )
        
        # Track which timeframe is most important
        self.timeframe_importance = None
        
    def forward(self, data: Dict[str, torch.Tensor], return_attention: bool = False):
        """
        Args:
            data: Dict mapping timeframe to tensor, e.g. {'1m': tensor, '15m': tensor, ...}
                  Each tensor: (batch, seq_len, features)
        Returns:
            direction_probs, expected_range, confidence, (optional) attention_weights
        """
        batch_size = None
        device = None
        
        # Encode each timeframe
        timeframe_embeddings = []
        for tf in self.TIMEFRAMES:
            if tf in data:
                x = data[tf]
                if batch_size is None:
                    batch_size = x.size(0)
                    device = x.device
                embed, _ = self.encoders[tf](x)
                timeframe_embeddings.append(embed)
            else:
                # If missing, use zeros (will be low-weighted by attention)
                if batch_size is not None:
                    embed = torch.zeros(batch_size, self.hidden_size * 2, device=device)
                    timeframe_embeddings.append(embed)
        
        # Stack: (batch, num_timeframes, embed_dim)
        stacked = torch.stack(timeframe_embeddings, dim=1)
        
        # Cross-timeframe fusion
        fused, tf_weights = self.cross_attn(stacked)
        self.timeframe_importance = tf_weights.detach()
        
        # Feature extraction
        out = self.fc1(fused)
        out = self.bn1(out)
        out = self.activation(out)
        out = self.dropout(out)
        
        out = self.fc2(out)
        out = self.bn2(out)
        out = self.activation(out)
        out = self.dropout(out)
        
        # Predictions
        direction_logits = self.direction_head(out)
        direction_probs = F.softmax(direction_logits, dim=1)
        
        expected_range = self.range_head(out)
        confidence = self.confidence_head(out)
        
        if return_attention:
            return direction_probs, expected_range, confidence, tf_weights
        
        return direction_probs, expected_range, confidence
    
    def get_timeframe_importance(self) -> Dict[str, float]:
        """Returns which timeframe the model considers most important."""
        if self.timeframe_importance is None:
            return {tf: 0.25 for tf in self.TIMEFRAMES}
        
        # Average across batch
        weights = self.timeframe_importance.mean(dim=0).cpu().numpy()
        return {tf: float(w) for tf, w in zip(self.TIMEFRAMES, weights)}


class MultiTimeframeDataLoader:
    """
    Utility to prepare multi-timeframe data for the model.
    Handles alignment between different timeframe sequences.
    """
    
    def __init__(self, seq_lengths: Dict[str, int] = None):
        self.seq_lengths = seq_lengths or {
            '1m': 60,    # Last 60 minutes (1 hour of 1M data)
            '15m': 20,   # Last 20 bars of 15M (5 hours)
            '1h': 24,    # Last 24 hours
            '1d': 30     # Last 30 days
        }
    
    def prepare_batch(self, raw_data: Dict[str, np.ndarray]) -> Dict[str, torch.Tensor]:
        """
        Converts raw numpy data to model-ready tensors.
        
        Args:
            raw_data: Dict mapping timeframe to numpy array of shape (batch, seq, features)
        """
        result = {}
        for tf, seq_len in self.seq_lengths.items():
            if tf in raw_data:
                data = raw_data[tf]
                # Truncate or pad to expected length
                if data.shape[1] > seq_len:
                    data = data[:, -seq_len:, :]
                elif data.shape[1] < seq_len:
                    pad = np.zeros((data.shape[0], seq_len - data.shape[1], data.shape[2]))
                    data = np.concatenate([pad, data], axis=1)
                
                result[tf] = torch.tensor(data, dtype=torch.float32)
        
        return result


if __name__ == "__main__":
    print("Multi-Timeframe Attention Network Test")
    print("=" * 60)
    
    # Create model
    model = MultiTimeframeModel(input_size=7, hidden_size=64)
    
    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    print(f"Total parameters: {total_params:,}")
    
    # Simulate multi-timeframe data
    batch_size = 8
    test_data = {
        '1m': torch.randn(batch_size, 60, 7),   # 60 one-minute bars
        '15m': torch.randn(batch_size, 20, 7),  # 20 fifteen-minute bars
        '1h': torch.randn(batch_size, 24, 7),   # 24 one-hour bars
        '1d': torch.randn(batch_size, 30, 7)    # 30 daily bars
    }
    
    # Forward pass
    probs, range_est, confidence, attn = model(test_data, return_attention=True)
    
    print(f"\nInput shapes:")
    for tf, tensor in test_data.items():
        print(f"  {tf}: {tensor.shape}")
    
    print(f"\nOutput shapes:")
    print(f"  Direction probs: {probs.shape}")
    print(f"  Range estimate: {range_est.shape}")
    print(f"  Confidence: {confidence.shape}")
    print(f"  Attention weights: {attn.shape}")
    
    print(f"\nSample prediction:")
    print(f"  Direction: {['DOWN', 'FLAT', 'UP'][torch.argmax(probs[0]).item()]}")
    print(f"  Probabilities: {probs[0].detach().numpy()}")
    print(f"  Confidence: {confidence[0].item():.2%}")
    
    print(f"\nTimeframe Importance:")
    importance = model.get_timeframe_importance()
    for tf, weight in importance.items():
        bar = "█" * int(weight * 40)
        print(f"  {tf:4s}: {weight:.2%} {bar}")
