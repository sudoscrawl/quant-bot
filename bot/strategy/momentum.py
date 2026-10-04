"""Stateful EMA/RSI spot-momentum strategy."""

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from bot.config import Config

Signal = Literal["BUY", "SELL", "HOLD"]


@dataclass
class PairState:
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=240))
    entry_price: float = 0.0
    hold_cycles: int = 0
    ticks_above_slow: int = 0
    cooldown_cycles: int = 0
    restored: bool = False


def ema(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return float("nan")
    multiplier = 2 / (period + 1)
    result = prices[0]
    for price in prices[1:]:
        result = price * multiplier + result * (1 - multiplier)
    return result


def rsi(prices: list[float], period: int) -> float:
    if len(prices) < period + 1:
        return 50.0
    changes = [prices[i + 1] - prices[i] for i in range(-period - 1, -1)]
    gain = sum(change for change in changes if change > 0) / period
    loss = sum(-change for change in changes if change < 0) / period
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


class MomentumStrategy:
    def __init__(self, settings: Config) -> None:
        self.settings = settings
        self._states: dict[str, PairState] = {}

    def _state(self, pair: str) -> PairState:
        return self._states.setdefault(pair, PairState())

    def restore(self, pair: str, prices: list[float]) -> None:
        state = self._state(pair)
        state.prices.extend(reversed([price for price in prices if price > 0]))
        state.restored = True

    def notify_bought(self, pair: str, price: float) -> None:
        state = self._state(pair)
        state.entry_price, state.hold_cycles = price, 0

    def notify_sold(self, pair: str, was_loss: bool) -> None:
        state = self._state(pair)
        state.entry_price, state.hold_cycles = 0.0, 0
        if was_loss:
            state.cooldown_cycles = self.settings.loss_cooldown_cycles

    def update(self, pair: str, price: float) -> Signal:
        if price <= 0:
            return "HOLD"
        state, cfg = self._state(pair), self.settings
        state.prices.append(price)
        prices = list(state.prices)
        if len(prices) < cfg.min_history:
            return "HOLD"
        if state.restored:
            state.restored = False
            return "HOLD"

        fast, slow = ema(prices, cfg.fast_ema_period), ema(prices, cfg.slow_ema_period)
        previous_fast = ema(prices[:-1], cfg.fast_ema_period)
        previous_slow = ema(prices[:-1], cfg.slow_ema_period)
        current_rsi = rsi(prices, cfg.rsi_period)
        separation = (fast - slow) / slow * 100 if slow > 0 else 0.0
        state.ticks_above_slow = state.ticks_above_slow + 1 if fast > slow else 0
        state.cooldown_cycles = max(0, state.cooldown_cycles - 1)

        if state.entry_price:
            state.hold_cycles += 1
            pnl = (price - state.entry_price) / state.entry_price * 100
            if pnl <= -cfg.stop_loss_pct or pnl >= cfg.take_profit_pct:
                return "SELL"
            crossed_down = previous_fast >= previous_slow and fast < slow
            if (
                crossed_down
                and current_rsi >= cfg.rsi_sell_min
                and state.hold_cycles >= cfg.min_hold_cycles
                and pnl >= cfg.min_profit_pct
            ):
                return "SELL"
            return "HOLD"

        crossed_up = previous_fast <= previous_slow and fast > slow
        confirmed = cfg.confirm_ticks <= state.ticks_above_slow <= cfg.confirm_ticks + 2
        if (
            (crossed_up or confirmed)
            and state.cooldown_cycles == 0
            and cfg.rsi_buy_min <= current_rsi <= cfg.rsi_buy_max
            and separation >= cfg.ema_separation_pct
        ):
            return "BUY"
        return "HOLD"

    def indicators(self, pair: str) -> dict[str, float | int | bool]:
        state, cfg = self._state(pair), self.settings
        prices = list(state.prices)
        if len(prices) < cfg.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}
        fast, slow = ema(prices, cfg.fast_ema_period), ema(prices, cfg.slow_ema_period)
        return {
            "warming_up": False,
            "rsi": round(rsi(prices, cfg.rsi_period), 2),
            "fast_ema": fast,
            "slow_ema": slow,
            "ema_sep_pct": (fast - slow) / slow * 100 if slow else 0.0,
            "entry_price": state.entry_price,
            "hold_cycles": state.hold_cycles,
        }
