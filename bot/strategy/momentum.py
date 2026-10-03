"""
momentum.py — EMA crossover + RSI strategy

Entry:
  - fast EMA crosses above slow EMA (fresh, within confirm_ticks window)
  - RSI in the 45–58 zone
  - EMA separation >= 0.05%
  - no active loss cooldown

Exit:
  - stop-loss at -2%
  - take-profit at +3%
  - EMA cross-down if profitable and held long enough
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

log = logging.getLogger(__name__)

Signal = Literal["BUY", "SELL", "HOLD"]


@dataclass
class MomentumConfig:
    fast_ema: int = 8
    slow_ema: int = 21
    rsi_period: int = 14
    min_history: int = 50

    rsi_buy_min: float = 45.0
    rsi_buy_max: float = 58.0
    rsi_sell_min: float = 45.0

    ema_separation_pct: float = 0.05
    confirm_ticks: int = 2

    min_hold_cycles: int = 3
    min_profit_pct: float = 0.25
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 3.0

    loss_cooldown_cycles: int = 3


@dataclass
class PairState:
    prices: deque = field(default_factory=lambda: deque(maxlen=200))
    last_signal: Signal = "HOLD"
    entry_price: float = 0.0
    hold_cycles: int = 0
    just_restored: bool = False
    ticks_above_slow: int = 0
    cooldown_cycles: int = 0


def _ema(prices: list, period: int) -> float:
    if len(prices) < period:
        return float("nan")
    k = 2 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return val


def _rsi(prices: list, period: int) -> float:
    if len(prices) < period + 1:
        return 50.0
    chunk = prices[-(period + 1):]
    deltas = [chunk[i + 1] - chunk[i] for i in range(len(chunk) - 1)]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


class MomentumStrategy:
    def __init__(self, config: MomentumConfig | None = None):
        self.config = config or MomentumConfig()
        self._states: dict[str, PairState] = {}
        self._restoring = False

    def _state(self, pair: str) -> PairState:
        if pair not in self._states:
            self._states[pair] = PairState()
        return self._states[pair]

    def update(self, pair: str, price: float) -> Signal:
        if price <= 0:
            return "HOLD"

        st = self._state(pair)
        st.prices.append(price)
        prices = list(st.prices)
        cfg = self.config

        if len(prices) < cfg.min_history:
            return "HOLD"

        if st.just_restored:
            st.just_restored = False
            return "HOLD"

        fast = _ema(prices, cfg.fast_ema)
        slow = _ema(prices, cfg.slow_ema)
        rsi = _rsi(prices, cfg.rsi_period)
        prev = prices[:-1]
        prev_fast = _ema(prev, cfg.fast_ema)
        prev_slow = _ema(prev, cfg.slow_ema)

        sep_pct = ((fast - slow) / slow * 100) if slow > 0 else 0

        if fast > slow:
            st.ticks_above_slow += 1
        else:
            st.ticks_above_slow = 0

        if st.cooldown_cycles > 0:
            st.cooldown_cycles -= 1

        signal: Signal = "HOLD"

        crossed_up = prev_fast <= prev_slow and fast > slow

        # narrow window after crossover fires the buy
        trend_ok = (
            st.ticks_above_slow >= cfg.confirm_ticks
            and st.ticks_above_slow <= cfg.confirm_ticks + 2
            and cfg.rsi_buy_min <= rsi <= cfg.rsi_buy_max
            and sep_pct >= cfg.ema_separation_pct
            and st.cooldown_cycles == 0
            and not st.just_restored
            and st.entry_price == 0
            and st.last_signal != "BUY"
        )

        buy_on_cross = (
            crossed_up
            and fast > slow
            and cfg.rsi_buy_min <= rsi <= cfg.rsi_buy_max
            and sep_pct >= cfg.ema_separation_pct
            and st.cooldown_cycles == 0
            and not st.just_restored
            and st.entry_price == 0
            and st.last_signal != "BUY"
        )

        if trend_ok or buy_on_cross:
            signal = "BUY"
            st.entry_price = price
            st.hold_cycles = 0

        elif st.last_signal == "BUY" or st.hold_cycles > 0:
            st.hold_cycles += 1
            pnl = ((price - st.entry_price) / st.entry_price * 100) if st.entry_price > 0 else 0

            if not self._restoring and pnl <= -cfg.stop_loss_pct:
                signal = "SELL"
                st.cooldown_cycles = cfg.loss_cooldown_cycles
                log.warning("%s stop-loss hit: pnl=%.2f%%", pair, pnl)

            elif not self._restoring and pnl >= cfg.take_profit_pct:
                signal = "SELL"
                log.info("%s take-profit hit: pnl=%.2f%%", pair, pnl)

            elif (
                prev_fast >= prev_slow and fast < slow
                and rsi > cfg.rsi_sell_min
                and st.hold_cycles >= cfg.min_hold_cycles
                and pnl >= cfg.min_profit_pct
            ):
                signal = "SELL"
                log.info("%s ema cross-down sell: pnl=%.2f%% cycles=%d", pair, pnl, st.hold_cycles)

        if signal != "HOLD":
            log.info(
                "%s %s | fast=%.4f slow=%.4f rsi=%.1f sep=%.3f%% cd=%d ticks=%d",
                pair, signal, fast, slow, rsi, sep_pct,
                st.cooldown_cycles, st.ticks_above_slow,
            )

        if signal == "SELL":
            st.entry_price = 0.0
            st.hold_cycles = 0

        st.last_signal = signal
        return signal

    def notify_bought(self, pair: str, price: float):
        st = self._state(pair)
        st.entry_price = price
        st.hold_cycles = 0
        st.last_signal = "BUY"

    def notify_sold(self, pair: str, was_loss: bool = False):
        st = self._state(pair)
        st.entry_price = 0.0
        st.hold_cycles = 0
        st.last_signal = "HOLD"
        if was_loss:
            st.cooldown_cycles = self.config.loss_cooldown_cycles

    def indicators(self, pair: str) -> dict:
        st = self._state(pair)
        prices = list(st.prices)
        cfg = self.config
        if len(prices) < cfg.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}
        fast = _ema(prices, cfg.fast_ema)
        slow = _ema(prices, cfg.slow_ema)
        sep = ((fast - slow) / slow * 100) if slow > 0 else 0
        return {
            "warming_up": False,
            "pair": pair,
            "last_price": prices[-1],
            "fast_ema": round(fast, 6),
            "slow_ema": round(slow, 6),
            "ema_sep_pct": round(sep, 4),
            "rsi": round(_rsi(prices, cfg.rsi_period), 2),
            "hold_cycles": st.hold_cycles,
            "entry_price": st.entry_price,
            "ticks_above_slow": st.ticks_above_slow,
            "cooldown_cycles": st.cooldown_cycles,
            "prices_collected": len(prices),
        }

    def reset(self, pair: str | None = None):
        if pair:
            self._states.pop(pair, None)
        else:
            self._states.clear()
