"""The application's only configuration surface."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    # Exchange / persistence
    api_key: str = Field(default="", alias="API_KEY")
    api_secret: str = Field(default="", alias="API_SECRET")
    db_url: str = Field(default="sqlite:///data/quant_bot.sqlite3", alias="DB_URL")
    exchange_base_url: str = Field(
        default="https://mock-api.roostoo.com", alias="EXCHANGE_BASE_URL"
    )
    request_timeout_seconds: float = Field(
        default=10.0, alias="REQUEST_TIMEOUT_SECONDS"
    )

    # Live execution. There is deliberately no paper-trading mode.
    live_trading_enabled: bool = Field(default=False, alias="LIVE_TRADING_ENABLED")
    poll_interval_seconds: int = Field(default=60, alias="POLL_INTERVAL_SECONDS")
    symbols: tuple[str, ...] = Field(
        default=("BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD"), alias="SYMBOLS"
    )
    max_open_positions: int = Field(default=4, alias="MAX_OPEN_POSITIONS")

    # Momentum strategy: intentionally a little more aggressive than the reference bot.
    fast_ema_period: int = Field(default=6, alias="FAST_EMA_PERIOD")
    slow_ema_period: int = Field(default=18, alias="SLOW_EMA_PERIOD")
    rsi_period: int = Field(default=14, alias="RSI_PERIOD")
    min_history: int = Field(default=40, alias="MIN_HISTORY")
    rsi_buy_min: float = Field(default=42.0, alias="RSI_BUY_MIN")
    rsi_buy_max: float = Field(default=62.0, alias="RSI_BUY_MAX")
    rsi_sell_min: float = Field(default=43.0, alias="RSI_SELL_MIN")
    ema_separation_pct: float = Field(default=0.035, alias="EMA_SEPARATION_PCT")
    confirm_ticks: int = Field(default=2, alias="CONFIRM_TICKS")
    min_hold_cycles: int = Field(default=2, alias="MIN_HOLD_CYCLES")
    min_profit_pct: float = Field(default=0.20, alias="MIN_PROFIT_PCT")
    stop_loss_pct: float = Field(default=2.25, alias="STOP_LOSS_PCT")
    take_profit_pct: float = Field(default=3.50, alias="TAKE_PROFIT_PCT")
    loss_cooldown_cycles: int = Field(default=2, alias="LOSS_COOLDOWN_CYCLES")

    # Risk / execution. Values are fractions except strategy thresholds.
    max_position_pct: float = Field(default=0.18, alias="MAX_POSITION_PCT")
    base_position_pct: float = Field(default=0.055, alias="BASE_POSITION_PCT")
    min_order_usd: float = Field(default=10.0, alias="MIN_ORDER_USD")
    max_drawdown_pct: float = Field(default=0.15, alias="MAX_DRAWDOWN_PCT")
    commission_rate: float = Field(default=0.001, alias="COMMISSION_RATE")
    reserve_pct: float = Field(default=0.03, alias="RESERVE_PCT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("symbols", mode="before")
    @classmethod
    def split_symbols(cls, value: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(
                symbol.strip().upper() for symbol in value.split(",") if symbol.strip()
            )
        return tuple(str(symbol).strip().upper() for symbol in value)

    def validate_live_execution(self) -> None:
        """Fail closed before an order-capable process is started."""
        if not self.live_trading_enabled:
            raise RuntimeError(
                "Set LIVE_TRADING_ENABLED=true before starting the live bot."
            )
        if not self.api_key or not self.api_secret:
            raise RuntimeError("API_KEY and API_SECRET must be set for live execution.")

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        return (
            Path(self.db_url.removeprefix(prefix))
            if self.db_url.startswith(prefix)
            else None
        )


config = Config()
