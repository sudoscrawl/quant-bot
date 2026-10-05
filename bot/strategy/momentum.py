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
<<<<<<< HEAD
    relevant = prices[-(period + 1) :]
    changes = [relevant[i + 1] - relevant[i] for i in range(len(relevant) - 1)]
=======
    changes = [prices[i + 1] - prices[i] for i in range(-period - 1, -1)]
>>>>>>> parent of 7d70ca2 (feat: new strategy implementations)
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
<<<<<<< HEAD
            pnl = (
                (price - state.entry_price) / state.entry_price * 100
                if state.entry_price > 0
                else 0.0
            )

            # 1. Take profit
            if pnl >= cfg.take_profit_pct:
                signal = "SELL"
                logger.info("%s TAKE-PROFIT: pnl=%.2f%%", pair, pnl)

            # 2. Soft stop-loss — only exit when momentum has already turned
            #    (fast EMA below slow for ≥ 2 ticks confirms it's not a brief spike).
            #    Avoids panic-selling a dip that's still in an uptrend.
            elif pnl <= -cfg.stop_loss_pct and state.ticks_below_slow >= 2:
                signal = "SELL"
                state.cooldown_cycles = cfg.loss_cooldown_cycles
                logger.warning(
                    "%s SOFT STOP-LOSS: pnl=%.2f%% ema_below=%d ticks — cooldown %d",
                    pair,
                    pnl,
                    state.ticks_below_slow,
                    cfg.loss_cooldown_cycles,
                )

            # 3. EMA bearish crossover — fresh cross of fast below slow, held long
            #    enough to not be noise, and RSI not already oversold.
            #    No profit requirement: if momentum reversed we exit regardless.
            elif (
                previous_fast >= previous_slow
                and fast < slow
=======
            pnl = (price - state.entry_price) / state.entry_price * 100
            if pnl <= -cfg.stop_loss_pct or pnl >= cfg.take_profit_pct:
                return "SELL"
            crossed_down = previous_fast >= previous_slow and fast < slow
            if (
                crossed_down
>>>>>>> parent of 7d70ca2 (feat: new strategy implementations)
                and current_rsi >= cfg.rsi_sell_min
                and state.hold_cycles >= cfg.min_hold_cycles
                and pnl >= cfg.min_profit_pct
            ):
                return "SELL"
            return "HOLD"

<<<<<<< HEAD
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
=======
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
>>>>>>> parent of 7d70ca2 (feat: new strategy implementations)

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
