"""Thin sync wrapper around py_clob_client for read-only operations."""

import os
from typing import Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    BalanceAllowanceParams,
    AssetType,
    TradeParams,
)


class ClobReader:
    """Read-only interface to the Polymarket CLOB API."""

    def __init__(self):
        self._client: Optional[ClobClient] = None

    def _get_client(self) -> ClobClient:
        if self._client is None:
            creds = ApiCreds(
                api_key=os.environ["POLYMARKET_CLOB_API_KEY"],
                api_secret=os.environ["POLYMARKET_CLOB_SECRET"],
                api_passphrase=os.environ["POLYMARKET_CLOB_PASSPHRASE"],
            )
            self._client = ClobClient(
                host="https://clob.polymarket.com",
                key=os.environ["POLYMARKET_PRIVATE_KEY"],
                chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                creds=creds,
                signature_type=int(os.environ.get("POLYMARKET_SIG_TYPE", "1")),
                funder=os.environ["POLYMARKET_WALLET_ADDRESS"],
            )
        return self._client

    def get_balance(self) -> float:
        """Get USDC wallet balance."""
        client = self._get_client()
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        result = client.get_balance_allowance(params)
        return float(result.get("balance", 0)) / 1e6

    def get_share_balance(self, token_id: str) -> float:
        """Get conditional token balance for a market outcome."""
        client = self._get_client()
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=token_id,
        )
        result = client.get_balance_allowance(params)
        return float(result.get("balance", 0)) / 1e6

    def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price for a token."""
        client = self._get_client()
        result = client.get_midpoint(token_id)
        if isinstance(result, dict) and "mid" in result:
            return float(result["mid"])
        return 0.0

    def get_order_book(self, token_id: str) -> dict:
        """Get order book, normalized to plain dicts."""
        client = self._get_client()
        result = client.get_order_book(token_id)

        def _normalize(level):
            if hasattr(level, "price"):
                return {"price": float(level.price), "size": float(level.size)}
            elif isinstance(level, dict):
                return {"price": float(level["price"]), "size": float(level["size"])}
            return None

        asks_raw = getattr(result, "asks", None) or (
            result.get("asks") if isinstance(result, dict) else []
        )
        bids_raw = getattr(result, "bids", None) or (
            result.get("bids") if isinstance(result, dict) else []
        )

        asks = sorted(
            [a for a in (_normalize(l) for l in (asks_raw or [])) if a],
            key=lambda x: x["price"],
        )
        bids = sorted(
            [b for b in (_normalize(l) for l in (bids_raw or [])) if b],
            key=lambda x: x["price"],
            reverse=True,
        )

        spread = (asks[0]["price"] - bids[0]["price"]) if asks and bids else None
        mid = (asks[0]["price"] + bids[0]["price"]) / 2 if asks and bids else None

        return {"asks": asks, "bids": bids, "spread": spread, "midpoint": mid}

    def get_spread(self, token_id: str) -> dict:
        """Get bid-ask spread for a token."""
        client = self._get_client()
        result = client.get_spread(token_id)
        if isinstance(result, dict):
            return {k: float(v) if v is not None else None for k, v in result.items()}
        return {"bid": None, "ask": None, "spread": None}

    def get_price(self, token_id: str, side: str = "buy") -> float:
        """Get best bid or ask price."""
        client = self._get_client()
        result = client.get_price(token_id, side.upper())
        if isinstance(result, (int, float, str)):
            return float(result)
        if isinstance(result, dict) and "price" in result:
            return float(result["price"])
        return 0.0

    def get_market(self, condition_id: str) -> dict:
        """Get market details by condition ID."""
        client = self._get_client()
        result = client.get_market(condition_id)
        if isinstance(result, dict):
            return result
        return {}

    def search_markets(self, next_cursor: str = "") -> dict:
        """Get active/sampling markets (paginated)."""
        client = self._get_client()
        result = client.get_sampling_simplified_markets(next_cursor)
        if isinstance(result, dict):
            return result
        return {"data": [], "next_cursor": ""}

    def get_open_orders(self) -> list:
        """Get user's open orders on the CLOB."""
        client = self._get_client()
        result = client.get_orders()
        if isinstance(result, list):
            return result
        return []

    def get_trades(self, hours: int = 24) -> list:
        """Get user's recent CLOB trades."""
        import time
        client = self._get_client()
        cutoff = int(time.time()) - (hours * 3600)
        params = TradeParams(after=cutoff)
        result = client.get_trades(params)
        if isinstance(result, list):
            return result
        return []

    def get_server_time(self) -> str:
        """Get CLOB server timestamp."""
        client = self._get_client()
        return str(client.get_server_time())

    def get_fee_rate(self, token_id: str) -> float:
        """Get fee rate in basis points for a token."""
        client = self._get_client()
        result = client.get_fee_rate_bps(token_id)
        if isinstance(result, (int, float)):
            return float(result)
        return 0.0
