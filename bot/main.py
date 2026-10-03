"""
main.py — live trading loop (no paper mode)

Edit the CONFIG section below before running.
Stop with Ctrl+C — it will close all open positions cleanly.
"""

import logging
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from bot.data.db import queries as db
from bot.logger.trade_logger import TradeLogger
from bot.risk.manager import RiskManager
from bot.services.roostoo import RoostooClient
from bot.strategy.momentum import MomentumConfig, MomentumStrategy

IST = ZoneInfo("Asia/Kolkata")

import os
os.makedirs("logs", exist_ok=True)


class _Tee:
    """Mirror print() to both terminal and log file."""
    def __init__(self, path: str):
        self._term = sys.__stdout__
        self._file = open(path, "a", buffering=1)

    def write(self, msg):
        self._term.write(msg)
        self._file.write(msg)

    def flush(self):
        self._term.flush()
        self._file.flush()

    def isatty(self):
        return self._term.isatty()


sys.stdout = _Tee("logs/bot.log")


class _ISTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=IST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S IST")


_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(_ISTFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_h])
log = logging.getLogger("main")

# ── CONFIG ────────────────────────────────────────────────────────────
POLL_INTERVAL     = 300    # seconds between cycles (5 min)
PERF_INTERVAL     = 900    # how often to print performance summary (15 min)
PROFIT_TARGET_PCT = 0.0    # 0 = disabled; set e.g. 2.0 to auto-stop at +2%
STOP_LOSS_PCT     = 5.57   # stop if portfolio drops by this %
MAX_POSITIONS     = 6      # max open positions at once
# ─────────────────────────────────────────────────────────────────────

TRADE_PAIRS: list[str] = []
STARTING_BALANCE: float = 0.0


def _now() -> str:
    return datetime.now(IST).strftime("%H:%M:%S IST")


def _ts_ms() -> int:
    return int(time.time() * 1000)


def _amt_precision(exchange_info: dict, pair: str) -> int:
    return exchange_info.get("TradePairs", {}).get(pair, {}).get("AmountPrecision", 6)


def _min_order(exchange_info: dict, pair: str) -> float:
    return exchange_info.get("TradePairs", {}).get(pair, {}).get("MiniOrder", 1.0)


# ── display ───────────────────────────────────────────────────────────

def print_header(cycle: int, portfolio: float, usd_free: float,
                 warmed: int, total: int, avg_ticks: float, ticks_needed: int):
    pnl = portfolio - STARTING_BALANCE
    pnl_pct = (pnl / STARTING_BALANCE) * 100 if STARTING_BALANCE else 0
    icon = "UP" if pnl >= 0 else "DOWN"
    print(f"\n{'='*62}")
    print(f"  Trade Bot [LIVE]  |  {_now()}  |  Cycle #{cycle}")
    print(f"{'='*62}")
    print(f"  Portfolio:  ${portfolio:>12,.2f}   {icon} {pnl:+.2f} ({pnl_pct:+.4f}%)")
    print(f"  USD free:   ${usd_free:>12,.2f}")
    if warmed < total:
        pct = (avg_ticks / ticks_needed) * 100
        filled = int(pct / 5)
        bar = "#" * filled + "." * (20 - filled)
        print(f"  Warmup: [{bar}] {avg_ticks:.1f}/{ticks_needed} ticks  ({warmed}/{total} ready)")
    else:
        print(f"  All {total} pairs warmed — strategy active")
    print(f"{'-'*62}")


def print_signal(pair: str, price: float, signal: str, ind: dict, action: str = ""):
    icon = {"BUY": "[BUY]", "SELL": "[SELL]", "HOLD": "[ ]"}.get(signal, "[ ]")
    if ind.get("warming_up"):
        print(f"  {icon}  {pair:<14}  ${price:>12,.4f}   warming ({ind.get('prices_collected',0)}/50)")
    else:
        fast  = ind.get("fast_ema", 0)
        slow  = ind.get("slow_ema", 0)
        rsi   = ind.get("rsi", 0)
        trend = "^" if fast > slow else "v"
        line  = f"  {icon}  {pair:<14}  ${price:>12,.4f}   RSI={rsi:>5.1f}  EMA {trend}  {signal}"
        if action:
            line += f"  <- {action}"
        print(line)


def print_trade(result: dict):
    d = result.get("OrderDetail", result)
    print(
        f"       [OK] [LIVE] {d.get('Side','?')} {d.get('FilledQuantity','?')} "
        f"{d.get('Pair','?')} @ ${d.get('FilledAverPrice') or d.get('Price','?')}  "
        f"fee=${d.get('CommissionChargeValue', 0)}  "
        f"ID={d.get('OrderID','?')}  [{d.get('Status','?')}]"
    )


def print_performance(summary: dict, portfolio: float):
    print(f"\n{'-'*62}")
    print(f"  PERFORMANCE SUMMARY")
    print(f"{'-'*62}")
    print(f"  Total return:  {summary.get('total_return_pct', 0):+.4f}%")
    print(f"  Sharpe:        {summary.get('sharpe_ratio', 0):.4f}")
    print(f"  Sortino:       {summary.get('sortino_ratio', 0):.4f}")
    print(f"  Calmar:        {summary.get('calmar_ratio', 0):.4f}")
    print(f"  Max drawdown:  {summary.get('max_drawdown_pct', 0):.4f}%")
    print(f"  Portfolio:     ${portfolio:,.2f}")
    print(f"{'-'*62}")


def shutdown(reason: str, client: RoostooClient, trade_log: TradeLogger,
             tickers: dict, total_buys: int, total_sells: int, session_id: str):
    print(f"\n{'='*62}")
    print(f"  STOP  {reason}")
    print(f"  {_now()}")
    print(f"{'='*62}")

    wallet      = client.get_balance().get("SpotWallet") or {}
    tickers_now = client.get_all_tickers()

    for coin, amounts in wallet.items():
        if coin == "USD":
            continue
        qty = amounts.get("Free", 0.0)
        if qty <= 0:
            continue
        price = tickers_now.get(f"{coin}/USD", {}).get("LastPrice", 0)
        print(f"  Closing {qty} {coin} @ ${price}")
        result = client.place_order(coin, "SELL", qty)
        print_trade(result)
        if result.get("Success"):
            trade_log.log_order(result, note="shutdown_sell")
            db.insert_trade_from_order(result, mode="live", session_id=session_id)

    final_val = sum(
        (amounts.get("Free", 0) + amounts.get("Lock", 0)) *
        tickers_now.get(f"{c}/USD", {}).get("LastPrice", 0)
        if c != "USD" else amounts.get("Free", 0) + amounts.get("Lock", 0)
        for c, amounts in wallet.items()
    )

    summary = trade_log.performance_summary(STARTING_BALANCE, final_val)
    print_performance(summary, final_val)
    print(f"  Trades:  {total_buys} buys / {total_sells} sells")
    print(f"  Logs:    logs/")
    print(f"{'='*62}\n")


def _restore_warmup(strategy: MomentumStrategy, pairs: list, live_tickers: dict) -> int:
    """
    Load stored prices from DB into strategy so it doesn't cold-start.
    Suppresses stop-loss/take-profit during loading to avoid phantom signals.
    Resets all position state after — clean slate for this session.
    """
    now_ms = _ts_ms()
    total = 0
    strategy._restoring = True

    for pair in pairs:
        rows = db.get_prices(pair, limit=200)
        if not rows:
            continue
        for row in reversed(rows):
            strategy.update(pair, row["price"])
            total += 1

        # if DB data is stale (>10 min), push current live price for drift correction
        if live_tickers and rows:
            live_price = live_tickers.get(pair, {}).get("LastPrice", 0)
            age_mins = (now_ms - rows[0]["timestamp_ms"]) / 60000
            if live_price and age_mins > 10:
                strategy.update(pair, live_price)

        # reset position tracking — no phantom positions from last session
        st = strategy._state(pair)
        st.entry_price  = 0.0
        st.hold_cycles  = 0
        st.last_signal  = "HOLD"
        st.just_restored = True

    strategy._restoring = False

    # ensure all pairs skip the first live tick to block false crossover
    for pair in pairs:
        strategy._state(pair).last_signal = "HOLD"

    return total


# ── main ──────────────────────────────────────────────────────────────

def run():
    global STARTING_BALANCE, TRADE_PAIRS

    print(f"\n{'='*62}")
    print(f"  ROOSTOO BOT — LIVE TRADING")
    print(f"  {_now()}")
    print(f"{'='*62}")

    db.init_db()
    client = RoostooClient()

    st = client.server_time()
    if "ServerTime" not in st:
        print("[FAIL] Cannot reach API.")
        return
    print(f"  [OK]  Connected  |  Server: {st['ServerTime']}")

    exchange_info = client.exchange_info()
    all_pairs = exchange_info.get("TradePairs", {})
    TRADE_PAIRS = [p for p, m in all_pairs.items() if m.get("CanTrade", False)]
    if not TRADE_PAIRS:
        print("[FAIL] No tradable pairs found.")
        return
    print(f"  [OK]  {len(TRADE_PAIRS)} pairs loaded")

    # auto-set starting balance from live account
    real_bal     = client.get_balance().get("SpotWallet") or {}
    real_tickers = client.get_all_tickers()
    usd_free     = real_bal.get("USD", {}).get("Free", 0.0)
    coin_value   = sum(
        (real_bal[c].get("Free", 0) + real_bal[c].get("Lock", 0)) *
        real_tickers.get(f"{c}/USD", {}).get("LastPrice", 0)
        for c in real_bal if c != "USD"
    )
    STARTING_BALANCE = round(usd_free + coin_value, 2)
    print(f"  USD free:  ${usd_free:,.2f}  |  Total: ${STARTING_BALANCE:,.2f}")
    print(f"  STARTING_BALANCE set to ${STARTING_BALANCE:,.2f}")

    if PROFIT_TARGET_PCT:
        print(f"  Target: +{PROFIT_TARGET_PCT}%  (${STARTING_BALANCE * (1 + PROFIT_TARGET_PCT/100):,.2f})")
    if STOP_LOSS_PCT:
        print(f"  Guard:  -{STOP_LOSS_PCT}%  (${STARTING_BALANCE * (1 - STOP_LOSS_PCT/100):,.2f})")
    print(f"  Ctrl+C to stop\n")

    session_id = str(int(time.time()))

    strategy  = MomentumStrategy(MomentumConfig(
        fast_ema=8,
        slow_ema=21,
        rsi_period=14,
        min_history=50,
        rsi_buy_min=45.0,
        rsi_buy_max=58.0,
        rsi_sell_min=45.0,
        ema_separation_pct=0.05,
        confirm_ticks=2,
        min_hold_cycles=3,
        min_profit_pct=0.25,
        stop_loss_pct=2.0,
        take_profit_pct=3.0,
        loss_cooldown_cycles=3,
    ))
    risk      = RiskManager(initial_balance=STARTING_BALANCE)
    trade_log = TradeLogger()

    db.set_state("mode", "live")
    db.set_state("start_balance", str(STARTING_BALANCE))
    db.set_state("started_at", str(_ts_ms()))
    log.info("DB ready — %s", db.db_stats())

    # restore warmup from stored prices
    print(f"  Loading stored prices for warmup...")
    restored = _restore_warmup(strategy, TRADE_PAIRS, real_tickers)
    if restored:
        warmed_count = sum(1 for p in TRADE_PAIRS if not strategy.indicators(p).get("warming_up"))
        print(f"  Restored {restored:,} price rows — {warmed_count}/{len(TRADE_PAIRS)} pairs warmed")
        if warmed_count == len(TRADE_PAIRS):
            print(f"  All pairs warmed — trading starts immediately!")
        else:
            print(f"  {len(TRADE_PAIRS) - warmed_count} pairs still need more ticks")
    else:
        mins = 50 * POLL_INTERVAL // 60
        print(f"  No prior data — warmup needed (~{mins} min)")

    # reconcile existing wallet positions into strategy so stop-loss tracks them
    existing_wallet = client.get_balance().get("SpotWallet") or {}
    reconciled = []
    for coin, amounts in existing_wallet.items():
        if coin == "USD":
            continue
        qty = amounts.get("Free", 0) + amounts.get("Lock", 0)
        if qty <= 0:
            continue
        pair  = f"{coin}/USD"
        price = real_tickers.get(pair, {}).get("LastPrice", 0)
        if price and pair in TRADE_PAIRS:
            strategy.notify_bought(pair, price)
            reconciled.append(f"{pair}@${price:.4f}")
    if reconciled:
        print(f"  Reconciled {len(reconciled)} existing positions: {', '.join(reconciled)}")
        print(f"  Stop-loss and take-profit now active on these.")

    last_perf   = time.time()
    cycle       = 0
    total_buys  = 0
    total_sells = 0

    while True:
        try:
            cycle += 1

            all_tickers = client.get_all_tickers()
            if not all_tickers:
                print(f"  [WARN] [{_now()}] Empty ticker — retrying in {POLL_INTERVAL}s")
                db.log_api_event("/v3/ticker", False, "empty ticker response")
                time.sleep(POLL_INTERVAL)
                continue

            ts_now = _ts_ms()
            db.insert_prices(all_tickers, timestamp_ms=ts_now)
            db.log_api_event("/v3/ticker", True, f"{len(all_tickers)} pairs")

            # portfolio value
            bal    = client.get_balance()
            wallet = bal.get("SpotWallet") or {}
            usd_free = wallet.get("USD", {}).get("Free", 0.0)
            portfolio_val = usd_free
            for coin, amounts in wallet.items():
                if coin == "USD":
                    continue
                qty   = amounts.get("Free", 0) + amounts.get("Lock", 0)
                price = all_tickers.get(f"{coin}/USD", {}).get("LastPrice", 0)
                portfolio_val += qty * price

            risk.update_balance(portfolio_val)
            trade_log.snapshot(portfolio_val)
            db.insert_equity(portfolio_val, timestamp_ms=ts_now)

            # auto-stop checks
            pnl_pct = (portfolio_val - STARTING_BALANCE) / STARTING_BALANCE * 100
            if PROFIT_TARGET_PCT and pnl_pct >= PROFIT_TARGET_PCT:
                shutdown(f"PROFIT TARGET HIT: +{pnl_pct:.2f}%",
                         client, trade_log, all_tickers, total_buys, total_sells, session_id)
                return
            if STOP_LOSS_PCT and pnl_pct <= -STOP_LOSS_PCT:
                shutdown(f"STOP LOSS HIT: {pnl_pct:.2f}%",
                         client, trade_log, all_tickers, total_buys, total_sells, session_id)
                return

            warmed    = sum(1 for p in TRADE_PAIRS if not strategy.indicators(p).get("warming_up"))
            avg_ticks = (
                sum(strategy.indicators(p).get("prices_collected", 0) for p in TRADE_PAIRS)
                / max(len(TRADE_PAIRS), 1)
            )
            log.info("cycle %d warmed=%d/%d avg_ticks=%.1f portfolio=%.2f usd=%.2f",
                     cycle, warmed, len(TRADE_PAIRS), avg_ticks, portfolio_val, usd_free)

            print_header(cycle, portfolio_val, usd_free, warmed, len(TRADE_PAIRS), avg_ticks, 50)

            if risk.is_halted():
                print(f"  HALTED — drawdown {risk.drawdown(portfolio_val)*100:.2f}% exceeds limit.")
                time.sleep(POLL_INTERVAL)
                continue

            actions = 0

            for pair in TRADE_PAIRS:
                ticker = all_tickers.get(pair)
                if not ticker:
                    continue
                price = ticker.get("LastPrice", 0.0)
                if not price:
                    continue

                signal = strategy.update(pair, price)
                ind    = strategy.indicators(pair)
                coin   = pair.split("/")[0]

                if not ind.get("warming_up"):
                    print_signal(pair, price, signal, ind)
                if ind.get("warming_up"):
                    continue

                # always fetch fresh live wallet for this pair
                live_wallet  = client.get_balance().get("SpotWallet") or {}
                coin_amounts = live_wallet.get(coin.upper(), {})
                coin_held    = coin_amounts.get("Free", 0.0) + coin_amounts.get("Lock", 0.0)

                # ── trailing stop ─────────────────────────────────────
                # once a position is up >1.5%, move the stop to breakeven
                # (entry + 0.2% round-trip commission) so fees are covered
                if coin_held > 0:
                    entry = ind.get("entry_price", 0)
                    if entry > 0:
                        current_pnl = (price - entry) / entry * 100
                        breakeven   = entry * 1.002  # entry + 0.2% round-trip fees
                        st = strategy._state(pair)
                        if current_pnl >= 1.5 and st.entry_price < breakeven:
                            log.info(
                                "TRAILING STOP %s: pnl=%.2f%% — entry moved %.4f -> %.4f",
                                pair, current_pnl, st.entry_price, breakeven,
                            )
                            st.entry_price = breakeven

                # ── proactive exit ────────────────────────────────────
                # if a HOLD position is steadily losing, EMA has turned
                # down, and RSI is weakening — exit before hitting -2%
                if coin_held > 0 and signal == "HOLD":
                    entry       = ind.get("entry_price", 0)
                    hold_cycles = ind.get("hold_cycles", 0)
                    if entry > 0:
                        held_pnl = (price - entry) / entry * 100
                        fast     = ind.get("fast_ema", 0)
                        slow_ema = ind.get("slow_ema", 0)
                        rsi      = ind.get("rsi", 50)
                        if (
                            held_pnl <= -0.8
                            and fast < slow_ema       # trend reversed
                            and rsi < 40              # momentum weakening
                            and hold_cycles >= 3      # not a brand-new position
                            and held_pnl > -2.0       # stop-loss hasn't kicked in yet
                        ):
                            sell_qty = coin_amounts.get("Free", 0.0)
                            if sell_qty > 0:
                                print(f"\n  [SELL] PROACTIVE EXIT {pair}  pnl={held_pnl:+.2f}%  EMA↓  RSI={rsi:.0f}")
                                result = client.place_order(coin, "SELL", sell_qty)
                                print_trade(result)
                                if result.get("Success"):
                                    trade_log.log_order(result, note="proactive_exit")
                                    db.insert_trade_from_order(result, mode="live", session_id=session_id)
                                    strategy.notify_sold(pair, was_loss=True)
                                    _pw = client.get_balance().get("SpotWallet") or {}
                                    usd_free = _pw.get("USD", {}).get("Free", 0.0)
                                    total_sells += 1
                                    actions += 1
                                    continue  # next pair

                amt_prec = _amt_precision(exchange_info, pair)

                if signal == "BUY" and coin_held == 0:
                    bal_wallet = client.get_balance().get("SpotWallet") or {}
                    open_coins = {
                        c: (a.get("Free", 0), a.get("Free", 0) + a.get("Lock", 0))
                        for c, a in bal_wallet.items()
                        if c != "USD" and (a.get("Free", 0) + a.get("Lock", 0)) > 0
                    }
                    open_positions = len(open_coins)

                    # rotation: if full, see if we can swap out a losing position
                    if open_positions >= MAX_POSITIONS:
                        new_ind   = strategy.indicators(pair)
                        new_rsi   = new_ind.get("rsi", 50)
                        new_sep   = new_ind.get("ema_sep_pct", 0)
                        new_score = new_sep * 10 + (1 - abs(new_rsi - 52) / 10)

                        worst_pair = None
                        worst_pnl  = 0.0

                        for held_coin, held_qty_info in open_coins.items():
                            held_pair  = f"{held_coin}/USD"
                            held_price = all_tickers.get(held_pair, {}).get("LastPrice", 0)
                            held_entry = strategy.indicators(held_pair).get("entry_price", 0)
                            if held_entry <= 0 or held_price <= 0:
                                continue
                            held_pnl_pct = (held_price - held_entry) / held_entry * 100
                            # only rotate out a small loser (stop-loss handles the big ones)
                            if -1.8 <= held_pnl_pct <= -0.5 and held_pnl_pct < worst_pnl:
                                worst_pnl  = held_pnl_pct
                                worst_pair = held_pair

                        if worst_pair and new_score > 0.5:
                            worst_coin     = worst_pair.split("/")[0]
                            worst_qty_free = open_coins[worst_coin][0]
                            print(f"\n  ROTATE: selling {worst_pair} ({worst_pnl:+.2f}%) to buy {pair}")
                            rot = client.place_order(worst_coin, "SELL", worst_qty_free)
                            if rot.get("Success"):
                                trade_log.log_order(rot, note="rotation_sell")
                                db.insert_trade_from_order(rot, mode="live", session_id=session_id)
                                strategy.notify_sold(worst_pair, was_loss=(worst_pnl < 0))
                                print_trade(rot)
                                _post_rot = client.get_balance().get("SpotWallet") or {}
                                usd_free  = _post_rot.get("USD", {}).get("Free", 0.0)
                                open_positions -= 1
                                total_sells += 1
                        else:
                            log.info("SKIP %s: max positions %d/%d, no rotation candidate",
                                     pair, open_positions, MAX_POSITIONS)
                            continue

                    MIN_CASH_RESERVE = STARTING_BALANCE * 0.20
                    if usd_free < MIN_CASH_RESERVE:
                        log.info("SKIP %s: usd_free=%.2f below reserve %.2f",
                                 pair, usd_free, MIN_CASH_RESERVE)
                        continue

                    size_usd = risk.position_size_usd(usd_free, portfolio_val)
                    if size_usd <= 0:
                        log.info("SKIP %s: size_usd <= 0", pair)
                        continue

                    factor = 10 ** amt_prec
                    qty = int((size_usd / price) * factor) / factor
                    if qty * price < _min_order(exchange_info, pair):
                        log.info("SKIP %s: order value %.2f < min_order", pair, qty * price)
                        continue

                    print(f"\n  [BUY] {pair}  qty={qty}  ~${qty*price:.2f}  [{open_positions+1}/{MAX_POSITIONS}]")
                    result = client.place_order(coin, "BUY", qty)
                    print_trade(result)
                    if result.get("Success"):
                        trade_log.log_order(result, note="live_buy")
                        db.insert_trade_from_order(result, mode="live", session_id=session_id)
                        filled_price = result.get("OrderDetail", {}).get("FilledAverPrice") or price
                        strategy.notify_bought(pair, filled_price)
                        _post_buy = client.get_balance().get("SpotWallet") or {}
                        usd_free  = _post_buy.get("USD", {}).get("Free", 0.0)
                        total_buys += 1
                        actions += 1

                elif signal == "SELL" and coin_held > 0:
                    entry    = ind.get("entry_price", 0)
                    pnl_pct  = ((price - entry) / entry * 100) if entry > 0 else 0
                    was_loss = pnl_pct <= -2.0
                    reason   = "STOP-LOSS" if was_loss else "TAKE-PROFIT" if pnl_pct >= 3.0 else "SIGNAL"
                    # sell only Free qty — locked qty is in a pending order
                    sell_qty = coin_amounts.get("Free", 0.0)
                    if sell_qty <= 0:
                        log.warning("SKIP SELL %s: coin_held=%.6f but Free=0 (all locked)", pair, coin_held)
                        continue
                    print(f"\n  [SELL] [{reason}] {pair}  qty={sell_qty}  pnl={pnl_pct:+.2f}%  ~${sell_qty*price:.2f}")
                    result = client.place_order(coin, "SELL", sell_qty)
                    print_trade(result)
                    if result.get("Success"):
                        trade_log.log_order(result, note="live_sell")
                        db.insert_trade_from_order(result, mode="live", session_id=session_id)
                        strategy.notify_sold(pair, was_loss=was_loss)
                        total_sells += 1
                        actions += 1

            if actions == 0 and warmed == len(TRADE_PAIRS):
                print(f"  [ ]  No signals this cycle.")

            print(f"\n  Trades: {total_buys} buys / {total_sells} sells")
            print(f"  Next poll in {POLL_INTERVAL}s...")

            if time.time() - last_perf >= PERF_INTERVAL:
                summary = trade_log.performance_summary(STARTING_BALANCE, portfolio_val)
                print_performance(summary, portfolio_val)
                stats = db.db_stats()
                print(
                    f"  DB: {stats['price_rows']:,} prices | "
                    f"{stats['trade_rows']} trades | "
                    f"{stats['equity_rows']} equity pts"
                )
                last_perf = time.time()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            shutdown(
                "Stopped by user (Ctrl+C)",
                client, trade_log,
                all_tickers if "all_tickers" in dir() else {},
                total_buys, total_sells, session_id,
            )
            db.set_state("stopped_at", str(_ts_ms()))
            log.info("DB saved: %s", db.db_stats())
            break

        except Exception as e:
            print(f"  [WARN] [{_now()}] {e} — retrying in {POLL_INTERVAL}s")
            log.exception("Unexpected error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
