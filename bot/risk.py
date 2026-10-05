"""
Sizing and portfolio circuit-breaker for live spot orders.

Sizing uses 7.5% of portfolio as base (calibrated for $100k starting balance):
  base = equity × 0.075  →  ~$7,500 per trade on $100k

  Adjustments applied on top of the base:
    - Signal strength [0, 1] scales size from 0.75× to 1.25× of base
    - Volatility reduces size: 1% vol → 1×, 3% vol → 0.63×, 5% vol → 0.45×
    - Hard cap:  never more than 12% of portfolio per trade (~$12,000 on $100k)
    - Floor:     never less than $1,000 (below this commission drag is too high)
    - Available cash reserve is always preserved
"""

from math import floor

from bot.config import Config


class RiskManager:
    def __init__(self, initial_equity: float, settings: Config) -> None:
        self.settings = settings
        self.peak_equity = initial_equity
        self.halted = False

    def update_equity(self, equity: float) -> None:
        """Update peak equity. Circuit-breaker halt removed; bot runs continuously."""
        self.peak_equity = max(self.peak_equity, equity)

    def size_usd(
        self,
        available_usd: float,
        equity: float,
        signal_strength: float,
        volatility_pct: float,
    ) -> float:
        """
        Return the USD amount to spend on a single position.

        signal_strength  — float [0, 1]: 0 = weak EMA/RSI, 1 = strong alignment
        volatility_pct   — recent mean absolute return as a percentage (e.g. 1.5)
        """
        if self.halted or equity <= 0:
            return 0.0

        # Base size from config (7.5% of portfolio = ~$7,500 on $100k)
        base = equity * self.settings.base_position_pct

        # Signal strength: scale from 0.75× (weak) to 1.25× (strong)
        signal_mult = 0.75 + min(max(signal_strength, 0.0), 1.0) * 0.50

        # Volatility adjustment: higher volatility → smaller position
        # 1% vol → 1×,  3% vol → 0.63×,  5% vol → 0.45×
        vol_mult = max(0.40, 1.0 / (1.0 + max(volatility_pct, 0.0) * 0.30))

        size = base * signal_mult * vol_mult

        # Hard cap: never exceed 12% of portfolio per trade
        HARD_CAP_PCT = 0.12
        size = min(size, equity * HARD_CAP_PCT)

        # Also respect config max_position_pct if it's tighter
        size = min(size, equity * self.settings.max_position_pct)

        # Never spend more than (available cash − reserve)
        spendable = available_usd * (1.0 - self.settings.reserve_pct)
        size = min(size, spendable)

        # Floor: below $1,000 commission drag is too high on a $100k portfolio
        FLOOR = 1_000.0
        return size if size >= FLOOR else 0.0

    def quantity(self, usd_amount: float, price: float, precision: int) -> float:
        """Convert a USD budget to a coin quantity, accounting for commission."""
        if price <= 0:
            return 0.0
        raw = usd_amount / (price * (1 + self.settings.commission_rate))
        factor = 10**precision
        return floor(raw * factor) / factor
