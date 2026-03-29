import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
from datetime import datetime
import json
import os
import structlog
from market_agent.models.advanced_models import AMVLSTMModel, RegimeSpecificEnsemble

logger = structlog.get_logger()

class AdvancedTrainer:
    """
    Phase 13: Enhanced Trainer with Accuracy Tracking
    
    Features:
    1. Supports AMV-LSTM and Ensemble models
    2. Tracks per-epoch accuracy metrics
    3. Implements Early Stopping
    4. Saves accuracy history for analysis
    5. Confidence calibration
    """
    
    def __init__(self, model_type="amv_lstm", input_size=7, hidden_size=128, learning_rate=0.001):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model_type = model_type
        
        if model_type == "amv_lstm":
            self.model = AMVLSTMModel(input_size=input_size, hidden_size=hidden_size).to(self.device)
        elif model_type == "ensemble":
            self.model = RegimeSpecificEnsemble(input_size=input_size, hidden_size=hidden_size).to(self.device)
        else:
            raise ValueError(f"Unknown model type: {model_type}")
        
        self.optimizer = optim.AdamW(self.model.parameters(), lr=learning_rate, weight_decay=0.01)
        self.scheduler = optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, patience=3, factor=0.5)
        
        # Loss functions
        self.direction_loss_fn = nn.CrossEntropyLoss()
        self.range_loss_fn = nn.SmoothL1Loss()  # More robust than MSE
        self.confidence_loss_fn = nn.BCELoss()
        
        # Accuracy tracking
        self.history = {
            "epochs": [],
            "train_loss": [],
            "train_accuracy": [],
            "val_loss": [],
            "val_accuracy": [],
            "confidence_calibration": []
        }
        
        self.best_val_accuracy = 0
        self.patience_counter = 0
        
        logger.info("advanced_trainer_initialized", 
                   device=str(self.device), 
                   model_type=model_type,
                   params=sum(p.numel() for p in self.model.parameters()))
    
    def train(self, X, Y_dir, Y_range, epochs=50, batch_size=32, validation_split=0.2, early_stopping=True, patience=5):
        """
        Train with validation split and accuracy tracking.
        """
        # Split data
        split_idx = int(len(X) * (1 - validation_split))
        X_train, X_val = X[:split_idx], X[split_idx:]
        Y_dir_train, Y_dir_val = Y_dir[:split_idx], Y_dir[split_idx:]
        Y_range_train, Y_range_val = Y_range[:split_idx], Y_range[split_idx:]
        
        # Convert to tensors
        train_dataset = TensorDataset(
            torch.tensor(X_train, dtype=torch.float32),
            torch.tensor(Y_dir_train, dtype=torch.long),
            torch.tensor(Y_range_train, dtype=torch.float32).unsqueeze(1)
        )
        val_dataset = TensorDataset(
            torch.tensor(X_val, dtype=torch.float32),
            torch.tensor(Y_dir_val, dtype=torch.long),
            torch.tensor(Y_range_val, dtype=torch.float32).unsqueeze(1)
        )
        
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size)
        
        logger.info("training_started", 
                   train_samples=len(X_train), 
                   val_samples=len(X_val),
                   epochs=epochs)
        
        for epoch in range(epochs):
            # Training phase
            self.model.train()
            train_loss, train_correct, train_total = 0, 0, 0
            
            for batch_x, batch_y_dir, batch_y_range in train_loader:
                batch_x = batch_x.to(self.device)
                batch_y_dir = batch_y_dir.to(self.device)
                batch_y_range = batch_y_range.to(self.device)
                
                self.optimizer.zero_grad()
                
                if self.model_type == "ensemble":
                    probs, range_est, confidence, _ = self.model(batch_x)
                else:
                    probs, range_est, confidence = self.model(batch_x)
                
                # Multi-task loss
                loss_dir = self.direction_loss_fn(probs, batch_y_dir)
                loss_range = self.range_loss_fn(range_est, batch_y_range)
                
                # Confidence calibration loss (confidence should match correctness)
                predictions = torch.argmax(probs, dim=1)
                correct_mask = (predictions == batch_y_dir).float().unsqueeze(1)
                loss_conf = self.confidence_loss_fn(confidence, correct_mask)
                
                # Combined loss
                loss = loss_dir + 0.5 * loss_range + 0.3 * loss_conf
                loss.backward()
                
                # Gradient clipping
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                
                self.optimizer.step()
                
                train_loss += loss.item()
                train_correct += (predictions == batch_y_dir).sum().item()
                train_total += len(batch_y_dir)
            
            train_accuracy = train_correct / train_total
            
            # Validation phase
            val_loss, val_accuracy, conf_calibration = self._validate(val_loader)
            
            # Update scheduler
            self.scheduler.step(val_loss)
            
            # Log metrics
            self.history["epochs"].append(epoch + 1)
            self.history["train_loss"].append(train_loss / len(train_loader))
            self.history["train_accuracy"].append(train_accuracy)
            self.history["val_loss"].append(val_loss)
            self.history["val_accuracy"].append(val_accuracy)
            self.history["confidence_calibration"].append(conf_calibration)
            
            logger.info("epoch_complete",
                       epoch=epoch + 1,
                       train_acc=f"{train_accuracy:.2%}",
                       val_acc=f"{val_accuracy:.2%}",
                       conf_cal=f"{conf_calibration:.2%}")
            
            # Early stopping
            if val_accuracy > self.best_val_accuracy:
                self.best_val_accuracy = val_accuracy
                self.patience_counter = 0
                self._save_best_model()
            else:
                self.patience_counter += 1
                
            if early_stopping and self.patience_counter >= patience:
                logger.info("early_stopping", epoch=epoch + 1, best_val_acc=f"{self.best_val_accuracy:.2%}")
                break
        
        self._save_history()
        return self.history
    
    def _validate(self, val_loader):
        """Validate and compute accuracy metrics."""
        self.model.eval()
        val_loss, val_correct, val_total = 0, 0, 0
        confidence_correct = 0
        
        with torch.no_grad():
            for batch_x, batch_y_dir, batch_y_range in val_loader:
                batch_x = batch_x.to(self.device)
                batch_y_dir = batch_y_dir.to(self.device)
                batch_y_range = batch_y_range.to(self.device)
                
                if self.model_type == "ensemble":
                    probs, range_est, confidence, _ = self.model(batch_x)
                else:
                    probs, range_est, confidence = self.model(batch_x)
                
                loss_dir = self.direction_loss_fn(probs, batch_y_dir)
                loss_range = self.range_loss_fn(range_est, batch_y_range)
                val_loss += (loss_dir + 0.5 * loss_range).item()
                
                predictions = torch.argmax(probs, dim=1)
                correct = predictions == batch_y_dir
                val_correct += correct.sum().item()
                val_total += len(batch_y_dir)
                
                # Confidence calibration: high confidence on correct, low on wrong
                high_conf_correct = ((confidence > 0.6).squeeze() & correct).sum().item()
                confidence_correct += high_conf_correct
        
        val_accuracy = val_correct / val_total
        conf_calibration = confidence_correct / val_correct if val_correct > 0 else 0
        
        return val_loss / len(val_loader), val_accuracy, conf_calibration
    
    def _save_best_model(self):
        """Save the best model checkpoint."""
        path = f"market_agent/models/checkpoints/best_{self.model_type}.pth"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'best_accuracy': self.best_val_accuracy
        }, path)
    
    def _save_history(self):
        """Save training history for analysis."""
        path = f"market_agent/models/checkpoints/{self.model_type}_history.json"
        with open(path, 'w') as f:
            json.dump(self.history, f, indent=2)
        logger.info("history_saved", path=path)
    
    def generate_accuracy_report(self) -> str:
        """Generate a human-readable accuracy report."""
        if not self.history["epochs"]:
            return "No training history available."
        
        report = []
        report.append("=" * 50)
        report.append("BRAIN ACCURACY REPORT")
        report.append("=" * 50)
        report.append(f"Model: {self.model_type}")
        report.append(f"Device: {self.device}")
        report.append(f"Best Validation Accuracy: {self.best_val_accuracy:.2%}")
        report.append("")
        report.append("Training Progress:")
        report.append("-" * 50)
        
        for i in range(len(self.history["epochs"])):
            report.append(
                f"Epoch {self.history['epochs'][i]:3d}: "
                f"Train={self.history['train_accuracy'][i]:.2%} "
                f"Val={self.history['val_accuracy'][i]:.2%} "
                f"Conf={self.history['confidence_calibration'][i]:.2%}"
            )
        
        report.append("")
        report.append("HONEST ASSESSMENT:")
        report.append("-" * 50)
        
        final_acc = self.history['val_accuracy'][-1] if self.history['val_accuracy'] else 0
        if final_acc >= 0.60:
            report.append("GOOD: Model exceeds 60% accuracy (statistically significant edge)")
        elif final_acc >= 0.55:
            report.append("MODERATE: Model at 55-60% (slight edge, needs improvement)")
        else:
            report.append("NEEDS WORK: Model below 55% (close to random, implement more improvements)")
        
        return "\n".join(report)


if __name__ == "__main__":
    print("Testing Advanced Trainer")
    print("=" * 50)
    
    # Generate synthetic training data
    np.random.seed(42)
    n_samples = 1000
    seq_len = 20
    features = 7
    
    X = np.random.randn(n_samples, seq_len, features).astype(np.float32)
    Y_dir = np.random.randint(0, 3, n_samples)
    Y_range = np.abs(np.random.randn(n_samples)).astype(np.float32)
    
    # Train AMV-LSTM
    trainer = AdvancedTrainer(model_type="amv_lstm", input_size=features)
    history = trainer.train(X, Y_dir, Y_range, epochs=10, batch_size=32)
    
    print("\n" + trainer.generate_accuracy_report())
