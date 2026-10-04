from bot.config import config
from bot.risk import RiskManager
from bot.strategy import MomentumStrategy


def settings(**updates):
    return config.model_copy(update=updates)


def test_strategy_sells_at_hard_stop() -> None:
    strategy = MomentumStrategy(settings(min_history=5, stop_loss_pct=2.0))
    for price in (100, 101, 102, 103, 104):
        strategy.update("BTC/USD", price)
    strategy.notify_bought("BTC/USD", 100)

    assert strategy.update("BTC/USD", 97) == "SELL"


def test_restored_prices_do_not_emit_an_immediate_signal() -> None:
    strategy = MomentumStrategy(settings(min_history=5))
    strategy.restore("BTC/USD", [100, 101, 102, 103, 104])

    assert strategy.update("BTC/USD", 105) == "HOLD"


def test_risk_halts_and_caps_position_size() -> None:
    risk = RiskManager(1_000, settings(max_position_pct=0.18, max_drawdown_pct=0.15))
    assert risk.size_usd(1_000, 1_000, 1.0, 0.0) <= 180

    risk.update_equity(850)
    assert risk.halted
    assert risk.size_usd(1_000, 850, 1.0, 0.0) == 0.0
