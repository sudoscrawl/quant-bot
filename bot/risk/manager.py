"""
manager.py — position sizing and drawdown guard

"""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_position_pct: float = 0.15   # max 15% of portfolio per trade
    min_order_usd: float = 10.0
    max_drawdown_pct: float = 0.12   # halt if drawdown > 12%
    reserve_pct: float = 0.05        # keep 5% cash untouched


class RiskManager:
    def __init__(self, initial_balance: float, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self.initial_balance = initial_balance
        self.peak_balance = initial_balance
        self._halted = False

    def update_balance(self, current: float):
        """Call each cycle with current portfolio value."""
        if current > self.peak_balance:
            self.peak_balance = current

        dd = (self.peak_balance - current) / self.peak_balance
        if dd >= self.config.max_drawdown_pct:
            if not self._halted:
                log.warning(
                    "drawdown halt: %.1f%% (peak=%.2f now=%.2f)",
                    dd * 100, self.peak_balance, current,
                )
            self._halted = True
        else:
            if self._halted:
                log.info("drawdown recovered, resuming")
            self._halted = False

    def is_halted(self) -> bool:
        return self._halted

    def position_size_usd(self, available_usd: float, portfolio_value: float) -> float:
        """How much USD to allocate to a single trade."""
        if self._halted:
            return 0.0

        max_alloc = portfolio_value * self.config.max_position_pct
        spendable = available_usd * (1 - self.config.reserve_pct)
        size = min(max_alloc, spendable)

        if size < self.config.min_order_usd:
            return 0.0

        return size

    def check_order(self, qty: float, price: float) -> bool:
        return qty * price >= self.config.min_order_usd

    def drawdown(self, current: float) -> float:
        if self.peak_balance == 0:
            return 0.0
        return max(0.0, (self.peak_balance - current) / self.peak_balance)

    def total_return(self, current: float) -> float:
        return (current - self.initial_balance) / self.initial_balance
