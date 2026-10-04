from bot.services.roostoo import roostoo


class RoostooHelpers:
    @staticmethod
    def get_price(self, pair: str) -> float | None:
        """Return the last price for a pair"""

        data = roostoo.get_ticker(pair)

        return data.get("Data", {}).get(pair, {}).get("LastPrice")

    @staticmethod
    def get_all_tickers(self) -> dict:
        """Return the data dict of all pair tickers."""
        data = roostoo.get_ticker()

        return data.get("Data", {})

    @staticmethod
    def get_usd_balance() -> float:
        data = roostoo.get_balance()

        wallet = data.get("SpotWallet") or data.get("Wallet", {})
        return wallet.get("USD", {}).get("Free", 0.0)

    @staticmethod
    def get_coin_balance(self, coin: str) -> float:
        data = self.balance()

        wallet = data.get("SpotWallet") or data.get("Wallet", {})

        return wallet.get(coin.upper(), {}).get("Free", 0.0)
