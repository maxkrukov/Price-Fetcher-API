from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from dotenv import load_dotenv
import httpx
import asyncio
import os
from time import time

# === Load environment ===
load_dotenv()

app = FastAPI()

# === Config ===
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 300))
DEFAULT_QUOTE = os.getenv("DEFAULT_QUOTE", "USDT").upper()
COINGECKO_LIST_TTL = int(os.getenv("COINGECKO_LIST_TTL", 86400))
FAILURE_TTL_SECONDS = int(os.getenv("FAILURE_TTL", 600))

# === Cache ===
price_cache = {}
coin_id_cache = {}
coingecko_coin_list_cache = []
coingecko_coin_list_last_fetched = 0
failure_cache = {}

# === Helpers ===
def make_cache_key(symbol, quote):
    return f"{symbol.upper()}-{quote.upper()}"

def get_valid_cache_entries(symbol, quote, ttl_seconds):
    key = make_cache_key(symbol, quote)
    all_sources = price_cache.get(key, {})
    now = time()
    valid_entries = []
    for source, data in all_sources.items():
        if now - data["timestamp"] < ttl_seconds:
            valid_entries.append({
                "source": source,
                "price": data["price"],
                "inverted": data.get("inverted", False),
                "expires_in": ttl_seconds - (now - data["timestamp"])
            })
    return valid_entries

def set_cache(symbol, quote, price, source, inverted=False):
    key = make_cache_key(symbol, quote)
    if key not in price_cache:
        price_cache[key] = {}
    price_cache[key][source] = {
        "price": price,
        "inverted": inverted,
        "timestamp": int(time())
    }

def normalize_quote_for_coingecko(quote):
    return "usd" if quote.upper() in {"USDT", "USDC"} else quote.lower()

def is_failure_cached(symbol, quote, source):
    key = f"{make_cache_key(symbol, quote)}:{source}"
    entry = failure_cache.get(key)
    return entry and (time() - entry) < FAILURE_TTL_SECONDS

def cache_failure(symbol, quote, source):
    key = f"{make_cache_key(symbol, quote)}:{source}"
    failure_cache[key] = time()

# === CoinGecko ===
async def resolve_coingecko_id(client, symbol: str):
    global coingecko_coin_list_cache, coingecko_coin_list_last_fetched

    symbol = symbol.lower()
    now = time()

    if symbol in coin_id_cache:
        print(f"Found {symbol} in cache!")
        return coin_id_cache[symbol]

    if not coingecko_coin_list_cache or now - coingecko_coin_list_last_fetched > COINGECKO_LIST_TTL:
        try:
            print("Fetching CoinGecko list...")
            res = await client.get("https://api.coingecko.com/api/v3/coins/list")
            res.raise_for_status()
            coingecko_coin_list_cache = res.json()
            coingecko_coin_list_last_fetched = now
        except Exception as e:
            print(f"Failed to fetch CoinGecko coin list: {e}")
            return None

    for coin in coingecko_coin_list_cache:
        if coin["symbol"].lower() == symbol:
            print(f"Matched {symbol} to CoinGecko ID: {coin['id']}")
            coin_id_cache[symbol] = coin["id"]
            return coin["id"]

    print(f"No match found for {symbol} in CoinGecko")
    return None

async def get_price_coingecko(client, symbol, quote):
    try:
        coin_id = await resolve_coingecko_id(client, symbol)
        if coin_id:
            vs_currency = normalize_quote_for_coingecko(quote)
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={vs_currency}"
            res = await client.get(url)
            res.raise_for_status()
            data = res.json()
            if coin_id in data and vs_currency in data[coin_id]:
                return {
                    "source": "coingecko",
                    "price": float(data[coin_id][vs_currency]),
                    "inverted": False
                }

        coin_id_alt = await resolve_coingecko_id(client, quote)
        if coin_id_alt:
            vs_currency_alt = normalize_quote_for_coingecko(symbol)
            url_alt = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id_alt}&vs_currencies={vs_currency_alt}"
            res_alt = await client.get(url_alt)
            res_alt.raise_for_status()
            data_alt = res_alt.json()
            if coin_id_alt in data_alt and vs_currency_alt in data_alt[coin_id_alt]:
                price = float(data_alt[coin_id_alt][vs_currency_alt])
                if price != 0:
                    print(f"Used inverted price for {symbol}/{quote} from {quote}/{symbol}")
                    return {
                        "source": "coingecko",
                        "price": 1 / price,
                        "inverted": True
                    }

        print(f"Coingecko failed for both {symbol}/{quote} and fallback {quote}/{symbol}")
        return None
    except Exception as e:
        print(f"Coingecko error for {symbol}/{quote} (not cached): {e}")
        return None

# === Exchange Fetchers ===
async def get_price_binance(client, symbol, quote):
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}{quote.upper()}"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        return {"source": "binance", "price": float(data["price"]), "inverted": False}
    except Exception as e:
        print(f"Binance error for {symbol}/{quote}: {e}")
        return None

async def get_price_okx(client, symbol, quote):
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={symbol.upper()}-{quote.upper()}"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        return {"source": "okx", "price": float(data["data"][0]["last"]), "inverted": False}
    except Exception as e:
        print(f"OKX error for {symbol}/{quote}: {e}")
        return None

async def get_price_kraken(client, symbol, quote):
    try:
        pair = symbol.upper() + quote.upper()
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        result = list(data["result"].values())[0]
        price = float(result["c"][0])
        return {"source": "kraken", "price": price, "inverted": False}
    except Exception as e:
        print(f"Kraken error for {symbol}/{quote}: {e}")
        return None

async def get_price_coinbase(client, symbol, quote):
    try:
        url = f"https://api.coinbase.com/v2/prices/{symbol.upper()}-{quote.upper()}/spot"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        return {"source": "coinbase", "price": float(data["data"]["amount"]), "inverted": False}
    except Exception as e:
        print(f"Coinbase error for {symbol}/{quote}: {e}")
        return None

async def get_price_mexc(client, symbol, quote):
    try:
        url = f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol.upper()}{quote.upper()}"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        return {"source": "mexc", "price": float(data["price"]), "inverted": False}
    except Exception as e:
        print(f"MEXC error for {symbol}/{quote}: {e}")
        return None

# === Fetcher Registry ===
FETCHERS = {
    "binance": get_price_binance,
    "okx": get_price_okx,
    "kraken": get_price_kraken,
    "coinbase": get_price_coinbase,
    "mexc": get_price_mexc,
    "coingecko": get_price_coingecko,
}

SOURCE_PRIORITY = [
    "binance",
    "okx",
    "kraken",
    "coinbase",
    "mexc",
    "coingecko",
]

# === Price Endpoint ===
@app.get("/price")
async def get_price(token: str = Query(...), quote: str = Query(DEFAULT_QUOTE), query: str = Query(None), source: str = Query(None)):
    symbol = token.upper()
    quote = quote.upper()

    if source:
        source = source.lower()
        if source not in FETCHERS:
            return PlainTextResponse("Invalid source specified", status_code=400)
        sources_to_query = [FETCHERS[source]]
    else:
        sources_to_query = [FETCHERS[src] for src in SOURCE_PRIORITY]

    cached = get_valid_cache_entries(symbol, quote, CACHE_TTL_SECONDS)
    if cached:
        if source:
            cached = [entry for entry in cached if entry["source"] == source]
            if not cached:
                return PlainTextResponse(f"No data found for {source} on {symbol}/{quote}", status_code=404)

        max_entry = max(cached, key=lambda x: x["price"])
        result = {
            "cached": True,
            "symbol": symbol,
            "quote": quote,
            "max_price": max_entry["price"],
            "max_source": max_entry["source"],
            "inverted": max_entry.get("inverted", False),
            "expires_in": max_entry["expires_in"],
            "sources": cached,
        }
        return PlainTextResponse(str(result[query])) if query and query in result else JSONResponse(result)

    async with httpx.AsyncClient(timeout=5) as client:
        results = []
        for fetcher in sources_to_query:
            source_name = fetcher.__name__.replace("get_price_", "")
            skip_failure_cache = source_name == "coingecko"

            if not skip_failure_cache and is_failure_cached(symbol, quote, source_name):
                print(f"Skipping {source_name} for {symbol}/{quote} — recently failed, will retry after {FAILURE_TTL_SECONDS}s")
                continue

            try:
                result = await fetcher(client, symbol, quote)
                if result:
                    results.append(result)
                elif not skip_failure_cache:
                    print(f"Ignoring {source_name} for {symbol}/{quote} — no data returned, caching failure for {FAILURE_TTL_SECONDS}s")
                    cache_failure(symbol, quote, source_name)
                else:
                    print(f"{source_name} returned no data for {symbol}/{quote} (not cached)")
            except Exception as e:
                if not skip_failure_cache:
                    print(f"Ignoring {source_name} for {symbol}/{quote} due to error: {e} — caching failure for {FAILURE_TTL_SECONDS}s")
                    cache_failure(symbol, quote, source_name)
                else:
                    print(f"{source_name} error for {symbol}/{quote} (not cached): {e}")

    prices = [r for r in results if r]

    if not prices:
        return PlainTextResponse("0.0", status_code=404)

    for entry in prices:
        set_cache(symbol, quote, entry["price"], entry["source"], inverted=entry.get("inverted", False))

    if source:
        prices = [entry for entry in prices if entry["source"] == source]
        if not prices:
            return PlainTextResponse(f"No data found for {source} on {symbol}/{quote}", status_code=404)

    max_entry = max(prices, key=lambda x: x["price"])
    result = {
        "cached": False,
        "symbol": symbol,
        "quote": quote,
        "max_price": max_entry["price"],
        "max_source": max_entry["source"],
        "inverted": max_entry.get("inverted", False),
        "expires_in": CACHE_TTL_SECONDS,
        "sources": prices,
    }

    return PlainTextResponse(str(result[query])) if query and query in result else JSONResponse(result)

# === Health Endpoint ===
@app.get("/health")
async def health():
    return {"status": "ok"}
