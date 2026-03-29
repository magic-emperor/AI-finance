import torch
import torch.nn as nn
import numpy as np

class MicroPriceNN(nn.Module):
    """
    Layer 3: Neural Networks
    An LSTM-based model to predict direction and price range probability.
    Architecture:
    - LSTM Layer (Sequence context)
    - Dropout (Hallucination/Overfitting prevention)
    - Dense Layers (Feature extraction)
    - Multi-head Output: Direction (Softmax) & Range (Linear)
    """
    def __init__(self, input_size=10, hidden_size=64, num_layers=2, output_size=3):
        super(MicroPriceNN, self).__init__()
        
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        
        # LSTM to capture temporal patterns
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.2)
        
        # Fully connected layers
        self.fc1 = nn.Linear(hidden_size, 32)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(0.2)
        
        # Head 1: Direction Probability (Down, Flat, Up)
        self.direction_head = nn.Linear(32, output_size)
        self.softmax = nn.Softmax(dim=1)
        
        # Head 2: Expected Range (Absolute move forecast)
        self.range_head = nn.Linear(32, 1)

    def forward(self, x):
        # Initialize hidden state
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size).to(x.device)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size).to(x.device)
        
        # LSTM forward
        out, _ = self.lstm(x, (h0, c0))
        
        # Take the output of the last time step
        out = out[:, -1, :]
        
        out = self.fc1(out)
        out = self.relu(out)
        out = self.dropout(out)
        
        direction_logits = self.direction_head(out)
        direction_probs = self.softmax(direction_logits)
        
        expected_range = self.range_head(out)
        
        return direction_probs, expected_range

if __name__ == "__main__":
    # Test with dummy batch
    # (batch_size, sequence_length, features)
    dummy_input = torch.randn(8, 10, 10) 
    model = MicroPriceNN(input_size=10)
    
    probs, range_est = model(dummy_input)
    print("Direction Probabilities (Down, Flat, Up):")
    print(probs)
    print("\nExpected Price Range Estimate:")
    print(range_est)
