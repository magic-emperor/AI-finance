
import structlog
import time
import sys
import site

logger = structlog.get_logger()

# Ensure user site packages are in path (Streamlit sometimes misses them)
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.append(user_site)

def fetch_nse_price(symbol: str) -> float:
    """
    Fetch live price using nsepython library.
    Symbol: "ITC.NS" -> "ITC"
    """
    try:
        try:
            from nsepython import nse_quote
        except ImportError:
            # Fallback: try adding common paths
            if user_site not in sys.path:
                 sys.path.append(user_site)
            from nsepython import nse_quote
        
        clean_sym = symbol.replace(".NS", "").replace("^", "")
        if "NSEBANK" in clean_sym:
            clean_sym = "NIFTY BANK"
            
        quote = nse_quote(clean_sym)
        if quote and 'priceInfo' in quote:
            # Handle standard equity quote structure
            price = quote['priceInfo'].get('lastPrice')
            if price:
                return float(price)
        elif quote and 'lastPrice' in quote:
             # Handle indices or alternative structure
            return float(quote['lastPrice'])
            
    except Exception as e:
        logger.error("nsepython_fetch_failed", symbol=symbol, error=str(e))
        
    return None
