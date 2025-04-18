from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from dotenv import load_dotenv
import httpx
import os
from time import time
import logging
from decimal import Decimal, getcontext

# === Logging Setup ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Environment Setup ===
load_dotenv()
app = FastAPI()

# Configuration Constants
CACHE_TIME_TO_LIVE_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 300))
DEFAULT_QUOTE_ASSET = os.getenv("DEFAULT_QUOTE", "USDT").upper()
COINGECKO_LIST_CACHE_TIME = int(os.getenv("COINGECKO_LIST_TTL", 86400))
FAILURE_CACHE_TIME_TO_LIVE = int(os.getenv("FAILURE_TTL", 600))
INTERMEDIATE_TRADING_SYMBOL = os.getenv("INTERMEDIATE_SYMBOL", "USDT").upper()

# Set decimal precision
getcontext().prec = 8

# === Data Stores ===
price_data_cache = {}
coin_identifier_cache = {}
coingecko_coin_list_cache = []
coingecko_coin_list_last_update_time = 0
failed_request_cache = {}

# === Cache Management Functions ===
def generate_cache_key(source_name, base_asset, quote_asset):
    return f"{source_name.lower()}_{base_asset.upper()}_{quote_asset.upper()}"

def get_cached_prices(base_asset, quote_asset):
    current_time = time()
    valid_entries = []
    
    for source in EXCHANGE_PRIORITY_ORDER:
        cache_key = generate_cache_key(source, base_asset, quote_asset)
        if cache_key in price_data_cache:
            cached_data = price_data_cache[cache_key]
            cache_age = current_time - cached_data["timestamp"]
            
            if cache_age < CACHE_TIME_TO_LIVE_SECONDS:
                valid_entries.append({
                    "source": source,
                    "price": cached_data["price"],
                    "inverted": bool(cached_data.get("inverted", False)),
                    "expires_in": round(CACHE_TIME_TO_LIVE_SECONDS - cache_age, 2)
                })
    
    return valid_entries

def store_price_in_cache(source_name, base_asset, quote_asset, price_value, is_inverted=False):
    cache_key = generate_cache_key(source_name, base_asset, quote_asset)
    price_data_cache[cache_key] = {
        "price": price_value,
        "inverted": bool(is_inverted),
        "timestamp": int(time())
    }

# === Failure Cache Management ===
def generate_failure_cache_key(source_name, base_asset, quote_asset):
    return f"{source_name.lower()}_{base_asset.upper()}_{quote_asset.upper()}"

def is_request_failure_cached(source_name, base_asset, quote_asset):
    failure_key = generate_failure_cache_key(source_name, base_asset, quote_asset)
    failure_entry = failed_request_cache.get(failure_key)
    return failure_entry and (time() - failure_entry) < FAILURE_CACHE_TIME_TO_LIVE

def record_failed_request(source_name, base_asset, quote_asset):
    failure_key = generate_failure_cache_key(source_name, base_asset, quote_asset)
    failed_request_cache[failure_key] = time()

# === Price Data Fetching Functions ===
async def fetch_coin_identifier(client, asset_symbol):
    global coingecko_coin_list_cache, coingecko_coin_list_last_update_time

    normalized_symbol = asset_symbol.lower()
    current_time = time()

    if normalized_symbol in coin_identifier_cache:
        return coin_identifier_cache[normalized_symbol]

    if (not coingecko_coin_list_cache or 
        current_time - coingecko_coin_list_last_update_time > COINGECKO_LIST_CACHE_TIME):
        try:
            response = await client.get("https://api.coingecko.com/api/v3/coins/list")
            response.raise_for_status()
            coingecko_coin_list_cache = response.json()
            coingecko_coin_list_last_update_time = current_time
        except Exception as error:
            logger.warning(f"Failed to fetch CoinGecko coin list: {error}")
            return None

    for coin in coingecko_coin_list_cache:
        if coin["symbol"].lower() == normalized_symbol:
            coin_identifier_cache[normalized_symbol] = coin["id"]
            return coin["id"]

    return None

def normalize_quote_asset_for_coingecko(quote_asset):
    return "usd" if quote_asset.upper() in {"USDT", "USDC"} else quote_asset.lower()

async def get_price_from_coingecko(client, base_asset, quote_asset):
    try:
        base_coin_id = await fetch_coin_identifier(client, base_asset)
        target_currency = normalize_quote_asset_for_coingecko(quote_asset)
        
        if base_coin_id:
            request_url = (f"https://api.coingecko.com/api/v3/simple/price"
                         f"?ids={base_coin_id}&vs_currencies={target_currency}")
            response = await client.get(request_url)
            response.raise_for_status()
            price_data = response.json()
            
            if base_coin_id in price_data and target_currency in price_data[base_coin_id]:
                price_value = float(price_data[base_coin_id][target_currency])
                logger.info(f"CoinGecko price for {base_asset}/{quote_asset}: {price_value} (direct)")
                return {
                    "source": "coingecko",
                    "price": price_value,
                    "inverted": False
                }

        quote_coin_id = await fetch_coin_identifier(client, quote_asset)
        if quote_coin_id:
            inverted_currency = normalize_quote_asset_for_coingecko(base_asset)
            inverted_url = (f"https://api.coingecko.com/api/v3/simple/price"
                           f"?ids={quote_coin_id}&vs_currencies={inverted_currency}")
            inverted_response = await client.get(inverted_url)
            inverted_response.raise_for_status()
            inverted_data = inverted_response.json()
            
            if (quote_coin_id in inverted_data and 
                inverted_currency in inverted_data[quote_coin_id]):
                inverted_price = float(inverted_data[quote_coin_id][inverted_currency])
                if inverted_price != 0:
                    calculated_price = float(Decimal(1) / Decimal(str(inverted_price)))
                    logger.info(f"CoinGecko price for {base_asset}/{quote_asset}: {calculated_price} (inverted)")
                    return {
                        "source": "coingecko",
                        "price": calculated_price,
                        "inverted": True,
                        "original_price": inverted_price
                    }
        
        return None
    except Exception as error:
        logger.warning(f"CoinGecko error for {base_asset}/{quote_asset}: {error}")
        return None

# === Exchange API Implementations ===
async def get_price_from_binance(client, base_asset, quote_asset):
    try:
        request_url = f"https://api.binance.com/api/v3/ticker/price?symbol={base_asset.upper()}{quote_asset.upper()}"
        response = await client.get(request_url)
        response.raise_for_status()
        market_data = response.json()
        price_value = float(market_data["price"])
        logger.info(f"Binance price for {base_asset}/{quote_asset}: {price_value} (direct)")
        return {
            "source": "binance",
            "price": price_value,
            "inverted": False
        }
    except Exception as error:
        logger.warning(f"Binance error for {base_asset}/{quote_asset}: {error}")
        return None

async def get_price_from_okx(client, base_asset, quote_asset):
    try:
        request_url = f"https://www.okx.com/api/v5/market/ticker?instId={base_asset.upper()}-{quote_asset.upper()}"
        response = await client.get(request_url)
        response.raise_for_status()
        market_data = response.json()
        price_value = float(market_data["data"][0]["last"])
        logger.info(f"OKX price for {base_asset}/{quote_asset}: {price_value} (direct)")
        return {
            "source": "okx",
            "price": price_value,
            "inverted": False
        }
    except Exception as error:
        logger.warning(f"OKX error for {base_asset}/{quote_asset}: {error}")
        return None

async def get_price_from_kraken(client, base_asset, quote_asset):
    try:
        trading_pair = base_asset.upper() + quote_asset.upper()
        request_url = f"https://api.kraken.com/0/public/Ticker?pair={trading_pair}"
        response = await client.get(request_url)
        response.raise_for_status()
        market_data = response.json()
        first_result = list(market_data["result"].values())[0]
        price_value = float(first_result["c"][0])
        logger.info(f"Kraken price for {base_asset}/{quote_asset}: {price_value} (direct)")
        return {
            "source": "kraken",
            "price": price_value,
            "inverted": False
        }
    except Exception as error:
        logger.warning(f"Kraken error for {base_asset}/{quote_asset}: {error}")
        return None

async def get_price_from_coinbase(client, base_asset, quote_asset):
    try:
        # First attempt with direct pair
        direct_pair_url = f"https://api.coinbase.com/v2/prices/{base_asset.upper()}-{quote_asset.upper()}/spot"
        direct_response = await client.get(direct_pair_url)
        direct_response.raise_for_status()
        direct_data = direct_response.json()
        direct_price = float(direct_data["data"]["amount"])
        logger.info(f"Coinbase price for {base_asset}/{quote_asset}: {direct_price} (direct)")
        return {
            "source": "coinbase",
            "price": direct_price,
            "inverted": False
        }
    except Exception as direct_error:
        logger.warning(f"Coinbase direct pair error for {base_asset}/{quote_asset}: {direct_error}")
        
        try:
            # Fallback to inverted pair
            inverted_pair_url = f"https://api.coinbase.com/v2/prices/{quote_asset.upper()}-{base_asset.upper()}/spot"
            inverted_response = await client.get(inverted_pair_url)
            inverted_response.raise_for_status()
            inverted_data = inverted_response.json()
            inverted_price = float(inverted_data["data"]["amount"])
            
            if inverted_price != 0:
                calculated_price = float(Decimal(1) / Decimal(str(inverted_price)))
                logger.info(f"Coinbase price for {base_asset}/{quote_asset}: {calculated_price} (inverted)")
                return {
                    "source": "coinbase",
                    "price": calculated_price,
                    "inverted": True,
                    "original_price": inverted_price
                }
            return None
        except Exception as inverted_error:
            logger.warning(f"Coinbase inverted pair error for {quote_asset}/{base_asset}: {inverted_error}")
            return None

async def get_price_from_mexc(client, base_asset, quote_asset):
    try:
        request_url = f"https://api.mexc.com/api/v3/ticker/price?symbol={base_asset.upper()}{quote_asset.upper()}"
        response = await client.get(request_url)
        response.raise_for_status()
        market_data = response.json()
        price_value = float(market_data["price"])
        logger.info(f"MEXC price for {base_asset}/{quote_asset}: {price_value} (direct)")
        return {
            "source": "mexc",
            "price": price_value,
            "inverted": False
        }
    except Exception as error:
        logger.warning(f"MEXC error for {base_asset}/{quote_asset}: {error}")
        return None

# === Exchange Configuration ===
EXCHANGE_PRICE_FETCHERS = {
    "binance": get_price_from_binance,
    "okx": get_price_from_okx,
    "kraken": get_price_from_kraken,
    "coinbase": get_price_from_coinbase,
    "mexc": get_price_from_mexc,
    "coingecko": get_price_from_coingecko,
}

EXCHANGE_PRIORITY_ORDER = ["binance", "okx", "kraken", "coinbase", "mexc", "coingecko"]

# === Price Calculation Core ===
async def fetch_best_available_price(client, base_asset, quote_asset):
    for exchange in EXCHANGE_PRIORITY_ORDER:
        if exchange != "coingecko" and not is_request_failure_cached(exchange, base_asset, quote_asset):
            try:
                price_fetcher = EXCHANGE_PRICE_FETCHERS[exchange]
                price_result = await price_fetcher(client, base_asset, quote_asset)
                if price_result:
                    return price_result
                else:
                    record_failed_request(exchange, base_asset, quote_asset)
            except Exception:
                record_failed_request(exchange, base_asset, quote_asset)
    
    return await get_price_from_coingecko(client, base_asset, quote_asset)

async def calculate_derived_price(client, base_asset, quote_asset, intermediate_asset=None):
    intermediate_symbol = (intermediate_asset or INTERMEDIATE_TRADING_SYMBOL).upper()
    
    if (base_asset.upper() == intermediate_symbol or 
        quote_asset.upper() == intermediate_symbol):
        return None

    # Get first leg (BASE/INTERMEDIATE)
    first_leg_prices = get_cached_prices(base_asset, intermediate_symbol)
    if not first_leg_prices:
        first_leg_result = await fetch_best_available_price(client, base_asset, intermediate_symbol)
        if first_leg_result:
            store_price_in_cache(
                first_leg_result["source"],
                base_asset,
                intermediate_symbol,
                first_leg_result["price"],
                first_leg_result.get("inverted", False)
            )
            first_leg_prices = [first_leg_result]

    # Get second leg (INTERMEDIATE/QUOTE)
    second_leg_prices = get_cached_prices(intermediate_symbol, quote_asset)
    if not second_leg_prices:
        second_leg_result = await fetch_best_available_price(client, intermediate_symbol, quote_asset)
        if second_leg_result:
            store_price_in_cache(
                second_leg_result["source"],
                intermediate_symbol,
                quote_asset,
                second_leg_result["price"],
                second_leg_result.get("inverted", False)
            )
            second_leg_prices = [second_leg_result]

    if first_leg_prices and second_leg_prices:
        expiration_time = min(
            first_leg_prices[0].get("expires_in", CACHE_TIME_TO_LIVE_SECONDS),
            second_leg_prices[0].get("expires_in", CACHE_TIME_TO_LIVE_SECONDS)
        )
        
        return {
            "source": "derived",
            "price": first_leg_prices[0]["price"] * second_leg_prices[0]["price"],
            "inverted": False,
            "expires_in": expiration_time,
            "components": [
                {"pair": f"{base_asset}/{intermediate_symbol}", **first_leg_prices[0]},
                {"pair": f"{intermediate_symbol}/{quote_asset}", **second_leg_prices[0]}
            ]
        }

    return None

# === API Endpoints ===
@app.get("/price")
async def get_asset_price(
    token: str = Query(...),
    quote: str = Query(DEFAULT_QUOTE_ASSET),
    query: str = Query(None),
    source: str = Query(None),
    intermediate: str = Query(None)
):
    base_asset = token.upper()
    quote_asset = quote.upper()
    requested_source = source.lower() if source else None

    cached_results = get_cached_prices(base_asset, quote_asset)
    cached_sources = {entry["source"] for entry in cached_results}

    if requested_source:
        if requested_source not in EXCHANGE_PRICE_FETCHERS:
            return PlainTextResponse("Invalid source specified", status_code=400)
        missing_sources = [requested_source] if requested_source not in cached_sources else []
    else:
        missing_sources = [exchange for exchange in EXCHANGE_PRIORITY_ORDER 
                          if exchange not in cached_sources and exchange != "coingecko"]

    async with httpx.AsyncClient(timeout=5) as client:
        for exchange in missing_sources:
            price_fetcher = EXCHANGE_PRICE_FETCHERS[exchange]
            skip_failure_cache = exchange == "coingecko"
            
            if not skip_failure_cache and is_request_failure_cached(exchange, base_asset, quote_asset):
                logger.info(f"Skipping {exchange} for {base_asset}/{quote_asset} - within failure cache TTL")
                continue
                
            try:
                price_result = await price_fetcher(client, base_asset, quote_asset)
                if price_result:
                    store_price_in_cache(
                        price_result["source"],
                        base_asset,
                        quote_asset,
                        price_result["price"],
                        price_result.get("inverted", False)
                    )
                    cached_results.append({
                        "source": price_result["source"],
                        "price": price_result["price"],
                        "inverted": price_result.get("inverted", False),
                        "expires_in": CACHE_TIME_TO_LIVE_SECONDS
                    })
                elif not skip_failure_cache:
                    record_failed_request(exchange, base_asset, quote_asset)
            except Exception:
                if not skip_failure_cache:
                    record_failed_request(exchange, base_asset, quote_asset)

        if not cached_results and not requested_source:
            derived_price = await calculate_derived_price(client, base_asset, quote_asset, intermediate)
            if derived_price:
                store_price_in_cache("derived", base_asset, quote_asset, derived_price["price"])
                cached_results.append({
                    "source": "derived",
                    "price": derived_price["price"],
                    "inverted": False,
                    "expires_in": derived_price.get("expires_in", CACHE_TIME_TO_LIVE_SECONDS),
                    "components": derived_price.get("components", [])
                })

    if not cached_results:
        return PlainTextResponse("0.0", status_code=404)

    if requested_source:
        cached_results = [result for result in cached_results if result["source"] == requested_source]
        if not cached_results:
            return PlainTextResponse(f"No data found for {requested_source} on {base_asset}/{quote_asset}", status_code=404)

    best_price_entry = max(cached_results, key=lambda entry: entry["price"])

    response_data = {
        "symbol": base_asset,
        "quote": quote_asset,
        "price": best_price_entry["price"],
        "source": best_price_entry["source"],
        "inverted": best_price_entry.get("inverted", False),
        "expires_in": best_price_entry.get("expires_in", CACHE_TIME_TO_LIVE_SECONDS),
        "sources": cached_results,
    }

    if query and query in response_data:
        return PlainTextResponse(str(response_data[query]))
    return JSONResponse(response_data)

@app.get("/health")
async def health_check():
    return {"status": "operational"}
