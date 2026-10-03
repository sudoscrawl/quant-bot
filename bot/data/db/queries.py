"""
queries.py — DB access layer using SQLAlchemy ORM

Schema mirrors trade-bot exactly so the data is compatible:
  - prices/equity/api_events use integer timestamp_ms (13-digit ms)
  - trades has session_id column
  - get_prices() returns dicts with 'price' and 'timestamp_ms' keys
    (used by _restore_warmup in main.py)
"""

import logging
import time

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bot.data.db.engine import Base, engine, session_maker
# importing the package registers all models with Base.metadata
import bot.data.db.models  # noqa: F401
from bot.data.db.models.api_events import ApiEvents
from bot.data.db.models.equity import Equity
from bot.data.db.models.prices import Prices
from bot.data.db.models.state import State
from bot.data.db.models.trades import Trades

log = logging.getLogger(__name__)


def _ts() -> int:
    return int(time.time() * 1000)


def init_db():
    """Create all tables if they don't exist. Also ensures the data/ dir exists for SQLite."""
    import os
    from bot.config import config
    # for sqlite:///data/bot.sqlite3, ensure the directory exists
    db_url = config.db_url
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
        os.makedirs(os.path.dirname(db_path), exist_ok=True) if os.path.dirname(db_path) else None
    Base.metadata.create_all(engine)
    log.info("DB tables ready")


# -- state -------------------------------------------------------------

def set_state(key: str, value: str):
    with session_maker() as s:
        existing = s.get(State, key)
        if existing:
            existing.value = value
        else:
            s.add(State(key=key, value=value))
        s.commit()


def get_state(key: str, default: str | None = None) -> str | None:
    with session_maker() as s:
        row = s.get(State, key)
        return row.value if row else default


# -- prices ------------------------------------------------------------

def insert_prices(tickers: dict, timestamp_ms: int | None = None):
    """
    Bulk-insert a ticker snapshot.
    tickers = { 'BTC/USD': { MaxBid, MinAsk, LastPrice, Change, UnitTradeValue }, ... }
    """
    ts = timestamp_ms or _ts()
    rows = []
    for pair, t in tickers.items():
        bid = t.get("MaxBid", 0.0)
        ask = t.get("MinAsk", 0.0)
        price = t.get("LastPrice", 0.0)
        spread_bps = ((ask - bid) / price * 10000) if price > 0 else 0.0
        rows.append({
            "timestamp_ms": ts,
            "pair": pair,
            "price": price,
            "bid": bid,
            "ask": ask,
            "change_24h": t.get("Change", 0.0),
            "unit_trade_value": t.get("UnitTradeValue", 0.0),
            "spread_bps": round(spread_bps, 4),
        })
    if not rows:
        return
    with session_maker() as s:
        stmt = sqlite_insert(Prices).values(rows).prefix_with("OR IGNORE")
        s.execute(stmt)
        s.commit()


def get_prices(pair: str, limit: int = 200) -> list[dict]:
    """Returns dicts with 'price' and 'timestamp_ms' keys — used by warmup restore."""
    with session_maker() as s:
        rows = (
            s.query(Prices)
            .filter(Prices.pair == pair)
            .order_by(Prices.timestamp_ms.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "timestamp_ms": r.timestamp_ms,
                "pair": r.pair,
                "price": r.price,
                "bid": r.bid,
                "ask": r.ask,
                "change_24h": r.change_24h,
                "unit_trade_value": r.unit_trade_value,
                "spread_bps": r.spread_bps,
            }
            for r in rows
        ]


# -- trades ------------------------------------------------------------

def insert_trade(
    pair: str,
    side: str,
    quantity: float,
    price: float,
    fee: float,
    mode: str,
    order_id: str | None = None,
    status: str = "FILLED",
    timestamp_ms: int | None = None,
    session_id: str | None = None,
):
    ts = timestamp_ms or _ts()
    with session_maker() as s:
        s.add(Trades(
            timestamp_ms=ts,
            pair=pair,
            side=side.upper(),
            quantity=quantity,
            price=price,
            notional=quantity * price,
            fee=fee,
            mode=mode,
            order_id=order_id,
            status=status,
            session_id=session_id,
        ))
        s.commit()


def insert_trade_from_order(order_result: dict, mode: str, session_id: str | None = None):
    """Insert a trade from a place_order() API response dict."""
    d = order_result.get("OrderDetail", order_result)
    pair = d.get("Pair", "")
    side = d.get("Side", "")
    qty = float(d.get("FilledQuantity") or d.get("Quantity") or 0)
    price = float(d.get("FilledAverPrice") or d.get("Price") or 0)
    fee = float(d.get("CommissionChargeValue") or 0)
    order_id = str(d.get("OrderID") or d.get("ID") or "")
    status = d.get("Status", "FILLED")
    ts = d.get("CreateTimestamp") or _ts()
    if pair and qty and price:
        insert_trade(pair, side, qty, price, fee, mode, order_id, status, ts, session_id)


def get_trades(limit: int = 50) -> list[dict]:
    with session_maker() as s:
        rows = (
            s.query(Trades)
            .order_by(Trades.timestamp_ms.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "timestamp_ms": r.timestamp_ms,
                "pair": r.pair,
                "side": r.side,
                "quantity": r.quantity,
                "price": r.price,
                "notional": r.notional,
                "fee": r.fee,
                "mode": r.mode,
                "order_id": r.order_id,
                "status": r.status,
                "session_id": r.session_id,
            }
            for r in rows
        ]


def get_session_trades(session_id: str, limit: int = 200) -> list[dict]:
    with session_maker() as s:
        rows = (
            s.query(Trades)
            .filter(Trades.session_id == session_id)
            .order_by(Trades.timestamp_ms.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "timestamp_ms": r.timestamp_ms,
                "pair": r.pair,
                "side": r.side,
                "quantity": r.quantity,
                "price": r.price,
                "notional": r.notional,
                "fee": r.fee,
                "mode": r.mode,
                "order_id": r.order_id,
                "status": r.status,
                "session_id": r.session_id,
            }
            for r in rows
        ]


# -- equity ------------------------------------------------------------

def insert_equity(equity_val: float, timestamp_ms: int | None = None):
    ts = timestamp_ms or _ts()
    with session_maker() as s:
        stmt = (
            sqlite_insert(Equity)
            .values(timestamp_ms=ts, equity=round(equity_val, 4))
            .prefix_with("OR REPLACE")
        )
        s.execute(stmt)
        s.commit()


def get_equity_history(limit: int = 200) -> list[dict]:
    with session_maker() as s:
        rows = (
            s.query(Equity)
            .order_by(Equity.timestamp_ms.desc())
            .limit(limit)
            .all()
        )
        return [{"timestamp_ms": r.timestamp_ms, "equity": r.equity} for r in rows]


# -- api_events --------------------------------------------------------

def log_api_event(endpoint: str, success: bool, message: str = "", timestamp_ms: int | None = None):
    ts = timestamp_ms or _ts()
    with session_maker() as s:
        s.add(ApiEvents(
            timestamp_ms=ts,
            endpoint=endpoint,
            success=int(success),
            message=message,
        ))
        s.commit()


# -- stats -------------------------------------------------------------

def db_stats() -> dict:
    with session_maker() as s:
        price_count = s.query(Prices).count()
        trade_count = s.query(Trades).count()
        equity_count = s.query(Equity).count()
        event_count = s.query(ApiEvents).count()
        latest = s.query(Equity).order_by(Equity.timestamp_ms.desc()).first()
        return {
            "price_rows": price_count,
            "trade_rows": trade_count,
            "equity_rows": equity_count,
            "api_events": event_count,
            "latest_equity": latest.equity if latest else None,
        }
