"""
Optional 2y backfill for market_data (Path A §9, §11 Phase 5).
Fetches 2 years of 1h + 1 month of 1m per symbol and stores via BulkFreeIngester.

Run: python -m market_agent.runner.backfill_historical_ohlc
     python -m market_agent.runner.backfill_historical_ohlc --symbols AAPL ITC.NS RELIANCE.NS
"""
import argparse
import structlog
from market_agent.data.storage.postgres import PostgresStorage
from market_agent.data.ingestion.bulk_ingester import BulkFreeIngester

logger = structlog.get_logger()

DEFAULT_SYMBOLS = ["ITC.NS", "AAPL"]


def run_backfill(symbols: list = None) -> dict:
    """
    Run institutional bootstrap (2y 1h + 1mo 1m) for each symbol.
    Returns: {"symbols": [...], "status": "ok" | "error", "message": str}
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    try:
        storage = PostgresStorage()
        ingester = BulkFreeIngester(storage)
        logger.info("backfill_historical_ohlc_start", symbols=symbols)
        ingester.run_institutional_bootstrap(symbols)
        logger.info("backfill_historical_ohlc_done", symbols=symbols)
        return {"symbols": symbols, "status": "ok", "message": f"Backfilled 2y 1h + 1mo 1m for {len(symbols)} symbols."}
    except Exception as e:
        logger.error("backfill_historical_ohlc_failed", symbols=symbols, error=str(e))
        return {"symbols": symbols, "status": "error", "message": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Backfill market_data with 2y 1h + 1mo 1m per symbol (Path A optional)."
    )
    parser.add_argument(
        "--symbols", nargs="+", default=DEFAULT_SYMBOLS,
        help=f"Symbols to backfill (default: {DEFAULT_SYMBOLS})",
    )
    args = parser.parse_args()
    result = run_backfill(args.symbols)
    print(result["message"])
    if result["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
