
import requests
import time
import structlog

logger = structlog.get_logger()

# Simple cache: {symbol: (price, timestamp)}
_PRICE_CACHE = {}
_CACHE_TTL = 30  # seconds

# Simple rate limiting: 25 calls per minute
_CALL_HISTORY = []
_MAX_CALLS_PER_MIN = 25

def fetch_coingecko_price(symbol: str) -> float:
    """
    Fetch price from CoinGecko API (No API Key required for simple/price).
    Symbol format: "BTC-USD" -> 'bitcoin'
    """
    global _CALL_HISTORY

    # 1. Map common symbols to CoinGecko IDs
    # You can expand this mapping as needed
    cg_ids = {
        "BTC-USD": "bitcoin",
        "ETH-USD": "ethereum",
        "SOL-USD": "solana",
        "XRP-USD": "ripple",
        "DOGE-USD": "dogecoin",
        "ADA-USD": "cardano",
        "BTC": "bitcoin",
        "ETH": "ethereum",
        # Forex & Commodities (CoinGecko is mainly crypto, but has some pairs)
        "GBPJPY=X": "british-pound-sterling", # Approximations
        "USDJPY=X": "japanese-yen",
        "GC=F": "tether-gold", # PAX Gold or similar for proxy if needed
        "CL=F": "wti-crude-oil" # Very unreliable for non-crypto on CG
    }
    
    clean_sym = symbol.upper().replace("/", "-")
    coin_id = cg_ids.get(clean_sym)
    
    if not coin_id:
        return None
        
    # 2. Check cache
    if coin_id in _PRICE_CACHE:
        price, ts = _PRICE_CACHE[coin_id]
        if time.time() - ts < _CACHE_TTL:
            return price

    # 3. Rate Limit Check (25 calls per rolling 60s)
    now = time.time()
    _CALL_HISTORY = [t for t in _CALL_HISTORY if now - t < 60]
    if len(_CALL_HISTORY) >= _MAX_CALLS_PER_MIN:
        logger.warning("coingecko_rate_limit_exceeded", count=len(_CALL_HISTORY))
        return None

    # 4. Fetch
    try:
        _CALL_HISTORY.append(now)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        # Add user-agent to avoid immediate 403
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        # Strict 2s timeout to prevent UI freezing
        resp = requests.get(url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if coin_id in data and 'usd' in data[coin_id]:
                price = float(data[coin_id]['usd'])
                _PRICE_CACHE[coin_id] = (price, time.time())
                return price
        else:
            logger.warning("coingecko_error", status=resp.status_code, symbol=symbol)
            
    except Exception as e:
        logger.error("coingecko_fetch_failed", error=str(e), symbol=symbol)
        
    return None
