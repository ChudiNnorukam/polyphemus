#!/usr/bin/env python3
"""
HyroTrader ByBit Challenge Bot — BTC Momentum on ByBit USDT Perpetuals

Reuses the same 60s/0.28% momentum signal as the Polymarket lagbot.
Executes on ByBit USDT-M perps via ByBit V5 API.

Confirmed with HyroTrader support (2026-02-21):
  - CLEO platform does NOT expose API keys (UI-only).
  - ByBit demo accounts DO provide API keys for custom bots.
  - ByBit DEMO works from US IP addresses (demo = no real capital = no law violation).
  - API must be created with "No IP restrictions" in ByBit settings.

HyroTrader rules enforced automatically:
  R1. SL+TP set inline on the entry order — single atomic API call, no await gap.
  R2. Max $150 loss/trade — RISK_PER_TRADE_USD defaults to $50 (3x safety margin).
  R3. Daily loss halt: daily P&L <= -$250 (5% of $5K) stops all entries.
  R4. Total drawdown halt: total P&L <= -$500 (10% of $5K) stops all entries.
  R5. 40% single-trade profit cap — TP price reduced when needed.

Setup:
  1. Buy HyroTrader $5K ByBit challenge at my.hyrotrader.com/new-challenge
     (select ByBit, NOT CLEO — ByBit gives API keys)
  2. In challenge dashboard, generate ByBit demo API key with "No IP restrictions"
  3. cp .env.example .env  && fill in BYBIT_API_KEY / BYBIT_API_SECRET
  4. python hyro_bot.py          # DRY_RUN=true first (paper mode)
  5. DRY_RUN=false python hyro_bot.py  # go live
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from collections import deque
from typing import Optional

import aiohttp
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv()

API_KEY    = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
SYMBOL     = os.getenv("SYMBOL", "BTCUSDT")
LEVERAGE   = int(os.getenv("LEVERAGE", "10"))
DRY_RUN    = os.getenv("DRY_RUN", "true").lower() == "true"

# Signal (same tuning as lagbot — proven on 23 BTC trades, 69.6% WR)
MOMENTUM_TRIGGER_PCT = float(os.getenv("MOMENTUM_TRIGGER_PCT", "0.0028"))
MOMENTUM_WINDOW_SECS = int(os.getenv("MOMENTUM_WINDOW_SECS", "60"))
ENTRY_COOLDOWN_SECS  = int(os.getenv("ENTRY_COOLDOWN_SECS", "120"))
MAX_HOLD_SECS        = int(os.getenv("MAX_HOLD_SECS", "270"))  # exit 30s before 5m boundary

# Risk (HyroTrader $5K two-step rules — DO NOT raise RISK_PER_TRADE_USD above 150)
INITIAL_BALANCE    = float(os.getenv("INITIAL_BALANCE", "5000"))
RISK_PER_TRADE_USD = float(os.getenv("RISK_PER_TRADE_USD", "50"))   # $50 risk/trade
SL_PCT             = float(os.getenv("SL_PCT", "0.005"))             # 0.5% SL from entry
TP_PCT             = float(os.getenv("TP_PCT", "0.005"))             # 0.5% TP (1:1 R:R)
DAILY_LOSS_LIMIT   = float(os.getenv("DAILY_LOSS_LIMIT", "-250"))    # halt at -$250/day (5%)
MAX_DRAWDOWN       = float(os.getenv("MAX_DRAWDOWN", "-500"))        # halt at -$500 total (10%)

# ByBit endpoints
BYBIT_DEMO_BASE = "https://api-demo.bybit.com"   # demo/challenge account
BYBIT_LIVE_BASE = "https://api.bybit.com"         # real account (not used for challenge)
BASE_URL        = os.getenv("BYBIT_BASE_URL", BYBIT_DEMO_BASE)

# Binance spot WS for price signal (public, no auth, works in US)
SPOT_WS = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@kline_1s"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hyro")


# ── ByBit V5 REST client ──────────────────────────────────────────────────────

class BybitFutures:
    """
    Signed REST client for ByBit V5 API (USDT linear perpetuals).
    Demo URL: https://api-demo.bybit.com
    Docs: https://bybit-exchange.github.io/docs/v5/intro
    """

    RECV_WINDOW = "5000"

    def __init__(self, api_key: str, api_secret: str, base_url: str = BYBIT_DEMO_BASE):
        self._key    = api_key
        self._secret = api_secret.encode()
        self._base   = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _sign(self, timestamp: str, payload: str) -> str:
        """ByBit V5 signature: HMAC-SHA256(ts + api_key + recv_window + payload)."""
        msg = timestamp + self._key + self.RECV_WINDOW + payload
        return hmac.new(self._secret, msg.encode(), hashlib.sha256).hexdigest()

    def _headers(self, timestamp: str, signature: str) -> dict:
        return {
            "X-BAPI-API-KEY":     self._key,
            "X-BAPI-SIGN":        signature,
            "X-BAPI-SIGN-TYPE":   "2",
            "X-BAPI-TIMESTAMP":   timestamp,
            "X-BAPI-RECV-WINDOW": self.RECV_WINDOW,
            "Content-Type":       "application/json",
        }

    async def _get(self, path: str, params: dict = None) -> dict:
        qs = urllib.parse.urlencode(params or {})
        ts = str(int(time.time() * 1000))
        sig = self._sign(ts, qs)
        session = await self._get_session()
        url = f"{self._base}{path}?{qs}" if qs else f"{self._base}{path}"
        async with session.get(url, headers=self._headers(ts, qs)) as r:
            return await r.json()

    async def _post(self, path: str, body: dict) -> dict:
        payload = json.dumps(body)
        ts = str(int(time.time() * 1000))
        sig = self._sign(ts, payload)
        session = await self._get_session()
        async with session.post(
            f"{self._base}{path}",
            data=payload,
            headers=self._headers(ts, sig),
        ) as r:
            return await r.json()

    async def get_balance(self) -> float:
        """Return USDT equity in the unified/contract wallet."""
        data = await self._get("/v5/account/wallet-balance", {
            "accountType": "CONTRACT",
            "coin": "USDT",
        })
        try:
            for item in data["result"]["list"]:
                for coin in item.get("coin", []):
                    if coin["coin"] == "USDT":
                        return float(coin["walletBalance"])
        except (KeyError, TypeError):
            pass
        return 0.0

    async def get_mark_price(self) -> float:
        """Current mark price for SYMBOL."""
        data = await self._get("/v5/market/tickers", {
            "category": "linear",
            "symbol": SYMBOL,
        })
        try:
            return float(data["result"]["list"][0]["markPrice"])
        except (KeyError, IndexError, TypeError):
            return 0.0

    async def set_leverage(self, leverage: int) -> None:
        resp = await self._post("/v5/position/set-leverage", {
            "category":    "linear",
            "symbol":      SYMBOL,
            "buyLeverage":  str(leverage),
            "sellLeverage": str(leverage),
        })
        if resp.get("retCode") == 0:
            log.info(f"Leverage set: {leverage}x on {SYMBOL}")
        else:
            log.warning(f"set_leverage response: {resp}")

    async def place_order(
        self,
        side: str,          # "Buy" or "Sell"
        qty: float,
        sl_price: float,
        tp_price: float,
    ) -> dict:
        """
        Place a market order with inline SL and TP.
        R1 compliant: SL+TP are set atomically in the same API call as entry.
        ByBit V5 supports stopLoss/takeProfit fields on the order itself.
        """
        body = {
            "category":    "linear",
            "symbol":      SYMBOL,
            "side":        side,
            "orderType":   "Market",
            "qty":         f"{qty:.3f}",
            "stopLoss":    f"{sl_price:.2f}",
            "takeProfit":  f"{tp_price:.2f}",
            "slTriggerBy": "MarkPrice",
            "tpTriggerBy": "MarkPrice",
            "timeInForce": "IOC",
        }
        return await self._post("/v5/order/create", body)

    async def close_position(self, side: str, qty: float) -> dict:
        """Market close — used for max-hold force exit."""
        close_side = "Sell" if side == "LONG" else "Buy"
        return await self._post("/v5/order/create", {
            "category":    "linear",
            "symbol":      SYMBOL,
            "side":        close_side,
            "orderType":   "Market",
            "qty":         f"{qty:.3f}",
            "reduceOnly":  True,
            "timeInForce": "IOC",
        })

    async def cancel_all_orders(self) -> dict:
        return await self._post("/v5/order/cancel-all", {
            "category": "linear",
            "symbol":   SYMBOL,
        })

    async def get_position(self) -> Optional[dict]:
        """Return current open position for SYMBOL, or None."""
        data = await self._get("/v5/position/list", {
            "category": "linear",
            "symbol":   SYMBOL,
        })
        try:
            for pos in data["result"]["list"]:
                if float(pos.get("size", 0)) > 0:
                    return pos
        except (KeyError, TypeError):
            pass
        return None

    async def get_closed_pnl(self) -> float:
        """Return realized P&L from the most recent closed trade."""
        data = await self._get("/v5/position/closed-pnl", {
            "category": "linear",
            "symbol":   SYMBOL,
            "limit":    "1",
        })
        try:
            return float(data["result"]["list"][0]["closedPnl"])
        except (KeyError, IndexError, TypeError):
            return 0.0

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ── Risk guard ────────────────────────────────────────────────────────────────

class RiskGuard:
    """Enforces all HyroTrader challenge rules in one place."""

    def __init__(self):
        self.total_pnl: float = 0.0
        self.daily_pnl: float = 0.0
        self._day: int = 0
        self.trade_count: int = 0

    def _maybe_reset_daily(self) -> None:
        today = int(time.time() // 86400)
        if today != self._day:
            log.info(f"Daily reset | day P&L: {self.daily_pnl:+.2f}")
            self.daily_pnl = 0.0
            self._day = today

    def record_trade(self, pnl: float) -> None:
        self._maybe_reset_daily()
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.trade_count += 1
        status = "WIN" if pnl > 0 else "LOSS"
        log.info(
            f"[{status}] P&L: {pnl:+.2f} | Day: {self.daily_pnl:+.2f} | "
            f"Total: {self.total_pnl:+.2f} | Trades: {self.trade_count}"
        )

    def is_trading_allowed(self) -> tuple[bool, str]:
        self._maybe_reset_daily()
        if self.daily_pnl <= DAILY_LOSS_LIMIT:
            return False, f"daily loss limit ({self.daily_pnl:.2f} <= {DAILY_LOSS_LIMIT})"
        if self.total_pnl <= MAX_DRAWDOWN:
            return False, f"max drawdown ({self.total_pnl:.2f} <= {MAX_DRAWDOWN})"
        return True, ""

    def capped_tp_usd(self, risk_usd: float) -> float:
        """
        R5: No single trade may be > 40% of total realized profit.
        max_win = total_pnl * 2/3  ensures win/(total+win) < 0.40
        Only applies when total_pnl > 0 and the win would breach the cap.
        """
        if self.total_pnl <= 0:
            return risk_usd
        max_win = (self.total_pnl * 2.0) / 3.0
        if risk_usd > max_win:
            log.info(f"40% cap: TP capped ${risk_usd:.2f} -> ${max_win:.2f}")
            return max_win
        return risk_usd


# ── Momentum detector ─────────────────────────────────────────────────────────

class MomentumDetector:
    """
    Binance spot kline_1s WS -> rolling 60s price buffer -> momentum signal.
    Same core logic as polyphemus/binance_momentum.py, stripped to BTC only.
    Uses Binance spot for the signal (public API, no auth, works from US).
    """

    def __init__(self, on_signal):
        self._buffer: deque = deque(maxlen=600)
        self._on_signal = on_signal
        self._last_signal_ts: float = 0.0
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws = None

    async def start(self) -> None:
        attempt = 0
        while True:
            try:
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(SPOT_WS, timeout=10)
                log.info(f"Binance spot WS connected")
                attempt = 0
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                delay = min(2 ** attempt, 30)
                log.warning(f"Spot WS error: {e}, retry in {delay}s")
                attempt += 1
                await asyncio.sleep(delay)
            finally:
                await self._close()

    async def _read_loop(self) -> None:
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    k = json.loads(msg.data).get("k", {})
                    price = float(k.get("c", 0))
                    if price > 0:
                        now = time.time()
                        self._buffer.append((now, price))
                        await self._check_momentum(now, price)
                except Exception as e:
                    log.debug(f"WS msg error: {e}")
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _check_momentum(self, now: float, current_price: float) -> None:
        cutoff = now - MOMENTUM_WINDOW_SECS
        oldest_price = next((p for ts, p in self._buffer if ts >= cutoff), None)
        if not oldest_price:
            return

        pct = (current_price - oldest_price) / oldest_price
        if abs(pct) < MOMENTUM_TRIGGER_PCT:
            return
        if abs(pct) > 0.05:  # flash-crash guard
            log.warning(f"Flash crash guard: {pct:+.3%} ignored")
            return
        if now - self._last_signal_ts < ENTRY_COOLDOWN_SECS:
            return

        direction = "LONG" if pct > 0 else "SHORT"
        log.info(
            f"Momentum: BTC {direction} {pct:+.3%} in {MOMENTUM_WINDOW_SECS}s "
            f"({oldest_price:.2f} -> {current_price:.2f})"
        )
        self._last_signal_ts = now
        await self._on_signal(direction, current_price)

    async def _close(self) -> None:
        for obj in (self._ws, self._session):
            try:
                if obj:
                    await obj.close()
            except Exception:
                pass
        self._ws = self._session = None


# ── Position tracker ──────────────────────────────────────────────────────────

class Position:
    def __init__(self, side: str, entry_price: float, quantity: float,
                 sl_price: float, tp_price: float):
        self.side        = side
        self.entry_price = entry_price
        self.quantity    = quantity
        self.sl_price    = sl_price
        self.tp_price    = tp_price
        self.opened_at   = time.time()
        self.deadline    = self.opened_at + MAX_HOLD_SECS


# ── Main bot ──────────────────────────────────────────────────────────────────

class HyroBot:

    def __init__(self):
        self._exchange  = BybitFutures(API_KEY, API_SECRET, BASE_URL)
        self._risk      = RiskGuard()
        self._detector  = MomentumDetector(on_signal=self._on_signal)
        self._position: Optional[Position] = None
        self._exit_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        log.info(
            f"HyroBot | DRY_RUN={DRY_RUN} | exchange={BASE_URL} | "
            f"trigger={MOMENTUM_TRIGGER_PCT*100:.2f}% / {MOMENTUM_WINDOW_SECS}s | "
            f"risk=${RISK_PER_TRADE_USD} SL={SL_PCT*100:.1f}% TP={TP_PCT*100:.1f}%"
        )
        if not DRY_RUN:
            if not API_KEY or not API_SECRET:
                raise RuntimeError("BYBIT_API_KEY / BYBIT_API_SECRET required in .env")
            balance = await self._exchange.get_balance()
            log.info(f"ByBit account balance: ${balance:.2f} USDT")
            await self._exchange.set_leverage(LEVERAGE)

        await self._detector.start()

    async def _on_signal(self, direction: str, signal_price: float) -> None:
        if self._position is not None:
            log.debug(f"Signal {direction} skipped — position already open")
            return

        allowed, reason = self._risk.is_trading_allowed()
        if not allowed:
            log.warning(f"Entry blocked: {reason}")
            return

        if DRY_RUN:
            notional = RISK_PER_TRADE_USD / SL_PCT
            qty = round(notional / signal_price, 3)
            sl = signal_price * (1 - SL_PCT) if direction == "LONG" else signal_price * (1 + SL_PCT)
            tp = signal_price * (1 + TP_PCT) if direction == "LONG" else signal_price * (1 - TP_PCT)
            log.info(
                f"[DRY RUN] {direction} {qty} BTC @ ~{signal_price:.2f} | "
                f"SL={sl:.2f} TP={tp:.2f} | risk=${qty*signal_price*SL_PCT:.2f}"
            )
            return

        await self._enter(direction, signal_price)

    async def _enter(self, direction: str, approx_price: float) -> None:
        # ── Size calculation ─────────────────────────────────────────────────
        notional = RISK_PER_TRADE_USD / SL_PCT        # e.g. $50/0.005 = $10,000
        qty      = round(notional / approx_price, 3)  # e.g. 0.104 BTC
        dollar_risk = qty * approx_price * SL_PCT

        # Hard guard: verify rule R2 ($150 max loss)
        if dollar_risk > 150:
            log.critical(f"ABORT: dollar_risk ${dollar_risk:.2f} > $150 — R2 breach prevented")
            return

        # ── SL / TP prices ───────────────────────────────────────────────────
        tp_usd  = self._risk.capped_tp_usd(RISK_PER_TRADE_USD)  # R5 cap
        tp_dist = TP_PCT * (tp_usd / RISK_PER_TRADE_USD)        # scale TP if capped

        if direction == "LONG":
            sl_price = round(approx_price * (1 - SL_PCT),  2)
            tp_price = round(approx_price * (1 + tp_dist), 2)
            bybit_side = "Buy"
        else:
            sl_price = round(approx_price * (1 + SL_PCT),  2)
            tp_price = round(approx_price * (1 - tp_dist), 2)
            bybit_side = "Sell"

        log.info(
            f"Entering {direction} | qty={qty} BTC @ ~{approx_price:.2f} | "
            f"SL={sl_price:.2f} TP={tp_price:.2f} | "
            f"margin=${notional/LEVERAGE:.0f} risk=${dollar_risk:.2f}"
        )

        # ── R1: SL+TP set atomically inside the entry order ──────────────────
        try:
            resp = await self._exchange.place_order(bybit_side, qty, sl_price, tp_price)

            if resp.get("retCode") != 0:
                log.error(f"Order failed: {resp.get('retMsg')} | full={resp}")
                return

            order_id    = resp["result"]["orderId"]
            entry_price = float(resp["result"].get("price") or approx_price)
            log.info(f"Order placed: {order_id} | entry~{entry_price:.2f}")

            self._position = Position(
                side=direction,
                entry_price=entry_price,
                quantity=qty,
                sl_price=sl_price,
                tp_price=tp_price,
            )
            self._exit_task = asyncio.create_task(self._max_hold_exit())

        except Exception as e:
            log.exception(f"Order placement error: {e}")
            self._position = None

    async def _max_hold_exit(self) -> None:
        """Force-close if MAX_HOLD_SECS elapses without SL/TP fill."""
        pos = self._position
        if not pos:
            return
        sleep = pos.deadline - time.time()
        if sleep > 0:
            await asyncio.sleep(sleep)
        if self._position is None:
            return  # already closed by SL/TP

        log.info(f"Max hold reached — force-closing {pos.side}")
        try:
            resp = await self._exchange.close_position(pos.side, pos.quantity)
            log.info(f"Force-close response: {resp.get('retCode')} {resp.get('retMsg')}")
            pnl = await self._exchange.get_closed_pnl()
            self._risk.record_trade(pnl)
        except Exception as e:
            log.error(f"Force-close error: {e}")
        finally:
            self._position = None

    async def _poll_position_loop(self) -> None:
        """Poll ByBit every 5s to detect SL/TP fills and record P&L."""
        while True:
            await asyncio.sleep(5)
            if self._position is None:
                continue
            try:
                open_pos = await self._exchange.get_position()
                if open_pos is not None:
                    continue  # still open

                # Position gone — SL or TP filled
                pnl = await self._exchange.get_closed_pnl()
                log.info(f"Position closed (SL/TP) | P&L: {pnl:+.2f}")
                self._risk.record_trade(pnl)

                if self._exit_task and not self._exit_task.done():
                    self._exit_task.cancel()
                self._position = None

            except Exception as e:
                log.debug(f"Position poll error: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    bot = HyroBot()
    await asyncio.gather(
        bot.start(),
        bot._poll_position_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
