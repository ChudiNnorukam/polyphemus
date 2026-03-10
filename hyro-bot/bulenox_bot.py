#!/usr/bin/env python3
"""
Bulenox MBT Momentum Bot
Signal    : Binance spot WS, 0.28%/60s BTC momentum (proven lagbot signal)
Execution : CME Micro Bitcoin Futures (MBT) via Rithmic (async_rithmic)
Firm      : Bulenox $50K Option 1 — $3,000 target, $2,500 trailing drawdown

Setup:
  pip install async-rithmic aiohttp python-dotenv
  cp .env.example .env  # fill Rithmic credentials from Bulenox dashboard
  python bulenox_bot.py

Rithmic credentials come from your Bulenox dashboard after signup.
Start with ORDER_QTY=2. Scale toward 7 after $500+ profit.

MBT tick math:
  1 tick = $5.00/BTC price move. MBT = 0.1 BTC.
  P&L per tick per contract = 0.1 × $5 = $0.50
  0.5% SL at $95K BTC = $475 move = 95 ticks = $47.50/contract risk
"""

import asyncio
import aiohttp
import json
import logging
import os
import time
from collections import deque
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from async_rithmic import RithmicClient, OrderType, TransactionType

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bulenox")

# ─── Config ───────────────────────────────────────────────────────────────────
RITHMIC_USER        = os.getenv("RITHMIC_USER")
RITHMIC_PASSWORD    = os.getenv("RITHMIC_PASSWORD")
RITHMIC_SYSTEM_NAME = os.getenv("RITHMIC_SYSTEM_NAME", "Rithmic Paper Trading")
RITHMIC_APP_NAME    = os.getenv("RITHMIC_APP_NAME", "BulenoxMBTBot")
RITHMIC_APP_VERSION = os.getenv("RITHMIC_APP_VERSION", "1.0")
RITHMIC_URL         = os.getenv("RITHMIC_URL", "rituz00100.rithmic.com:443")
RITHMIC_ACCOUNT_ID  = os.getenv("RITHMIC_ACCOUNT_ID", "")   # leave blank if only 1 account

ORDER_QTY        = int(os.getenv("ORDER_QTY", "2"))          # start at 2, scale to 7
MAX_CONTRACTS    = int(os.getenv("MAX_CONTRACTS", "7"))       # Bulenox $50K limit

MOMENTUM_TRIGGER = float(os.getenv("MOMENTUM_TRIGGER_PCT", "0.0028"))
MOMENTUM_WINDOW  = int(os.getenv("MOMENTUM_WINDOW_SECS", "60"))
ENTRY_COOLDOWN   = int(os.getenv("ENTRY_COOLDOWN_SECS", "120"))
MAX_HOLD_SECS    = int(os.getenv("MAX_HOLD_SECS", "360"))

SL_PCT  = float(os.getenv("SL_PCT", "0.005"))    # 0.5% — 95 ticks at $95K BTC
TP_PCT  = float(os.getenv("TP_PCT", "0.010"))    # 1.0% — 2:1 R:R

# Bulenox $50K Option 1 rules
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "-1100"))   # $1,100/day
MAX_DRAWDOWN     = float(os.getenv("MAX_DRAWDOWN", "-2500"))        # $2,500 trailing

DRY_RUN     = os.getenv("DRY_RUN", "true").lower() == "true"
KILL_SWITCH = os.getenv("KILL_SWITCH_FILE", "/tmp/BULENOX_KILL")

TRADING_START_CT  = os.getenv("TRADING_START_CST", "08:00")
TRADING_CUTOFF_CT = os.getenv("TRADING_CUTOFF_CST", "15:50")

TICK_SIZE  = 5.0   # $5.00 per BTC per tick (CME MBT spec)
SPOT_WS    = "wss://stream.binance.com:9443/ws/btcusdt@kline_1s"

_CT = ZoneInfo("America/Chicago")

def _is_trading_hours() -> bool:
    """Return True only during peak CME volume window (avoids 3:59pm CT hard close rule)."""
    now = datetime.now(_CT).time()
    h0, m0 = map(int, TRADING_START_CT.split(":"))
    h1, m1 = map(int, TRADING_CUTOFF_CT.split(":"))
    return dtime(h0, m0) <= now <= dtime(h1, m1)


# ─── Risk Guard ───────────────────────────────────────────────────────────────
class RiskGuard:
    """
    Enforces Bulenox $50K Option 1 rules.
    P&L is updated via Rithmic account PnL subscription (on_account_pnl_update).
    """

    def __init__(self):
        self._last_signal_ts: float = 0.0
        self._day = date.today()
        self._daily_pnl: float = 0.0
        self._total_pnl: float = 0.0

    def _check_day_reset(self):
        today = date.today()
        if today != self._day:
            self._daily_pnl = 0.0
            self._day = today

    def update_pnl(self, daily: float, total: float):
        """Called by Rithmic account PnL update event."""
        self._daily_pnl = daily
        self._total_pnl = total

    def kill_switch_active(self) -> bool:
        return os.path.exists(KILL_SWITCH)

    def can_trade(self) -> tuple[bool, str]:
        self._check_day_reset()
        if self.kill_switch_active():
            return False, "KILL_SWITCH"
        if self._daily_pnl <= DAILY_LOSS_LIMIT:
            return False, f"daily loss limit ({self._daily_pnl:.2f} <= {DAILY_LOSS_LIMIT})"
        if self._total_pnl <= MAX_DRAWDOWN:
            return False, f"max drawdown ({self._total_pnl:.2f} <= {MAX_DRAWDOWN})"
        return True, ""

    def cooldown_ok(self) -> bool:
        return (time.time() - self._last_signal_ts) >= ENTRY_COOLDOWN

    def mark_entry(self):
        self._last_signal_ts = time.time()

    def effective_qty(self) -> int:
        """Return ORDER_QTY capped at MAX_CONTRACTS."""
        return min(ORDER_QTY, MAX_CONTRACTS)


# ─── Momentum Detector ────────────────────────────────────────────────────────
class MomentumDetector:
    """
    Consumes Binance 1s klines. Emits (direction, price) when BTC moves
    >= MOMENTUM_TRIGGER within MOMENTUM_WINDOW seconds.
    Same logic as lagbot / polyphemus binance_momentum.py.
    """

    def __init__(self, on_signal):
        self._prices: deque[tuple[float, float]] = deque()
        self._last_price: float = 0.0
        self._on_signal = on_signal

    @property
    def last_price(self) -> float:
        return self._last_price

    async def run(self):
        while True:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.ws_connect(SPOT_WS) as ws:
                        log.info("Binance WS connected")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                k = data.get("k", {})
                                if k.get("x"):
                                    price = float(k["c"])
                                    await self._update(price)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as exc:
                log.warning("Binance WS error: %s — reconnecting in 3s", exc)
                await asyncio.sleep(3)

    async def _update(self, price: float):
        now = time.time()
        self._last_price = price
        self._prices.append((now, price))

        cutoff = now - MOMENTUM_WINDOW
        while self._prices and self._prices[0][0] < cutoff:
            self._prices.popleft()

        if len(self._prices) < 2:
            return

        oldest = self._prices[0][1]
        pct = (price - oldest) / oldest

        if abs(pct) >= MOMENTUM_TRIGGER:
            direction = "LONG" if pct > 0 else "SHORT"
            await self._on_signal(direction, price)


# ─── Rithmic Executor ─────────────────────────────────────────────────────────
class RithmicExecutor:
    """Wraps async_rithmic client for MBT order placement and position tracking."""

    def __init__(self, guard: RiskGuard):
        self._guard = guard
        self._client: RithmicClient | None = None
        self._security_code: str | None = None
        self._in_position: bool = False
        self._entry_time: float = 0.0
        self._order_counter: int = 0

    def _make_order_id(self) -> str:
        self._order_counter += 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"mbt_{ts}_{self._order_counter}"

    def _price_to_ticks(self, price: float, pct: float) -> int:
        """Convert a % move at a given BTC price to Rithmic tick count."""
        dollar_move = price * pct
        return max(1, int(round(dollar_move / TICK_SIZE)))

    async def connect(self):
        if not RITHMIC_USER or not RITHMIC_PASSWORD:
            raise ValueError("RITHMIC_USER and RITHMIC_PASSWORD must be set in .env")

        self._client = RithmicClient(
            user=RITHMIC_USER,
            password=RITHMIC_PASSWORD,
            system_name=RITHMIC_SYSTEM_NAME,
            app_name=RITHMIC_APP_NAME,
            app_version=RITHMIC_APP_VERSION,
            url=RITHMIC_URL,
        )

        # Wire up PnL events for risk tracking
        self._client.on_account_pnl_update += self._on_pnl_update
        # Wire fill/reject/cancel notifications (CRITICAL-3: clear position on rejection)
        self._client.on_exchange_order_notification += self._on_order_update

        await self._client.connect()
        log.info("Rithmic connected (system=%s)", RITHMIC_SYSTEM_NAME)

        # Subscribe to live P&L updates
        account_id = RITHMIC_ACCOUNT_ID or None
        await self._client.subscribe_to_pnl_updates()
        log.info("P&L subscription active")

        # Resolve front month MBT contract (auto — no quarterly updates needed)
        self._security_code = await self._client.get_front_month_contract("MBT", "CME")
        log.info("Front month contract: %s", self._security_code)

    def _on_pnl_update(self, update):
        """
        Rithmic account-level P&L event.
        Fields: closed_position_pnl (realized) + open_position_pnl (unrealized).
        """
        try:
            daily = float(getattr(update, "closed_position_pnl", 0) or 0)
            unrealized = float(getattr(update, "open_position_pnl", 0) or 0)
            total = daily + unrealized
            log.debug(
                "PnL update: daily=%.2f unrealized=%.2f total=%.2f",
                daily, unrealized, total,
            )
            self._guard.update_pnl(daily, total)
        except Exception as exc:
            log.debug("PnL update parse error: %s", exc)

    async def place_bracket(self, direction: str, price: float) -> bool:
        """
        Place market order + bracket (SL + TP) on MBT.
        stop_ticks / target_ticks computed from live BTC spot price.
        Returns True if order was submitted.
        """
        qty = self._guard.effective_qty()
        stop_ticks   = self._price_to_ticks(price, SL_PCT)
        target_ticks = self._price_to_ticks(price, TP_PCT)
        txn_type = TransactionType.BUY if direction == "LONG" else TransactionType.SELL
        order_id = self._make_order_id()

        risk_per_contract = stop_ticks * (TICK_SIZE * 0.1)  # 0.1 BTC per contract
        log.info(
            "%s %dx%s | stop=%d ticks ($%.2f/contract) target=%d ticks",
            direction, qty, self._security_code, stop_ticks, risk_per_contract, target_ticks,
        )

        if DRY_RUN:
            log.info("[DRY_RUN] Would submit order_id=%s", order_id)
            self._in_position = True
            self._entry_time = time.time()
            return True

        account_id = RITHMIC_ACCOUNT_ID or None
        await self._client.submit_order(
            order_id,
            self._security_code,
            "CME",
            qty=qty,
            order_type=OrderType.MARKET,
            transaction_type=txn_type,
            stop_ticks=stop_ticks,
            target_ticks=target_ticks,
            account_id=account_id,
        )

        self._in_position = True
        self._entry_time = time.time()
        return True

    async def check_position_closed(self) -> bool:
        """
        Poll Rithmic for net position in MBT.
        Returns True if flat (SL or TP hit).
        """
        if DRY_RUN:
            return False  # dry run never auto-closes
        try:
            summary = await self._client.list_account_summary(
                account_id=RITHMIC_ACCOUNT_ID or None
            )
            if summary:
                # net_quantity field from proto
                net = int(getattr(summary[0], "net_quantity", 0) or 0)
                return net == 0
        except Exception as exc:
            log.debug("Position check error: %s", exc)
        return False

    @property
    def in_position(self) -> bool:
        return self._in_position

    @property
    def entry_time(self) -> float:
        return self._entry_time

    def clear_position(self):
        self._in_position = False
        self._entry_time = 0.0

    def _on_order_update(self, update):
        """
        Rithmic order notification. Clears position state on reject/cancel so
        the bot doesn't stay locked for MAX_HOLD_SECS on a rejected entry.
        """
        status = str(getattr(update, "status", "") or "").upper()
        if status in ("REJECT", "CANCEL", "CANCELLED", "FAILED"):
            if self._in_position:
                log.warning("Order %s — clearing position state", status)
                self._in_position = False
                self._entry_time = 0.0

    async def cancel_all(self, account_id: str | None = None):
        """Cancel all resting orders (called before clear_position on timeout)."""
        await self._client.cancel_all_orders(account_id=account_id)

    async def disconnect(self):
        if self._client:
            await self._client.disconnect()


# ─── Bot Orchestrator ─────────────────────────────────────────────────────────
class BulenoxBot:

    def __init__(self):
        self._guard    = RiskGuard()
        self._executor = RithmicExecutor(self._guard)
        self._detector = MomentumDetector(on_signal=self._on_signal)

    async def _on_signal(self, direction: str, price: float):
        if not _is_trading_hours():
            return
        ok, reason = self._guard.can_trade()
        if not ok:
            log.info("Signal %s BLOCKED: %s", direction, reason)
            return
        if not self._guard.cooldown_ok():
            return
        if self._executor.in_position:
            return

        log.info("SIGNAL %s @ $%.2f", direction, price)
        self._guard.mark_entry()
        await self._executor.place_bracket(direction, price)

    async def _position_monitor(self):
        """
        Every 10s: check if position closed by SL/TP.
        Force-exit after MAX_HOLD_SECS (same as lagbot).
        """
        while True:
            await asyncio.sleep(10)
            if not self._executor.in_position:
                continue

            held = time.time() - self._executor.entry_time
            if held > MAX_HOLD_SECS:
                log.warning("MAX_HOLD_SECS exceeded (%.0fs) — cancelling all orders", held)
                if not DRY_RUN:
                    try:
                        await self._executor.cancel_all(RITHMIC_ACCOUNT_ID or None)
                    except Exception as exc:
                        log.error("cancel_all failed: %s — flatten manually in R|Trader", exc)
                self._executor.clear_position()
                continue

            closed = await self._executor.check_position_closed()
            if closed:
                log.info("Position closed (SL/TP hit)")
                self._executor.clear_position()

    async def run(self):
        log.info(
            "BulenoxBot starting | DRY_RUN=%s QTY=%d MAX_QTY=%d SL=%.1f%% TP=%.1f%%",
            DRY_RUN, ORDER_QTY, MAX_CONTRACTS, SL_PCT * 100, TP_PCT * 100,
        )
        log.info(
            "Bulenox $50K limits: daily_loss=$%.0f drawdown=$%.0f",
            DAILY_LOSS_LIMIT, MAX_DRAWDOWN,
        )

        await self._executor.connect()

        try:
            await asyncio.gather(
                self._detector.run(),
                self._position_monitor(),
            )
        finally:
            await self._executor.disconnect()


if __name__ == "__main__":
    asyncio.run(BulenoxBot().run())
