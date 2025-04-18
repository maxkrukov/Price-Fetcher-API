# **Price Fetcher API Documentation**

---

## **1. Overview**

The **Price Fetcher API** provides real-time price data for a wide array of **cryptocurrencies** and **fiat currencies**.  
It supports querying for nearly all cryptocurrencies and fiat currencies, including popular assets such as **Bitcoin (BTC)**, **Ethereum (ETH)**, **Tether (USDT)**, **USD**, **EUR**, and many others. Prices are fetched from multiple exchanges, including **Binance**, **Coinbase**, **Kraken**, **OKX**, **MEXC**, and **CoinGecko**, allowing you to compare rates across different platforms.

### **Key Features:**
- ✅ **Crypto-to-Fiat Conversion**: e.g., BTC to USD
- ✅ **Fiat-to-Fiat Conversion**: e.g., PLN to UAH
- ✅ **Crypto-to-Crypto Conversion**: e.g., ETH to BTC
- ✅ **Cross-Exchange Comparison**: Compare rates across Binance, Kraken, etc.
- ✅ **Automatic Inversion (CoinGecko)**: If CoinGecko doesn’t support direct pair, it auto-fetches the reverse and inverts the value.
- ✅ **Flexible Query Parameters**: Customize response with fields like `max_price`, `expires_in`, and more.
- ✅ **Cache with Expiry**: Avoids frequent external calls by caching results with TTL.

---

## **2. Query Parameters**

### **Required Parameters**:
| Parameter | Description | Example |
|----------|-------------|---------|
| `token`  | The asset (crypto or fiat) you want the price **of**. | `BTC`, `EUR`, `USDT`, etc. |
| `quote`  | The currency you want the price **in**. | `USD`, `PLN`, `BTC`, etc. |

### **Optional Parameters**:
| Parameter | Description |
|-----------|-------------|
| `source` | Specific exchange to query: `binance`, `okx`, `kraken`, `coinbase`, `mexc`, `coingecko`. |
| `query`  | Return only one value from the response:<br>`max_price`, `max_source`, `cached`, `expires_in`, `symbol`, `quote`, `inverted`, `sources`. |

---

## **3. Response Format**

Full JSON response example:
```json
{
  "cached": true,
  "symbol": "EUR",
  "quote": "PLN",
  "max_price": 37.96449560371141,
  "max_source": "coingecko",
  "inverted": true,
  "expires_in": 229.04,
  "sources": [
    {
      "source": "coingecko",
      "price": 37.96449560371141,
      "inverted": true,
      "expires_in": 229.04
    }
  ]
}
```

### **Key Fields:**
| Field | Description |
|-------|-------------|
| `cached` | Whether the result was returned from cache |
| `symbol` | Token requested |
| `quote` | Quote currency |
| `max_price` | Highest price among sources |
| `max_source` | Source that returned the highest price |
| `inverted` | `true` if the price was calculated using an inverted pair (CoinGecko only) |
| `expires_in` | Time left before cache expires |
| `sources` | List of prices returned from each source, with their metadata |

---

## **4. Supported Currency Pairs**

Examples include, but are not limited to:

### 🔹 **Crypto-to-Fiat**
- BTC → USD
- ETH → EUR
- USDT → PLN

### 🔹 **Fiat-to-Fiat**
- USD → EUR
- PLN → UAH

### 🔹 **Crypto-to-Crypto**
- ETH → BTC
- USDT → BTC

---

## **5. Examples**

### 🔹 Example 1: Basic query
```bash
curl -s "http://localhost:5000/price?token=BTC&quote=USDT"
```
**Response:**
```json
{
  "cached": false,
  "symbol": "BTC",
  "quote": "USDT",
  "max_price": 84524.0,
  "max_source": "okx",
  "inverted": false,
  "expires_in": 300,
  "sources": [
    {
      "source": "binance",
      "price": 84518.86,
      "inverted": false
    },
    {
      "source": "okx",
      "price": 84524.0,
      "inverted": false
    },
    {
      "source": "kraken",
      "price": 84516.9,
      "inverted": false
    },
    {
      "source": "coinbase",
      "price": 84514.53,
      "inverted": false
    },
    {
      "source": "mexc",
      "price": 84521.29,
      "inverted": false
    },
    {
      "source": "coingecko",
      "price": 0.00003613,
      "inverted": false
    }
  ]
}

```

### 🔹 Example 2: Specify exchange
```bash
curl -s "http://localhost:5000/price?token=ETH&quote=USDT&source=binance"
```
**Response:**
```json
{
  "cached": false,
  "symbol": "ETH",
  "quote": "USDT",
  "max_price": 1589.98,
  "max_source": "binance",
  "inverted": false,
  "expires_in": 300,
  "sources": [
    {
      "source": "binance",
      "price": 1589.98,
      "inverted": false
    }
  ]
}

```

### 🔹 Example 3: Use query param to extract only `max_price`
```bash
curl -s "http://localhost:5000/price?token=eth&quote=btc&query=max_price"
```

**Response:**
```json
0.01881
```

### 🔹 Example 4: CoinGecko with automatic inversion
```bash
curl -s "http://localhost:5000/price?token=eur&quote=pln&source=coingecko"
```

**Response:**
```json
{
  "cached": true,
  "symbol": "EUR",
  "quote": "PLN",
  "max_price": 37.96,
  "max_source": "coingecko",
  "inverted": true,
  "expires_in": 229.04,
  "sources": [
    {
      "source": "coingecko",
      "price": 37.96,
      "inverted": true,
      "expires_in": 229.04
    }
  ]
}
```

---

## **6. Google Sheets Integration**

Use `IMPORTDATA` in any cell:

```excel
=IMPORTDATA("https://<public-url>/price?token=eth&quote=btc&query=max_price")
```

---

## **7. Python Example**

```python
import requests

url = "http://localhost:5000/price"
params = {
    "token": "BTC",
    "quote": "USDT",
    "query": "max_price"
}

response = requests.get(url, params=params)
print(response.json())
```

---

## **8. Supported Sources**

| Source     | Description              |
|------------|--------------------------|
| `binance`  | Binance spot market      |
| `okx`      | OKX spot market          |
| `kraken`   | Kraken exchange          |
| `coinbase` | Coinbase spot market     |
| `mexc`     | MEXC global              |
| `coingecko`| CoinGecko aggregator     |

---

## **9. Error Handling**

### 🔸 Missing required parameters
```json
400 Bad Request
{
  "detail": "Missing required parameters"
}
```

### 🔸 Invalid source
```json
400 Bad Request
{
  "detail": "Invalid source specified"
}
```

### 🔸 No data found
```json
404 Not Found
{
  "detail": "No data found for coingecko on USD/EUR"
}
```

---

## **10. Running via Docker Compose**

```bash
# Clone repo
git clone <repo>
cd <repo>

# Build and start
docker compose build
docker compose up -d

# Stop
docker compose down
```
---

## **11. ❤️ Support the Project**

If you find this API useful, consider making a donation to help **cover infrastructure and hosting costs**. Your support directly helps keep the app running smoothly, reliably, and freely accessible for everyone.

I’m currently setting up dedicated infrastructure to host this service 24/7 — donations will go toward maintaining the servers, API uptime, monitoring, and future scaling.

### 💸 Donation Addresses:

- **Solana (SOL)**  
  `GTsL1ZJqHthtdDLSKJt4D6x4a52NoHEG7tMtWeg1kCjK`

- **Ethereum / Polygon (ETH/POL)**  
  `0xa9Ce7AD40027a80C2EEf3475CcCc6b0B22f1Ed6D`

Even small contributions make a big difference 🙏
