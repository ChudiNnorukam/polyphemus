import asyncio
import json
import logging
import os
import time
from datetime import datetime, time as dt_time
from typing import Optional
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")
_MARKET_OPEN = dt_time(9, 30)
_MARKET_CLOSE = dt_time(14, 50)  # 2:50pm ET = 3:50pm CST — 9-min buffer before Bulenox 3:59pm CST deadline

from config import BulenoxConfig
from binance_feed import BinanceFeed
from rithmic_client import RithmicClient
from trade_store import TradeStore

logger = logging.getLogger(__name__)


class Position:
    def __init__(self, basket_id: str, direction: str, signal_pct: float = 0.0):
        self.basket_id = basket_id
        self.direction = direction
        self.signal_pct = signal_pct
        self.entry_time = time.monotonic()
        self.entry_price: Optional[float] = None
        self.tp_price: Optional[float] = None
        self.sl_price: Optional[float] = None
        self.closed = False


MAX_CONSECUTIVE_LOSSES = 2


class BulenoxBot:
    def __init__(self, cfg: BulenoxConfig):
        self._cfg = cfg
        self._rithmic = RithmicClient(cfg, on_fill=self._on_fill, on_order_ack=self._on_order_ack)
        self._feed = BinanceFeed(
            symbol=cfg.binance_symbol,
            window_secs=cfg.momentum_window_secs,
            trigger_pct=cfg.momentum_trigger_pct,
            on_signal=self._on_signal,
            cooldown_secs=cfg.entry_cooldown_secs,
        )
        self._positions: dict[str, Position] = {}
        self._closing_orders: dict[str, Position] = {}
        self._pending_close: Optional[Position] = None
        self._consecutive_losses: int = 0
        self._halted: bool = False
        os.makedirs(cfg.data_dir, exist_ok=True)
        self._store = TradeStore(os.path.join(cfg.data_dir, "trades.db"))
        self._state_path = os.path.join(cfg.data_dir, "bot_state.json")
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            self._consecutive_losses = int(data.get("consecutive_losses", 0))
            self._halted = bool(data.get("halted", False))
            if self._halted:
                logger.warning(
                    f"Loaded halted state — bot is halted. "
                    f"Delete {self._state_path} or set halted=false to resume."
                )
        except Exception as e:
            logger.error(f"Failed to load state from {self._state_path}: {e}")

    def _save_state(self) -> None:
        try:
            with open(self._state_path, "w") as f:
                json.dump({"consecutive_losses": self._consecutive_losses, "halted": self._halted}, f)
        except Exception as e:
            logger.error(f"Failed to save state to {self._state_path}: {e}")

    def _seed_positions(self) -> None:
        open_trades = self._store.get_open_trades()
        for basket_id, direction, entry_price, entry_ts in open_trades:
            if entry_price is None:
                continue
            pos = Position(basket_id, direction)
            pos.entry_price = entry_price
            elapsed = max(0.0, time.time() - entry_ts) if entry_ts else 0.0
            pos.entry_time = time.monotonic() - elapsed
            self._positions[basket_id] = pos
            logger.info(f"Seeded open position from DB: basket_id={basket_id} elapsed={elapsed:.0f}s")
        if open_trades:
            logger.info(f"Seeded {len(open_trades)} open position(s) from DB on restart")

    async def start(self) -> None:
        self._store.setup()
        self._seed_positions()
        logger.info(
            f"BulenoxBot starting | symbol={self._cfg.symbol} exchange={self._cfg.exchange} "
            f"dry_run={self._cfg.dry_run}"
        )
        await asyncio.gather(
            self._rithmic.connect(),
            self._feed.start(),
            self._position_monitor(),
        )

    async def _on_signal(self, direction: str, pct: float) -> None:
        if self._cfg.kill_switch_path and os.path.exists(self._cfg.kill_switch_path):
            logger.warning("Kill switch active — no new trades")
            return

        if self._halted:
            logger.warning(f"Signal {direction} ignored: bot halted after {self._consecutive_losses} consecutive losses")
            return

        now_et = datetime.now(_EASTERN).time()
        if not (_MARKET_OPEN <= now_et < _MARKET_CLOSE):
            logger.info(f"Signal {direction} ignored: outside market hours ({now_et.strftime('%H:%M')} ET)")
            return

        today = datetime.now(_EASTERN).strftime("%Y-%m-%d")
        daily_pnl_usd = self._store.get_daily_pnl(today) * self._cfg.point_value * self._cfg.contracts
        if daily_pnl_usd <= -self._cfg.max_daily_loss_usd:
            logger.warning(f"Daily loss limit hit: ${daily_pnl_usd:.2f} — no new trades today")
            return

        total_pnl_usd = self._store.get_total_pnl() * self._cfg.point_value * self._cfg.contracts
        daily_profit_usd = max(0.0, daily_pnl_usd)
        if total_pnl_usd > 0 and daily_profit_usd / total_pnl_usd > self._cfg.max_daily_profit_ratio:
            logger.warning(
                f"Daily P&L cap: today ${daily_profit_usd:.2f} = "
                f"{daily_profit_usd/total_pnl_usd:.0%} of total ${total_pnl_usd:.2f} — Bulenox consistency rule buffer"
            )
            return

        open_count = sum(1 for p in self._positions.values() if not p.closed)
        if open_count >= self._cfg.max_open_positions:
            logger.info(f"Signal {direction} ignored: max_open_positions reached ({open_count})")
            return

        if self._cfg.dry_run:
            logger.info(f"[DRY RUN] Signal {direction} {pct:.4%} -- would place {self._cfg.contracts} {self._cfg.symbol}")
            basket_id = await self._rithmic.place_order("BUY" if direction == "UP" else "SELL")
            pos = Position(basket_id, direction, signal_pct=pct)
            self._positions[basket_id] = pos
            logger.info(f"[DRY RUN] Opened position basket_id={basket_id}")
            return

        if not self._rithmic._ready.is_set():
            logger.warning("Signal fired but Rithmic not ready, skipping")
            return

        side = "BUY" if direction == "UP" else "SELL"
        logger.info(f"Placing {side} order for signal {direction} {pct:.4%}")
        basket_id = await self._rithmic.place_order(side)
        # basket_id may be empty here — filled in via ResponseNewOrder (313)
        # We track a placeholder; basket_id gets confirmed on fill callback
        pos = Position(basket_id or f"PENDING-{time.monotonic():.0f}", direction, signal_pct=pct)
        self._positions[pos.basket_id] = pos
        logger.info(f"Position opened: basket_id={pos.basket_id} direction={direction}")

    async def _on_order_ack(self, basket_id: str) -> None:
        # Find the most recent PENDING-* position and re-key it to the real basket_id
        for key, pos in list(self._positions.items()):
            if key.startswith("PENDING-") and not pos.closed:
                del self._positions[key]
                pos.basket_id = basket_id
                self._positions[basket_id] = pos
                logger.info(f"basket_id resolved: {key} -> {basket_id}")
                return
        # Resolve a pending closing order
        if self._pending_close is not None:
            self._closing_orders[basket_id] = self._pending_close
            logger.info(f"Closing order basket_id resolved: {basket_id} for original={self._pending_close.basket_id}")
            self._pending_close = None

    async def _on_fill(self, fill: dict) -> None:
        basket_id = fill.get("basket_id", "")
        fill_price = fill.get("fill_price", 0.0)

        pos = self._positions.get(basket_id)
        if pos is None:
            pos = self._closing_orders.pop(basket_id, None)
            if pos is not None:
                pnl_ticks = (fill_price - pos.entry_price) * (1 if pos.direction == "UP" else -1)
                is_loss = pnl_ticks < 0
                if is_loss:
                    self._consecutive_losses += 1
                    logger.warning(
                        f"Close fill LOSS: basket_id={basket_id} price={fill_price} "
                        f"pnl~={pnl_ticks:.2f} consecutive_losses={self._consecutive_losses}"
                    )
                    if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                        self._halted = True
                        logger.warning(f"HALTED: {self._consecutive_losses} consecutive losses — no new trades")
                else:
                    self._consecutive_losses = 0
                    logger.info(f"Close fill WIN: basket_id={basket_id} price={fill_price} pnl~={pnl_ticks:.2f}")
                self._save_state()
                self._store.record_exit(pos.basket_id, fill_price, "max_hold", time.time())
                return
            logger.warning(f"Fill for unknown basket_id={basket_id}")
            return

        if pos.entry_price is None:
            pos.entry_price = fill_price
            tick = self._cfg.tick_size
            sign = 1 if pos.direction == "UP" else -1
            pos.tp_price = fill_price + tick * self._cfg.take_profit_ticks * sign
            pos.sl_price = fill_price - tick * self._cfg.stop_loss_ticks * sign
            logger.info(
                f"Fill confirmed: basket_id={basket_id} price={fill_price} direction={pos.direction} "
                f"TP={pos.tp_price:.2f} SL={pos.sl_price:.2f}"
            )
            side = fill.get("transaction_type", "BUY" if pos.direction == "UP" else "SELL")
            self._store.record_entry(
                basket_id, self._cfg.symbol, pos.direction, side,
                pos.signal_pct, fill_price, time.time(),
            )
        else:
            # Closing fill
            pnl_ticks = (fill_price - pos.entry_price) * (1 if pos.direction == "UP" else -1)
            is_loss = pnl_ticks < 0
            if is_loss:
                self._consecutive_losses += 1
                logger.warning(
                    f"Close fill LOSS: basket_id={basket_id} price={fill_price} "
                    f"pnl~={pnl_ticks:.2f} consecutive_losses={self._consecutive_losses}"
                )
                if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    self._halted = True
                    logger.warning(f"HALTED: {self._consecutive_losses} consecutive losses — no new trades")
            else:
                self._consecutive_losses = 0
                logger.info(f"Close fill WIN: basket_id={basket_id} price={fill_price} pnl~={pnl_ticks:.2f}")
            self._save_state()
            self._store.record_exit(basket_id, fill_price, "fill", time.time())
            pos.closed = True

    async def _position_monitor(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            for pos in list(self._positions.values()):
                if pos.closed:
                    continue
                held = now - pos.entry_time
                if held >= self._cfg.max_hold_secs:
                    logger.info(
                        f"Max hold reached ({held:.0f}s): closing position basket_id={pos.basket_id} "
                        f"direction={pos.direction}"
                    )
                    await self._close_position(pos)

    async def _close_position(self, pos: Position) -> None:
        if pos.closed:
            return
        closing_side = "SELL" if pos.direction == "UP" else "BUY"
        if self._cfg.dry_run:
            logger.info(f"[DRY RUN] Close position basket_id={pos.basket_id} side={closing_side}")
            pos.closed = True
            return

        logger.info(f"Placing closing order: side={closing_side} basket_id={pos.basket_id}")
        pos.closed = True  # prevent monitor re-trigger before ack arrives
        await self._rithmic.place_order(closing_side)
        self._pending_close = pos  # resolved in _on_order_ack, filled in _on_fill
