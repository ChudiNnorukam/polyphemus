"""Balance Manager — USDC balance tracking, caching, and deployment ratio monitoring.

Responsibilities:
- Cache balance with configurable TTL
- Track deployed capital (position notional value)
- Enforce low-balance guards
- Monitor deployment ratio (deployed / total)
- Startup wallet reconciliation
"""

import time
import logging
from typing import TYPE_CHECKING

from .types import BALANCE_CACHE_TTL
from .config import setup_logger, assert_wallet_reconciliation

if TYPE_CHECKING:
    from .clob_wrapper import ClobWrapper
    from .position_store import PositionStore
    from .performance_db import PerformanceDB
    from .config import Settings


class BalanceManager:
    """Manage USDC balance caching, guard conditions, and deployment tracking."""

    def __init__(self, clob: "ClobWrapper", store: "PositionStore", config: "Settings"):
        """Initialize balance manager.

        Args:
            clob: ClobWrapper instance for CLOB API calls
            store: PositionStore instance for accessing open positions
            config: Settings instance with balance thresholds and constraints
        """
        self._clob = clob
        self._store = store
        self._config = config
        self._logger = setup_logger("polyphemus.balance")

        # Cache state
        self._cached_balance: float = 0.0
        self._cache_time: float = 0.0

        # Simulated balance for accumulator dry-run mode only
        self._sim_balance: float = config.dry_run_balance if config.accum_dry_run else 0.0

    async def get_balance(self) -> float:
        """Get USDC balance from CLOB, with caching.

        Checks cache before fetching. Returns cached balance if within TTL.
        Otherwise fetches from CLOB, updates cache, and returns.
        In DRY_RUN mode, returns simulated balance for sizing tests.

        Returns:
            USDC balance as float (e.g., 162.07)
        """
        if self._config.accum_dry_run:
            # Use sim balance only when the ACCUMULATOR is in dry-run mode.
            # Bug fix: previously checked config.dry_run (signal bot flag), which caused
            # get_balance() to return fake $900 sim balance whenever signal bot was set to
            # DRY_RUN=true — even though the accumulator was live. Accumulator over-sized
            # orders against the real ~$490 wallet and caused "not enough balance" spam.
            self._cached_balance = self._sim_balance
            self._cache_time = time.time()
            return self._sim_balance

        now = time.time()
        if now - self._cache_time < BALANCE_CACHE_TTL:
            return self._cached_balance

        # Fetch fresh balance
        balance = await self._clob.get_balance()
        self._cached_balance = balance
        self._cache_time = now

        return balance

    def sim_deduct(self, amount: float):
        """Deduct from simulated balance (dry-run fills)."""
        self._sim_balance -= amount
        self._logger.info(f"[SIM] Deducted ${amount:.2f} | balance=${self._sim_balance:.2f}")

    def sim_credit(self, amount: float):
        """Credit to simulated balance (dry-run settlements/unwinds)."""
        self._sim_balance += amount
        self._logger.info(f"[SIM] Credited ${amount:.2f} | balance=${self._sim_balance:.2f}")

    async def get_available(self) -> float:
        """Get available balance after subtracting deployed capital.

        Available = Balance - (sum of entry_price * entry_size for all open positions)

        Returns:
            Available USDC balance, minimum 0.0
        """
        balance = await self.get_balance()

        deployed = sum(
            pos.entry_price * pos.entry_size
            for pos in self._store.get_open()
        )

        available = balance - deployed
        return max(0.0, available)

    async def get_available_for_momentum(self) -> float:
        """Available capital for momentum strategy (total - deployed_momentum - accum_reserved)."""
        balance = await self.get_balance()
        momentum_deployed = sum(
            pos.entry_price * pos.entry_size
            for pos in self._store.get_open()
            if not (pos.metadata and pos.metadata.get("is_accumulator", False))
        )
        accum_reserved = balance * self._config.accum_capital_pct
        return max(0.0, balance - momentum_deployed - accum_reserved)

    async def get_available_for_accumulator(self) -> float:
        """Available capital for accumulator (reserved_pct * balance - accum_deployed)."""
        if not self._config.enable_accumulator:
            return 0.0
        if self._config.accum_capital_pct > 0.60:
            raise ValueError(
                f"ACCUM_CAPITAL_PCT={self._config.accum_capital_pct} exceeds 0.60 safety limit. "
                "A debug value of 0.90 amplified bugs into $500 losses. "
                "Set ACCUM_CAPITAL_PCT <= 0.60 in .env before starting."
            )
        balance = await self.get_balance()
        accum_reserved = balance * self._config.accum_capital_pct
        accum_deployed = sum(
            pos.entry_price * pos.entry_size
            for pos in self._store.get_open()
            if pos.metadata.get("is_accumulator", False)
        )
        return max(0.0, accum_reserved - accum_deployed)

    def get_deployment_ratio(self) -> float:
        """Get ratio of deployed capital to total capital.

        Deployed = sum of entry_price * entry_size for all open positions
        Total = Deployed + Cached Balance

        Returns:
            Ratio between 0.0 and 1.0, or 0.0 if total is zero
        """
        deployed = sum(
            pos.entry_price * pos.entry_size
            for pos in self._store.get_open()
        )

        total = deployed + self._cached_balance

        if total <= 0:
            return 0.0

        return deployed / total

    async def is_safe_to_trade(self) -> bool:
        """Check if trading is safe given current balance and deployment.

        Safe trading requires:
        1. Balance >= low_balance_threshold
        2. Deployment ratio < max_deployment_ratio

        Returns:
            True if safe to trade, False otherwise (logs warnings)
        """
        balance = await self.get_balance()

        # Check low balance guard
        if balance < self._config.low_balance_threshold:
            self._logger.warning(
                f"Low balance guard: ${balance:.2f} < ${self._config.low_balance_threshold:.2f}"
            )
            return False

        # Check deployment ratio guard
        ratio = self.get_deployment_ratio()
        if ratio >= self._config.max_deployment_ratio:
            self._logger.warning(
                f"Deployment ratio too high: {ratio:.2%} >= {self._config.max_deployment_ratio:.2%}"
            )
            return False

        return True

    async def reconcile_at_startup(self) -> bool:
        """Reconcile wallet state at startup.

        Logs wallet balance, position notional value, and total deployed capital.
        Uses assert_wallet_reconciliation to validate balance expectations.

        Returns:
            True if reconciliation passes (or passes with warning)
            False if critical mismatch (currently returns True for all cases)
        """
        wallet_balance = await self.get_balance()

        position_notional = sum(
            pos.entry_price * pos.entry_size
            for pos in self._store.get_open()
        )

        deployed_capital = wallet_balance + position_notional

        self._logger.info(
            f"Wallet reconciliation: balance=${wallet_balance:.2f}, "
            f"positions=${position_notional:.2f}, total=${deployed_capital:.2f}"
        )

        # Validate reconciliation (logs warning if mismatch, doesn't crash)
        assert_wallet_reconciliation(
            wallet_balance=wallet_balance,
            position_notional=position_notional,
            deployed_capital=deployed_capital,
        )

        # Live-mode safety gate: block trading if balance is suspicious
        if not self._config.dry_run:
            if wallet_balance <= 0:
                self._logger.critical(
                    "LIVE MODE: Balance is $0 — likely auth/signature_type error. "
                    "Trading will be HALTED (exits still allowed)."
                )
                return False
            if wallet_balance < 10.0:
                self._logger.critical(
                    f"LIVE MODE: Balance ${wallet_balance:.2f} below $10 safety threshold. "
                    "Trading will be HALTED (exits still allowed)."
                )
                return False

        return True

    async def reconcile_trades(self, db: "PerformanceDB") -> tuple:
        """Compare CLOB trade history against DB for last N hours.

        Returns:
            (passed: bool, message: str)
            passed=False means trading should be halted (CRITICAL drift).
        """
        import os
        lookback_hours = int(os.getenv("AUDIT_LOOKBACK_HOURS", "24"))
        try:
            clob_trades = await self._clob.get_recent_trades(hours=lookback_hours)
        except Exception as e:
            msg = f"CLOB trade fetch failed ({e}) — skipping audit (balance check is backup)"
            self._logger.warning(msg)
            return (True, msg)

        # Count distinct MAKER order IDs from our wallet across all taker trades.
        # The CLOB trade API returns TAKER-side records. Our maker orders appear in
        # the nested `maker_orders` array. One maker order can be split across
        # multiple taker fills (multiple CLOB records per our fill), so counting
        # taker record IDs inflates the count vs DB entries.
        my_addr = os.getenv("WALLET_ADDRESS", "").lower()
        clob_order_ids = set()
        for t in clob_trades:
            # Check maker_orders for our wallet's contributions
            for mo in t.get("maker_orders", []):
                if mo.get("maker_address", "").lower() == my_addr:
                    oid = mo.get("order_id", "")
                    if oid:
                        clob_order_ids.add(oid)
            # Also handle case where we are the taker (rare with post_only, but fallback)
            if t.get("maker_address", "").lower() == my_addr:
                oid = t.get("order_id") or t.get("id", "")
                if oid:
                    clob_order_ids.add(oid)
        clob_count = len(clob_order_ids)

        # Count DB entries in last N hours
        cutoff = time.time() - (lookback_hours * 3600)
        conn = db._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE entry_time > ? AND entry_time IS NOT NULL",
                (cutoff,),
            ).fetchone()
            db_count = row[0] if row else 0
        finally:
            conn.close()

        window = f"{lookback_hours}h"

        # Both zero = fresh bot, pass
        if clob_count == 0 and db_count == 0:
            return (True, f"No trades in last {window} (CLOB=0, DB=0)")

        # CLOB has trades but DB has none = data loss
        if clob_count > 0 and db_count == 0:
            msg = (
                f"CRITICAL: CLOB has {clob_count} trades in {window} but DB has 0 — "
                f"possible data loss (Bug #42 scenario)"
            )
            return (False, msg)

        ratio = db_count / clob_count if clob_count > 0 else 1.0

        if ratio < 0.50:
            msg = (
                f"CRITICAL: DB has {db_count} trades vs CLOB {clob_count} in {window} "
                f"(ratio={ratio:.1%} < 50%) — halting trading"
            )
            return (False, msg)

        if ratio < 0.80:
            msg = (
                f"WARNING: DB has {db_count} trades vs CLOB {clob_count} in {window} "
                f"(ratio={ratio:.1%} < 80%) — continuing with caution"
            )
            self._logger.warning(msg)
            return (True, msg)

        msg = f"OK: DB={db_count}, CLOB={clob_count} trades in {window} (ratio={ratio:.1%})"
        return (True, msg)
