import httpx
from .auth import KalshiAuth

PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"


class KalshiClient:
    """Async Kalshi API client with RSA-PSS authentication."""

    def __init__(self, key_id: str = None, private_key_path: str = None, demo: bool = True):
        self.base_url = DEMO_BASE if demo else PROD_BASE
        self.auth = KalshiAuth(key_id, private_key_path) if key_id and private_key_path else None
        self._client = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    def _auth_headers(self, method: str, path: str) -> dict:
        if self.auth:
            return self.auth.headers(method, path)
        return {}

    def _path(self, endpoint: str) -> str:
        """Extract path for signing (no host, no query)."""
        return f"/trade-api/v2{endpoint}"

    async def _get(self, endpoint: str, params: dict = None, auth: bool = False) -> dict:
        url = f"{self.base_url}{endpoint}"
        headers = self._auth_headers("GET", self._path(endpoint)) if auth else {}
        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, endpoint: str, json_body: dict = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        headers = self._auth_headers("POST", self._path(endpoint))
        resp = await self._client.post(url, json=json_body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, endpoint: str) -> dict:
        url = f"{self.base_url}{endpoint}"
        headers = self._auth_headers("DELETE", self._path(endpoint))
        resp = await self._client.delete(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # --- Public endpoints (no auth) ---

    async def get_markets(self, status: str = "open", limit: int = 100,
                          cursor: str = None, series_ticker: str = None,
                          event_ticker: str = None) -> dict:
        """List markets with optional filters."""
        params = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        return await self._get("/markets", params=params)

    async def get_all_markets(self, status: str = "open", **kwargs) -> list:
        """Paginate through all markets."""
        all_markets = []
        cursor = None
        while True:
            resp = await self.get_markets(status=status, limit=1000, cursor=cursor, **kwargs)
            markets = resp.get("markets", [])
            all_markets.extend(markets)
            cursor = resp.get("cursor")
            if not cursor or not markets:
                break
        return all_markets

    async def get_market(self, ticker: str) -> dict:
        """Get single market details."""
        return await self._get(f"/markets/{ticker}")

    async def get_orderbook(self, ticker: str) -> dict:
        """Get order book for a market."""
        return await self._get(f"/markets/{ticker}/orderbook")

    async def get_series(self, series_ticker: str) -> dict:
        """Get series metadata."""
        return await self._get(f"/series/{series_ticker}")

    # --- Private endpoints (auth required) ---

    async def get_balance(self) -> dict:
        """Get account balance."""
        return await self._get("/portfolio/balance", auth=True)

    async def get_positions(self) -> dict:
        """Get open positions."""
        return await self._get("/portfolio/positions", auth=True)

    async def get_orders(self, status: str = None) -> dict:
        """Get orders."""
        params = {}
        if status:
            params["status"] = status
        return await self._get("/portfolio/orders", params=params, auth=True)

    async def place_order(self, ticker: str, action: str, side: str,
                          count: int, price: str, order_type: str = "limit") -> dict:
        """Place a limit order.

        Args:
            ticker: market ticker (e.g., "KXBTCD-25DEC31T100000")
            action: "buy" or "sell"
            side: "yes" or "no"
            count: number of contracts
            price: price as dollar string (e.g., "0.6500")
            order_type: "limit" (market orders removed from API)
        """
        body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "type": order_type,
            "count": count,
            "yes_price": price if side == "yes" else None,
            "no_price": price if side == "no" else None,
        }
        # Remove None values
        body = {k: v for k, v in body.items() if v is not None}
        return await self._post("/portfolio/orders", json_body=body)

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an order."""
        return await self._delete(f"/portfolio/orders/{order_id}")

    async def get_fills(self, limit: int = 100) -> dict:
        """Get fill history."""
        return await self._get("/portfolio/fills", params={"limit": limit}, auth=True)

    async def get_settlements(self, limit: int = 100) -> dict:
        """Get settlement history."""
        return await self._get("/portfolio/settlements", params={"limit": limit}, auth=True)
