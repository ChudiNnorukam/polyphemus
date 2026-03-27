import asyncio
import json
import logging
import os
import time
from datetime import datetime, time as dt_time
from typing import Optional
from zoneinfo import ZoneInfo

import requests

_CENTRAL = ZoneInfo("America/Chicago")
_MARKET_OPEN = dt_time(17, 0)   # 5:00 PM CT (Bulenox trading window opens)
_MARKET_CLOSE = dt_time(15, 55)  # 3:55 PM CT (4-min buffer before Bulenox 3:59 PM CT flat deadline)

# MBT contract month codes: Jan=F, Feb=G, ..., Dec=Z
_MBT_MONTHS = {1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}


def get_front_month_symbol() -> str:
    """Auto-detect MBT front month. Switch to next month when <5 days to expiry."""
    now = datetime.now(_CENTRAL)
    day = now.day
    month = now.month
    year = now.year
    # If past 25th, roll to next month
    if day >= 26:
        month = month + 1 if month < 12 else 1
        year = year if month > 1 else year + 1
    code = _MBT_MONTHS[month]
    return "MBT" + code + str(year)[-1]

from config import BulenoxConfig
from binance_feed import BinanceFeed
from rithmic_client import RithmicClient
from ticker_plant import TickerPlant
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
        self.mfe: float = 0.0  # max favorable excursion in price points
        self.mae: float = 0.0  # max adverse excursion in price points


MAX_CONSECUTIVE_LOSSES = 5  # At 40% loss rate: P(5 consecutive) = 1%. Was 2 (16% = halted every 6 trades)


class BulenoxBot:
    def __init__(self, cfg: BulenoxConfig):
        self._cfg = cfg
        self._rithmic = RithmicClient(cfg, on_fill=self._on_fill, on_order_ack=self._on_order_ack)
        self._ticker = TickerPlant(cfg)
        self._feed = BinanceFeed(
            symbol=cfg.binance_symbol,
            window_secs=cfg.momentum_window_secs,
            trigger_pct=cfg.momentum_trigger_pct,
            on_signal=self._on_signal,
            on_price=self._on_tick,
            cooldown_secs=cfg.entry_cooldown_secs,
        )
        self._positions: dict[str, Position] = {}
        self._closing_orders: dict[str, Position] = {}
        self._pending_close: Optional[Position] = None
        self._consecutive_losses: int = 0
        self._halted: bool = False
        self._recent_raw_dirs: list = []  # last N raw signal directions for trend detection
        self._extreme_cooldown_until: float = 0.0  # monotonic time when extreme event cooldown expires
        os.makedirs(cfg.data_dir, exist_ok=True)
        self._store = TradeStore(os.path.join(cfg.data_dir, "trades.db"))
        self._state_path = os.path.join(cfg.data_dir, "bot_state.json")
        # Trailing drawdown: tracks peak balance. Account terminates if balance drops
        # more than max_trailing_drawdown below peak.
        self._starting_balance: float = 50000.0  # Bulenox $50K account
        self._peak_balance: float = self._starting_balance
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            self._consecutive_losses = int(data.get("consecutive_losses", 0))
            self._halted = bool(data.get("halted", False))
            self._peak_balance = float(data.get("peak_balance", self._starting_balance))
            if self._halted:
                logger.warning(
                    f"Loaded halted state — bot is halted. "
                    f"Delete {self._state_path} or set halted=false to resume."
                )
            logger.info(f"State loaded: peak_balance=${self._peak_balance:.2f} "
                        f"drawdown_room=${self._cfg.max_trailing_drawdown - (self._peak_balance - self._current_balance):.2f}")
        except Exception as e:
            logger.error(f"Failed to load state from {self._state_path}: {e}")

    @property
    def _current_balance(self) -> float:
        """Current balance = starting + total realized P&L."""
        total_pnl = self._store.get_total_pnl() * self._cfg.point_value * self._cfg.contracts
        return self._starting_balance + total_pnl

    def _send_alert(self, msg: str) -> None:
        """Send alert to Slack if configured."""
        slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
        slack_channel = os.environ.get("SLACK_CHANNEL_ID", "")
        if not slack_token or not slack_channel:
            return
        try:
            requests.post(
                "https://slack.com/api/chat.postMessage",
                json={"channel": slack_channel, "text": f"[BulenoxBot] {msg}"},
                headers={"Authorization": f"Bearer {slack_token}"},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Slack alert failed: {e}")

    def _update_drawdown(self) -> None:
        """Update peak balance and check trailing drawdown. HALTS if breached."""
        balance = self._current_balance
        if balance > self._peak_balance:
            self._peak_balance = balance
            logger.info(f"New peak balance: ${self._peak_balance:.2f}")
        drawdown = self._peak_balance - balance
        room = self._cfg.max_trailing_drawdown - drawdown
        if room <= 0:
            self._halted = True
            msg = (f"TRAILING DRAWDOWN BREACHED: peak=${self._peak_balance:.2f} "
                   f"current=${balance:.2f} drawdown=${drawdown:.2f} >= limit=${self._cfg.max_trailing_drawdown}")
            logger.critical(msg)
            self._send_alert(msg)
            self._save_state()
        elif room < self._cfg.max_trailing_drawdown * 0.25:
            msg = (f"DRAWDOWN WARNING: ${room:.2f} remaining of ${self._cfg.max_trailing_drawdown} "
                   f"(peak=${self._peak_balance:.2f} current=${balance:.2f})")
            logger.warning(msg)
            self._send_alert(msg)
        elif room < self._cfg.max_trailing_drawdown * 0.50:
            logger.info(
                f"Drawdown status: ${room:.2f} remaining of ${self._cfg.max_trailing_drawdown} "
                f"(peak=${self._peak_balance:.2f} current=${balance:.2f})"
            )

    def _check_profit_target(self) -> bool:
        """Check if qualification profit target is reached."""
        balance = self._current_balance
        profit = balance - self._starting_balance
        if profit >= self._cfg.profit_target:
            logger.info(
                f"PROFIT TARGET REACHED: ${profit:.2f} >= ${self._cfg.profit_target} "
                f"Balance: ${balance:.2f}. Qualification may be complete!"
            )
            return True
        return False

    def _save_state(self) -> None:
        try:
            with open(self._state_path, "w") as f:
                json.dump({
                    "consecutive_losses": self._consecutive_losses,
                    "halted": self._halted,
                    "peak_balance": self._peak_balance,
                }, f)
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

        # Auto-rollover: detect front month contract
        if self._cfg.auto_rollover:
            front = get_front_month_symbol()
            if front != self._cfg.symbol:
                logger.warning(f"CONTRACT ROLLOVER: {self._cfg.symbol} -> {front}")
                self._cfg.symbol = front
            else:
                logger.info(f"Contract: {front} (current)")

        # Fix 3: Timezone verification — confirm CT time is correct before trading
        now_ct = datetime.now(_CENTRAL)
        logger.info(
            f"TIMEZONE CHECK: Central Time = {now_ct.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
            f"Market open: {_MARKET_OPEN} | Force close: {_MARKET_CLOSE}"
        )
        in_hours = (now_ct.time() >= _MARKET_OPEN or now_ct.time() < _MARKET_CLOSE)
        logger.info(f"  Currently {'INSIDE' if in_hours else 'OUTSIDE'} trading hours")

        # Fix 4: Force 1 contract safety — warn if config allows more
        if self._cfg.contracts > 1:
            logger.warning(
                f"SAFETY: contracts={self._cfg.contracts} but AUDIT recommends 1 until 100+ trades "
                f"confirm WR. Override at your own risk."
            )

        # Fix 6: Trade count gate — log progress toward 50-trade minimum
        total_trades = self._store.get_total_trades() if hasattr(self._store, 'get_total_trades') else 0
        if total_trades < 50:
            logger.info(
                f"PAPER TRADE GATE: {total_trades}/50 trades completed. "
                f"Do NOT optimize TP/SL until 50+ trades establish baseline WR."
            )

        balance = self._current_balance
        drawdown_room = self._cfg.max_trailing_drawdown - (self._peak_balance - balance)
        logger.info(
            f"BulenoxBot starting | symbol={self._cfg.symbol} exchange={self._cfg.exchange} "
            f"dry_run={self._cfg.dry_run} | balance=${balance:.2f} | "
            f"peak=${self._peak_balance:.2f} | drawdown_room=${drawdown_room:.2f} | "
            f"target=${self._cfg.profit_target} | TP={self._cfg.take_profit_ticks}ticks SL={self._cfg.stop_loss_ticks}ticks"
        )
        await asyncio.gather(
            self._rithmic.connect(),
            self._ticker.connect(),
            self._feed.start(),
            self._position_monitor(),
        )

    def _spawn_shadow(self, direction: str, pct: float, reason: str) -> None:
        """Record a counterfactual shadow trade for a rejected signal.
        Spawns an async task to watch price and resolve outcome."""
        price = self._ticker.last_trade_price or self._feed.last_price
        if price <= 0:
            return
        row_id = self._store.record_shadow_trade(time.time(), direction, price, reason)
        asyncio.ensure_future(self._resolve_shadow(row_id, direction, price))

    async def _resolve_shadow(self, row_id: int, direction: str, entry_price: float) -> None:
        """Watch price for max_hold_secs and determine if TP or SL would have been hit."""
        tick = self._cfg.tick_size
        sign = 1 if direction == "UP" else -1
        tp_price = entry_price + tick * self._cfg.take_profit_ticks * sign
        sl_price = entry_price - tick * self._cfg.stop_loss_ticks * sign
        start = time.monotonic()
        max_hold = self._cfg.max_hold_secs
        while time.monotonic() - start < max_hold:
            await asyncio.sleep(1.0)
            price = self._ticker.last_trade_price or self._feed.last_price
            if price <= 0:
                continue
            if (sign > 0 and price >= tp_price) or (sign < 0 and price <= tp_price):
                pnl = abs(tp_price - entry_price) * sign
                self._store.resolve_shadow_trade(row_id, "win", pnl)
                logger.debug(f"Shadow trade {row_id}: WIN (TP hit at {price:.2f})")
                return
            if (sign > 0 and price <= sl_price) or (sign < 0 and price >= sl_price):
                pnl = -abs(sl_price - entry_price)
                self._store.resolve_shadow_trade(row_id, "loss", pnl)
                logger.debug(f"Shadow trade {row_id}: LOSS (SL hit at {price:.2f})")
                return
        # Timeout: close at current price
        price = self._ticker.last_trade_price or self._feed.last_price
        pnl = (price - entry_price) * sign if price > 0 else 0.0
        outcome = "win" if pnl > 0 else "loss"
        self._store.resolve_shadow_trade(row_id, outcome, pnl)
        logger.debug(f"Shadow trade {row_id}: {outcome.upper()} (timeout, pnl={pnl:.2f})")

    async def _on_signal(self, direction: str, pct: float) -> None:
        # direction is already FADED by binance_feed.py
        raw_dir = "DOWN" if direction == "UP" else "UP"  # reverse the fade to get raw

        if self._cfg.kill_switch_path and os.path.exists(self._cfg.kill_switch_path):
            logger.warning("Kill switch active — no new trades")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="kill_switch")
            return

        if self._halted:
            logger.warning(f"Signal {direction} ignored: bot halted after {self._consecutive_losses} consecutive losses")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="halted")
            return

        now_ct = datetime.now(_CENTRAL).time()
        # CME trades 5pm-4pm CT (overnight session). Check: now >= 17:00 OR now < 15:55
        in_market_hours = (now_ct >= _MARKET_OPEN or now_ct < _MARKET_CLOSE)
        if not in_market_hours:
            logger.info(f"Signal {direction} ignored: outside market hours ({now_ct.strftime('%H:%M')} CT, halt 15:55-17:00)")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="market_hours")
            return

        # Session filter: optimal FADE window (v2.0 audit - mid-day mean reversion strongest)
        fade_start = dt_time(*map(int, self._cfg.fade_start_ct.split(":")))
        fade_end = dt_time(*map(int, self._cfg.fade_end_ct.split(":")))
        if not (fade_start <= now_ct <= fade_end):
            logger.info(f"Signal {direction} skipped: outside FADE window "
                        f"({now_ct.strftime('%H:%M')} CT, window {self._cfg.fade_start_ct}-{self._cfg.fade_end_ct})")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="session_filter")
            self._spawn_shadow(direction, pct, "session_filter")
            return

        # Basis check: MBT futures vs Coinbase spot (v2.0 audit - basis divergence risk)
        mbt_price = self._ticker.last_trade_price
        spot_price = self._feed.last_price
        if mbt_price > 0 and spot_price > 0:
            basis_pct = abs(mbt_price - spot_price) / spot_price
            logger.info(f"Basis: MBT={mbt_price:.2f} Spot={spot_price:.2f} "
                        f"diff={mbt_price - spot_price:.2f} ({basis_pct:.4%})")
            if basis_pct > self._cfg.max_basis_pct:
                logger.warning(f"Signal {direction} skipped: basis too wide "
                               f"({basis_pct:.2%} > {self._cfg.max_basis_pct:.0%})")
                self._store.record_signal(raw_dir, direction, pct, "rejected", reason="basis_wide")
                self._spawn_shadow(direction, pct, "basis_wide")
                return

        # Extreme event cooldown: skip all signals for N seconds after a 3%+ move (Gap #11)
        if time.monotonic() < self._extreme_cooldown_until:
            remaining = int(self._extreme_cooldown_until - time.monotonic())
            logger.warning(f"Signal {direction} skipped: extreme event cooldown ({remaining}s remaining)")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="extreme_event")
            self._spawn_shadow(direction, pct, "extreme_event")
            return
        # Check if current signal itself is extreme (|pct| > threshold)
        if abs(pct) > self._cfg.extreme_move_pct:
            self._extreme_cooldown_until = time.monotonic() + self._cfg.extreme_cooldown_secs
            logger.warning(f"EXTREME EVENT: {pct:.2%} move exceeds {self._cfg.extreme_move_pct:.0%}. "
                           f"Cooldown {self._cfg.extreme_cooldown_secs}s activated.")
            self._send_alert(f"Extreme event: {pct:.2%} move. Cooldown {self._cfg.extreme_cooldown_secs}s.")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="extreme_event")
            self._spawn_shadow(direction, pct, "extreme_event")
            return

        # Trend filter: skip if 4+ of last 5 signals same direction (v2.0 strengthened from 3/3)
        self._recent_raw_dirs.append(raw_dir)
        if len(self._recent_raw_dirs) > 5:
            self._recent_raw_dirs.pop(0)
        if len(self._recent_raw_dirs) >= 5:
            dominant = max(set(self._recent_raw_dirs), key=self._recent_raw_dirs.count)
            if self._recent_raw_dirs.count(dominant) >= 4:
                logger.info(f"Signal {direction} skipped: trend filter "
                            f"({self._recent_raw_dirs.count(dominant)}/5 {dominant} signals)")
                self._store.record_signal(raw_dir, direction, pct, "rejected", reason="trend_filter")
                self._spawn_shadow(direction, pct, "trend_filter")
                return

        # Directional gate: skip if this direction's rolling WR < threshold on n>=10 (Gap #5)
        dir_wr, dir_n = self._store.get_directional_wr(direction, lookback=20)
        if dir_n >= 10 and dir_wr < self._cfg.directional_gate_wr:
            logger.warning(f"Signal {direction} skipped: directional gate "
                           f"(WR={dir_wr:.0%} on last {dir_n} {direction} trades, threshold={self._cfg.directional_gate_wr:.0%})")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="directional_gate")
            self._spawn_shadow(direction, pct, "directional_gate")
            return

        today = datetime.now(_CENTRAL).strftime("%Y-%m-%d")
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
            sim_price = self._ticker.last_trade_price or self._feed.last_price
            if sim_price <= 0:
                logger.warning(f"[DRY RUN] Signal {direction} skipped: no price available")
                return
            side = "BUY" if direction == "UP" else "SELL"
            basket_id = await self._rithmic.place_order(side)
            pos = Position(basket_id, direction, signal_pct=pct)
            pos.entry_price = sim_price
            tick = self._cfg.tick_size
            sign = 1 if direction == "UP" else -1
            pos.tp_price = sim_price + tick * self._cfg.take_profit_ticks * sign
            pos.sl_price = sim_price - tick * self._cfg.stop_loss_ticks * sign
            self._positions[basket_id] = pos
            self._store.record_entry(
                basket_id, self._cfg.symbol, direction, side,
                pct, sim_price, time.time(),
            )
            logger.info(
                f"[DRY RUN] Entry: {side} basket_id={basket_id} price={sim_price:.2f} "
                f"TP={pos.tp_price:.2f} SL={pos.sl_price:.2f} signal={pct:.4%}"
            )
            self._store.record_signal(raw_dir, direction, pct, "executed", basket_id=basket_id)
            return

        if not self._rithmic._ready.is_set():
            logger.warning("Signal fired but Rithmic not ready, skipping")
            self._store.record_signal(raw_dir, direction, pct, "rejected", reason="rithmic_not_ready")
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

    async def _on_tick(self, price: float) -> None:
        """Called on every Coinbase tick (~81ms). Checks TP/SL with minimal latency."""
        if not self._positions or price <= 0:
            return
        for pos in list(self._positions.values()):
            if pos.closed or pos.entry_price is None:
                continue
            # Track MFE/MAE
            if pos.direction == "UP":
                excursion = price - pos.entry_price
            else:
                excursion = pos.entry_price - price
            if excursion > pos.mfe:
                pos.mfe = excursion
            if excursion < pos.mae:
                pos.mae = excursion
            # SL check
            if pos.sl_price is not None:
                sl_hit = (
                    (pos.direction == "UP" and price <= pos.sl_price)
                    or (pos.direction == "DOWN" and price >= pos.sl_price)
                )
                if sl_hit:
                    logger.warning(f"SL hit (tick): basket_id={pos.basket_id} price={price:.2f} sl={pos.sl_price:.2f}")
                    await self._close_position(pos)
                    continue
            # TP check
            if pos.tp_price is not None:
                tp_hit = (
                    (pos.direction == "UP" and price >= pos.tp_price)
                    or (pos.direction == "DOWN" and price <= pos.tp_price)
                )
                if tp_hit:
                    logger.info(f"TP hit (tick): basket_id={pos.basket_id} price={price:.2f} tp={pos.tp_price:.2f}")
                    await self._close_position(pos)

    async def _force_close_all(self, reason: str) -> None:
        """Emergency close all open positions."""
        open_positions = [p for p in self._positions.values() if not p.closed]
        if not open_positions:
            return
        logger.warning(f"FORCE CLOSE ALL ({len(open_positions)} positions): {reason}")
        for pos in open_positions:
            await self._close_position(pos)

    async def _position_monitor(self) -> None:
        """Handles max_hold timeout, force-close, and drawdown. TP/SL handled by _on_tick."""
        while True:
            await asyncio.sleep(5)
            now_ct = datetime.now(_CENTRAL).time()

            # Friday force-close: weekend gap protection (CME closed Sat-Sun)
            now_full = datetime.now(_CENTRAL)
            if now_full.weekday() == 4 and now_ct >= _MARKET_CLOSE:  # Friday
                await self._force_close_all("Friday close: weekend gap protection")
                await asyncio.sleep(300)
                continue

            # Force close before Bulenox 3:59 PM CT deadline
            if _MARKET_CLOSE <= now_ct < _MARKET_OPEN:
                await self._force_close_all(
                    f"Bulenox daily close deadline: {now_ct.strftime('%H:%M')} CT (halt 15:55-17:00)"
                )
                await asyncio.sleep(60)
                continue

            self._update_drawdown()
            self._save_state()

            if self._check_profit_target():
                logger.info("Consider stopping bot — qualification target reached.")

            now = time.monotonic()
            for pos in list(self._positions.values()):
                if pos.closed:
                    continue
                held = now - pos.entry_time

                # Max hold timeout
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
            sim_price = self._ticker.last_trade_price or self._feed.last_price
            pos.closed = True
            exit_reason = "dry_run"
            if pos.entry_price is not None and sim_price > 0:
                pnl_ticks = (sim_price - pos.entry_price) * (1 if pos.direction == "UP" else -1)
                is_loss = pnl_ticks < 0
                if is_loss:
                    self._consecutive_losses += 1
                    if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                        self._halted = True
                        logger.warning(f"[DRY RUN] HALTED: {self._consecutive_losses} consecutive losses")
                else:
                    self._consecutive_losses = 0
                mfe_tk = pos.mfe / self._cfg.tick_size if self._cfg.tick_size else 0
                mae_tk = pos.mae / self._cfg.tick_size if self._cfg.tick_size else 0
                self._store.record_exit(pos.basket_id, sim_price, exit_reason, time.time(), mfe_ticks=mfe_tk, mae_ticks=mae_tk)
                self._update_drawdown()
                self._save_state()
                logger.info(
                    f"[DRY RUN] Exit: {closing_side} basket_id={pos.basket_id} "
                    f"entry={pos.entry_price:.2f} exit={sim_price:.2f} "
                    f"pnl={pnl_ticks:+.2f}pts {'WIN' if not is_loss else 'LOSS'}"
                )
            else:
                logger.info(f"[DRY RUN] Close position basket_id={pos.basket_id} (no price)")
            return

        logger.info(f"Placing closing order: side={closing_side} basket_id={pos.basket_id}")
        pos.closed = True  # prevent monitor re-trigger before ack arrives
        await self._rithmic.place_order(closing_side)
        self._pending_close = pos  # resolved in _on_order_ack, filled in _on_fill
