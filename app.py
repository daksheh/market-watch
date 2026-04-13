"""
market-watch — FastAPI backend
Real-time stock quotes + VWAP scalping signal engine.
"""

import os
import json
import time
import csv
import tempfile
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from algo import VWAPScalpingEngine, AlgoConfig

# Note: this is a pure API — use /docs for interactive testing (FastAPI auto-generates it)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

APP_NAME = "market-watch"
VERSION  = "0.2.0"

SYMBOLS_CACHE = Path("symbols.json")

FINNHUB_TOKEN    = os.getenv("FINNHUB_TOKEN", "")
FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED  = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

GITHUB_FALLBACKS = [
    ("https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_company_list.csv", "NASDAQ"),
    ("https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nyse/nyse_company_list.csv",   "NYSE"),
    ("https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/amex/amex_company_list.csv",   "AMEX"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("market-watch")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"{APP_NAME}/{VERSION} (+http://localhost)"})

# In-memory state
SYMBOLS: List[Dict[str, Any]] = []
LAST_LOAD_DETAIL: Dict[str, Any] = {"source": "none", "count": 0, "error": None}

# Algo engine — one shared instance per process
_algo_engine = VWAPScalpingEngine(AlgoConfig())


# ---------------------------------------------------------------------------
# Symbol loading
# ---------------------------------------------------------------------------

def _parse_pipe_table(raw: str) -> List[Dict[str, str]]:
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("|")
    rows: List[Dict[str, str]] = []
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        parts = ln.split("|")
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def _dedupe(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: set = set()
    out: List[Dict[str, str]] = []
    for s in items:
        sym = (s.get("symbol") or s.get("Symbol") or "").strip()
        if sym and sym not in seen:
            seen.add(sym)
            out.append({
                "symbol":   sym,
                "name":     (s.get("name") or s.get("Security Name") or s.get("Name") or "").strip(),
                "exchange": (s.get("exchange") or s.get("Exchange") or "").strip() or "UNKNOWN",
            })
    return out


def _load_from_nasdaqtrader() -> List[Dict[str, str]]:
    log.info("Fetching symbols from NasdaqTrader...")
    nasdaq_r = SESSION.get(NASDAQ_LISTED, timeout=30)
    other_r  = SESSION.get(OTHER_LISTED,  timeout=30)
    nasdaq_r.raise_for_status()
    other_r.raise_for_status()

    symbols: List[Dict[str, str]] = []
    for r in _parse_pipe_table(nasdaq_r.text):
        if r.get("Test Issue") == "Y":
            continue
        sym = (r.get("Symbol") or "").strip()
        if sym:
            symbols.append({"symbol": sym, "name": (r.get("Security Name") or "").strip(), "exchange": "NASDAQ"})
    for r in _parse_pipe_table(other_r.text):
        if r.get("Test Issue") == "Y":
            continue
        sym = (r.get("ACT Symbol") or "").strip()
        if sym:
            symbols.append({"symbol": sym, "name": (r.get("Security Name") or "").strip(), "exchange": (r.get("Exchange") or "").strip()})

    return _dedupe(symbols)


def _load_from_github_csv(url: str, exchange_hint: str) -> List[Dict[str, str]]:
    log.info(f"Fetching fallback CSV: {url}")
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    reader = csv.DictReader(r.text.splitlines())
    out: List[Dict[str, str]] = []
    for row in reader:
        sym  = (row.get("Symbol") or row.get("symbol") or "").strip()
        name = (row.get("Name") or row.get("Security Name") or row.get("name") or "").strip()
        if sym:
            out.append({"symbol": sym, "name": name, "exchange": exchange_hint})
    return _dedupe(out)


def _write_cache_atomic(data: List[Dict[str, str]]) -> None:
    payload = json.dumps({"updated": int(time.time()), "symbols": data})
    SYMBOLS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(SYMBOLS_CACHE.parent), encoding="utf-8")
    try:
        tmp.write(payload)
        tmp.flush()
        os.replace(tmp.name, SYMBOLS_CACHE)
    finally:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass


def _load_cache() -> List[Dict[str, str]]:
    if SYMBOLS_CACHE.exists():
        try:
            return json.loads(SYMBOLS_CACHE.read_text(encoding="utf-8")).get("symbols", [])
        except (OSError, json.JSONDecodeError):
            return []
    return []


def refresh_symbols() -> List[Dict[str, str]]:
    """Load symbols from NasdaqTrader; fall back to GitHub CSV mirrors on failure."""
    global LAST_LOAD_DETAIL
    try:
        primary = _load_from_nasdaqtrader()
        if primary:
            LAST_LOAD_DETAIL = {"source": "NasdaqTrader", "count": len(primary), "error": None}
            log.info(f"Loaded {len(primary)} symbols from NasdaqTrader")
            _write_cache_atomic(primary)
            return primary
        log.warning("NasdaqTrader returned 0 symbols; switching to fallbacks")
    except (requests.RequestException, csv.Error, ValueError) as exc:
        log.warning(f"NasdaqTrader error: {exc}; switching to fallbacks")

    merged: List[Dict[str, str]] = []
    errors: List[str] = []
    for url, exch in GITHUB_FALLBACKS:
        try:
            merged.extend(_load_from_github_csv(url, exch))
        except (requests.RequestException, csv.Error, ValueError) as exc:
            errors.append(f"{exch}: {exc}")

    merged = _dedupe(merged)
    if not merged:
        LAST_LOAD_DETAIL = {"source": "fallbacks_failed", "count": 0, "error": "; ".join(errors) or "unknown"}
        log.error(f"All symbol sources failed: {LAST_LOAD_DETAIL['error']}")
        return []

    LAST_LOAD_DETAIL = {"source": "GitHub fallback", "count": len(merged), "error": None}
    log.info(f"Loaded {len(merged)} symbols from GitHub fallbacks")
    _write_cache_atomic(merged)
    return merged


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global SYMBOLS
    SYMBOLS = _load_cache() or refresh_symbols()
    log.info(f"{APP_NAME} v{VERSION} ready — {len(SYMBOLS)} symbols loaded")
    yield


app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Real-time market data + VWAP scalping signal engine",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health + symbols
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["system"])
def api_health():
    return {
        "app":        APP_NAME,
        "version":    VERSION,
        "symbols":    len(SYMBOLS),
        "token_set":  bool(FINNHUB_TOKEN),
        "load_detail": LAST_LOAD_DETAIL,
    }


@app.get("/api/symbols/refresh", tags=["system"])
def api_symbols_refresh():
    global SYMBOLS
    SYMBOLS = refresh_symbols()
    return {"ok": True, "count": len(SYMBOLS), "source": LAST_LOAD_DETAIL.get("source")}


@app.get("/api/search", tags=["market"])
def api_search(q: str = Query("", min_length=1), limit: int = Query(8, ge=1, le=50)):
    """Typeahead search by ticker symbol or company name."""
    global SYMBOLS
    if not SYMBOLS:
        SYMBOLS = refresh_symbols()

    q_up = q.upper().strip()
    if not q_up:
        return {"results": []}

    starts  = [s for s in SYMBOLS if s["symbol"].startswith(q_up)]
    subs    = [s for s in SYMBOLS if q_up in s["symbol"] and not s["symbol"].startswith(q_up)]
    by_name = [s for s in SYMBOLS if q_up in (s["name"] or "").upper()]

    out, seen = [], set()
    for s in starts + subs + by_name:
        sym = s["symbol"]
        if sym in seen:
            continue
        seen.add(sym)
        out.append({"symbol": sym, "name": s["name"], "exchange": s["exchange"]})
        if len(out) >= limit:
            break
    return {"results": out}


@app.get("/api/quote", tags=["market"])
def api_quote(symbol: str = Query(..., min_length=1)):
    """Fetch live quote from Finnhub."""
    sym = symbol.upper().strip()
    if not FINNHUB_TOKEN:
        raise HTTPException(500, detail="FINNHUB_TOKEN not configured on server. See README for setup.")
    try:
        r = SESSION.get(FINNHUB_QUOTE_URL, params={"symbol": sym, "token": FINNHUB_TOKEN}, timeout=10)
        if r.status_code == 429:
            raise HTTPException(503, detail="Rate limit hit (429). Try again in a moment.")
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        raise HTTPException(502, detail=f"Provider error: {exc}")

    if not isinstance(data, dict) or "c" not in data:
        raise HTTPException(502, detail="Unexpected response from quote provider")

    return {
        "symbol":    sym,
        "price":     data.get("c"),
        "open":      data.get("o"),
        "high":      data.get("h"),
        "low":       data.get("l"),
        "prevClose": data.get("pc"),
        "change":    data.get("d"),
        "changePct": data.get("dp"),
        "volume":    data.get("v", 0),
        "provider":  "finnhub",
        "ts":        int(time.time()),
    }


# ---------------------------------------------------------------------------
# Algo endpoints
# ---------------------------------------------------------------------------

@app.get("/api/algo/signal", tags=["algo"])
def api_algo_signal(
    symbol: str  = Query(..., min_length=1, description="Ticker symbol"),
    price:  float = Query(..., gt=0,         description="Current price"),
    volume: float = Query(0.0, ge=0,          description="Current volume (shares)"),
):
    """
    Feed a price/volume tick into the VWAP scalping engine and get a trade signal.

    Returns BUY, SELL, or HOLD with confidence score, VWAP, and target/stop prices.
    """
    sym = symbol.upper().strip()
    result = _algo_engine.evaluate(sym, price, volume)
    return result


@app.get("/api/algo/state", tags=["algo"])
def api_algo_state(symbol: str = Query(..., min_length=1)):
    """Return the current VWAP and rolling statistics for a symbol."""
    sym = symbol.upper().strip()
    state = _algo_engine.get_state(sym)
    if state is None:
        raise HTTPException(404, detail=f"No data for {sym}. Send at least one tick first.")
    return state


@app.post("/api/algo/reset", tags=["algo"])
def api_algo_reset(symbol: str = Query(..., min_length=1)):
    """Clear accumulated VWAP data for a symbol (e.g. at start of new session)."""
    sym = symbol.upper().strip()
    _algo_engine.reset(sym)
    return {"ok": True, "symbol": sym}


@app.get("/api/algo/config", tags=["algo"])
def api_algo_config():
    """Return the current algorithm configuration."""
    return _algo_engine.config.__dict__


# TODO: WebSocket endpoint for real-time signal streaming (in progress)
# @app.websocket("/ws/signals/{symbol}")
# async def ws_signals(websocket: WebSocket, symbol: str):
#     ...
