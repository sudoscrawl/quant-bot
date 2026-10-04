"""Live execution loop. Run with ``python -m bot.main`` after configuration."""

import json
import logging
import signal
import time
import uuid
from collections.abc import Mapping

from bot.config import config
from bot.data.db.engine import Base, engine
from bot.helpers.client import free_balance, portfolio_value_usd, ticker_data
from bot.risk import RiskManager
from bot.services.db_queries import DB
from bot.services.roostoo import RoostooClient
from bot.strategy import MomentumStrategy

logger = logging.getLogger(__name__)


def _exchange_rules(info: Mapping[str, object], pair: str) -> tuple[int, float]:
    rules = (info.get("TradePairs", {}) if isinstance(info, Mapping) else {}).get(
        pair, {}
    )
    if not isinstance(rules, Mapping):
        return 6, 0.0
    return int(rules.get("AmountPrecision", 6)), float(rules.get("MiniOrder", 0.0))


def _order_detail(order: Mapping[str, object]) -> Mapping[str, object]:
    detail = order.get("OrderDetail", order)
    return detail if isinstance(detail, Mapping) else {}


def _filled_price(order: Mapping[str, object], fallback: float) -> float:
    detail = _order_detail(order)
    return float(detail.get("FilledAverPrice") or detail.get("Price") or fallback)


def _signal_strength(indicators: Mapping[str, object]) -> float:
    separation = abs(float(indicators.get("ema_sep_pct", 0.0)))
    rsi_value = float(indicators.get("rsi", 50.0))
    return min(
        1.0, separation / 0.12 * 0.6 + max(0.0, 1 - abs(rsi_value - 52) / 20) * 0.4
    )


def _volatility_pct(pair: str) -> float:
    prices = [float(row["price"]) for row in DB.get_prices(pair, limit=12)]
    if len(prices) < 3:
        return 1.0
    returns = [
        abs(prices[index] / prices[index + 1] - 1) * 100
        for index in range(len(prices) - 1)
        if prices[index + 1] > 0
    ]
    return sum(returns) / len(returns) if returns else 1.0


def _load_positions(
    strategy: MomentumStrategy, balance: Mapping[str, object]
) -> dict[str, float]:
    saved = json.loads(DB.get_state("bot_positions", "{}") or "{}")
    active: dict[str, float] = {}
    for pair, entry_price in saved.items():
        asset = pair.split("/", 1)[0]
        if free_balance(dict(balance), asset) > 0:
            active[pair] = float(entry_price)
            strategy.notify_bought(pair, float(entry_price))
    return active


def _save_positions(positions: Mapping[str, float]) -> None:
    DB.set_state("bot_positions", json.dumps(positions, sort_keys=True))


def _accepted(order: Mapping[str, object], pair: str, side: str) -> bool:
    if order.get("Success", True):
        return True
    logger.error("LIVE %s %s rejected: %s", side, pair, order.get("ErrMsg", order))
    return False


def run() -> None:
    config.validate_live_execution()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    Base.metadata.create_all(engine)
    client, strategy = RoostooClient(), MomentumStrategy(config)
    running, session_id = True, str(uuid.uuid4())

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        exchange_info = client.get_exchange_info()
        initial_balance = client.get_balance()
        initial_tickers = ticker_data(client)
        risk = RiskManager(
            portfolio_value_usd(initial_balance, initial_tickers), config
        )
        positions = _load_positions(strategy, initial_balance)
        for pair in config.symbols:
            strategy.restore(
                pair,
                [
                    float(row["price"])
                    for row in DB.get_prices(pair, config.min_history)
                ],
            )

        while running:
            try:
                tickers, balance = ticker_data(client), client.get_balance()
                selected = {
                    pair: tickers[pair] for pair in config.symbols if pair in tickers
                }
                DB.insert_prices(selected)
                equity = portfolio_value_usd(balance, tickers)
                risk.update_equity(equity)
                DB.insert_equity(equity)
                available_usd = free_balance(balance, "USD")

                for pair, ticker in selected.items():
                    price = float(ticker.get("LastPrice", 0.0))
                    action = strategy.update(pair, price)
                    indicators = strategy.indicators(pair)
                    if action == "SELL" and pair in positions:
                        asset = pair.split("/", 1)[0]
                        quantity = free_balance(balance, asset)
                        precision, _ = _exchange_rules(exchange_info, pair)
                        factor = 10**precision
                        quantity = int(quantity * factor) / factor
                        if quantity > 0:
                            result = client.place_order(pair, "SELL", quantity)
                            if _accepted(result, pair, "SELL"):
                                fill = _filled_price(result, price)
                                DB.insert_trade_from_order(result, "LIVE", session_id)
                                strategy.notify_sold(pair, fill < positions[pair])
                                positions.pop(pair, None)
                                _save_positions(positions)
                                logger.info(
                                    "LIVE SELL %s qty=%s @ %.8f", pair, quantity, fill
                                )
                    elif (
                        action == "BUY"
                        and pair not in positions
                        and len(positions) < config.max_open_positions
                        and not risk.halted
                    ):
                        precision, exchange_minimum = _exchange_rules(
                            exchange_info, pair
                        )
                        budget = risk.size_usd(
                            available_usd,
                            equity,
                            _signal_strength(indicators),
                            _volatility_pct(pair),
                        )
                        quantity = risk.quantity(budget, price, precision)
                        if quantity > 0 and quantity * price >= max(
                            config.min_order_usd, exchange_minimum
                        ):
                            result = client.place_order(pair, "BUY", quantity)
                            if _accepted(result, pair, "BUY"):
                                fill = _filled_price(result, price)
                                DB.insert_trade_from_order(result, "LIVE", session_id)
                                strategy.notify_bought(pair, fill)
                                positions[pair] = fill
                                _save_positions(positions)
                                logger.info(
                                    "LIVE BUY %s qty=%s @ %.8f", pair, quantity, fill
                                )
                    elif action != "HOLD":
                        logger.info(
                            "%s signal ignored: positions=%d halted=%s",
                            pair,
                            len(positions),
                            risk.halted,
                        )
                time.sleep(config.poll_interval_seconds)
            except Exception:
                logger.exception("cycle failed; retrying after poll interval")
                time.sleep(config.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    run()
