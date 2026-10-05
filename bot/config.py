"""The application's only configuration surface."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    # Exchange / persistence
    api_key: str = Field(default="", alias="API_KEY")
    api_secret: str = Field(default="", alias="API_SECRET")
    db_url: str = Field(
        default="postgresql+psycopg://quant_bot:quant_bot@localhost:5432/quant_bot",
        alias="DB_URL",
    )
    exchange_base_url: str = Field(
        default="https://mock-api.roostoo.com", alias="EXCHANGE_BASE_URL"
    )
    request_timeout_seconds: float = Field(
        default=10.0, alias="REQUEST_TIMEOUT_SECONDS"
    )
    log_file: str = Field(default="logs/quant-bot.log", alias="LOG_FILE")

    # Live execution. There is deliberately no paper-trading mode.
    live_trading_enabled: bool = Field(default=False, alias="LIVE_TRADING_ENABLED")
    poll_interval_seconds: int = Field(default=120, alias="POLL_INTERVAL_SECONDS")
    symbols: tuple[str, ...] = Field(
        default=(
            "BTC/USD",
            "ETH/USD",
            "SOL/USD",
            "BNB/USD",
            "DOGE/USD",
            "XRP/USD",
            "ADA/USD",
            "AVAX/USD",
            "SUI/USD",
            "NEAR/USD",
            "LINK/USD",
            "DOT/USD",
            "LTC/USD",
            "UNI/USD",
            "APT/USD",
            "AAVE/USD",
            "PEPE/USD",
            "SHIB/USD",
            "TON/USD",
            "TRX/USD",
        ),
        alias="SYMBOLS",
    )
    max_open_positions: int = Field(default=10, alias="MAX_OPEN_POSITIONS")
    min_symbols_tracked: int = Field(default=15, alias="MIN_SYMBOLS_TRACKED")
    track_all_coins: bool = Field(default=True, alias="TRACK_ALL_COINS")
    auto_select_top_symbols: bool = Field(default=True, alias="AUTO_SELECT_TOP_SYMBOLS")

    # Momentum strategy: conservative v2 parameters aligned with trade-bot reference.
    # Tick speed is 2 min, so at 25 ticks warmup takes ~50 minutes.
    fast_ema_period: int = Field(default=8, alias="FAST_EMA_PERIOD")
    slow_ema_period: int = Field(default=21, alias="SLOW_EMA_PERIOD")
    rsi_period: int = Field(default=14, alias="RSI_PERIOD")
    min_history: int = Field(default=25, alias="MIN_HISTORY")
    rsi_buy_min: float = Field(default=45.0, alias="RSI_BUY_MIN")
    rsi_buy_max: float = Field(default=58.0, alias="RSI_BUY_MAX")
    rsi_sell_min: float = Field(default=45.0, alias="RSI_SELL_MIN")
    ema_separation_pct: float = Field(default=0.05, alias="EMA_SEPARATION_PCT")
    confirm_ticks: int = Field(default=3, alias="CONFIRM_TICKS")
    min_hold_cycles: int = Field(default=4, alias="MIN_HOLD_CYCLES")
    stagnant_exit_cycles: int = Field(default=10, alias="STAGNANT_EXIT_CYCLES")
    min_profit_pct: float = Field(default=0.25, alias="MIN_PROFIT_PCT")
    stop_loss_pct: float = Field(default=2.0, alias="STOP_LOSS_PCT")
    take_profit_pct: float = Field(default=3.0, alias="TAKE_PROFIT_PCT")
    loss_cooldown_cycles: int = Field(default=3, alias="LOSS_COOLDOWN_CYCLES")

    # Risk / execution. Half-Kelly sizing with tighter drawdown guard (trade-bot reference).
    max_position_pct: float = Field(default=0.10, alias="MAX_POSITION_PCT")
    base_position_pct: float = Field(default=0.034, alias="BASE_POSITION_PCT")
    min_order_usd: float = Field(default=10.0, alias="MIN_ORDER_USD")
    min_buy_score: float = Field(default=0.8, alias="MIN_BUY_SCORE")
    max_drawdown_pct: float = Field(default=0.12, alias="MAX_DRAWDOWN_PCT")
    commission_rate: float = Field(default=0.001, alias="COMMISSION_RATE")
    reserve_pct: float = Field(default=0.05, alias="RESERVE_PCT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("db_url", mode="before")
    @classmethod
    def use_psycopg_driver(cls, value: str) -> str:
        """Accept standard PostgreSQL/Neon URLs while selecting psycopg explicitly."""
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

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


config = Config()
