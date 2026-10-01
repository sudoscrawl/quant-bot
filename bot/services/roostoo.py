import hashlib
import hmac
import time

import httpx

from bot.config import config

BASE_URL = "https://mock-api.roostoo.com"


class RoostooClient:
    def __init__(self) -> None:
        self.base_url = BASE_URL
        self.api_key = config.api_key
        self.api_secret = config.api_secret

        self.client = httpx.Client(base_url=self.base_url, timeout=10.0)

    def _generate_signature(self, params: dict) -> str:
        """Create an HMAC-SHA256 hex signature over sorted query params."""
        query_string = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, params: dict) -> dict:
        """Return the authentication headers required by the Roostoo API."""
        return {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": self._generate_signature(params),
        }

    @staticmethod
    def _timestamp_ms() -> int:
        """Return the current UNIX timestamp in milliseconds."""
        return int(time.time() * 1000)

    def ping(self) -> dict:
        """Get the server time from the Roostoo API to verify connectivity."""
        response = self.client.get("/v3/serverTime")
        response.raise_for_status()
        return response.json()

    def get_exchange_info(self) -> dict:
        """Get exchange information (trading rules, symbol list, etc.)."""
        response = self.client.get("/v3/exchangeInfo")
        response.raise_for_status()
        return response.json()

    def get_ticker(self, pair: str | None = None) -> dict:
        """Get price ticker, optionally filtered by trading *pair* (e.g. ``"BTC/USD"``)."""
        params: dict = {"timestamp": int(time.time())}
        if pair is not None:
            params["pair"] = pair

        response = self.client.get("/v3/ticker", params=params)
        response.raise_for_status()
        return response.json()

    def get_balance(self) -> dict:
        """Get account balances for all assets."""
        params = {"timestamp": self._timestamp_ms()}
        response = self.client.get(
            "/v3/balance",
            params=params,
            headers=self._auth_headers(params),
        )
        response.raise_for_status()
        return response.json()

    def place_order(
        self,
        coin: str,
        side: str,
        quantity: float,
        price: float | None = None,
    ) -> dict:
        """Place a MARKET or LIMIT order.

        Args:
            coin: Base asset symbol (e.g. ``"BNB"``).  The pair is built as ``coin/USD``.
            side: ``"BUY"`` or ``"SELL"``.
            quantity: Order quantity.
            price: Limit price.  When *None* a MARKET order is placed.
        """
        payload: dict = {
            "timestamp": self._timestamp_ms(),
            "pair": f"{coin}/USD",
            "side": side,
            "quantity": quantity,
        }

        if price is None:
            payload["type"] = "MARKET"
        else:
            payload["type"] = "LIMIT"
            payload["price"] = price

        response = self.client.post(
            "/v3/place_order",
            data=payload,
            headers=self._auth_headers(payload),
        )
        response.raise_for_status()
        return response.json()

    def cancel_order(
        self,
        pair: str,
        order_id: int | None = None,
    ) -> dict:
        """Cancel open orders for a *pair*, optionally targeting a specific *order_id*."""
        payload: dict = {
            "timestamp": self._timestamp_ms(),
            "pair": pair,
        }
        if order_id is not None:
            payload["order_id"] = order_id

        response = self.client.post(
            "/v3/cancel_order",
            data=payload,
            headers=self._auth_headers(payload),
        )
        response.raise_for_status()
        return response.json()

    def query_order(
        self,
        order_id: int | None = None,
        pair: str | None = None,
        pending_only: bool | None = None,
    ) -> dict:
        """Query orders with optional filters.

        Args:
            order_id: Filter by a specific order ID.
            pair: Filter by trading pair (e.g. ``"DASH/USD"``).
            pending_only: When ``True``, return only pending orders.
        """
        payload: dict = {"timestamp": self._timestamp_ms()}
        if order_id is not None:
            payload["order_id"] = order_id
        if pair is not None:
            payload["pair"] = pair
        if pending_only is not None:
            payload["pending_only"] = pending_only

        response = self.client.post(
            "/v3/query_order",
            data=payload,
            headers=self._auth_headers(payload),
        )
        response.raise_for_status()
        return response.json()

    def get_pending_count(self) -> dict:
        """Get the number of pending orders."""
        params = {"timestamp": self._timestamp_ms()}
        response = self.client.get(
            "/v3/pending_count",
            params=params,
            headers=self._auth_headers(params),
        )
        response.raise_for_status()
        return response.json()


roostoo = RoostooClient()
