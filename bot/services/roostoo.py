import httpx

from bot.config import config

BASE_URL = "https://mock-api.roostoo.com"


class RoostooClient:
    def __init__(self) -> None:
        self.base_url = BASE_URL
        self.api_key = config.api_key
        self.api_secret = config.api_secret

        self.client = httpx.Client(
            base_url = self.base_url,
            timeout= 10.0
        )

    def ping(self) -> dict:
        """Get the server time from the Roostoo API to verify connectivity."""
        response = self.client.get("/v3/serverTime")
        response.raise_for_status()
        return response.json()



roostoo = RoostooClient()
