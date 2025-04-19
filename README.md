# **📘 Price Fetcher API Documentation**

---

## **1. Overview**

The **Price Fetcher API** provides real-time price data (based on CACHE_TTL_SECONDS) for a wide variety of **cryptocurrencies** and **fiat currencies**.  
It fetches prices from top exchanges such as **Binance**, **Coinbase**, **Kraken**, **OKX**, **MEXC**, and uses **CoinGecko** as a **fallback source** when needed.  
If no direct price is available, the API can also **calculate a derived price** using an intermediate quote (e.g., `BTC → USDT → PLN`).

### **🔑 Key Features**
- ✅ **Crypto-to-Fiat Conversion** (e.g., BTC → USD)
- ✅ **Fiat-to-Fiat Conversion** (e.g., PLN → UAH)
- ✅ **Crypto-to-Crypto Conversion** (e.g., ETH → BTC)
- ✅ **Multi-Exchange Price Comparison**
- ✅ **Automatic Inversion via CoinGecko**
- ✅ **Smart Caching** with TTL expiry
- ✅ **Derived Pricing via Intermediate (e.g., BTC → USDT → PLN)**
- ✅ **Minimal JSON Output via `fields` parameter**

---

## **2. Query Parameters**

### 🔹 **Required**
| Parameter | Description | Example |
|----------|-------------|---------|
| `token`  | Asset you're pricing (crypto or fiat) | `BTC`, `ETH`, `USD`, etc. |
| `quote`  | Currency the price is quoted in        | `USD`, `PLN`, `BTC`, etc. |

### 🔹 **Optional**
| Parameter | Description |
|-----------|-------------|
| `source` | Specific exchange to use (e.g., `binance`, `okx`, `kraken`, `coinbase`, `mexc`, `coingecko`, `derived`[cannot use it as parameter]) |
| `fields` | Return only selected fields from the response. Use comma-separated values like:<br>`price`, `source`, `symbol`, `quote`, `inverted`, `expires_in`, `expires_at`, `sources` |
| `intermediate` | Specify an intermediate asset to try for derived pricing (e.g., `USDT`) |

---

## **3. Response Format**

### **Example Full Response**
```json
{
  "symbol": "ETH",
  "quote": "USDT",
  "price": 1600.0,
  "source": "kraken",
  "inverted": false,
  "expires_in": 299.18226385116577,
  "expires_at": "2025-04-18T21:47:39.767047",
  "sources": [
    {
      "source": "binance",
      "price": 1597.49,
      "inverted": false,
      "expires_in": 298.5671670436859,
      "expires_at": "2025-04-18T21:47:39.152017"
    },
    {
      "source": "okx",
      "price": 1597.69,
      "inverted": false,
      "expires_in": 298.8730113506317,
      "expires_at": "2025-04-18T21:47:39.457880"
    },
    {
      "source": "kraken",
      "price": 1600.0,
      "inverted": false,
      "expires_in": 299.18216705322266,
      "expires_at": "2025-04-18T21:47:39.767051"
    },
    {
      "source": "coinbase",
      "price": 1598.025,
      "inverted": false,
      "expires_in": 299.4878776073456,
      "expires_at": "2025-04-18T21:47:40.072777"
    },
    {
      "source": "mexc",
      "price": 1597.48,
      "inverted": false,
      "expires_in": 299.99830055236816,
      "expires_at": "2025-04-18T21:47:40.583215"
    }
  ]
}
```

### **Response Field Descriptions**
| Field        | Description |
|--------------|-------------|
| `symbol`     | Base token being priced |
| `quote`      | Currency in which the price is quoted |
| `price`      | Selected price (typically the highest or prioritized) |
| `source`     | Exchange providing the selected `price` (`derived` if calculated) |
| `inverted`   | `true` if reversed pair was used (CoinGecko only) |
| `expires_in` | Time remaining before cached result expires (in seconds) |
| `expires_at` | Time when the cached result will expire (ISO timestamp) |
| `sources`    | List of all source prices with their own TTL and inversion status |

---

## **4. Supported Currency Pairs**

### 🔹 Crypto-to-Fiat
- BTC → USD
- ETH → EUR
- USDT → PLN

### 🔹 Fiat-to-Fiat
- USD → EUR
- PLN → UAH

### 🔹 Crypto-to-Crypto
- ETH → BTC
- USDT → BTC

---

## **5. Usage Examples**

### ➤ Basic query
```bash
curl -s "http://localhost:5000/price?token=BTC&quote=USDT"
```

### ➤ Specific source only
```bash
curl -s "http://localhost:5000/price?token=ETH&quote=USDT&source=binance"
```

### ➤ Query just one field (`price`)
```bash
curl -s "http://localhost:5000/price?token=ETH&quote=BTC&fields=price"
```
---

## **6. Google Sheets Integration**

Use this in a cell:
```excel
=IMPORTDATA("https://<your-api-url>/price?token=ETH&quote=BTC&fields=price")
```

---

## **7. Python Integration**

```python
import requests

url = "http://localhost:5000/price"
params = {"token": "BTC", "quote": "USDT", "fields": "price"}

res = requests.get(url, params=params)
print(res.json())
```

---

## **8. Supported Sources**

| Source     | Description                             |
|------------|-----------------------------------------|
| `binance`  | Binance spot market                     |
| `okx`      | OKX spot market                         |
| `kraken`   | Kraken exchange                         |
| `coinbase` | Coinbase spot market                    |
| `mexc`     | MEXC global                             |
| `coingecko`| CoinGecko aggregator — used as fallback |
| `derived`  | Calculated using intermediate pairs (e.g., `BTC → USDT → PLN`) |

---

## **9. Error Handling**

### 🔸 Missing parameters
```json
400 Bad Request
"Missing required parameters"
```

### 🔸 Invalid source
```json
400 Bad Request
"Invalid source specified"
```

### 🔸 No data found
```json
404 Not Found
"No data found for coingecko on USD/EUR"
```

---

## **10. Docker Compose Deployment**

```bash
git clone <your-repo-url>
cd <repo-dir>
docker compose build
docker compose up -d
# Shutdown:
docker compose down
```

---

## **11. ❤️ Support the Project**

This service is free, but if you'd like to help with hosting or development:

### 💸 Donation Wallets

- **Solana (SOL)**  
  `GTsL1ZJqHthtdDLSKJt4D6x4a52NoHEG7tMtWeg1kCjK`

- **Ethereum / Polygon (ETH/POL)**  
  `0xa9Ce7AD40027a80C2EEf3475CcCc6b0B22f1Ed6D`

Even small contributions help — thank you! 🙏

