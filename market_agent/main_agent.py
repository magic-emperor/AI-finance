import os
import time
import structlog
from datetime import datetime
import pandas as pd
import yfinance as yf

# Core Layers
from market_agent.data.storage.postgres import PostgresStorage
from market_agent.data.ingestion.market_feed import MarketFeed
from market_agent.patterns.perception import PerceptionEngine
from market_agent.patterns.analytics import AnalyticsEngine
from market_agent.models.micro_price_nn import MicroPriceNN
from market_agent.models.preprocessing import DataPreprocessor
from market_agent.learning.calibrator import ConfidenceCalibrator
from market_agent.learning.evaluator import RegretEngine
from market_agent.agent.curiosity_engine import CuriosityEngine
from market_agent.research.coordinator import ResearchCoordinator
from market_agent.reporting.report_synthesizer import ReportSynthesizer
from market_agent.reporting.semantic_narrator import SemanticNarrator

# Phase 11: Institutional & Discovery
from market_agent.data.ingestion.sentiment_sentinel import SentimentSentinel
from market_agent.research.opportunity_detector import OpportunityDetector
from market_agent.data.ingestion.bulk_ingester import BulkFreeIngester

logger = structlog.get_logger()

class MarketResearchAgent:
    """
    The Unified AI Research Assistant (Layers 0-9)
    """
    def __init__(self, symbol: str):
        self.symbol = symbol
        
        # 1. Foundation & Storage
        self.storage = PostgresStorage()
        
        # 2. Perception & Analytics
        self.perception = PerceptionEngine()
        self.analytics = AnalyticsEngine()
        
        # 3. Model & Preprocessing
        self.preprocessor = DataPreprocessor()
        self.model = MicroPriceNN(input_size=7)
        # Load best model if exists
        # self.model.load_state_dict(torch.load("path"))
        
        # 4. Self-Awareness & Learning
        self.calibrator = ConfidenceCalibrator()
        self.evaluator = RegretEngine(self.storage)
        
        # 5. Curiosity & Research
        self.curiosity = CuriosityEngine()
        self.research = ResearchCoordinator(self.storage)
        
        # 6. Communication
        self.synthesizer = ReportSynthesizer()
        self.narrator = SemanticNarrator()
        
        # 7. Institutional Discovery (Phase 11)
        self.sentinel = SentimentSentinel()
        self.detector = OpportunityDetector()
        self.ingester = BulkFreeIngester(self.storage)

        logger.info("agent_system_ready", symbol=self.symbol)

    def run_iteration(self):
        """
        One complete research cycle.
        """
        logger.info("iteration_started", symbol=self.symbol)
        
        # L0: Fetch Data
        raw_data = self.storage.get_latest_data(self.symbol, "1m", limit=100)
        if not raw_data:
            logger.warning("no_data_available", symbol=self.symbol)
            return

        df = pd.DataFrame([{"timestamp": d["timestamp"], **d["data"]} for d in raw_data])
        df.set_index("timestamp", inplace=True)
        
        # L1: Perception
        perception_data = self.perception.get_perception_snapshot(df)
        
        # L2-3: Patterns & Analytics
        analytics_data = self.analytics.analyze(df)
        
        # L4: Intelligence (NN Prediction - Mocked if torch error)
        # In a real run, we preprocess and run model.forward()
        prediction = {
            "direction": "UP" if perception_data["vwap"] < df["close"].iloc[-1] else "DOWN",
            "confidence": 0.51, # Simulating a sub-threshold case for testing
            "price_range": [float(df["close"].iloc[-1]), float(df["close"].iloc[-1] * 1.01)]
        }
        
        # L5: Self-Awareness
        calibrated_conf = self.calibrator.calibrate([0.1, 0.1, 0.51], analytics_data["regime"], "micro_v1")
        
        # L6-8: Curiosity & Research
        # NEW: Check for logic gaps (Low confidence triggers research)
        self.curiosity.check_logic_gap(prediction, analytics_data)
        self.curiosity.check_for_contradiction(prediction, analytics_data)
        
        tasks = self.curiosity.get_pending_tasks()
        research_finding = ""
        if tasks:
            task = tasks.pop(0)
            research_finding = self.research.perform_deep_research(
                task, self.symbol, analytics_data["regime"], perception_data
            )
            
        # Phase 11: Broad Market Discovery
        opportunities = self.run_discovery_radar()
            
        # L9: Communication & Actionability (Silence Mode)
        evaluation = {
            "calibrated_confidence": calibrated_conf,
            "reliability_score": "LOW" if calibrated_conf < 0.52 else "MODERATE",
            "attribution": "Regime Volatility" if calibrated_conf < 0.52 else "Regime Match",
            "status": "SILENCE_MODE" if calibrated_conf < 0.52 else "ACTIVE"
        }
        
        report_data = self.synthesizer.synthesize(
            self.symbol, perception_data, analytics_data, prediction, evaluation, research_finding, opportunities
        )
        
        markdown_report = self.narrator.generate_story(report_data)
        
        # Save Report
        filename = f"reports/report_{self.symbol}_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
        os.makedirs("reports", exist_ok=True)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(markdown_report)
            
        logger.info("iteration_complete", report_saved=filename)
        return markdown_report

    def run_discovery_radar(self):
        """
        Broad Market Sentinel: Scans for 'Coiled Spring' opportunities 
        beyond the primary symbol.
        """
        logger.info("discovery_radar_started")
        
        # 1. Scan broad news for velocity spikes
        trending_tickers = self.sentinel.scan_broad_news()
        
        opportunities = []
        for ticker in trending_tickers:
            if ticker == self.symbol: continue # Skip primary symbol
                
            # 2. Fetch lightweight context (Last 20 bars of 1h)
            # This is fast and small, avoids memory spikes.
            ticker_yf = f"{ticker}.NS" if not ticker.endswith(".NS") else ticker
            try:
                # Minimal fetch for discovery
                df_discovery = yf.Ticker(ticker_yf).history(period="1mo", interval="1h")
                if df_discovery.empty: continue
                
                # 3. Deep Thinking: Is it a Coiled Spring?
                opp = self.detector.evaluate_opportunity(ticker, df_discovery, self.sentinel.mention_counts.get(ticker, 0))
                
                if opp["status"] != "NEUTRAL":
                    opportunities.append(opp)
                    # Optionally store discovery data for permanent context
                    # self.storage.store_ohlc(...)
            except Exception as e:
                logger.error("discovery_fetch_failed", ticker=ticker, error=str(e))

        logger.info("discovery_radar_complete", opportunities_found=len(opportunities))
        return opportunities

if __name__ == "__main__":
    agent = MarketResearchAgent("ITC.NS")
    # Run once for testing
    print("\n--- RUNNING AGENT CYCLE ---\n")
    report = agent.run_iteration()
    print(report)
