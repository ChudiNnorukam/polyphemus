"""Thin wrapper around py_clob_client.

All CLOB interactions go through this module. Ensures:
- create_and_post_order (NEVER create_order)
- Balance returned as float USDC (not microcents)
- All calls wrapped in asyncio.wait_for(timeout=60)
- Proper error handling and logging
"""

import asyncio
import logging
import time
from typing import Optional

from py_clob_client.clob_types import (
    OrderArgs,
    MarketOrderArgs,
    BalanceAllowanceParams,
    AssetType,
    OrderType,
    TradeParams,
)
from py_clob_client.order_builder.constants import BUY, SELL

from .types import ExecutionResult, ORDER_TIMEOUT


class ClobWrapper:
    """Thin async wrapper around py_clob_client.

    Converts synchronous py_clob_client methods to async via executor,
    applies timeouts, and handles errors consistently.
    """

    def __init__(self, client, logger: logging.Logger):
        """Initialize wrapper.

        Args:
            client: py_clob_client.ClobClient instance
            logger: logging.Logger instance
        """
        self._client = client
        self._logger = logger
        self._market_ws = None  # Optional MarketWS for real-time midpoints
        # Latency tracking: method -> {"count": int, "total_ms": float, "max_ms": float}
        self._latency_stats: dict = {}

    async def place_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        post_only: bool = False,
    ) -> ExecutionResult:
        """Place an order on CLOB.

        Args:
            token_id: Token ID for the market outcome
            price: Price (0.0-1.0)
            size: Size in shares
            side: BUY or SELL constant
            post_only: If True, use two-step approach (sign then post with post_only=True)

        Returns:
            ExecutionResult with success status and order_id or error
        """
        t0 = time.time()
        try:
            if post_only:
                # Step 1: Sign the order
                order_args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=side,
                )
                loop = asyncio.get_event_loop()
                signed_order = await asyncio.wait_for(
                    loop.run_in_executor(None, self._client.create_order, order_args),
                    timeout=ORDER_TIMEOUT,
                )
                # Step 2: Post with post_only=True
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self._client.post_order(signed_order, OrderType.GTC, post_only=True),
                    ),
                    timeout=ORDER_TIMEOUT,
                )
                order_id = result.get("orderID", "")
                self._logger.info(f"Post-only order placed: {order_id} @ {price} x {size} {side}")
            else:
                # Normal taker order: create and post atomically
                order_args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=side,
                )

                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._client.create_and_post_order,
                        order_args,
                    ),
                    timeout=ORDER_TIMEOUT,
                )

                order_id = result.get("orderID", "")
                self._logger.info(f"Order placed: {order_id} @ {price} x {size} {side}")

            self._record_latency("place_order", t0)
            return ExecutionResult(success=True, order_id=order_id)
        except asyncio.TimeoutError:
            self._record_latency("place_order", t0)
            msg = f"Order placement timeout after {ORDER_TIMEOUT}s"
            self._logger.error(msg)
            return ExecutionResult(success=False, error=msg)
        except Exception as e:
            self._record_latency("place_order", t0)
            msg = f"Order placement failed: {str(e)}"
            self._logger.error(msg)
            return ExecutionResult(success=False, error=msg)

    async def place_fok_order(
        self,
        token_id: str,
        amount: float,
        side: str,
    ) -> ExecutionResult:
        """Place FOK (Fill-or-Kill) market order. Fills instantly or cancels entirely.

        Args:
            token_id: Token ID for the market outcome
            amount: Dollars to spend (BUY) or shares to sell (SELL)
            side: BUY or SELL constant
        """
        import math
        t0 = time.time()
        # FOK precision: SELL must floor (never request more shares than exist),
        # BUY rounds to nearest (dollar amount, slight over is fine).
        from py_clob_client.order_builder.constants import SELL as _SELL
        if side == _SELL:
            amount = math.floor(amount * 100) / 100
        else:
            amount = round(amount, 2)
        if amount < 0.01:
            return ExecutionResult(success=False, error=f"FOK amount too small: ${amount}")
        try:
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=side,
            )
            loop = asyncio.get_event_loop()
            signed_order = await asyncio.wait_for(
                loop.run_in_executor(None, self._client.create_market_order, order_args),
                timeout=ORDER_TIMEOUT,
            )
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self._client.post_order(signed_order, OrderType.FOK),
                ),
                timeout=ORDER_TIMEOUT,
            )
            order_id = result.get("orderID", "")
            self._logger.info(f"FOK order filled: {order_id} | ${amount:.2f} {side}")
            self._record_latency("place_fok_order", t0)
            return ExecutionResult(success=True, order_id=order_id)
        except asyncio.TimeoutError:
            self._record_latency("place_fok_order", t0)
            msg = f"FOK order timeout after {ORDER_TIMEOUT}s"
            self._logger.error(msg)
            return ExecutionResult(success=False, error=msg)
        except Exception as e:
            self._record_latency("place_fok_order", t0)
            msg = f"FOK order failed: {str(e)}"
            self._logger.error(msg)
            return ExecutionResult(success=False, error=msg)

    async def get_order_status(self, order_id: str) -> str:
        """Get order status.

        Args:
            order_id: Order ID from placement

        Returns:
            Status string (LIVE, FILLED, MATCHED, CANCELLED, FAILED) or ERROR
        """
        t0 = time.time()
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._client.get_order,
                    order_id,
                ),
                timeout=ORDER_TIMEOUT,
            )

            status = result.get("status", "UNKNOWN")
            self._record_latency("get_order_status", t0)
            return status
        except asyncio.TimeoutError:
            self._record_latency("get_order_status", t0)
            self._logger.error(f"Order status check timeout for {order_id}")
            return "ERROR"
        except Exception as e:
            self._record_latency("get_order_status", t0)
            self._logger.error(f"Order status check failed for {order_id}: {str(e)}")
            return "ERROR"

    async def get_order_details(self, order_id: str) -> Optional[dict]:
        """Get full order details including fill info.

        Args:
            order_id: Order ID from placement

        Returns:
            Dict with status, size_matched, original_size, price — or None on error
        """
        t0 = time.time()
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._client.get_order,
                    order_id,
                ),
                timeout=ORDER_TIMEOUT,
            )

            self._record_latency("get_order_details", t0)
            return {
                "status": result.get("status", "UNKNOWN"),
                "size_matched": float(result.get("size_matched", 0)),
                "original_size": float(result.get("original_size", 0)),
                "price": float(result.get("price", 0)),
            }
        except asyncio.TimeoutError:
            self._record_latency("get_order_details", t0)
            self._logger.error(f"Order details check timeout for {order_id}")
            return None
        except Exception as e:
            self._record_latency("get_order_details", t0)
            self._logger.error(f"Order details check failed for {order_id}: {str(e)}")
            return None

    async def get_balance(self) -> float:
        """Get USDC balance.

        Returns:
            Balance in USDC (float) or 0.0 on error (timeout or API failure).
            Caller must check logs/errors to distinguish API error (0.0) from
            actually broke (0.0). If critical: use higher-level get_available()
            which handles edge cases.
        """
        t0 = time.time()
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._client.get_balance_allowance,
                    params,
                ),
                timeout=ORDER_TIMEOUT,
            )

            balance_microcents = float(result.get("balance", 0))
            balance_usdc = balance_microcents / 1e6

            self._record_latency("get_balance", t0)
            return balance_usdc
        except asyncio.TimeoutError:
            self._record_latency("get_balance", t0)
            self._logger.error("Balance check timeout")
            return 0.0
        except Exception as e:
            self._record_latency("get_balance", t0)
            self._logger.error(f"Balance check failed: {str(e)}")
            return 0.0

    async def ping(self) -> bool:
        """Test CLOB API connectivity. Returns True if API responds within 10s.

        Tries /ok first (lightweight). If it hangs (Polymarket occasionally lets /ok
        timeout while the API is fully functional), falls back to get_sampling_markets
        as a connectivity proof.
        """
        t0 = time.time()
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, self._client.get_ok),
                timeout=6.0,
            )
            ms = (time.time() - t0) * 1000
            self._logger.info(f"CLOB ping OK via /ok ({ms:.0f}ms)")
            return True
        except (asyncio.TimeoutError, Exception) as first_err:
            self._logger.debug(f"CLOB /ok failed ({first_err}), trying fallback endpoint")

        # Fallback: /markets?limit=1 proves API is reachable even when /ok hangs
        try:
            t1 = time.time()
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._client.get_markets(next_cursor="")
                ),
                timeout=8.0,
            )
            ms = (time.time() - t1) * 1000
            self._logger.info(f"CLOB ping OK via /markets fallback ({ms:.0f}ms)")
            return True
        except Exception as e:
            self._logger.error(f"CLOB ping failed (both /ok and /markets): {e}")
            return False

    def set_market_ws(self, ws) -> None:
        """Inject MarketWS for real-time midpoint lookups."""
        self._market_ws = ws

    async def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price for a token.

        Tries MarketWS cache first (0ms), falls back to REST API (~68ms).

        Args:
            token_id: Token ID for the market outcome

        Returns:
            Midpoint price (float) or 0.0 on error
        """
        # Fast path: WS midpoint
        if self._market_ws:
            ws_mid = self._market_ws.get_midpoint(token_id)
            if ws_mid > 0:
                return ws_mid

        t0 = time.time()
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._client.get_midpoint,
                    token_id,
                ),
                timeout=10,
            )

            if not isinstance(result, dict) or "mid" not in result:
                self._logger.warning(
                    f"Unexpected midpoint response for {token_id}: "
                    f"type={type(result).__name__} keys={list(result.keys()) if isinstance(result, dict) else 'N/A'}"
                )
                self._record_latency("get_midpoint", t0)
                return 0.0

            price = float(result["mid"])
            self._record_latency("get_midpoint", t0)
            return price
        except asyncio.TimeoutError:
            self._record_latency("get_midpoint", t0)
            self._logger.warning(f"Midpoint timeout (10s) for {token_id}")
            return 0.0
        except Exception as e:
            self._record_latency("get_midpoint", t0)
            self._logger.debug(f"Midpoint unavailable for {token_id}: {type(e).__name__}: {e}")
            return 0.0

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully, False otherwise
        """
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._client.cancel,
                    order_id,
                ),
                timeout=ORDER_TIMEOUT,
            )

            self._logger.info(f"Order cancelled: {order_id}")
            return True
        except asyncio.TimeoutError:
            self._logger.error(f"Order cancellation timeout for {order_id}")
            return False
        except Exception as e:
            self._logger.error(f"Order cancellation failed for {order_id}: {str(e)}")
            return False

    async def get_order_book(self, token_id: str) -> dict:
        """Get order book for a token.

        Returns normalized {"bids": [{"price": float, "size": float}, ...],
                            "asks": [{"price": float, "size": float}, ...]}.
        py_clob_client returns OrderBookSummary with OrderSummary objects —
        this method normalizes to plain dicts with float values.
        """
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._client.get_order_book, token_id
                ),
                timeout=10,
            )

            def _normalize_level(level):
                if hasattr(level, 'price'):
                    return {"price": float(level.price), "size": float(level.size)}
                elif isinstance(level, dict):
                    return {"price": float(level["price"]), "size": float(level["size"])}
                return None

            asks_raw = getattr(result, 'asks', None) or (result.get('asks') if isinstance(result, dict) else [])
            bids_raw = getattr(result, 'bids', None) or (result.get('bids') if isinstance(result, dict) else [])

            asks = sorted(
                [a for a in (_normalize_level(l) for l in (asks_raw or [])) if a],
                key=lambda x: x["price"],
            )
            bids = sorted(
                [b for b in (_normalize_level(l) for l in (bids_raw or [])) if b],
                key=lambda x: x["price"], reverse=True,
            )

            return {"asks": asks, "bids": bids}
        except Exception as e:
            self._logger.warning(f"Order book fetch failed for {token_id}: {e}")
            return {"asks": [], "bids": []}

    def _record_latency(self, method: str, start_time: float):
        """Record API call latency for monitoring."""
        elapsed_ms = (time.time() - start_time) * 1000
        if method not in self._latency_stats:
            self._latency_stats[method] = {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
        stats = self._latency_stats[method]
        stats["count"] += 1
        stats["total_ms"] += elapsed_ms
        stats["max_ms"] = max(stats["max_ms"], elapsed_ms)
        self._logger.debug(f"CLOB latency: {method} {elapsed_ms:.0f}ms")

    def get_latency_stats(self) -> dict:
        """Get latency stats for dashboard/monitoring."""
        result = {}
        for method, stats in self._latency_stats.items():
            avg = stats["total_ms"] / stats["count"] if stats["count"] else 0
            result[method] = {
                "count": stats["count"],
                "avg_ms": round(avg, 1),
                "max_ms": round(stats["max_ms"], 1),
            }
        return result

    async def get_share_balance(self, token_id: str) -> float:
        """Get conditional token (share) balance for a market outcome.

        Args:
            token_id: Token ID for the market outcome

        Returns:
            Balance in shares (float) or 0.0 on error
        """
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
            )
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._client.get_balance_allowance,
                    params,
                ),
                timeout=ORDER_TIMEOUT,
            )

            balance_microcents = float(result.get("balance", 0))
            balance_shares = balance_microcents / 1e6

            return balance_shares
        except asyncio.TimeoutError:
            self._logger.error(f"Share balance check timeout for {token_id}")
            return 0.0
        except Exception as e:
            self._logger.error(f"Share balance check failed for {token_id}: {str(e)}")
            return 0.0

    async def get_recent_trades(self, hours: int = 24) -> list:
        """Get authenticated user's CLOB trades from last N hours.

        Returns list of trade dicts from py_clob_client.get_trades().
        """
        t0 = time.time()
        try:
            cutoff = int(time.time()) - (hours * 3600)
            params = TradeParams(after=cutoff)
            loop = asyncio.get_event_loop()
            trades = await asyncio.wait_for(
                loop.run_in_executor(None, self._client.get_trades, params),
                timeout=30,
            )
            self._record_latency("get_recent_trades", t0)
            return trades if isinstance(trades, list) else []
        except asyncio.TimeoutError:
            self._record_latency("get_recent_trades", t0)
            self._logger.error(f"get_recent_trades timeout (30s)")
            raise
        except Exception as e:
            self._record_latency("get_recent_trades", t0)
            self._logger.error(f"get_recent_trades failed: {e}")
            raise
