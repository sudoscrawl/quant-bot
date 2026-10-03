"""
trade_logger.py — logs trades to CSV and portfolio snapshots to JSONL

  logs/trades.csv      — one row per filled order
  logs/performance.jsonl — portfolio value over time + performance stats

  Note: This is just a basic logging which i used in my testing modal, should change to a more elegant one....:>
"""

import csv
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"
LOGS_DIR.mkdir(exist_ok=True)

TRADE_LOG = LOGS_DIR / "trades.csv"
PERF_LOG = LOGS_DIR / "performance.jsonl"

_TRADE_FIELDS = [
    "timestamp", "pair", "side", "type",
    "quantity", "price", "order_id", "status",
    "commission_usd", "note",
]


def _now() -> str:
    return datetime.now(IST).isoformat()


class TradeLogger:
    def __init__(self):
        self._snapshots: list[dict] = []
        self._ensure_header()

    def _ensure_header(self):
        if not TRADE_LOG.exists():
            with open(TRADE_LOG, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=_TRADE_FIELDS).writeheader()

    def log_order(self, order_response: dict, note: str = ""):
        d = order_response.get("OrderDetail", order_response)
        row = {
            "timestamp": _now(),
            "pair": d.get("Pair", ""),
            "side": d.get("Side", ""),
            "type": d.get("Type", ""),
            "quantity": d.get("FilledQuantity") or d.get("Quantity") or "",
            "price": d.get("FilledAverPrice") or d.get("Price") or "",
            "order_id": d.get("OrderID") or d.get("ID", ""),
            "status": d.get("Status", ""),
            "commission_usd": d.get("CommissionChargeValue") or "",
            "note": note,
        }
        with open(TRADE_LOG, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=_TRADE_FIELDS).writerow(row)

        log.info(
            "trade %s %s qty=%s @ %s [%s]",
            row["pair"], row["side"], row["quantity"], row["price"], row["status"],
        )

    def snapshot(self, portfolio_usd: float, extra: dict | None = None):
        entry = {"timestamp": _now(), "portfolio_usd": round(portfolio_usd, 4)}
        if extra:
            entry.update(extra)
        self._snapshots.append(entry)
        with open(PERF_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def performance_summary(self, initial: float, current: float) -> dict:
        if len(self._snapshots) < 2:
            return {"status": "not enough data"}

        values = [s["portfolio_usd"] for s in self._snapshots]
        returns = [
            (values[i + 1] - values[i]) / values[i]
            for i in range(len(values) - 1)
            if values[i] != 0
        ]

        if not returns:
            return {"status": "no returns yet"}

        total_return = (current - initial) / initial
        avg_r = sum(returns) / len(returns)
        std_r = (sum((r - avg_r) ** 2 for r in returns) / len(returns)) ** 0.5

        downside = [r for r in returns if r < 0]
        down_std = (sum(r ** 2 for r in downside) / len(downside)) ** 0.5 if downside else 1e-9

        peak, max_dd = initial, 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

        n = len(returns)
        sharpe = (avg_r / std_r) * math.sqrt(n) if std_r > 0 else 0.0
        sortino = (avg_r / down_std) * math.sqrt(n) if down_std > 0 else 0.0
        calmar = total_return / max_dd if max_dd > 0 else 0.0

        return {
            "total_return_pct": round(total_return * 100, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "calmar_ratio": round(calmar, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "num_snapshots": len(values),
        }
