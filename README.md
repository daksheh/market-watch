# market-watch

A Python backend for real-time stock data and VWAP-based scalping signals.

Built with FastAPI. Exposes a REST API for symbol search, live quotes (via Finnhub), and an intraday VWAP scalping algorithm. Use the auto-generated docs at `/docs` to interact with every endpoint.

---

## Features

| Status | Feature |
|--------|---------|
| ✅ | Typeahead symbol search — 10,000+ US tickers (NasdaqTrader + GitHub fallbacks) |
| ✅ | Live quotes via Finnhub (price, OHLC, change %) |
| ✅ | VWAP scalping signal engine with volume-spike confirmation |
| ✅ | Configurable algorithm parameters (`AlgoConfig`) |
| ✅ | Atomic symbol cache (`symbols.json`) — no re-download on restart |
| 🚧 | Backtesting harness (`backtester.py`) |
| 🚧 | Order submission API |
| 📋 | WebSocket real-time signal streaming |
| 📋 | ML-based signal confidence scoring |

---

## Algorithm — VWAP Scalping

The signal engine uses **VWAP mean-reversion scalping** with volume-spike confirmation — a common intraday algorithmic trading strategy.

### Logic

1. Every price tick updates a rolling VWAP accumulator per symbol.
2. **VWAP** = `Σ(price × volume) / Σ(volume)` — the volume-weighted "fair value" for the session.
3. A **BUY signal** fires when:
   - Price is ≥ `vwap_threshold` % **below** VWAP (oversold relative to intraday mean)
   - Current volume ≥ `volume_spike_multiplier` × recent average (crowd confirms the move)
4. A **SELL signal** fires when price is ≥ threshold above VWAP with a volume spike.
5. Each signal includes a `target_price` (take profit at ±1%) and `stop_price` (stop loss at ∓0.5%).

### Default parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `vwap_threshold` | `0.5%` | Min VWAP deviation to trigger a signal |
| `volume_spike_multiplier` | `1.5×` | Required volume vs. rolling average |
| `take_profit_pct` | `1.0%` | Take profit distance from entry |
| `stop_loss_pct` | `0.5%` | Stop loss distance from entry |
| `volume_window` | `20 ticks` | Rolling average window size |
| `min_ticks` | `5` | Warm-up period before signals emit |

All parameters are in `algo.py` → `AlgoConfig`.

---

## Setup

### 1. Clone

```bash
git clone https://github.com/daksheh/market-watch.git
cd market-watch
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Get a free Finnhub API token

Live quotes require a free key from [finnhub.io](https://finnhub.io/register).

### 4. Set the environment variable

```bash
export FINNHUB_TOKEN=your_token_here   # macOS / Linux
```

### 5. Run

```bash
uvicorn app:app --reload
```

Open **http://localhost:8000/docs** for the interactive API explorer.

---

## API Reference

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | App status, symbol count, token presence |
| `GET` | `/api/symbols/refresh` | Force re-download symbol list |

### Market Data

| Method | Endpoint | Params | Description |
|--------|----------|--------|-------------|
| `GET` | `/api/search` | `q`, `limit` | Typeahead symbol/name search |
| `GET` | `/api/quote` | `symbol` | Live OHLC quote from Finnhub |

### Algorithm

| Method | Endpoint | Params | Description |
|--------|----------|--------|-------------|
| `GET` | `/api/algo/signal` | `symbol`, `price`, `volume` | Feed a tick, get a VWAP signal |
| `GET` | `/api/algo/state` | `symbol` | Current VWAP and rolling stats |
| `GET` | `/api/algo/config` | — | Active algorithm parameters |
| `POST` | `/api/algo/reset` | `symbol` | Clear VWAP state for a new session |

Full interactive docs: **http://localhost:8000/docs**

---

## Project Structure

```
market-watch/
├── app.py           # FastAPI app — routes, symbol loading, quote proxy
├── algo.py          # VWAP scalping engine (VWAPScalpingEngine, AlgoConfig)
├── backtester.py    # Backtesting harness — in progress
├── requirements.txt
└── .gitignore
```

---

## Tech Stack

- **Python 3.11+**
- **FastAPI** — REST API framework
- **Uvicorn** — ASGI server
- **Requests** — HTTP client for Finnhub + NasdaqTrader
- **Finnhub** — free market data API

---

## Disclaimer

For educational and research purposes only. Not financial advice. Do not connect to a live brokerage without fully understanding the risks.
