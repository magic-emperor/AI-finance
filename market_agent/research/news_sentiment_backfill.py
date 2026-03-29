"""
News Sentiment Plan A — Backfill: align news (with sentiment_score) to next-day returns and store in FAISS.

Run: python -m market_agent.research.news_sentiment_backfill SYMBOL [--days 30]
     or call backfill_symbol(symbol, days=30) from code.
"""

import argparse
import structlog
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

logger = structlog.get_logger()


def _parse_news_date(published: Any) -> Optional[datetime]:
    """Parse published field (string or datetime) to naive date for alignment."""
    if published is None:
        return None
    if isinstance(published, datetime):
        return published.replace(tzinfo=None) if published.tzinfo else published
    s = str(published).strip()[:30]
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y"):
        try:
            return datetime.strptime(s[: len(fmt.replace("%Z", "").replace("%z", ""))].strip(), fmt.replace(" %Z", "").replace(" %z", ""))
        except ValueError:
            continue
    try:
        import email.utils
        parsed = email.utils.parsedate_to_datetime(s)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        pass
    return None


def _get_return_1d(symbol: str, trade_date: datetime) -> Optional[float]:
    """Get next-day return (close on/before trade_date to close of next trading day) in percent. Uses yfinance."""
    try:
        import yfinance as yf
        end = (trade_date + timedelta(days=5))
        start = (trade_date - timedelta(days=3))
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, interval="1d")
        if df is None or len(df) < 2:
            return None
        df = df.sort_index()
        try:
            pd = __import__("pandas")
            trade_ts = pd.Timestamp(trade_date.date() if hasattr(trade_date, "date") else trade_date)
        except Exception:
            trade_ts = trade_date
        # Rows on or before trade_d: take last; then next row is next trading day
        on_or_before = df.index <= trade_ts
        if not on_or_before.any():
            return None
        close_day = float(df.loc[on_or_before, "Close"].iloc[-1])
        last_idx = df.index[on_or_before][-1]
        after = df.index > last_idx
        if not after.any():
            return None
        close_next = float(df.loc[after, "Close"].iloc[0])
        return (close_next - close_day) / close_day * 100
    except Exception as e:
        logger.debug("return_1d_failed", symbol=symbol, date=str(trade_date), error=str(e))
        return None


def backfill_symbol(symbol: str, days: int = 30) -> Dict[str, Any]:
    """
    Fetch news for symbol, align each item to a trading date, compute return_1d, store in council memory.

    Returns: {stored: int, skipped: int, errors: int, sample_dates: list}
    """
    try:
        from market_agent.research.news_aggregator import news_aggregator
        from market_agent.brain.council_memory import get_council_memory
    except ImportError as e:
        logger.error("news_backfill_import_failed", error=str(e))
        return {"stored": 0, "skipped": 0, "errors": 1, "sample_dates": []}

    memory = get_council_memory()
    news_list = news_aggregator.fetch_news(symbol, limit=50)
    if not news_list:
        return {"stored": 0, "skipped": 0, "errors": 0, "sample_dates": []}

    stored = 0
    skipped = 0
    errors = 0
    seen = set()

    for item in news_list:
        published = item.get("published")
        sentiment = item.get("sentiment_score")
        if sentiment is None:
            skipped += 1
            continue
        dt = _parse_news_date(published)
        if dt is None:
            skipped += 1
            continue
        date_key = (symbol, dt.date().isoformat())
        if date_key in seen:
            skipped += 1
            continue
        return_1d = _get_return_1d(symbol, dt)
        if return_1d is None:
            errors += 1
            continue
        seen.add(date_key)
        try:
            memory.store_news_sentiment_outcome(
                symbol=symbol,
                date=dt.date().isoformat(),
                sentiment_score=float(sentiment),
                return_1d=return_1d,
            )
            stored += 1
        except Exception as e:
            logger.warning("store_news_sentiment_failed", symbol=symbol, date=date_key[1], error=str(e))
            errors += 1

    sample = sorted(seen)[:5] if seen else []
    return {
        "stored": stored,
        "skipped": skipped,
        "errors": errors,
        "sample_dates": [d[1] for d in sample],
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill news sentiment → return_1d into FAISS (Plan A)")
    parser.add_argument("symbol", nargs="?", default="AAPL", help="Symbol to backfill (e.g. AAPL, ITC.NS)")
    parser.add_argument("--days", type=int, default=30, help="Look back days for news (default 30)")
    args = parser.parse_args()
    result = backfill_symbol(args.symbol, days=args.days)
    print(f"Stored: {result['stored']}, Skipped: {result['skipped']}, Errors: {result['errors']}")
    if result.get("sample_dates"):
        print("Sample dates:", result["sample_dates"])


if __name__ == "__main__":
    main()
