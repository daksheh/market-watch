"""
market-watch — Backtesting Harness  [IN PROGRESS]
===================================================
Replay historical OHLCV data through the VWAP scalping engine and measure
strategy performance.

Status: stub — core metrics and data loader are wired up but the replay
loop and reporting are still being built out.

Planned metrics
---------------
- Total return %
- Sharpe ratio
- Max drawdown
- Win rate
- Avg holding time
- Profit factor

Usage (planned)
---------------
    python backtester.py --symbol AAPL --start 2024-01-01 --end 2024-06-01

TODO
----
- [ ] OHLCV CSV / Parquet data loader
- [ ] Tick replay loop feeding VWAPScalpingEngine
- [ ] Trade log + P&L ledger
- [ ] HTML report generation (matplotlib / plotly)
- [ ] Parameter sweep / grid search for AlgoConfig tuning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import time

from algo import AlgoConfig, VWAPScalpingEngine


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OHLCV:
    """One candlestick bar."""
    ts:     int    # unix timestamp (seconds)
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float


@dataclass
class Trade:
    symbol:     str
    side:       str   # "BUY" | "SELL"
    entry_price: float
    exit_price:  Optional[float] = None
    qty:         float = 1.0
    entry_ts:    int   = field(default_factory=lambda: int(time.time()))
    exit_ts:     Optional[int] = None
    pnl:         Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.exit_price is None


@dataclass
class BacktestResult:
    """Aggregate performance statistics."""
    symbol:        str
    total_trades:  int   = 0
    winning_trades: int  = 0
    total_pnl:     float = 0.0
    max_drawdown:  float = 0.0
    sharpe_ratio:  Optional[float] = None   # TODO: implement
    win_rate:      Optional[float] = None

    def summarize(self) -> dict:
        return {
            "symbol":         self.symbol,
            "total_trades":   self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate":       round(self.win_rate or 0, 4),
            "total_pnl":      round(self.total_pnl, 4),
            "max_drawdown":   round(self.max_drawdown, 4),
            "sharpe_ratio":   self.sharpe_ratio,
        }


# ---------------------------------------------------------------------------
# Backtester  (in progress)
# ---------------------------------------------------------------------------

class Backtester:
    """
    Replay engine for the VWAP scalping strategy.

    Feed it a list of OHLCV bars and it drives the algo engine tick-by-tick,
    recording trade entries and exits.
    """

    def __init__(self, config: AlgoConfig | None = None) -> None:
        self.config = config or AlgoConfig()
        self.engine = VWAPScalpingEngine(self.config)

    def run(self, symbol: str, bars: List[OHLCV]) -> BacktestResult:
        """
        Replay bars through the engine.

        TODO: implement full trade ledger, P&L tracking, drawdown calculation.
        """
        result = BacktestResult(symbol=symbol)
        open_trade: Optional[Trade] = None

        for bar in bars:
            # Use the bar's close price + volume as the tick
            signal_data = self.engine.evaluate(symbol, bar.close, bar.volume)
            signal      = signal_data["signal"]

            # --- entry ---
            if open_trade is None and signal in ("BUY", "SELL"):
                open_trade = Trade(
                    symbol=symbol,
                    side=signal,
                    entry_price=bar.close,
                    entry_ts=bar.ts,
                )
                result.total_trades += 1

            # --- exit: opposite signal or target/stop hit ---
            elif open_trade is not None:
                target = signal_data.get("target_price") or 0
                stop   = signal_data.get("stop_price")   or 0

                hit_target = (open_trade.side == "BUY"  and bar.high >= target) or \
                             (open_trade.side == "SELL" and bar.low  <= target)
                hit_stop   = (open_trade.side == "BUY"  and bar.low  <= stop) or \
                             (open_trade.side == "SELL" and bar.high >= stop)

                if hit_target or hit_stop or (signal != "HOLD" and signal != open_trade.side):
                    exit_price = target if hit_target else (stop if hit_stop else bar.close)
                    pnl_factor = 1 if open_trade.side == "BUY" else -1
                    pnl = (exit_price - open_trade.entry_price) * pnl_factor * open_trade.qty

                    open_trade.exit_price = exit_price
                    open_trade.exit_ts    = bar.ts
                    open_trade.pnl        = pnl

                    result.total_pnl += pnl
                    if pnl > 0:
                        result.winning_trades += 1
                    open_trade = None

        if result.total_trades > 0:
            result.win_rate = result.winning_trades / result.total_trades

        # TODO: compute Sharpe ratio and max drawdown from equity curve
        return result


# ---------------------------------------------------------------------------
# CLI entry point  (TODO: argparse)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Backtester CLI — coming soon. See backtester.py for current status.")
