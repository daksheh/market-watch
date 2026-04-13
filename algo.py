"""
market-watch — VWAP Scalping Engine
=====================================
Strategy: VWAP mean-reversion scalping with volume-spike confirmation.

How it works
------------
1. Every price tick is accumulated into a rolling intraday VWAP window.
2. VWAP = Σ(price × volume) / Σ(volume)
3. A BUY signal fires when:
     - price is BELOW vwap by at least `vwap_threshold` %
     - current volume is >= `volume_spike_multiplier` × recent average volume
     (interpretation: oversold vs. intraday mean, confirmed by crowd interest)
4. A SELL signal fires when:
     - price is ABOVE vwap by at least `vwap_threshold` %
     - volume spike confirmed
5. Each signal carries:
     - target_price  = entry ± take_profit_pct %
     - stop_price    = entry ∓ stop_loss_pct %
     - confidence    = scaled deviation / threshold  (capped at 1.0)

In-progress / TODO
------------------
- ML signal confidence layer  (see ml_signals.py — not yet integrated)
- Backtesting harness          (see backtester.py — stub only)
- ATR-based dynamic stop-loss
- Order-book depth confirmation (requires L2 data feed)
- Multi-symbol portfolio heat scoring
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class AlgoConfig:
    """Tunable parameters for the VWAP scalping engine."""

    # VWAP deviation required to trigger a signal (percent)
    vwap_threshold: float = 0.5

    # Volume must exceed this multiple of recent average to confirm signal
    volume_spike_multiplier: float = 1.5

    # Take-profit distance from entry (percent)
    take_profit_pct: float = 1.0

    # Stop-loss distance from entry (percent)
    stop_loss_pct: float = 0.5

    # Rolling window size for volume average (number of ticks)
    volume_window: int = 20

    # Rolling window for VWAP accumulation (number of ticks; 0 = session-wide)
    vwap_window: int = 0

    # Minimum ticks before signals are emitted (warm-up period)
    min_ticks: int = 5


# ---------------------------------------------------------------------------
# Per-symbol state
# ---------------------------------------------------------------------------

@dataclass
class SymbolState:
    """Accumulated VWAP data and rolling statistics for one symbol."""

    # Numerator / denominator for VWAP
    pv_sum: float = 0.0      # Σ price × volume
    vol_sum: float = 0.0     # Σ volume

    # Rolling price/volume history
    price_history:  Deque[float] = field(default_factory=deque)
    volume_history: Deque[float] = field(default_factory=deque)

    tick_count: int = 0
    last_signal: str = "HOLD"
    last_updated: float = field(default_factory=time.time)

    @property
    def vwap(self) -> Optional[float]:
        return self.pv_sum / self.vol_sum if self.vol_sum > 0 else None

    @property
    def avg_volume(self) -> float:
        if not self.volume_history:
            return 0.0
        return sum(self.volume_history) / len(self.volume_history)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class VWAPScalpingEngine:
    """
    Stateful VWAP scalping engine.

    Maintains independent state per ticker symbol.  Thread safety is not
    implemented — use a single-threaded event loop (FastAPI's default async
    workers handle this correctly for the current architecture).
    """

    def __init__(self, config: AlgoConfig | None = None) -> None:
        self.config: AlgoConfig = config or AlgoConfig()
        self._states: Dict[str, SymbolState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, symbol: str, price: float, volume: float = 0.0) -> dict:
        """
        Ingest a tick and return a signal dict.

        Parameters
        ----------
        symbol : str   Ticker (e.g. "AAPL")
        price  : float Latest trade price
        volume : float Trade volume for this tick (shares)

        Returns
        -------
        dict with keys: symbol, signal, confidence, vwap, deviation_pct,
                        target_price, stop_price, tick_count, ts
        """
        state = self._get_or_create(symbol)
        self._ingest_tick(state, price, volume)

        if state.tick_count < self.config.min_ticks:
            return self._build_result(symbol, state, "HOLD", 0.0, price)

        signal, confidence = self._compute_signal(state, price, volume)
        state.last_signal = signal
        return self._build_result(symbol, state, signal, confidence, price)

    def get_state(self, symbol: str) -> Optional[dict]:
        """Return the current rolling stats for a symbol, or None if unseen."""
        state = self._states.get(symbol)
        if state is None:
            return None
        vwap = state.vwap
        return {
            "symbol":       symbol,
            "vwap":         round(vwap, 4) if vwap else None,
            "avg_volume":   round(state.avg_volume, 2),
            "tick_count":   state.tick_count,
            "last_signal":  state.last_signal,
            "last_updated": state.last_updated,
        }

    def reset(self, symbol: str) -> None:
        """Clear all accumulated data for a symbol."""
        if symbol in self._states:
            del self._states[symbol]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(self, symbol: str) -> SymbolState:
        if symbol not in self._states:
            self._states[symbol] = SymbolState(
                price_history=deque(maxlen=self.config.volume_window or 100),
                volume_history=deque(maxlen=self.config.volume_window),
            )
        return self._states[symbol]

    def _ingest_tick(self, state: SymbolState, price: float, volume: float) -> None:
        eff_vol = max(volume, 1.0)  # guard against zero-volume ticks

        # Rolling VWAP: if windowed, subtract the oldest tick's contribution
        if self.config.vwap_window > 0 and len(state.price_history) >= self.config.vwap_window:
            old_price  = state.price_history[0]
            old_volume = state.volume_history[0] if state.volume_history else 1.0
            state.pv_sum  -= old_price * old_volume
            state.vol_sum -= old_volume
            state.pv_sum  = max(state.pv_sum, 0.0)
            state.vol_sum = max(state.vol_sum, 0.0)

        state.pv_sum  += price * eff_vol
        state.vol_sum += eff_vol
        state.price_history.append(price)
        state.volume_history.append(eff_vol)
        state.tick_count  += 1
        state.last_updated = time.time()

    def _compute_signal(
        self, state: SymbolState, price: float, volume: float
    ) -> Tuple[str, float]:
        vwap = state.vwap
        if vwap is None or vwap == 0:
            return "HOLD", 0.0

        deviation_pct = (price - vwap) / vwap * 100  # positive = above VWAP
        avg_vol       = state.avg_volume
        threshold     = self.config.vwap_threshold
        spike_mult    = self.config.volume_spike_multiplier

        volume_confirmed = avg_vol > 0 and volume >= avg_vol * spike_mult

        if not volume_confirmed:
            return "HOLD", 0.0

        if deviation_pct <= -threshold:
            # Price is sufficiently below VWAP → mean-reversion BUY
            raw_confidence = min(abs(deviation_pct) / threshold, 3.0) / 3.0
            return "BUY", round(raw_confidence, 3)

        if deviation_pct >= threshold:
            # Price is sufficiently above VWAP → mean-reversion SELL
            raw_confidence = min(abs(deviation_pct) / threshold, 3.0) / 3.0
            return "SELL", round(raw_confidence, 3)

        return "HOLD", 0.0

    def _build_result(
        self,
        symbol:     str,
        state:      SymbolState,
        signal:     str,
        confidence: float,
        price:      float,
    ) -> dict:
        vwap = state.vwap
        deviation_pct = ((price - vwap) / vwap * 100) if vwap else 0.0
        tp_pct = self.config.take_profit_pct / 100
        sl_pct = self.config.stop_loss_pct  / 100

        if signal == "BUY":
            target_price = round(price * (1 + tp_pct), 4)
            stop_price   = round(price * (1 - sl_pct), 4)
        elif signal == "SELL":
            target_price = round(price * (1 - tp_pct), 4)
            stop_price   = round(price * (1 + sl_pct), 4)
        else:
            target_price = None
            stop_price   = None

        return {
            "symbol":        symbol,
            "signal":        signal,
            "confidence":    confidence,
            "vwap":          round(vwap, 4) if vwap else None,
            "price":         price,
            "deviation_pct": round(deviation_pct, 4),
            "target_price":  target_price,
            "stop_price":    stop_price,
            "tick_count":    state.tick_count,
            "avg_volume":    round(state.avg_volume, 2),
            "ts":            int(time.time()),
        }
