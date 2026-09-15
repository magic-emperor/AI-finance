"""
Path A smoke tests: DB columns, council verdicts, signal generators, resolver helpers.
Run: python -m pytest tests/test_path_a.py -v
      or: python tests/test_path_a.py
"""
import os
import sys

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_signal_generators_produce_signals():
    """Signal generators produce >= 1 signal — Causal-Ensemble STRONG BUY in genuine RANGING regime."""
    import pandas as pd
    from market_agent.brain.signal_generators import generate_brain_signals
    import numpy as np

    # Validated data (do NOT change seed/params without re-running diagnose7.py):
    #   seed=1, sine-wave oscillation + 8-bar drop at end
    # -> Regime-Ensemble: RANGING (ADX=15 < 22)
    # -> Causal-Ensemble: STRONG BUY (pct_b=-0.045, RSI=28.9)
    # -> generate_brain_signals: 1+ signal, R:R >= 1.5
    np.random.seed(1)
    n = 100
    t = np.arange(n)
    close = 100.0 + 1.5 * np.sin(2 * np.pi * t / 40) + np.random.randn(n) * 0.15
    close[-8:] = close[-8] - np.linspace(0, 2.5, 8)  # oversold drop

    hist = pd.DataFrame({
        "Close":  close, "Open":   close * 0.999,
        "High":   close + 0.8,   "Low":    close - 0.8,
        "Volume": np.full(n, 1_200_000.0),
    })
    current_price = float(close[-1])
    atr = float((hist['High'] - hist['Low']).rolling(14).mean().iloc[-1])

    signals = generate_brain_signals("TEST-USD", current_price, atr, hist, {}, regime="RANGING")
    assert isinstance(signals, list)
    assert len(signals) >= 1, (
        f"Expected Causal-Ensemble STRONG BUY in genuine RANGING. "
        f"Got {len(signals)} signals. Check diagnose7.py to verify test data.\n"
        f"Note: symbol must NOT end in .NS/.BO (IST market-hours filter would block test runs outside market hours)."
    )
    for s in signals:
        assert s.get("symbol") == "TEST-USD"
        assert s.get("direction") in ("BUY", "SELL")
        assert s.get("entry_price", 0) > 0
        entry = s.get("entry_price", 0)
        t1    = s.get("target_1",    0)
        sl    = s.get("stop_loss",   0)
        if entry > 0 and sl > 0 and t1 > 0 and abs(sl - entry) > 0:
            rr = abs(t1 - entry) / abs(sl - entry)
            assert rr >= 1.5, f"R:R {rr:.2f} below minimum for {s.get('model_used')}"

def test_resolver_has_path_a_columns_and_helpers():
    """SignalResolver has binary_win in resolution and get_brain_points / get_recent_predictions_by_model."""
    from market_agent.learning.signal_resolver import SignalResolver, SignalPrediction

    assert hasattr(SignalPrediction, "binary_win")
    assert hasattr(SignalPrediction, "actual_direction")
    assert hasattr(SignalPrediction, "outcome_return_pct")
    assert hasattr(SignalPrediction, "timeframe_min")
    assert hasattr(SignalResolver, "get_brain_points")
    assert hasattr(SignalResolver, "get_recent_predictions_by_model")


def test_council_verdict_storage():
    """PostgresStorage has store_council_verdict and get_latest_council_verdict."""
    from market_agent.data.storage.postgres import PostgresStorage

    assert hasattr(PostgresStorage, "store_council_verdict")
    assert hasattr(PostgresStorage, "get_latest_council_verdict")


def test_cortex_stores_council_verdict():
    """Cortex has _store_council_verdict_from_debate (method exists)."""
    from market_agent.brain import cortex

    assert hasattr(cortex.CortexGatekeeper, "_store_council_verdict_from_debate")


def test_data_audit_runs():
    """Data audit script runs without error."""
    from market_agent.runner.data_audit import run_audit

    out = run_audit()
    assert isinstance(out, str)
    assert "market_data" in out or "signal_predictions" in out or "Summary" in out


def test_backtester_path_a_returns():
    """Backtester returns binary_wins, net_return_pct; run_backtest_with_buy_and_hold returns buy_hold_return_pct."""
    from market_agent.training.backtester import Backtester

    bt = Backtester()
    assert hasattr(bt, "run_backtest_with_buy_and_hold")
    # Run with a common symbol (may have insufficient data in CI)
    result = bt.run_backtest("AAPL", "Intraday (Scalp)", None, "1h")
    if result.get("total", 0) > 0:
        assert "binary_wins" in result
        assert "net_return_pct" in result
        assert "commission_total" in result
        assert "max_drawdown_pct" in result
    result2 = bt.run_backtest_with_buy_and_hold("AAPL", "Intraday (Scalp)", None, "1h")
    assert "buy_hold_return_pct" in result2
    assert "strategy_return_pct" in result2


def test_paper_trader():
    """PaperTrader open/close position and P&L summary."""
    from market_agent.trading.paper_trader import PaperTrader

    pt = PaperTrader()
    pt.open_position(
        {"symbol": "TEST", "direction": "BUY", "entry_price": 100, "target_1": 101, "target_2": 102, "stop_loss": 99}
    )
    assert len(pt.get_open_positions()) == 1
    closed = pt.close_position("TEST", 101, reason="T1")
    assert closed is not None
    assert closed["pnl"] is not None
    assert closed["symbol"] == "TEST"
    summary = pt.get_pnl_summary()
    assert summary["trade_count"] == 1
    assert summary["open_count"] == 0
    assert "total_pnl" in summary


def test_news_sentiment_plan_a():
    """News sentiment Plan A: storage, backfill API, RAG block."""
    from market_agent.brain.council_memory import CouncilMemory, get_council_memory

    assert "NEWS_SENTIMENT" in CouncilMemory.MEMORY_TYPES
    assert hasattr(CouncilMemory, "store_news_sentiment_outcome")

    from market_agent.research.news_sentiment_backfill import backfill_symbol

    out = backfill_symbol("AAPL", days=7)
    assert isinstance(out, dict)
    assert "stored" in out and "skipped" in out and "errors" in out

    from market_agent.brain import gemini_client

    src = open(gemini_client.__file__, encoding="utf-8").read()
    assert "NEWS_SENTIMENT" in src and "PAST SIMILAR SENTIMENT" in src


def test_remaining_three():
    """Remaining items: news tool, news sentiment rules, backfill docs/script."""
    from market_agent.brain import cortex

    assert hasattr(cortex.CortexGatekeeper, "_fetch_news_for_council")

    from market_agent.research import news_sentiment_rules

    assert hasattr(news_sentiment_rules, "get_news_sentiment_guidance")
    assert hasattr(news_sentiment_rules, "news_signal_from_guidance")
    g = news_sentiment_rules.get_news_sentiment_guidance("AAPL")
    assert isinstance(g, str)

    from market_agent.runner.backfill_historical_ohlc import run_backfill

    assert callable(run_backfill)
    result = run_backfill([])
    assert isinstance(result, dict)
    assert "status" in result and "symbols" in result

    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(os.path.join(base, ".agent", "ACCURACY_AND_BACKTEST.md"))


if __name__ == "__main__":
    test_signal_generators_produce_signals()
    print("  OK: signal_generators")
    test_resolver_has_path_a_columns_and_helpers()
    print("  OK: resolver path A columns and helpers")
    test_council_verdict_storage()
    print("  OK: council verdict storage")
    test_cortex_stores_council_verdict()
    print("  OK: cortex council verdict")
    test_data_audit_runs()
    print("  OK: data_audit")
    test_backtester_path_a_returns()
    print("  OK: backtester Path A")
    test_paper_trader()
    print("  OK: paper_trader")
    test_news_sentiment_plan_a()
    print("  OK: news_sentiment_plan_a")
    test_remaining_three()
    print("  OK: remaining_three (news tool, news rules, backfill)")
    print("All Path A smoke tests passed.")
