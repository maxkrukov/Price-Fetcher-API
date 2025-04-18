from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from dotenv import load_dotenv
import httpx
import os
from time import time
import logging
from decimal import Decimal, getcontext
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# === Configuration Setup ===
load_dotenv()
getcontext().prec = 8

@dataclass
class AppConfig:
    CACHE_TTL: int = int(os.getenv("CACHE_TTL_SECONDS", 300))
    DEFAULT_QUOTE: str = os.getenv("DEFAULT_QUOTE", "USDT").upper()
    COINGECKO_LIST_TTL: int = int(os.getenv("COINGECKO_LIST_TTL", 86400))
    FAILURE_TTL: int = int(os.getenv("FAILURE_TTL", 600))
    INTERMEDIATE_SYMBOL: str = os.getenv("INTERMEDIATE_SYMBOL", "USDT").upper()
    HTTP_TIMEOUT: int = int(os.getenv("HTTP_TIMEOUT", 5))

config = AppConfig()

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === Data Models ===
@dataclass
class PriceResult:
    source: str
    price: float
    base_asset: str
    quote_asset: str
    inverted: bool = False
    timestamp: float = field(default_factory=time)
    original_price: Optional[float] = None
    
    @property
    def expires_in(self) -> float:
        """Calculates remaining cache time in seconds"""
        return max(0, config.CACHE_TTL - (time() - self.timestamp))
    
    @property
    def expires_at(self) -> datetime:
        """Returns datetime when this price expires"""
        return datetime.now() + timedelta(seconds=self.expires_in)
    
    @property
    def pair(self) -> str:
        """Returns the trading pair in standard format"""
        return f"{self.base_asset}/{self.quote_asset}"

@dataclass
class DerivedPriceResult(PriceResult):
    components: List[PriceResult] = field(default_factory=list)
    
    @property
    def expires_in(self) -> float:
        """Calculates remaining cache time based on component with shortest expiry"""
        if not self.components:
            return config.CACHE_TTL
        return min(comp.expires_in for comp in self.components)

# === Cache System ===
class PriceCache:
    def __init__(self):
        self.price_data: Dict[str, PriceResult] = {}
        self.coin_ids: Dict[str, str] = {}
        self.failures: Dict[str, float] = {}
        self.coingecko_list: List[Dict] = []
        self.coingecko_list_last_updated: float = 0

    def _make_key(self, source: str, base: str, quote: str) -> str:
        return f"{source.lower()}_{base.upper()}_{quote.upper()}"

    def get_price(self, source: str, base: str, quote: str) -> Optional[PriceResult]:
        key = self._make_key(source, base, quote)
        result = self.price_data.get(key)
        if result and result.expires_in > 0:
            return result
        return None

    def set_price(self, result: PriceResult) -> None:
        key = self._make_key(result.source, result.base_asset, result.quote_asset)
        self.price_data[key] = result

    def is_failure_cached(self, source: str, base: str, quote: str) -> bool:
        key = self._make_key(source, base, quote)
        failure_time = self.failures.get(key, 0)
        return (time() - failure_time) < config.FAILURE_TTL

    def cache_failure(self, source: str, base: str, quote: str) -> None:
        key = self._make_key(source, base, quote)
        self.failures[key] = time()

cache = PriceCache()

# === Exchange Interfaces ===
class ExchangeBase:
    NAME = "base"
    PRIORITY = 0

    @classmethod
    async def fetch_price(cls, client: httpx.AsyncClient, base: str, quote: str) -> Optional[PriceResult]:
        raise NotImplementedError

class BinanceExchange(ExchangeBase):
    NAME = "binance"
    PRIORITY = 1

    @classmethod
    async def fetch_price(cls, client: httpx.AsyncClient, base: str, quote: str) -> Optional[PriceResult]:
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={base}{quote}"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return PriceResult(
                source=cls.NAME,
                price=float(data["price"]),
                base_asset=base,
                quote_asset=quote
            )
        except Exception as e:
            logger.warning(f"{cls.NAME} error for {base}/{quote}: {str(e)}")
            return None

class OKXExchange(ExchangeBase):
    NAME = "okx"
    PRIORITY = 2

    @classmethod
    async def fetch_price(cls, client: httpx.AsyncClient, base: str, quote: str) -> Optional[PriceResult]:
        try:
            url = f"https://www.okx.com/api/v5/market/ticker?instId={base}-{quote}"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return PriceResult(
                source=cls.NAME,
                price=float(data["data"][0]["last"]),
                base_asset=base,
                quote_asset=quote
            )
        except Exception as e:
            logger.warning(f"{cls.NAME} error for {base}/{quote}: {str(e)}")
            return None

class KrakenExchange(ExchangeBase):
    NAME = "kraken"
    PRIORITY = 3

    @classmethod
    async def fetch_price(cls, client: httpx.AsyncClient, base: str, quote: str) -> Optional[PriceResult]:
        try:
            pair = f"{base}{quote}"
            url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            ticker = next(iter(data["result"].values()))
            return PriceResult(
                source=cls.NAME,
                price=float(ticker["c"][0]),
                base_asset=base,
                quote_asset=quote
            )
        except Exception as e:
            logger.warning(f"{cls.NAME} error for {base}/{quote}: {str(e)}")
            return None

class CoinbaseExchange(ExchangeBase):
    NAME = "coinbase"
    PRIORITY = 4

    @classmethod
    async def fetch_price(cls, client: httpx.AsyncClient, base: str, quote: str) -> Optional[PriceResult]:
        try:
            # Try direct pair first
            url = f"https://api.coinbase.com/v2/prices/{base}-{quote}/spot"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return PriceResult(
                source=cls.NAME,
                price=float(data["data"]["amount"]),
                base_asset=base,
                quote_asset=quote
            )
        except Exception as e:
            logger.warning(f"{cls.NAME} direct pair error for {base}/{quote}: {str(e)}")
            # Try inverted pair if direct fails
            try:
                url = f"https://api.coinbase.com/v2/prices/{quote}-{base}/spot"
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                inverted_price = float(data["data"]["amount"])
                if inverted_price != 0:
                    return PriceResult(
                        source=cls.NAME,
                        price=float(Decimal(1) / Decimal(inverted_price)),
                        inverted=True,
                        original_price=inverted_price,
                        base_asset=base,
                        quote_asset=quote
                    )
                return None
            except Exception as e:
                logger.warning(f"{cls.NAME} inverted pair error for {quote}/{base}: {str(e)}")
                return None

class MEXCExchange(ExchangeBase):
    NAME = "mexc"
    PRIORITY = 5

    @classmethod
    async def fetch_price(cls, client: httpx.AsyncClient, base: str, quote: str) -> Optional[PriceResult]:
        try:
            url = f"https://api.mexc.com/api/v3/ticker/price?symbol={base}{quote}"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return PriceResult(
                source=cls.NAME,
                price=float(data["price"]),
                base_asset=base,
                quote_asset=quote
            )
        except Exception as e:
            logger.warning(f"{cls.NAME} error for {base}/{quote}: {str(e)}")
            return None

class CoinGeckoExchange(ExchangeBase):
    NAME = "coingecko"
    PRIORITY = 6  # Lowest priority
    
    @classmethod
    async def fetch_coin_id(cls, client: httpx.AsyncClient, symbol: str) -> Optional[str]:
        symbol = symbol.lower()
        if symbol in cache.coin_ids:
            return cache.coin_ids[symbol]

        if (not cache.coingecko_list or 
            (time() - cache.coingecko_list_last_updated) > config.COINGECKO_LIST_TTL):
            try:
                response = await client.get("https://api.coingecko.com/api/v3/coins/list")
                response.raise_for_status()
                cache.coingecko_list = response.json()
                cache.coingecko_list_last_updated = time()
            except Exception as e:
                logger.warning(f"Failed to fetch CoinGecko coin list: {str(e)}")
                return None

        for coin in cache.coingecko_list:
            if coin["symbol"].lower() == symbol:
                cache.coin_ids[symbol] = coin["id"]
                return coin["id"]
        return None

    @classmethod
    def _normalize_currency(cls, currency: str) -> str:
        return "usd" if currency.upper() in {"USDT", "USDC"} else currency.lower()

    @classmethod
    async def fetch_price(cls, client: httpx.AsyncClient, base: str, quote: str) -> Optional[PriceResult]:
        try:
            base_id = await cls.fetch_coin_id(client, base)
            quote_currency = cls._normalize_currency(quote)
            
            if base_id:
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={base_id}&vs_currencies={quote_currency}"
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                
                if base_id in data and quote_currency in data[base_id]:
                    return PriceResult(
                        source=cls.NAME,
                        price=float(data[base_id][quote_currency]),
                        base_asset=base,
                        quote_asset=quote
                    )

            # Try inverted pair
            quote_id = await cls.fetch_coin_id(client, quote)
            if quote_id:
                base_currency = cls._normalize_currency(base)
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={quote_id}&vs_currencies={base_currency}"
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                
                if quote_id in data and base_currency in data[quote_id]:
                    inverted_price = float(data[quote_id][base_currency])
                    if inverted_price != 0:
                        return PriceResult(
                            source=cls.NAME,
                            price=float(Decimal(1) / Decimal(inverted_price)),
                            inverted=True,
                            original_price=inverted_price,
                            base_asset=base,
                            quote_asset=quote
                        )
            return None
        except Exception as e:
            logger.warning(f"CoinGecko error for {base}/{quote}: {str(e)}")
            return None

# === Exchange Registry ===
EXCHANGES = [
    BinanceExchange,
    OKXExchange,
    KrakenExchange,
    CoinbaseExchange,
    MEXCExchange,
    CoinGeckoExchange
]

EXCHANGES.sort(key=lambda x: x.PRIORITY)

# === Price Service ===
class PriceService:
    @staticmethod
    async def get_direct_price(base: str, quote: str) -> List[PriceResult]:
        results = []
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
            # First try all exchanges except CoinGecko
            for exchange in [e for e in EXCHANGES if e.NAME != "coingecko"]:
                cached = cache.get_price(exchange.NAME, base, quote)
                if cached:
                    results.append(cached)
                    continue

                if cache.is_failure_cached(exchange.NAME, base, quote):
                    logger.info(f"Skipping {exchange.NAME} for {base}/{quote} - recent failure")
                    continue

                try:
                    result = await exchange.fetch_price(client, base, quote)
                    if result:
                        cache.set_price(result)
                        results.append(result)
                    else:
                        cache.cache_failure(exchange.NAME, base, quote)
                except Exception as e:
                    logger.error(f"Error fetching from {exchange.NAME}: {str(e)}")
                    cache.cache_failure(exchange.NAME, base, quote)

            # Only try CoinGecko if we have no results from other exchanges
            if not results:
                coingecko = next((e for e in EXCHANGES if e.NAME == "coingecko"), None)
                if coingecko:
                    cached = cache.get_price(coingecko.NAME, base, quote)
                    if cached:
                        results.append(cached)
                    elif not cache.is_failure_cached(coingecko.NAME, base, quote):
                        try:
                            result = await coingecko.fetch_price(client, base, quote)
                            if result:
                                cache.set_price(result)
                                results.append(result)
                            else:
                                cache.cache_failure(coingecko.NAME, base, quote)
                        except Exception as e:
                            logger.error(f"Error fetching from CoinGecko: {str(e)}")
                            cache.cache_failure(coingecko.NAME, base, quote)

        return results

    @staticmethod
    async def get_derived_price(base: str, quote: str, intermediate: str = None) -> Optional[DerivedPriceResult]:
        intermediate = intermediate or config.INTERMEDIATE_SYMBOL
        if base.upper() == intermediate or quote.upper() == intermediate:
            return None

        # Get first leg: BASE/INTERMEDIATE (e.g. RTM/USDT)
        first_leg = await PriceService.get_direct_price(base, intermediate)
        if not first_leg:
            return None

        # Get second leg: QUOTE/INTERMEDIATE (e.g. IDEX/USDT)
        second_leg = await PriceService.get_direct_price(quote, intermediate)
        if not second_leg:
            return None

        # Use best available prices from each leg
        best_first = max(first_leg, key=lambda x: x.price)
        best_second = max(second_leg, key=lambda x: x.price)

        # Calculate derived price: (BASE/INTERMEDIATE) / (QUOTE/INTERMEDIATE)
        if best_second.price != 0:
            derived_price = best_first.price / best_second.price
        else:
            return None

        return DerivedPriceResult(
            source="derived",
            price=derived_price,
            base_asset=base,
            quote_asset=quote,
            components=[
                best_first,
                PriceResult(
                    source=best_second.source,
                    price=best_second.price,
                    base_asset=quote,
                    quote_asset=intermediate,
                    inverted=False,
                    timestamp=best_second.timestamp
                )
            ]
        )

# === API Endpoints ===
app = FastAPI()

@app.get("/price")
async def get_price(
    token: str = Query(..., description="The base cryptocurrency symbol"),
    quote: str = Query(config.DEFAULT_QUOTE, description="The quote currency symbol"),
    source: str = Query(None, description="Specific exchange to use"),
    intermediate: str = Query(None, description="Intermediate currency for derived prices"),
    fields: str = Query(None, description="Comma-separated fields to return")
):
    base = token.upper()
    quote = quote.upper()

    # Validate source if specified
    if source:
        source = source.lower()
        if source not in [e.NAME for e in EXCHANGES]:
            raise HTTPException(status_code=400, detail="Invalid source specified")

    # Get prices
    prices = await PriceService.get_direct_price(base, quote)
    
    # If no direct prices and no source specified, try derived price
    if not prices and not source:
        derived = await PriceService.get_derived_price(base, quote, intermediate)
        if derived:
            cache.set_price(derived)
            prices.append(derived)

    # Filter by source if specified
    if source:
        prices = [p for p in prices if p.source == source]
        if not prices:
            raise HTTPException(
                status_code=404,
                detail=f"No data found for {source} on {base}/{quote}"
            )

    if not prices:
        raise HTTPException(status_code=404, detail="No price data available")

    # Prepare response
    best_price = max(prices, key=lambda x: x.price)
    response = {
        "symbol": base,
        "quote": quote,
        "price": best_price.price,
        "source": best_price.source,
        "inverted": best_price.inverted,
        "expires_in": best_price.expires_in,
        "expires_at": best_price.expires_at.isoformat(),
        "sources": [{
            "source": p.source,
            "price": p.price,
            "inverted": p.inverted,
            "expires_in": p.expires_in,
            "expires_at": p.expires_at.isoformat()
        } for p in prices]
    }

    # Add components if derived price
    if isinstance(best_price, DerivedPriceResult):
        response["components"] = [{
            "pair": c.pair,
            "source": c.source,
            "price": c.price,
            "inverted": c.inverted,
            "expires_in": c.expires_in,
            "expires_at": c.expires_at.isoformat()
        } for c in best_price.components]

    # Filter fields if requested
    if fields:
        field_list = [f.strip() for f in fields.split(",")]
        filtered = {k: v for k, v in response.items() if k in field_list}
        return JSONResponse(filtered) if len(filtered) > 1 else PlainTextResponse(str(next(iter(filtered.values()))))

    return JSONResponse(response)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
