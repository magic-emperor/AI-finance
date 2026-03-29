import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import structlog
import os
from market_agent.models.micro_price_nn import MicroPriceNN

logger = structlog.get_logger()

class Trainer:
    """
    Handles the training of Neural Networks.
    Optimized for RTX 4050 (CUDA).
    """
    def __init__(self, input_size, hidden_size=64, learning_rate=0.001):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = MicroPriceNN(input_size=input_size, hidden_size=hidden_size).to(self.device)
        self.optimizer = optim.Adam(self.model.parameters(), lr=learning_rate)
        
        # Loss functions
        self.direction_loss_fn = nn.CrossEntropyLoss()
        self.range_loss_fn = nn.MSELoss()
        
        logger.info("trainer_initialized", device=str(self.device))

    def train(self, X, Y_dir, Y_range, epochs=10, batch_size=32):
        """
        Runs the training loop.
        """
        # Convert to tensors
        X_tensor = torch.tensor(X, dtype=torch.float32).to(self.device)
        Y_dir_tensor = torch.tensor(Y_dir, dtype=torch.long).to(self.device)
        Y_range_tensor = torch.tensor(Y_range, dtype=torch.float32).unsqueeze(1).to(self.device)
        
        dataset = TensorDataset(X_tensor, Y_dir_tensor, Y_range_tensor)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
        
        self.model.train()
        for epoch in range(epochs):
            total_loss = 0
            for batch_x, batch_y_dir, batch_y_range in loader:
                self.optimizer.zero_grad()
                
                probs, range_est = self.model(batch_x)
                
                loss_dir = self.direction_loss_fn(probs, batch_y_dir)
                loss_range = self.range_loss_fn(range_est, batch_y_range)
                
                # Joint loss (Alpha = 1.0 for now)
                loss = loss_dir + loss_range
                loss.backward()
                self.optimizer.step()
                
                total_loss += loss.item()
            
            logger.info("epoch_complete", epoch=epoch+1, avg_loss=total_loss/len(loader))

    def fine_tune(self, X, Y_dir, Y_range, learning_rate=0.0001):
        """
        Fine-tunes the model on a specific set of high-regret samples.
        Uses a much lower learning rate to maintain stability.
        """
        original_lr = self.optimizer.param_groups[0]['lr']
        for param_group in self.optimizer.param_groups:
            param_group['lr'] = learning_rate
            
        logger.info("fine_tuning_started", samples=len(X), lr=learning_rate)
        self.train(X, Y_dir, Y_range, epochs=5, batch_size=min(len(X), 32))
        
        # Restore original learning rate
        for param_group in self.optimizer.param_groups:
            param_group['lr'] = original_lr
        
        logger.info("fine_tuning_complete")

    def save_model(self, path="market_agent/models/checkpoints/micro_price_v1.pth"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save(self.model.state_dict(), path)
        logger.info("model_saved", path=path)

    def load_model(self, path):
        if os.path.exists(path):
            self.model.load_state_dict(torch.load(path, map_location=self.device))
            logger.info("model_loaded", path=path)

if __name__ == "__main__":
    # Test trainer with synthetic data
    import numpy as np
    X_synthetic = np.random.randn(100, 10, 7) # 100 samples, 10 seq, 7 features
    Y_dir_synthetic = np.random.randint(0, 3, 100)
    Y_range_synthetic = np.random.randn(100)
    
    trainer = Trainer(input_size=7)
    trainer.train(X_synthetic, Y_dir_synthetic, Y_range_synthetic, epochs=2)
    trainer.save_model()
