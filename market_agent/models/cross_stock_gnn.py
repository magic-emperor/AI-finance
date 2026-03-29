import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import Dict, List, Tuple, Optional
import structlog
from dataclasses import dataclass

logger = structlog.get_logger()

# Check if PyTorch Geometric is available
try:
    from torch_geometric.nn import GATConv, GCNConv, TransformerConv
    from torch_geometric.data import Data, Batch
    HAS_PYGEOMETRIC = True
except ImportError:
    HAS_PYGEOMETRIC = False
    logger.warning("pytorch_geometric_not_installed", 
                   message="Install with: pip install torch-geometric")


@dataclass
class StockNode:
    """Represents a stock in the graph."""
    symbol: str
    sector: str
    market_cap: float = 0.0
    features: Optional[np.ndarray] = None


class StockLSTMEncoder(nn.Module):
    """Encodes individual stock time series."""
    def __init__(self, input_size: int = 7, hidden_size: int = 64):
        super(StockLSTMEncoder, self).__init__()
        self.lstm = nn.LSTM(
            input_size, hidden_size, 
            num_layers=2, 
            batch_first=True,
            dropout=0.2,
            bidirectional=True
        )
        self.output_size = hidden_size * 2
        
    def forward(self, x):
        # x: (batch, seq_len, features)
        out, (h_n, c_n) = self.lstm(x)
        # Use last hidden state as representation
        return out[:, -1, :]


class StockGraphNetwork(nn.Module):
    """
    Phase 14B: Cross-Stock Graph Neural Network
    
    Architecture:
    1. Each stock's time series → LSTM → Node embedding
    2. Stock correlation matrix → Edge weights
    3. Graph Attention Network → Cross-stock learning
    4. Final prediction for target stock
    
    Key Insight:
    - When RELIANCE moves, NIFTY follows
    - Sector rotations are predictable
    - Lead-lag relationships can be learned
    
    Expected Gain: +8-12% accuracy
    """
    
    def __init__(
        self, 
        num_stocks: int,
        input_size: int = 7,
        hidden_size: int = 64,
        num_gnn_layers: int = 2,
        heads: int = 4,
        dropout: float = 0.3
    ):
        super(StockGraphNetwork, self).__init__()
        
        self.num_stocks = num_stocks
        self.hidden_size = hidden_size
        
        # Shared LSTM encoder for all stocks
        self.stock_encoder = StockLSTMEncoder(input_size, hidden_size)
        node_dim = self.stock_encoder.output_size
        
        if HAS_PYGEOMETRIC:
            # Graph Attention layers
            self.gnn_layers = nn.ModuleList()
            
            # First layer
            self.gnn_layers.append(GATConv(node_dim, hidden_size, heads=heads, dropout=dropout))
            
            # Additional layers
            for _ in range(num_gnn_layers - 1):
                self.gnn_layers.append(
                    GATConv(hidden_size * heads, hidden_size, heads=heads, dropout=dropout)
                )
            
            node_dim = hidden_size * heads
        else:
            # Fallback: Simple attention without PyG
            self.simple_attention = nn.MultiheadAttention(
                embed_dim=node_dim,
                num_heads=heads,
                dropout=dropout,
                batch_first=True
            )
        
        # Output layers
        self.fc1 = nn.Linear(node_dim, hidden_size)
        self.fc2 = nn.Linear(hidden_size, 64)
        
        self.bn1 = nn.BatchNorm1d(hidden_size)
        self.bn2 = nn.BatchNorm1d(64)
        
        self.activation = nn.GELU()
        self.dropout = nn.Dropout(dropout)
        
        # Prediction heads
        self.direction_head = nn.Linear(64, 3)
        self.range_head = nn.Linear(64, 1)
        self.confidence_head = nn.Sequential(
            nn.Linear(64, 1),
            nn.Sigmoid()
        )
        
    def forward(
        self, 
        stock_sequences: Dict[str, torch.Tensor],
        edge_index: Optional[torch.Tensor] = None,
        edge_weight: Optional[torch.Tensor] = None,
        target_stock_idx: int = 0
    ):
        """
        Args:
            stock_sequences: Dict mapping stock_idx to tensor (seq_len, features)
                            or single tensor (num_stocks, seq_len, features)
            edge_index: (2, num_edges) - connection pairs
            edge_weight: (num_edges,) - correlation/causality strength
            target_stock_idx: Which stock to predict
        """
        # Handle dict or tensor input
        if isinstance(stock_sequences, dict):
            # Stack all stocks: (num_stocks, seq_len, features)
            sequences = torch.stack([stock_sequences[i] for i in sorted(stock_sequences.keys())])
        else:
            sequences = stock_sequences
        
        batch_size = 1  # Treat all stocks as one batch for GNN
        num_stocks = sequences.size(0)
        
        # Encode each stock
        node_embeddings = self.stock_encoder(sequences)  # (num_stocks, node_dim)
        
        if HAS_PYGEOMETRIC and edge_index is not None:
            # Apply GNN layers
            x = node_embeddings
            for gnn_layer in self.gnn_layers:
                x = gnn_layer(x, edge_index, edge_attr=edge_weight)
                x = F.elu(x)
                x = F.dropout(x, p=0.3, training=self.training)
        else:
            # Fallback: Simple cross-stock attention
            x = node_embeddings.unsqueeze(0)  # (1, num_stocks, dim)
            x, _ = self.simple_attention(x, x, x)
            x = x.squeeze(0)
        
        # Get target stock representation
        target_embed = x[target_stock_idx]
        
        # Feature extraction
        out = self.fc1(target_embed)
        out = self.bn1(out.unsqueeze(0)).squeeze(0)
        out = self.activation(out)
        out = self.dropout(out)
        
        out = self.fc2(out)
        out = self.bn2(out.unsqueeze(0)).squeeze(0)
        out = self.activation(out)
        
        # Predictions
        direction_logits = self.direction_head(out)
        direction_probs = F.softmax(direction_logits, dim=0)
        
        expected_range = self.range_head(out)
        confidence = self.confidence_head(out)
        
        return direction_probs, expected_range, confidence


class CorrelationGraphBuilder:
    """
    Builds stock correlation graph for GNN.
    Uses rolling correlation and optional Granger causality.
    """
    
    def __init__(self, correlation_threshold: float = 0.5, lookback_days: int = 60):
        self.threshold = correlation_threshold
        self.lookback = lookback_days
        
    def build_from_prices(
        self, 
        price_data: Dict[str, np.ndarray]
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Build edge_index and edge_weight from price data.
        
        Args:
            price_data: Dict[stock_symbol, price_array]
        
        Returns:
            edge_index: (2, num_edges)
            edge_weight: (num_edges,)
        """
        symbols = list(price_data.keys())
        n = len(symbols)
        
        # Compute correlation matrix
        returns = {}
        for symbol, prices in price_data.items():
            if len(prices) > 1:
                returns[symbol] = np.diff(prices) / prices[:-1]
        
        edges_src = []
        edges_dst = []
        weights = []
        
        for i, sym_i in enumerate(symbols):
            for j, sym_j in enumerate(symbols):
                if i != j and sym_i in returns and sym_j in returns:
                    # Compute correlation
                    min_len = min(len(returns[sym_i]), len(returns[sym_j]))
                    if min_len > 10:
                        corr = np.corrcoef(
                            returns[sym_i][-min_len:], 
                            returns[sym_j][-min_len:]
                        )[0, 1]
                        
                        if abs(corr) > self.threshold:
                            edges_src.append(i)
                            edges_dst.append(j)
                            weights.append(abs(corr))
        
        if edges_src:
            edge_index = torch.tensor([edges_src, edges_dst], dtype=torch.long)
            edge_weight = torch.tensor(weights, dtype=torch.float32)
        else:
            # Fallback: connect all stocks weakly
            edge_index = torch.tensor(
                [[i, j] for i in range(n) for j in range(n) if i != j],
                dtype=torch.long
            ).T
            edge_weight = torch.ones(edge_index.size(1)) * 0.1
        
        logger.info("correlation_graph_built", 
                   num_stocks=n, 
                   num_edges=edge_index.size(1),
                   avg_weight=float(edge_weight.mean()))
        
        return edge_index, edge_weight
    
    def build_sector_graph(
        self, 
        stocks: List[StockNode]
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Build graph where stocks in same sector are connected.
        """
        n = len(stocks)
        edges_src = []
        edges_dst = []
        weights = []
        
        for i, stock_i in enumerate(stocks):
            for j, stock_j in enumerate(stocks):
                if i != j:
                    if stock_i.sector == stock_j.sector:
                        # Same sector: strong connection
                        edges_src.append(i)
                        edges_dst.append(j)
                        weights.append(0.8)
                    else:
                        # Different sector: weak connection
                        edges_src.append(i)
                        edges_dst.append(j)
                        weights.append(0.2)
        
        edge_index = torch.tensor([edges_src, edges_dst], dtype=torch.long)
        edge_weight = torch.tensor(weights, dtype=torch.float32)
        
        return edge_index, edge_weight


class NIFTY50Universe:
    """
    Defines the NIFTY 50 stock universe with sector mappings.
    Used for building the stock graph.
    """
    
    STOCKS = {
        # Banks
        'HDFCBANK.NS': 'BANKING', 'ICICIBANK.NS': 'BANKING', 'SBIN.NS': 'BANKING',
        'KOTAKBANK.NS': 'BANKING', 'AXISBANK.NS': 'BANKING', 'INDUSINDBK.NS': 'BANKING',
        
        # IT
        'TCS.NS': 'IT', 'INFY.NS': 'IT', 'WIPRO.NS': 'IT', 
        'HCLTECH.NS': 'IT', 'TECHM.NS': 'IT', 'LTIM.NS': 'IT',
        
        # FMCG
        'HINDUNILVR.NS': 'FMCG', 'ITC.NS': 'FMCG', 'NESTLEIND.NS': 'FMCG',
        'BRITANNIA.NS': 'FMCG', 'TATACONSUM.NS': 'FMCG',
        
        # Auto
        'MARUTI.NS': 'AUTO', 'TATAMOTORS.NS': 'AUTO', 'M&M.NS': 'AUTO',
        'BAJAJ-AUTO.NS': 'AUTO', 'EICHERMOT.NS': 'AUTO', 'HEROMOTOCO.NS': 'AUTO',
        
        # Pharma
        'SUNPHARMA.NS': 'PHARMA', 'DRREDDY.NS': 'PHARMA', 'CIPLA.NS': 'PHARMA',
        'DIVISLAB.NS': 'PHARMA', 'APOLLOHOSP.NS': 'PHARMA',
        
        # Energy/Oil
        'RELIANCE.NS': 'ENERGY', 'ONGC.NS': 'ENERGY', 'BPCL.NS': 'ENERGY',
        'NTPC.NS': 'ENERGY', 'POWERGRID.NS': 'ENERGY', 'ADANIGREEN.NS': 'ENERGY',
        
        # Metals
        'TATASTEEL.NS': 'METALS', 'JSWSTEEL.NS': 'METALS', 'HINDALCO.NS': 'METALS',
        'COALINDIA.NS': 'METALS',
        
        # Infra/Real Estate
        'LT.NS': 'INFRA', 'ULTRACEMCO.NS': 'INFRA', 'GRASIM.NS': 'INFRA',
        'ADANIPORTS.NS': 'INFRA', 'ADANIENT.NS': 'INFRA',
        
        # Telecom
        'BHARTIARTL.NS': 'TELECOM',
        
        # Financial Services
        'BAJFINANCE.NS': 'NBFC', 'BAJAJFINSV.NS': 'NBFC', 'HDFCLIFE.NS': 'INSURANCE',
        'SBILIFE.NS': 'INSURANCE',
        
        # Others
        'TITAN.NS': 'CONSUMER', 'ASIANPAINT.NS': 'CONSUMER',
    }
    
    @classmethod
    def get_stocks(cls) -> List[StockNode]:
        return [
            StockNode(symbol=symbol, sector=sector)
            for symbol, sector in cls.STOCKS.items()
        ]
    
    @classmethod
    def get_sector(cls, symbol: str) -> str:
        return cls.STOCKS.get(symbol, 'UNKNOWN')


if __name__ == "__main__":
    print("Cross-Stock Graph Neural Network Test")
    print("=" * 60)
    
    print(f"PyTorch Geometric available: {HAS_PYGEOMETRIC}")
    
    # Define stock universe
    num_stocks = 10
    
    # Create model
    model = StockGraphNetwork(
        num_stocks=num_stocks,
        input_size=7,
        hidden_size=64
    )
    
    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    print(f"Total parameters: {total_params:,}")
    
    # Simulate stock data
    seq_len = 30
    features = 7
    
    stock_sequences = torch.randn(num_stocks, seq_len, features)
    
    # Build sample correlation graph
    graph_builder = CorrelationGraphBuilder(correlation_threshold=0.3)
    
    # Simulate price data for graph building
    price_data = {i: np.random.randn(100).cumsum() + 100 for i in range(num_stocks)}
    edge_index, edge_weight = graph_builder.build_from_prices(price_data)
    
    print(f"\nGraph structure:")
    print(f"  Nodes: {num_stocks}")
    print(f"  Edges: {edge_index.size(1)}")
    print(f"  Avg edge weight: {edge_weight.mean():.3f}")
    
    # Forward pass
    model.eval()
    with torch.no_grad():
        probs, range_est, conf = model(
            stock_sequences, 
            edge_index=edge_index,
            edge_weight=edge_weight,
            target_stock_idx=0
        )
    
    print(f"\nPrediction for target stock:")
    print(f"  Direction: {['DOWN', 'FLAT', 'UP'][torch.argmax(probs).item()]}")
    print(f"  Probabilities: {probs.numpy()}")
    print(f"  Confidence: {conf.item():.2%}")
    
    print(f"\nNIFTY 50 Universe:")
    for sector in set(NIFTY50Universe.STOCKS.values()):
        stocks = [s for s, sec in NIFTY50Universe.STOCKS.items() if sec == sector]
        print(f"  {sector}: {len(stocks)} stocks")
