"""
Stateful EMA/RSI spot-momentum strategy — v2.

Key changes from v1 (ported from trade-bot reference):
- Tighter RSI buy zone: 45–58  (was 42–62) — avoids overbought entries
- RSI sell floor raised to 45  (was 43)    — don't sell into oversold dips
- Stronger EMA separation: 0.05%           (was 0.035%)
- Longer slow EMA: 21 periods              (was 18)
- Higher min_history: 50 ticks             (was 20)
- last_signal tracking guards duplicate BUY events
- SELL only resets entry state after confirming the signal fired
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from bot.config import Config

logger = logging.getLogger(__name__)

Signal = Literal["BUY", "SELL", "HOLD"]


@dataclass
class PairState:
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=240))
    entry_price: float = 0.0
    hold_cycles: int = 0
    ticks_above_slow: int = 0
    ticks_below_slow: int = 0
    cooldown_cycles: int = 0
    restored: bool = False
    last_signal: Signal = "HOLD"


def ema(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return float("nan")
    multiplier = 2 / (period + 1)
    result = prices[0]
    for price in prices[1:]:
        result = price * multiplier + result * (1 - multiplier)
    return result


def rsi(prices: list[float], period: int) -> float:
    """Compute RSI using only the most recent `period + 1` prices."""
    if len(prices) < period + 1:
        return 50.0
    relevant = prices[-(period + 1) :]
    changes = [relevant[i + 1] - relevant[i] for i in range(len(relevant) - 1)]
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
        state.prices.extend(reversed([p for p in prices if p > 0]))
        state.restored = True

    def notify_bought(self, pair: str, price: float) -> None:
        state = self._state(pair)
        state.entry_price = price
        state.hold_cycles = 0
        state.last_signal = "BUY"

    def notify_sold(self, pair: str, was_loss: bool) -> None:
        state = self._state(pair)
        state.entry_price = 0.0
        state.hold_cycles = 0
        state.last_signal = "HOLD"
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

        # First live tick after a DB restore — skip to avoid phantom crossovers
        if state.restored:
            state.restored = False
            return "HOLD"

        fast = ema(prices, cfg.fast_ema_period)
        slow = ema(prices, cfg.slow_ema_period)
        prev_prices = prices[:-1]
        previous_fast = ema(prev_prices, cfg.fast_ema_period)
        previous_slow = ema(prev_prices, cfg.slow_ema_period)
        current_rsi = rsi(prices, cfg.rsi_period)
        separation = (fast - slow) / slow * 100 if slow > 0 else 0.0

        # Track consecutive ticks fast EMA has been above / below slow EMA
        if fast > slow:
            state.ticks_above_slow += 1
            state.ticks_below_slow = 0
        else:
            state.ticks_below_slow += 1
            state.ticks_above_slow = 0
        state.cooldown_cycles = max(0, state.cooldown_cycles - 1)

        signal: Signal = "HOLD"

        # ── SELL path ───────────────────────────────────────────────────────
        if state.entry_price > 0 or state.last_signal == "BUY":
            state.hold_cycles += 1
            pnl = (
                (price - state.entry_price) / state.entry_price * 100
                if state.entry_price > 0
                else 0.0
            )

            # 1. Take profit
            if pnl >= cfg.take_profit_pct:
                signal = "SELL"
                logger.info("%s TAKE-PROFIT: pnl=%.2f%%", pair, pnl)

            # 2. Hard stop-loss — exit immediately when loss exceeds threshold.
            #    No EMA confirmation required: when you're down stop_loss_pct
            #    the trade is simply wrong regardless of EMA position.
            elif pnl <= -cfg.stop_loss_pct:
                signal = "SELL"
                state.cooldown_cycles = cfg.loss_cooldown_cycles
                logger.warning(
                    "%s STOP-LOSS: pnl=%.2f%% — cooldown %d",
                    pair,
                    pnl,
                    cfg.loss_cooldown_cycles,
                )

            # 3. EMA bearish crossover — fresh cross of fast below slow, held long
            #    enough to not be noise, and RSI not already oversold.
            #    No profit requirement: if momentum reversed we exit regardless.
            elif (
                previous_fast >= previous_slow
                and fast < slow
                and current_rsi >= cfg.rsi_sell_min
                and state.hold_cycles >= cfg.min_hold_cycles
            ):
                signal = "SELL"
                if pnl < 0:
                    state.cooldown_cycles = cfg.loss_cooldown_cycles
                logger.info(
                    "%s EMA-SELL: pnl=%.2f%% cycles=%d", pair, pnl, state.hold_cycles
                )

            # 4. Stagnant exit — trade isn't working and EMA has turned against us.
            #    Requires EMA confirmed below slow for ≥ 2 ticks (not just a dip)
            #    and held long enough to be sure it's not a warmup artefact.
            elif (
                state.hold_cycles >= cfg.stagnant_exit_cycles
                and state.ticks_below_slow >= 2
                and pnl < cfg.min_profit_pct
            ):
                signal = "SELL"
                state.cooldown_cycles = cfg.loss_cooldown_cycles if pnl < 0 else 0
                logger.info(
                    "%s STAGNANT EXIT: pnl=%.2f%% cycles=%d ema_below=%d ticks",
                    pair,
                    pnl,
                    state.hold_cycles,
                    state.ticks_below_slow,
                )

            if signal == "SELL":
                state.entry_price = 0.0
                state.hold_cycles = 0

        # ── BUY path ────────────────────────────────────────────────────────
        else:
            # Fresh crossover: fast EMA just crossed above slow EMA this tick
            crossed_up = previous_fast <= previous_slow and fast > slow

            # Trend confirmed: crossover happened in the last confirm_ticks window
            # (narrow window prevents stale signals after DB restore)
            trend_confirmed = (
                cfg.confirm_ticks <= state.ticks_above_slow <= cfg.confirm_ticks + 2
            )

            if (
                (crossed_up or trend_confirmed)
                and state.cooldown_cycles == 0
                and cfg.rsi_buy_min <= current_rsi <= cfg.rsi_buy_max
                and separation >= cfg.ema_separation_pct
                and state.entry_price == 0  # don't overwrite open position
                and state.last_signal != "BUY"  # don't re-fire stale BUY state
            ):
                signal = "BUY"
                state.entry_price = price
                state.hold_cycles = 0

        if signal != "HOLD":
            logger.info(
                "%s signal=%s | fast=%.6f slow=%.6f rsi=%.1f sep=%.4f%% "
                "cooldown=%d confirm_ticks=%d",
                pair,
                signal,
                fast,
                slow,
                current_rsi,
                separation,
                state.cooldown_cycles,
                state.ticks_above_slow,
            )

        state.last_signal = signal
        return signal

    def indicators(self, pair: str) -> dict[str, float | int | bool]:
        state, cfg = self._state(pair), self.settings
        prices = list(state.prices)
        if len(prices) < cfg.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}
        fast = ema(prices, cfg.fast_ema_period)
        slow = ema(prices, cfg.slow_ema_period)
        return {
            "warming_up": False,
            "rsi": round(rsi(prices, cfg.rsi_period), 2),
            "fast_ema": fast,
            "slow_ema": slow,
            "ema_sep_pct": (fast - slow) / slow * 100 if slow else 0.0,
            "entry_price": state.entry_price,
            "hold_cycles": state.hold_cycles,
            "ticks_above_slow": state.ticks_above_slow,
            "ticks_below_slow": state.ticks_below_slow,
            "cooldown_cycles": state.cooldown_cycles,
            "prices_collected": len(prices),
        }
