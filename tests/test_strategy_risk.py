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

<<<<<<< HEAD
    # Hard crash — large enough to push fast EMA below slow
    strategy.update("BTC/USD", 90.0)  # tick 1: ticks_below_slow=1, HOLD
    result = strategy.update("BTC/USD", 90.0)  # tick 2: ticks_below_slow=2 → SELL
    assert result == "SELL"


def test_strategy_soft_stop_does_not_fire_without_ema_reversal() -> None:
    """Soft stop should NOT fire if price dips but fast EMA stays above slow."""
    cfg = settings(stop_loss_pct=2.0, stagnant_exit_cycles=100)
    strategy = MomentumStrategy(cfg)
    # Build strong uptrend so fast EMA is well above slow
    for i in range(30):
        strategy.update("BTC/USD", 100 + i * 1.0)
    strategy.notify_bought("BTC/USD", 129.0)

    # Single tick dip — EMA separation still large, ticks_below_slow won't reach 2
    result = strategy.update("BTC/USD", 126.0)  # ~-2.3% but EMA still bullish
    assert result == "HOLD"
=======
    assert strategy.update("BTC/USD", 97) == "SELL"
>>>>>>> parent of 7d70ca2 (feat: new strategy implementations)


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
