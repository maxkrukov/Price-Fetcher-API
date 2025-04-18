from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from dotenv import load_dotenv
import httpx
import os
from time import time

load_dotenv()
app = FastAPI()

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 300))
DEFAULT_QUOTE = os.getenv("DEFAULT_QUOTE", "USDT").upper()
COINGECKO_LIST_TTL = int(os.getenv("COINGECKO_LIST_TTL", 86400))
FAILURE_TTL_SECONDS = int(os.getenv("FAILURE_TTL", 600))

price_cache = {}
coin_id_cache = {}
coingecko_coin_list_cache = []
coingecko_coin_list_last_fetched = 0
failure_cache = {}

# === Cache helpers ===
def make_source_cache_key(source, symbol, quote):
    return f"{source.lower()}_{symbol.upper()}_{quote.upper()}"

def get_valid_cache_entries(symbol, quote):
    now = time()
    entries = []
    for src in SOURCE_PRIORITY:
        key = make_source_cache_key(src, symbol, quote)
        if key in price_cache:
            data = price_cache[key]
            age = now - data["timestamp"]
            if age < CACHE_TTL_SECONDS:
                entries.append({
                    "source": src,
                    "price": data["price"],
                    "inverted": bool(data.get("inverted", False)),
                    "expires_in": round(CACHE_TTL_SECONDS - age, 2)
                })
    return entries

def set_cache(source, symbol, quote, price, inverted=False):
    key = make_source_cache_key(source, symbol, quote)
    price_cache[key] = {
        "price": price,
        "inverted": bool(inverted),
        "timestamp": int(time())
    }

# === Failure cache helpers ===
def make_failure_cache_key(source, symbol, quote):
    return f"{source.lower()}_{symbol.upper()}_{quote.upper()}"

def is_failure_cached(source, symbol, quote):
    key = make_failure_cache_key(source, symbol, quote)
    entry = failure_cache.get(key)
    return entry and (time() - entry) < FAILURE_TTL_SECONDS

def cache_failure(source, symbol, quote):
    key = make_failure_cache_key(source, symbol, quote)
    failure_cache[key] = time()

def normalize_quote_for_coingecko(quote):
    return "usd" if quote.upper() in {"USDT", "USDC"} else quote.lower()

# === CoinGecko ===
async def resolve_coingecko_id(client, symbol: str):
    global coingecko_coin_list_cache, coingecko_coin_list_last_fetched

    symbol = symbol.lower()
    now = time()

    if symbol in coin_id_cache:
        return coin_id_cache[symbol]

    if not coingecko_coin_list_cache or now - coingecko_coin_list_last_fetched > COINGECKO_LIST_TTL:
        try:
            res = await client.get("https://api.coingecko.com/api/v3/coins/list")
            res.raise_for_status()
            coingecko_coin_list_cache = res.json()
            coingecko_coin_list_last_fetched = now
        except Exception as e:
            print(f"Failed to fetch CoinGecko coin list: {e}")
            return None

    for coin in coingecko_coin_list_cache:
        if coin["symbol"].lower() == symbol:
            coin_id_cache[symbol] = coin["id"]
            return coin["id"]

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
                price = float(data[coin_id][vs_currency])
                print(f"coingecko price for {symbol}/{quote}: {price} (inverted: False)")
                return {"source": "coingecko", "price": price, "inverted": False}

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
                    print(f"coingecko price for {symbol}/{quote}: {1 / price} (inverted: True)")
                    return {"source": "coingecko", "price": 1 / price, "inverted": True}
        return None
    except Exception as e:
        print(f"Coingecko error for {symbol}/{quote}: {e}")
        return None

# === Exchange Fetchers ===
async def get_price_binance(client, symbol, quote):
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}{quote.upper()}"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        price = float(data["price"])
        print(f"binance price for {symbol}/{quote}: {price} (inverted: False)")
        return {"source": "binance", "price": price, "inverted": False}
    except Exception as e:
        print(f"Binance error for {symbol}/{quote}: {e}")
        return None

async def get_price_okx(client, symbol, quote):
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={symbol.upper()}-{quote.upper()}"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        price = float(data["data"][0]["last"])
        print(f"okx price for {symbol}/{quote}: {price} (inverted: False)")
        return {"source": "okx", "price": price, "inverted": False}
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
        print(f"kraken price for {symbol}/{quote}: {price} (inverted: False)")
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
        price = float(data["data"]["amount"])
        print(f"coinbase price for {symbol}/{quote}: {price} (inverted: False)")
        return {"source": "coinbase", "price": price, "inverted": False}
    except Exception as e:
        print(f"Coinbase error for {symbol}/{quote}: {e} — trying inverted pair...")

    try:
        url = f"https://api.coinbase.com/v2/prices/{quote.upper()}-{symbol.upper()}/spot"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        price = float(data["data"]["amount"])
        if price != 0:
            print(f"coinbase price for {symbol}/{quote}: {1 / price} (inverted: True)")
            return {"source": "coinbase", "price": 1 / price, "inverted": True}
    except Exception as e:
        print(f"Inverted Coinbase fallback failed for {quote}/{symbol}: {e}")
        return None

async def get_price_mexc(client, symbol, quote):
    try:
        url = f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol.upper()}{quote.upper()}"
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
        price = float(data["price"])
        print(f"mexc price for {symbol}/{quote}: {price} (inverted: False)")
        return {"source": "mexc", "price": price, "inverted": False}
    except Exception as e:
        print(f"MEXC error for {symbol}/{quote}: {e}")
        return None

FETCHERS = {
    "binance": get_price_binance,
    "okx": get_price_okx,
    "kraken": get_price_kraken,
    "coinbase": get_price_coinbase,
    "mexc": get_price_mexc,
    "coingecko": get_price_coingecko,
}

SOURCE_PRIORITY = ["binance", "okx", "kraken", "coinbase", "mexc", "coingecko"]

@app.get("/price")
async def get_price(token: str = Query(...), quote: str = Query(DEFAULT_QUOTE), query: str = Query(None), source: str = Query(None)):
    symbol = token.upper()
    quote = quote.upper()
    source = source.lower() if source else None

    results = get_valid_cache_entries(symbol, quote)
    cached_sources = {entry["source"] for entry in results}

    if source:
        if source not in FETCHERS:
            return PlainTextResponse("Invalid source specified", status_code=400)
        missing_sources = [source] if source not in cached_sources else []
    else:
        missing_sources = [src for src in SOURCE_PRIORITY if src not in cached_sources and src != "coingecko"]

    async with httpx.AsyncClient(timeout=5) as client:
        for src in missing_sources:
            fetcher = FETCHERS[src]
            skip_failure_cache = src == "coingecko"
            if not skip_failure_cache and is_failure_cached(src, symbol, quote):
                print(f"Skipping {src} for {symbol}/{quote} — still within FAILURE_TTL_SECONDS")
                continue
            try:
                result = await fetcher(client, symbol, quote)
                if result:
                    set_cache(result["source"], symbol, quote, result["price"], result.get("inverted", False))
                    results.append({
                        "source": result["source"],
                        "price": result["price"],
                        "inverted": result.get("inverted", False),
                        "expires_in": CACHE_TTL_SECONDS
                    })
                elif not skip_failure_cache:
                    print(f"{src} failed for {symbol}/{quote}, ignoring for {FAILURE_TTL_SECONDS} seconds (empty result)")
                    cache_failure(src, symbol, quote)
            except Exception as e:
                if not skip_failure_cache:
                    print(f"{src} failed for {symbol}/{quote}, ignoring for {FAILURE_TTL_SECONDS} seconds — {e}")
                    cache_failure(src, symbol, quote)

        if not results and not source:
            result = await get_price_coingecko(client, symbol, quote)
            if result:
                set_cache(result["source"], symbol, quote, result["price"], result.get("inverted", False))
                results.append({
                    "source": result["source"],
                    "price": result["price"],
                    "inverted": result.get("inverted", False),
                    "expires_in": CACHE_TTL_SECONDS
                })

    if not results:
        return PlainTextResponse("0.0", status_code=404)

    if source:
        results = [r for r in results if r["source"] == source]
        if not results:
            return PlainTextResponse(f"No data found for {source} on {symbol}/{quote}", status_code=404)

    max_entry = max(results, key=lambda x: x["price"])

    response = {
        "symbol": symbol,
        "quote": quote,
        "price": max_entry["price"],
        "source": max_entry["source"],
        "inverted": max_entry.get("inverted", False),
        "expires_in": max_entry.get("expires_in", CACHE_TTL_SECONDS),
        "sources": results,
    }

    return PlainTextResponse(str(response[query])) if query and query in response else JSONResponse(response)

@app.get("/health")
async def health():
    return {"status": "ok"}
