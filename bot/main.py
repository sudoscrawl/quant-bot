"""Live execution loop. Run with ``python -m bot.main`` after configuration."""

import json
import logging
import signal
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.config import config
from bot.data.db.migrations import assert_schema_current
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


def _volatility_pct(pair: str, prices: list[float] | None = None) -> float:
    if prices is None:
        db_rows = DB.get_prices(pair, limit=12)
        prices = [float(row["price"]) for row in db_rows]
    if len(prices) < 3:
        return 1.0
    recent = prices[-12:]
    returns = [
        abs(recent[index] / recent[index - 1] - 1) * 100
        for index in range(1, len(recent))
        if recent[index - 1] > 0
    ]
    return sum(returns) / len(returns) if returns else 1.0


<<<<<<< HEAD
def _detect_market_state(
    tickers: Mapping[str, Mapping[str, Any]],
    strategy: MomentumStrategy,
    pairs: list[str],
) -> dict[str, Any]:
    """
    Classify the current market regime and return adaptive strategy overrides.

    States
    ------
    RECOVERY   — broadly oversold but bouncing upward; catch early entries
    TRENDING   — healthy uptrend with RSI in range; standard settings
    RANGING    — choppy, no clear direction; demand cleaner crossovers
    OVERBOUGHT — RSI elevated across the board; risk of reversal
    DOWNTURN   — majority falling; buy only the very strongest signals

    Returns a dict with keys ``state``, ``avg_rsi``, ``ema_up_pct``,
    ``rising_pct``, and ``params`` (overrides to apply to the strategy config).
    Returns {} when fewer than 80 % of pairs are warmed up.
    """
    warmed = [p for p in pairs if not strategy.indicators(p).get("warming_up", False)]
    if len(warmed) < max(1, len(pairs) * 0.8):
        return {}

    rsi_vals = [float(strategy.indicators(p).get("rsi", 50.0)) for p in warmed]
    avg_rsi = sum(rsi_vals) / len(rsi_vals)

    ema_up = sum(
        1
        for p in warmed
        if float(strategy.indicators(p).get("fast_ema", 0.0))
        > float(strategy.indicators(p).get("slow_ema", 1.0))
    )
    ema_up_pct = ema_up / len(warmed)

    changes = [float(tickers.get(p, {}).get("Change", 0.0)) * 100 for p in warmed]
    rising_pct = sum(1 for c in changes if c > 0) / len(changes)

    if avg_rsi < 38 and rising_pct > 0.4:
        state = "RECOVERY"
    elif avg_rsi < 42 and rising_pct < 0.4:
        state = "DOWNTURN"
    elif avg_rsi > 65 and ema_up_pct > 0.6:
        state = "OVERBOUGHT"
    elif 42 <= avg_rsi <= 62 and ema_up_pct > 0.45:
        state = "TRENDING"
    else:
        state = "RANGING"

    params: dict[str, dict[str, float | int]] = {
        "RECOVERY": {
            "rsi_buy_min": 38.0,
            "rsi_buy_max": 62.0,
            "ema_separation_pct": 0.02,
            "confirm_ticks": 2,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 2.5,
            "min_profit_pct": 0.20,
            "min_hold_cycles": 4,
            "stagnant_exit_cycles": 10,
        },
        "TRENDING": {
            "rsi_buy_min": 44.0,
            "rsi_buy_max": 60.0,
            "ema_separation_pct": 0.04,
            "confirm_ticks": 3,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 3.5,
            "min_profit_pct": 0.25,
            "min_hold_cycles": 4,
            "stagnant_exit_cycles": 12,
        },
        "RANGING": {
            "rsi_buy_min": 45.0,
            "rsi_buy_max": 56.0,
            "ema_separation_pct": 0.05,
            "confirm_ticks": 3,
            "stop_loss_pct": 1.8,
            "take_profit_pct": 2.0,
            "min_profit_pct": 0.20,
            "min_hold_cycles": 3,
            "stagnant_exit_cycles": 8,
        },
        "OVERBOUGHT": {
            "rsi_buy_min": 48.0,
            "rsi_buy_max": 56.0,
            "ema_separation_pct": 0.06,
            "confirm_ticks": 3,
            "stop_loss_pct": 1.5,
            "take_profit_pct": 2.0,
            "min_profit_pct": 0.15,
            "min_hold_cycles": 3,
            "stagnant_exit_cycles": 6,
        },
        "DOWNTURN": {
            "rsi_buy_min": 48.0,
            "rsi_buy_max": 58.0,
            "ema_separation_pct": 0.06,
            "confirm_ticks": 3,
            "stop_loss_pct": 1.5,
            "take_profit_pct": 2.0,
            "min_profit_pct": 0.15,
            "min_hold_cycles": 3,
            "stagnant_exit_cycles": 6,
        },
    }

    return {
        "state": state,
        "avg_rsi": round(avg_rsi, 1),
        "ema_up_pct": round(ema_up_pct * 100, 1),
        "rising_pct": round(rising_pct * 100, 1),
        "params": params[state],
    }


def _apply_market_state(
    strategy: MomentumStrategy,
    market: dict[str, Any],
    last_state: str | None,
) -> str | None:
    """
    Apply adaptive parameter overrides from _detect_market_state to the live
    strategy config object and log when the regime changes.

    Returns the new state string (or last_state if market is empty).
    """
    if not market:
        return last_state

    p = market["params"]
    cfg = strategy.settings
    cfg.rsi_buy_min = p["rsi_buy_min"]
    cfg.rsi_buy_max = p["rsi_buy_max"]
    cfg.ema_separation_pct = p["ema_separation_pct"]
    cfg.confirm_ticks = int(p["confirm_ticks"])
    cfg.stop_loss_pct = p["stop_loss_pct"]
    cfg.take_profit_pct = p["take_profit_pct"]
    cfg.min_profit_pct = p["min_profit_pct"]
    cfg.min_hold_cycles = int(p["min_hold_cycles"])
    cfg.stagnant_exit_cycles = int(p["stagnant_exit_cycles"])

    new_state = market["state"]
    if new_state != last_state:
        logger.info(
            "Market regime: %s → %s  (avg_rsi=%.1f  ema_up=%.0f%%  rising=%.0f%%)",
            last_state,
            new_state,
            market["avg_rsi"],
            market["ema_up_pct"],
            market["rising_pct"],
        )
        logger.info(
            "Adapted params: rsi=%s–%s  sep=%.2f%%  confirm=%d  stop=%.1f%%  "
            "target=%.1f%%  hold≥%d  stagnant≥%d",
            p["rsi_buy_min"],
            p["rsi_buy_max"],
            p["ema_separation_pct"],
            p["confirm_ticks"],
            p["stop_loss_pct"],
            p["take_profit_pct"],
            p["min_hold_cycles"],
            p["stagnant_exit_cycles"],
        )
    return new_state


=======
>>>>>>> parent of 7d70ca2 (feat: new strategy implementations)
def _select_tracked_symbols(
    exchange_info: Mapping[str, Any],
    tickers: Mapping[str, Mapping[str, Any]],
    min_count: int = 15,
) -> list[str]:
    """Select active trading pairs to monitor and trade across the exchange."""
    valid_pairs = set(tickers.keys())
    if exchange_info and isinstance(exchange_info, Mapping):
        pairs_info = exchange_info.get("TradePairs", {})
        if isinstance(pairs_info, Mapping) and pairs_info:
            valid_pairs = (
                valid_pairs.intersection(set(pairs_info.keys())) or valid_pairs
            )

    if getattr(config, "track_all_coins", True):
        # Track every single active coin listed on the exchange (~88 pairs)
        return sorted(
            [
                p
                for p in valid_pairs
                if float(tickers.get(p, {}).get("LastPrice", 0.0)) > 0
            ],
            key=lambda p: float(tickers.get(p, {}).get("UnitTradeValue", 0.0)),
            reverse=True,
        )

    # 1. Start with configured symbols that are actively trading
    selected: list[str] = [
        s
        for s in config.symbols
        if s in valid_pairs and float(tickers.get(s, {}).get("LastPrice", 0.0)) > 0
    ]

    # 2. Ensure we have at least `min_count` coins by adding highest USD-volume pairs
    target_count = max(min_count, len(config.symbols))
    if len(selected) < target_count or getattr(config, "auto_select_top_symbols", True):
        sorted_pairs = sorted(
            valid_pairs,
            key=lambda p: float(tickers.get(p, {}).get("UnitTradeValue", 0.0)),
            reverse=True,
        )
        for p in sorted_pairs:
            if (
                p not in selected
                and float(tickers.get(p, {}).get("LastPrice", 0.0)) > 0
            ):
                selected.append(p)
            if len(selected) >= target_count:
                break

    return selected


def _score_opportunity(
    pair: str,
    indicators: Mapping[str, Any],
    ticker: Mapping[str, Any],
    volatility: float,
) -> float:
    """Calculate a money-making opportunity score (higher = higher profit potential)."""
    if indicators.get("warming_up", False):
        return 0.0

    ema_sep = max(0.0, float(indicators.get("ema_sep_pct", 0.0)))
    rsi_val = float(indicators.get("rsi", 50.0))
    change_24h = float(ticker.get("Change", 0.0)) * 100.0
    unit_trade_val = float(ticker.get("UnitTradeValue", 0.0))
    price = float(ticker.get("LastPrice", 0.0))
    bid = float(ticker.get("MaxBid", 0.0))
    ask = float(ticker.get("MinAsk", 0.0))

    if price <= 0:
        return 0.0

    # 1. EMA trend strength (positive separation is rewarded)
    trend_factor = max(0.1, 1.0 + ema_sep * 10.0)

    # 2. RSI momentum positioning (optimal zone: 45 to 65)
    rsi_factor = max(0.1, 1.0 - abs(rsi_val - 55.0) / 30.0)

    # 3. 24h momentum (rewards positive trend)
    change_factor = max(0.2, 1.0 + (change_24h / 15.0))

    # 4. Volume / Liquidity factor
    volume_factor = (
        min(2.5, max(0.5, (unit_trade_val / 20_000.0) ** 0.25))
        if unit_trade_val > 0
        else 0.5
    )

    # 5. Volatility / Upside potential
    vol_factor = min(2.0, max(0.6, volatility / 1.2))

    # 6. Spread penalty (penalize wide bid-ask spreads)
    spread_bps = (ask - bid) / price * 10_000 if price > 0 else 0.0
    spread_factor = max(0.3, 1.0 - (spread_bps / 150.0))

    score = (
        trend_factor
        * rsi_factor
        * change_factor
        * volume_factor
        * vol_factor
        * spread_factor
    )
    return round(max(0.0, score), 4)


def _load_positions(
    strategy: MomentumStrategy,
    balance: Mapping[str, object],
    tickers: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, float]:
    """Load active positions from DB and adopt any pre-existing wallet holdings."""
    saved = json.loads(DB.get_state("bot_positions", "{}") or "{}")
    active: dict[str, float] = {}

    # 1. Restore previously tracked bot positions
    for pair, entry_price in saved.items():
        asset = pair.split("/", 1)[0]
        if free_balance(dict(balance), asset) > 0:
            active[pair] = float(entry_price)
            strategy.notify_bought(pair, float(entry_price))

    # 2. Adopt any pre-existing non-USD coins in the wallet
    wallet = balance.get("SpotWallet") or balance.get("Wallet") or {}
    if isinstance(wallet, Mapping) and tickers:
        for asset, amounts in wallet.items():
            if asset.upper() == "USD":
                continue
            pair = f"{asset.upper()}/USD"
            if pair in active:
                continue
            free_qty = (
                float(amounts.get("Free", 0.0)) if isinstance(amounts, Mapping) else 0.0
            )
            if free_qty > 0 and pair in tickers:
                current_price = float(tickers[pair].get("LastPrice", 0.0))
                if current_price > 0:
                    active[pair] = current_price
                    strategy.notify_bought(pair, current_price)
                    logger.info(
                        "Adopted pre-existing wallet holding %s (qty=%s) @ $%.4f",
                        pair,
                        free_qty,
                        current_price,
                    )

    return active


def _save_positions(positions: Mapping[str, float]) -> None:
    DB.set_state("bot_positions", json.dumps(positions, sort_keys=True))


def _save_state(
    session_id: str,
    risk: RiskManager,
    equity: float,
    available_usd: float,
    positions: Mapping[str, float],
    tracked_symbols: list[str],
    candidates: list[dict[str, Any]],
    cycle_summary: Mapping[str, Any],
) -> None:
    """Persist comprehensive operational bot state to the database."""
    now_iso = datetime.now(UTC).isoformat()
    peak_eq = max(risk.peak_equity, equity)
    drawdown_pct = round((peak_eq - equity) / peak_eq * 100, 2) if peak_eq > 0 else 0.0

    bot_state = {
        "status": "HALTED" if risk.halted else "RUNNING",
        "session_id": session_id,
        "last_cycle_timestamp": now_iso,
        "equity_usd": round(equity, 4),
        "peak_equity_usd": round(peak_eq, 4),
        "drawdown_pct": drawdown_pct,
        "risk_halted": risk.halted,
        "available_usd": round(available_usd, 4),
        "tracked_symbols_count": len(tracked_symbols),
        "open_positions_count": len(positions),
        "open_positions": dict(positions),
    }

    DB.set_multiple_states(
        {
            "bot_positions": json.dumps(positions, sort_keys=True),
            "bot_state": json.dumps(bot_state, sort_keys=True),
            "tracked_symbols": json.dumps(tracked_symbols),
            "top_candidates": json.dumps(candidates[:10]),
            "last_cycle_summary": json.dumps(dict(cycle_summary), sort_keys=True),
        }
    )


def _accepted(order: Mapping[str, object], pair: str, side: str) -> bool:
    if order.get("Success", True):
        return True
    logger.error("LIVE %s %s rejected: %s", side, pair, order.get("ErrMsg", order))
    return False


def _configure_logging() -> None:
    log_path = Path(config.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=formatter,
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path)],
        force=True,
    )


def run() -> None:
    config.validate_live_execution()
    _configure_logging()
    assert_schema_current()
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
        initial_tracked = _select_tracked_symbols(
            exchange_info, initial_tickers, config.min_symbols_tracked
        )

        risk = RiskManager(
            portfolio_value_usd(initial_balance, initial_tickers), config
        )
        positions = _load_positions(strategy, initial_balance, initial_tickers)

        # Bulk restore history for all tracked symbols from DB in a single query
        history_bulk = DB.get_recent_prices_bulk(initial_tracked, config.min_history)
        for pair in initial_tracked:
            pair_prices = [float(row["price"]) for row in history_bulk.get(pair, [])]
            strategy.restore(pair, pair_prices)

        logger.info(
            "Bot initialized with %d tracked coins (min required: %d). Open positions: %d",
            len(initial_tracked),
            config.min_symbols_tracked,
            len(positions),
        )

        while running:
            try:
                tickers, balance = ticker_data(client), client.get_balance()
                tracked_symbols = _select_tracked_symbols(
                    exchange_info, tickers, config.min_symbols_tracked
                )
                selected = {
                    pair: tickers[pair] for pair in tracked_symbols if pair in tickers
                }
                DB.insert_prices(selected)
                equity = portfolio_value_usd(balance, tickers)
                risk.update_equity(equity)
                DB.insert_equity(equity)
                available_usd = free_balance(balance, "USD")

                cycle_sells: list[str] = []
                cycle_buys: list[str] = []
                candidates: list[dict[str, Any]] = []

                # 1. Process SELL signals for currently held positions
                for pair in list(positions.keys()):
                    if pair not in selected:
                        continue
                    ticker = selected[pair]
                    price = float(ticker.get("LastPrice", 0.0))
<<<<<<< HEAD
                    entry_price = positions[pair]

                    # ── Trailing stop ─────────────────────────────────────
                    # Once PnL ≥ 1.5%, move the effective entry to breakeven
                    # (entry + round-trip commission) so the stop-loss now
                    # protects against any net loss on the trade.
                    if price > 0 and entry_price > 0:
                        current_pnl_pct = (price - entry_price) / entry_price * 100
                        breakeven = entry_price * (1 + 2 * config.commission_rate)
                        st = strategy._state(pair)
                        if current_pnl_pct >= 1.5 and st.entry_price < breakeven:
                            logger.info(
                                "TRAILING STOP %s: pnl=%.2f%% — entry moved "
                                "%.4f → %.4f (stop %.4f → %.4f)",
                                pair,
                                current_pnl_pct,
                                st.entry_price,
                                breakeven,
                                st.entry_price * (1 - config.stop_loss_pct / 100),
                                breakeven * (1 - config.stop_loss_pct / 100),
                            )
                            st.entry_price = breakeven
                            positions[pair] = breakeven

                    # ── Proactive exit ────────────────────────────────────
                    # Exit early (before stop-loss fires) when the position is
                    # quietly losing ground with momentum clearly reversed:
                    #   - PnL between -0.8% and -1.9% (stop not yet triggered)
                    #   - Fast EMA crossed below slow EMA
                    #   - RSI < 40 (momentum weakening)
                    #   - Held for ≥ 3 cycles (not a brand-new entry)
                    ind = strategy.indicators(pair)
                    if (
                        not ind.get("warming_up", False)
                        and price > 0
                        and entry_price > 0
                    ):
                        pnl_pct = (price - entry_price) / entry_price * 100
                        fast_ema = float(ind.get("fast_ema", 0.0))
                        slow_ema = float(ind.get("slow_ema", 1.0))
                        current_rsi = float(ind.get("rsi", 50.0))
                        hold_cycles = int(ind.get("hold_cycles", 0))

                        if (
                            -1.9 <= pnl_pct <= -0.8
                            and fast_ema < slow_ema
                            and current_rsi < 45
                            and hold_cycles >= 3
                        ):
                            asset = pair.split("/", 1)[0]
                            quantity = free_balance(balance, asset)
                            precision, _ = _exchange_rules(exchange_info, pair)
                            factor = 10**precision
                            quantity = int(quantity * factor) / factor
                            if quantity > 0:
                                result = client.place_order(pair, "SELL", quantity)
                                if _accepted(result, pair, "SELL"):
                                    fill = _filled_price(result, price)
                                    DB.insert_trade_from_order(
                                        result,
                                        mode="LIVE",
                                        session_id=session_id,
                                        fallback_pair=pair,
                                        fallback_side="SELL",
                                        fallback_price=fill,
                                        fallback_qty=quantity,
                                    )
                                    strategy.notify_sold(pair, was_loss=True)
                                    positions.pop(pair, None)
                                    _save_positions(positions)
                                    available_usd += quantity * fill
                                    cycle_sells.append(pair)
                                    logger.info(
                                        "PROACTIVE EXIT %s qty=%s @ %.8f "
                                        "pnl=%.2f%% rsi=%.1f",
                                        pair,
                                        quantity,
                                        fill,
                                        pnl_pct,
                                        current_rsi,
                                    )
                            continue  # skip normal SELL check for this pair

                    # ── Normal strategy SELL ──────────────────────────────
=======
>>>>>>> parent of 7d70ca2 (feat: new strategy implementations)
                    action = strategy.update(pair, price)
                    if action == "SELL":
                        asset = pair.split("/", 1)[0]
                        quantity = free_balance(balance, asset)
                        precision, _ = _exchange_rules(exchange_info, pair)
                        factor = 10**precision
                        quantity = int(quantity * factor) / factor
                        if quantity > 0:
                            result = client.place_order(pair, "SELL", quantity)
                            if _accepted(result, pair, "SELL"):
                                fill = _filled_price(result, price)
                                DB.insert_trade_from_order(
                                    result,
                                    mode="LIVE",
                                    session_id=session_id,
                                    fallback_pair=pair,
                                    fallback_side="SELL",
                                    fallback_price=fill,
                                    fallback_qty=quantity,
                                )
                                strategy.notify_sold(pair, fill < positions[pair])
                                positions.pop(pair, None)
                                _save_positions(positions)
                                cycle_sells.append(pair)
                                logger.info(
                                    "LIVE SELL %s qty=%s @ %.8f", pair, quantity, fill
                                )

                # 2. Evaluate BUY opportunities across all tracked coins not in positions
                for pair, ticker in selected.items():
                    if pair in positions:
                        continue
                    price = float(ticker.get("LastPrice", 0.0))
                    action = strategy.update(pair, price)
                    indicators = strategy.indicators(pair)
                    vol = _volatility_pct(pair, list(strategy._state(pair).prices))
                    score = _score_opportunity(pair, indicators, ticker, vol)

                    if action == "BUY":
                        candidates.append(
                            {
                                "pair": pair,
                                "score": score,
                                "price": price,
                                "indicators": indicators,
                                "volatility": vol,
                                "24h_change": float(ticker.get("Change", 0.0)),
                                "volume_usd": float(ticker.get("UnitTradeValue", 0.0)),
                            }
                        )

                # Sort candidates by money-making opportunity score (highest first)
                candidates.sort(key=lambda c: c["score"], reverse=True)

                # 3. Trade the most profitable / highest-scoring candidate coins
                available_slots = max(0, config.max_open_positions - len(positions))
<<<<<<< HEAD

                if not risk.halted and candidates:
                    for cand in candidates:
                        if (
                            available_slots <= 0
                            or len(positions) >= config.max_open_positions
                        ):
                            # ── Position rotation ─────────────────────────
                            # Slots are full. Rotate out the worst loser if:
                            #   - Its loss is between -0.5% and -1.8%
                            #     (stop-loss hasn't caught it yet)
                            #   - The new signal is meaningfully strong
                            # Only rotate if the incoming signal is stronger than
                            # the min_buy_score threshold — same bar as normal buys
                            if cand["score"] < config.min_buy_score:
                                continue  # new signal not strong enough to warrant rotation

                            worst_pair: str | None = None
                            worst_pnl = 0.0  # only consider negatives

                            for held_pair, held_entry in positions.items():
                                if held_pair not in selected:
                                    continue
                                held_price = float(
                                    selected[held_pair].get("LastPrice", 0.0)
                                )
                                if held_price <= 0 or held_entry <= 0:
                                    continue
                                held_pnl = (held_price - held_entry) / held_entry * 100
                                if -1.8 <= held_pnl <= -0.5 and held_pnl < worst_pnl:
                                    worst_pnl = held_pnl
                                    worst_pair = held_pair

                            if worst_pair is None:
                                continue  # no rotation candidate; skip this signal

                            # Sell the loser                            worst_asset = worst_pair.split("/", 1)[0]
                            worst_qty = free_balance(balance, worst_asset)
                            worst_price_now = float(
                                selected[worst_pair].get("LastPrice", 0.0)
                            )
                            precision, _ = _exchange_rules(exchange_info, worst_pair)
                            factor = 10**precision
                            worst_qty = int(worst_qty * factor) / factor
                            if worst_qty <= 0:
                                continue

                            rot_result = client.place_order(
                                worst_pair, "SELL", worst_qty
                            )
                            if not _accepted(rot_result, worst_pair, "SELL"):
                                continue

                            rot_fill = _filled_price(rot_result, worst_price_now)
                            DB.insert_trade_from_order(
                                rot_result,
                                mode="LIVE",
                                session_id=session_id,
                                fallback_pair=worst_pair,
                                fallback_side="SELL",
                                fallback_price=rot_fill,
                                fallback_qty=worst_qty,
                            )
                            strategy.notify_sold(worst_pair, was_loss=True)
                            positions.pop(worst_pair, None)
                            _save_positions(positions)
                            available_usd += worst_qty * rot_fill
                            cycle_sells.append(worst_pair)
                            available_slots += 1
                            logger.info(
                                "ROTATION SELL %s (pnl=%.2f%%) to make room for %s "
                                "(score=%.4f)",
                                worst_pair,
                                worst_pnl,
                                cand["pair"],
                                cand["score"],
                            )

                        if (
                            available_slots <= 0
                            or len(positions) >= config.max_open_positions
                        ):
                            break

=======
                if available_slots > 0 and not risk.halted and candidates:
                    logger.info(
                        "Found %d candidate coins. Top picks: %s",
                        len(candidates),
                        ", ".join(
                            f"{c['pair']} (score: {c['score']})"
                            for c in candidates[:available_slots]
                        ),
                    )
                    for cand in candidates[:available_slots]:
>>>>>>> parent of 7d70ca2 (feat: new strategy implementations)
                        pair = cand["pair"]
                        price = cand["price"]
                        indicators = cand["indicators"]
                        vol = cand["volatility"]

                        precision, exchange_minimum = _exchange_rules(
                            exchange_info, pair
                        )
                        budget = risk.size_usd(
                            available_usd,
                            equity,
                            _signal_strength(indicators),
                            vol,
                        )
                        quantity = risk.quantity(budget, price, precision)
                        if quantity > 0 and quantity * price >= max(
                            config.min_order_usd, exchange_minimum
                        ):
                            result = client.place_order(pair, "BUY", quantity)
                            if _accepted(result, pair, "BUY"):
                                fill = _filled_price(result, price)
                                DB.insert_trade_from_order(
                                    result,
                                    mode="LIVE",
                                    session_id=session_id,
                                    fallback_pair=pair,
                                    fallback_side="BUY",
                                    fallback_price=fill,
                                    fallback_qty=quantity,
                                )
                                strategy.notify_bought(pair, fill)
                                positions[pair] = fill
                                _save_positions(positions)
                                available_usd -= (
                                    quantity * fill * (1 + config.commission_rate)
                                )
                                cycle_buys.append(pair)
                                logger.info(
                                    "LIVE BUY %s qty=%s @ %.8f (score=%.4f)",
                                    pair,
                                    quantity,
                                    fill,
                                    cand["score"],
                                )

                # 4. Save state to DB
                cycle_summary = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "tracked_coins_count": len(selected),
                    "open_positions": len(positions),
                    "equity": round(equity, 4),
                    "buys_executed": cycle_buys,
                    "sells_executed": cycle_sells,
                    "candidates_count": len(candidates),
                }
                _save_state(
                    session_id=session_id,
                    risk=risk,
                    equity=equity,
                    available_usd=available_usd,
                    positions=positions,
                    tracked_symbols=list(selected.keys()),
                    candidates=candidates,
                    cycle_summary=cycle_summary,
                )

                logger.info(
                    "Cycle complete: %d coins monitored, %d positions open, equity=$%.2f",
                    len(selected),
                    len(positions),
                    equity,
                )
                time.sleep(config.poll_interval_seconds)
            except Exception:
                logger.exception("cycle failed; retrying after poll interval")
                time.sleep(config.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    run()
