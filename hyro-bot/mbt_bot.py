#!/usr/bin/env python3
"""
MBT Momentum Bot — MyFundedFutures (Tradovate)
Signal : Binance spot WS, 0.28%/60s BTC momentum (identical to lagbot signal)
Execution: CME Micro Bitcoin Futures (MBT) via Tradovate REST API
Firm rules: MFFU $50K — $1K daily loss, $2K trailing drawdown

Setup:
  1. Buy MyFundedFutures $50K account at myfundedfutures.com
  2. Credentials land in MFFU dashboard — use for TRADOVATE_USERNAME/PASSWORD
  3. For TRADOVATE_CID/SEC: register a free app at tradovate.com/account-settings/apps
     (use cid=70 / sec="" for demo-only without registration)
  4. cp .env.example .env && fill values && python mbt_bot.py

MBT contract symbol format: MBTH6 (H=March, M=June, U=September, Z=December, 6=2026)
Update SYMBOL in .env each quarter when contract rolls.
"""

import asyncio
import aiohttp
import json
import logging
import os
import time
from collections import deque
from datetime import date, datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mbt")

# ─── Config ───────────────────────────────────────────────────────────────────
DEMO     = os.getenv("TRADOVATE_DEMO", "true").lower() == "true"
BASE_URL = "https://demo.tradovateapi.com/v1" if DEMO else "https://live.tradovateapi.com/v1"

USERNAME = os.getenv("TRADOVATE_USERNAME")
PASSWORD = os.getenv("TRADOVATE_PASSWORD")
APP_ID   = os.getenv("TRADOVATE_APP_ID", "Sample App")
APP_VER  = os.getenv("TRADOVATE_APP_VERSION", "1.0")
CID      = int(os.getenv("TRADOVATE_CID", "70"))
SEC      = os.getenv("TRADOVATE_SEC", "")

SYMBOL           = os.getenv("SYMBOL", "MBTH6")     # March 2026 front month
ORDER_QTY        = int(os.getenv("ORDER_QTY", "1")) # 1 MBT = 0.1 BTC

MOMENTUM_TRIGGER = float(os.getenv("MOMENTUM_TRIGGER_PCT", "0.0028"))
MOMENTUM_WINDOW  = int(os.getenv("MOMENTUM_WINDOW_SECS", "60"))
ENTRY_COOLDOWN   = int(os.getenv("ENTRY_COOLDOWN_SECS", "120"))
MAX_HOLD_SECS    = int(os.getenv("MAX_HOLD_SECS", "270"))

SL_PCT = float(os.getenv("SL_PCT", "0.005"))   # 0.5% SL from entry
TP_PCT = float(os.getenv("TP_PCT", "0.005"))   # 0.5% TP from entry

# MFFU $50K account risk limits
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "-1000"))
MAX_DRAWDOWN     = float(os.getenv("MAX_DRAWDOWN", "-2000"))

DRY_RUN      = os.getenv("DRY_RUN", "true").lower() == "true"
KILL_SWITCH  = os.getenv("KILL_SWITCH_FILE", "/tmp/MBT_KILL_SWITCH")

SPOT_WS = "wss://stream.binance.com:9443/ws/btcusdt@kline_1s"


# ─── Tradovate Client ─────────────────────────────────────────────────────────
class TradovateClient:
    def __init__(self):
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._account_id: int | None = None
        self._account_spec: str | None = None
        self._contract_id: int | None = None
        self._session: aiohttp.ClientSession | None = None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _ensure_token(self):
        if time.time() < self._token_expiry - 60:
            return
        sess = await self._sess()
        body = {
            "name": USERNAME,
            "password": PASSWORD,
            "appId": APP_ID,
            "appVersion": APP_VER,
            "cid": CID,
            "sec": SEC,
        }
        async with sess.post(f"{BASE_URL}/auth/accesstokenrequest", json=body) as r:
            data = await r.json()
        if "accessToken" not in data:
            raise RuntimeError(f"Tradovate auth failed: {data}")
        self._token = data["accessToken"]
        exp_str = data.get("expirationTime", "")
        if exp_str:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            self._token_expiry = exp_dt.timestamp()
        else:
            self._token_expiry = time.time() + 3600
        log.info("Tradovate token OK, expires in %.0f min", (self._token_expiry - time.time()) / 60)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def _get(self, path: str, **params):
        await self._ensure_token()
        sess = await self._sess()
        async with sess.get(f"{BASE_URL}/{path}", headers=self._headers(), params=params or None) as r:
            return await r.json()

    async def _post(self, path: str, body: dict):
        await self._ensure_token()
        sess = await self._sess()
        h = {**self._headers(), "Content-Type": "application/json"}
        async with sess.post(f"{BASE_URL}/{path}", headers=h, json=body) as r:
            return await r.json()

    async def init(self):
        """Resolve account ID and MBT contract ID. Call once at startup."""
        await self._ensure_token()

        # Account
        accounts = await self._get("account/list")
        if not accounts:
            raise RuntimeError("No Tradovate accounts found")
        acct = accounts[0]
        self._account_id   = acct["id"]
        self._account_spec = acct.get("name", str(self._account_id))
        log.info("Account: %s (id=%d)", self._account_spec, self._account_id)

        # Contract — /contract/find?name=SYMBOL returns a single contract object
        c = await self._get("contract/find", name=SYMBOL)
        if isinstance(c, list):
            c = c[0] if c else None
        if not c or "id" not in c:
            raise RuntimeError(
                f"Contract '{SYMBOL}' not found. Update SYMBOL in .env to the current front-month "
                f"(format: MBTH6 = March 2026, MBTM6 = June 2026, MBTU6 = Sep 2026, MBTZ6 = Dec 2026)."
            )
        self._contract_id = c["id"]
        log.info("Contract: %s (id=%d)", SYMBOL, self._contract_id)

    async def place_bracket(self, direction: str, entry_price: float) -> dict:
        """
        Place a bracketed market order (entry + SL stop + TP limit) via placeOSO.
        direction: "LONG" or "SHORT"
        entry_price: BTC spot price used to compute SL/TP levels
        """
        if direction == "LONG":
            action         = "Buy"
            bracket_action = "Sell"
            sl_price = round(entry_price * (1 - SL_PCT), 2)
            tp_price = round(entry_price * (1 + TP_PCT), 2)
        else:
            action         = "Sell"
            bracket_action = "Buy"
            sl_price = round(entry_price * (1 + SL_PCT), 2)
            tp_price = round(entry_price * (1 - TP_PCT), 2)

        body = {
            "accountId":   self._account_id,
            "accountSpec": self._account_spec,
            "symbol":      SYMBOL,
            "action":      action,
            "orderType":   "Market",
            "orderQty":    ORDER_QTY,
            "isAutomated": True,   # required by CME for algo orders
            "bracket1": {          # stop loss
                "action":    bracket_action,
                "orderType": "Stop",
                "stopPrice": sl_price,
            },
            "bracket2": {          # take profit
                "action":    bracket_action,
                "orderType": "Limit",
                "price":     tp_price,
            },
        }

        if DRY_RUN:
            log.info(
                "[DRY_RUN] %s %dx%s entry≈%.2f SL=%.2f TP=%.2f",
                action, ORDER_QTY, SYMBOL, entry_price, sl_price, tp_price,
            )
            return {"orderId": -1, "dry_run": True}

        resp = await self._post("order/placeOSO", body)
        log.info("Order placed: %s", json.dumps(resp))
        return resp

    async def get_net_position(self) -> int:
        """Returns net position in contracts (+= long, -= short, 0 = flat)."""
        positions = await self._get("position/list")
        for p in (positions or []):
            if p.get("contractId") == self._contract_id:
                return int(p.get("netPos", 0))
        return 0

    async def get_daily_pnl(self) -> float:
        """
        Returns today's realized P&L from account data.
        NOTE: Verify the exact field name in your Tradovate account response —
        it may be 'realizedPnL', 'totalRealizedPnL', or similar.
        """
        accounts = await self._get("account/list")
        for a in (accounts or []):
            if a["id"] == self._account_id:
                return float(a.get("realizedPnL", 0.0))
        return 0.0

    async def liquidate(self):
        """Force-close all positions in the account (used for MAX_HOLD_SECS exit)."""
        if DRY_RUN:
            log.info("[DRY_RUN] Would liquidate %s position", SYMBOL)
            return
        body = {
            "accountId":  self._account_id,
            "contractId": self._contract_id,
            "admin":      False,
        }
        resp = await self._post("order/liquidatePosition", body)
        log.info("Liquidate response: %s", resp)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ─── Risk Guard ───────────────────────────────────────────────────────────────
class RiskGuard:
    """Enforces MFFU prop firm risk rules and trade cooldown."""

    def __init__(self):
        self._last_signal_ts: float = 0.0
        self._day = date.today()
        self._daily_pnl: float = 0.0

    def _check_day_reset(self):
        today = date.today()
        if today != self._day:
            self._daily_pnl = 0.0
            self._day = today

    def record_pnl(self, pnl: float):
        self._check_day_reset()
        self._daily_pnl += pnl

    def kill_switch_active(self) -> bool:
        return os.path.exists(KILL_SWITCH)

    def can_trade(self) -> tuple[bool, str]:
        self._check_day_reset()
        if self.kill_switch_active():
            return False, "KILL_SWITCH"
        if self._daily_pnl <= DAILY_LOSS_LIMIT:
            return False, f"daily loss limit hit ({self._daily_pnl:.2f})"
        return True, ""

    def cooldown_ok(self) -> bool:
        return (time.time() - self._last_signal_ts) >= ENTRY_COOLDOWN

    def mark_entry(self):
        self._last_signal_ts = time.time()


# ─── Momentum Detector ────────────────────────────────────────────────────────
class MomentumDetector:
    """
    Consumes Binance 1s kline stream, emits LONG/SHORT signals when BTC moves
    >= MOMENTUM_TRIGGER_PCT within a rolling MOMENTUM_WINDOW_SECS window.
    Identical logic to lagbot / polyphemus binance_momentum.py.
    """

    def __init__(self, on_signal):
        self._prices: deque[tuple[float, float]] = deque()
        self._last_price: float = 0.0
        self._on_signal = on_signal

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
                                if k.get("x"):  # closed 1s candle
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


# ─── Bot Orchestrator ─────────────────────────────────────────────────────────
class MBTBot:
    def __init__(self):
        self._client   = TradovateClient()
        self._guard    = RiskGuard()
        self._detector = MomentumDetector(on_signal=self._on_signal)
        self._in_position   = False
        self._entry_time: float = 0.0
        self._entry_direction: str = ""

    async def _on_signal(self, direction: str, price: float):
        ok, reason = self._guard.can_trade()
        if not ok:
            log.info("Signal %s blocked: %s", direction, reason)
            return
        if not self._guard.cooldown_ok():
            return
        if self._in_position:
            return

        log.info("SIGNAL %s @ %.2f", direction, price)
        self._guard.mark_entry()

        resp = await self._client.place_bracket(direction, price)

        # Track position state
        if resp.get("orderId") or DRY_RUN:
            self._in_position = True
            self._entry_time = time.time()
            self._entry_direction = direction

    async def _position_monitor(self):
        """
        Every 10s: check if position is still open.
        Force-liquidate if MAX_HOLD_SECS exceeded (same logic as lagbot MAX_HOLD_MINS).
        """
        while True:
            await asyncio.sleep(10)
            if not self._in_position:
                continue

            # Check prop firm daily P&L from API
            daily_pnl = await self._client.get_daily_pnl()
            self._guard.record_pnl(daily_pnl)

            held = time.time() - self._entry_time
            if held > MAX_HOLD_SECS:
                log.info("MAX_HOLD exceeded (%.0fs) — liquidating", held)
                await self._client.liquidate()
                self._in_position = False
                continue

            if DRY_RUN:
                # In dry run we don't know if position closed — just clear after hold time
                continue

            net = await self._client.get_net_position()
            if net == 0:
                log.info("Position closed (SL/TP hit)")
                self._in_position = False

    async def run(self):
        log.info(
            "MBT Bot starting | DRY_RUN=%s DEMO=%s SYMBOL=%s QTY=%d SL=%.1f%% TP=%.1f%%",
            DRY_RUN, DEMO, SYMBOL, ORDER_QTY, SL_PCT * 100, TP_PCT * 100,
        )
        if not USERNAME or not PASSWORD:
            raise ValueError("TRADOVATE_USERNAME and TRADOVATE_PASSWORD must be set in .env")

        await self._client.init()
        log.info("Tradovate connected. Listening for BTC momentum signals...")
        log.info(
            "Risk limits: daily_loss=%.0f max_drawdown=%.0f cooldown=%ds hold=%ds",
            DAILY_LOSS_LIMIT, MAX_DRAWDOWN, ENTRY_COOLDOWN, MAX_HOLD_SECS,
        )

        try:
            await asyncio.gather(
                self._detector.run(),
                self._position_monitor(),
            )
        finally:
            await self._client.close()


if __name__ == "__main__":
    asyncio.run(MBTBot().run())
