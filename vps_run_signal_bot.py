#!/usr/bin/env python3
"""
run_signal_bot.py - Main entry point for signal-based trading
Integrated with: ExitManager, PerformanceTracker, Live Price Feed, Pattern Learning, Resolution Tracker

Usage:
  DRY_RUN=true python run_signal_bot.py   # Paper trading (default)
  DRY_RUN=false python run_signal_bot.py  # Live trading
"""
import asyncio
import sqlite3
import json
import sys
import os
import signal as sig
from datetime import datetime, timezone, timedelta
import logging
import uuid
import aiohttp

from signal_tracker import SignalTracker
from signal_filter import SignalFilter
from signal_types import Signal, FilteredSignal
from paper_trader import PaperTrader
from signal_executor import SignalExecutor, ExecutorConfig
from signal_config import MIN_FILTER_SCORE, STARTING_CAPITAL, CLOB_HOST
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
from error_tracker import ErrorTracker
from healthcheck import notify_watchdog, notify_ready

# New integrations
from exit_manager import ExitManager, ExitConfig, Position, ExitReason
from performance_tracker import PerformanceTracker, TradeRecord
from pattern_learner import PatternLearner
from resolution_tracker import ResolutionTracker
from position_redeemer import PositionRedeemer
from self_tuner import SelfTuner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("signal_bot")


class PriceFeed:
    """Fetch live prices from Polymarket CLOB API"""
    
    def __init__(self, clob_host: str = None):
        self.clob_host = clob_host or CLOB_HOST
        self.session: aiohttp.ClientSession = None
        
    async def init(self):
        if not self.session:
            self.session = aiohttp.ClientSession()
            
    async def close(self):
        if self.session:
            await self.session.close()
            
    async def get_price(self, token_id: str) -> float:
        """Fetch current mid price for a token using order book"""
        try:
            await self.init()
            url = f"{self.clob_host}/book"
            params = {"token_id": token_id}
            async with self.session.get(url, params=params, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])
                    
                    best_bid = float(bids[0]["price"]) if bids else 0
                    best_ask = float(asks[0]["price"]) if asks else 1
                    
                    # Only return valid mid if spread is reasonable
                    if best_bid > 0.05 and best_ask < 0.95:
                        mid = (best_bid + best_ask) / 2
                        return mid
                    elif best_bid > 0:
                        return best_bid
                    elif best_ask < 1:
                        return best_ask
        except Exception as e:
            logger.debug(f"Price fetch failed for {token_id}: {e}")
        return 0.0
    
    async def get_prices(self, token_ids: list) -> dict:
        """Fetch prices for multiple tokens"""
        results = {}
        for token_id in token_ids:
            price = await self.get_price(token_id)
            if price > 0:
                results[token_id] = price
        return results


class SignalBot:
    """
    Main signal-based trading bot with exit management, performance tracking, 
    pattern learning, and market resolution tracking.
    Includes live price feed for realistic paper trading.
    """
    
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.tracker = SignalTracker()
        self.filter = SignalFilter(min_score=0.35, adaptive=False)  # Fixed threshold, adaptive disabled (death spiral bug)
        self.paper_trader = PaperTrader(starting_capital=STARTING_CAPITAL)
        
        # Initialize executor
        config = ExecutorConfig.from_env()
        config.dry_run = dry_run
        self.executor = SignalExecutor(config)
        
        # Exit manager for stop-loss/take-profit
        exit_config = ExitConfig(
            profit_target_pct=0.20,
            stop_loss_pct=0.95,
            trailing_stop_pct=0.50,
            trailing_stop_activation=0.30,
            time_exit_buffer_mins=5,
            check_interval_secs=10,
            max_hold_minutes=12  # Auto-exit after 12 minutes for 15-min markets
        )
        self.exit_manager = ExitManager(exit_config)
        
        # Performance tracker for P&L
        self.perf_tracker = PerformanceTracker()
        self.perf_tracker_initialized = False
        
        # Pattern learner for adaptive signal scoring
        self._pattern_learner = PatternLearner()
        
        # Price feed for live price updates
        self.price_feed = PriceFeed()
        
        # Resolution tracker for market resolution detection
        self.resolution_tracker = ResolutionTracker()
        
        # US-005: Auto-claim resolved positions
        self.position_redeemer = PositionRedeemer()
        
        # Self-tuning position sizing (v2 consensus plan)
        self.self_tuner = SelfTuner(
            db_path="data/performance.db",
            state_path="data/tuning_state.json"
        )
        self.executor.self_tuner = self.self_tuner
        
        # Error tracking for learning loop
        self.error_tracker = ErrorTracker()
        
        # Map token_id -> trade_id for performance tracking
        self.token_to_trade: dict = {}
        self.trade_metadata: dict = {}
        
        self.running = True
        self.db_cold_streak = False
        self.signals_received = 0
        self.signals_passed = 0
        self.orders_executed = 0
        self.exits_triggered = 0
        self.active_slugs: set = set()  # Track active market positions
        self.pending_sells: set = set()  # Track tokens with pending SELL orders
        self.MAX_OPEN_POSITIONS = 10
        self.MIN_ENTRY_PRICE = 0.65
        self.MAX_ENTRY_PRICE = 0.80  # Only trade 0.65-0.80 golden zone (2026-02-05)
        
        # US-001: Connection watchdog
        self._last_signal_time = 0.0
        self._reconnect_count = 0
        
        # US-002: Balance guard
        self._cached_balance = 0.0
        self._balance_cache_time = 0.0
        self._low_balance = False
        self.LOW_BALANCE_THRESHOLD = 10.0  # $10 USDC minimum

        # Portfolio awareness (lightweight)
        self._peak_equity = 0.0        # High water mark (resets on restart)
        self._deployment_ratio = 0.0   # (deployed / total portfolio)
        self._unfilled_streak = 0      # Reset on every restart

        # Self-tuning v1: trade rate monitor + anti-death-spiral
        self._last_trade_time = None          # Time of last executed trade
        self._trades_last_4h = 0              # Rolling trade count (updated in learning loop)
        self._force_reset_count = 0           # How many times auto-reset has fired
        self.DEFAULT_MIN_ENTRY_PRICE = 0.65   # Default to revert to on reset
        self._bucket_stats = {}               # Per-bucket WR: {"0.60-0.65": {"wins": 0, "losses": 0}, ...}
        self._coin_stats = {}                 # Per-coin WR: {"btc": {"wins": 0, "losses": 0}, ...}
        self.BALANCE_CACHE_TTL = 60  # Cache for 60 seconds
        
        # US-004: Health status tracking
        self._start_time = datetime.now(timezone.utc)
        self._last_health_signals = 0
        
        mode = "PAPER" if dry_run else "LIVE"
        logger.info(f"Signal bot initialized in {mode} mode")
        
    async def init_async(self):
        """Initialize async components"""
        await self.perf_tracker.init_db()
        self.perf_tracker_initialized = True
        await self.price_feed.init()
        await self.resolution_tracker.init()
        
        # Initialize balance cache on startup (prevents deploy_ratio deadlock)
        if not self.dry_run:
            try:
                import time as _tinit
                _loop = asyncio.get_event_loop()
                _bal_resp = await _loop.run_in_executor(
                    None,
                    lambda: self.executor.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                )
                if isinstance(_bal_resp, dict):
                    self._cached_balance = float(_bal_resp.get('balance', 0)) / 1e6
                self._balance_cache_time = _tinit.time()
                self._low_balance = self._cached_balance < self.LOW_BALANCE_THRESHOLD
                logger.info(f"Initial balance: ${self._cached_balance:.2f} USDC")
            except Exception as e:
                logger.warning(f"Initial balance fetch failed: {e}")

        # US-005: Initialize position redeemer
        try:
            self.position_redeemer.init()
        except Exception as e:
            logger.error(f"Position redeemer init failed: {e}")
        
        # Load existing open trade slugs from DB to prevent duplicates
        try:
            conn = sqlite3.connect(self.perf_tracker.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT slug FROM trades WHERE exit_time IS NULL AND slug IS NOT NULL")
            for row in cursor.fetchall():
                if row[0]:
                    self.active_slugs.add(row[0])
            conn.close()
            logger.info(f"Loaded {len(self.active_slugs)} active slugs from DB: {self.active_slugs}")
        except Exception as e:
            logger.error(f"Failed to load active slugs: {e}")

        # Load open positions into exit_manager for resolution tracking
        try:
            conn = sqlite3.connect(self.perf_tracker.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT trade_id, token_id, slug, entry_price, entry_size,
                       entry_time, outcome, side
                FROM trades WHERE exit_time IS NULL AND token_id IS NOT NULL
            """)
            loaded_positions = 0
            for row in cursor.fetchall():
                trade_id, token_id, slug, entry_price, entry_size, entry_ts, outcome, side = row
                entry_dt = datetime.fromtimestamp(entry_ts, tz=timezone.utc) if entry_ts else datetime.now(timezone.utc)
                market_end = entry_dt + timedelta(hours=24)
                position = Position(
                    token_id=token_id,
                    entry_price=entry_price,
                    size=entry_size,
                    entry_time=entry_dt,
                    market_end_time=market_end,
                    current_price=entry_price
                )
                self.exit_manager.add_position(position)
                self.token_to_trade[token_id] = trade_id
                self.trade_metadata[trade_id] = {
                    "token_id": token_id,
                    "entry_price": entry_price,
                    "size": entry_size,
                    "slug": slug or "",
                    "direction": side or "BUY",
                    "outcome": outcome or "Unknown",
                }
                loaded_positions += 1
            conn.close()
            logger.info(f"Loaded {loaded_positions} positions into exit_manager for resolution tracking")
        except Exception as e:
            logger.error(f"Failed to load positions into exit_manager: {e}")
        
        logger.info("Performance tracker, price feed, and resolution tracker initialized")
        
    def handle_signal(self, raw_signal: dict) -> None:
        """Process raw signal through filter and executor"""
        self.signals_received += 1
        import time as _time_wd; self._last_signal_time = _time_wd.time()
        logger.info(f"handle_signal called: {raw_signal.get('direction')} {raw_signal.get('outcome')} @ {raw_signal.get('price')}")
        
        try:
            sig_obj = Signal(
                tx_hash=raw_signal.get("tx_hash", ""),
                timestamp=raw_signal.get("timestamp", datetime.now().isoformat()),
                direction=raw_signal.get("direction", "UNKNOWN"),
                outcome=raw_signal.get("outcome", ""),
                asset=raw_signal.get("asset", ""),
                price=float(raw_signal.get("price", 0)),
                usdc_size=float(raw_signal.get("usdc_size", 0)),
                market_title=raw_signal.get("market_title", ""),
                slug=raw_signal.get("slug", "")
            )
            
            # Guard: Only follow real-time signals (< 30s old)
            import time as _time
            signal_ts = raw_signal.get("timestamp", 0)
            if signal_ts and isinstance(signal_ts, (int, float)):
                signal_age = _time.time() - signal_ts
                if signal_age > 30:
                    return  # Skip stale catchup signals
            
            # SELL signal: DISABLED - hold positions to natural resolution
            # Churn from following DB's sell signals cost -$69/6h. Now we only exit via:
            # market_resolved, profit_target, time_exit, max_hold
            direction = raw_signal.get("direction", "")
            if direction == "SELL":
                return  # Skip all sell signals
            
            filtered = self.filter.filter(raw_signal)
            
            if filtered["passed"]:
                # Guard 0.5: Cold streak filter - skip every other signal
                if self.db_cold_streak and self.signals_received % 2 == 0:
                    logger.debug(f"REJECTED cold streak filter: signal #{self.signals_received}")
                    return
                # Guard 1: Entry price bounds
                price = float(raw_signal.get("price", 0))
                if price < self.MIN_ENTRY_PRICE or price > self.MAX_ENTRY_PRICE:
                    logger.info(f"REJECTED price {price:.2f} outside [{self.MIN_ENTRY_PRICE}-{self.MAX_ENTRY_PRICE}]")
                    return
                # Guard 2: Dedup - 1 position per slug
                slug = raw_signal.get("slug", "")
                if slug and slug in self.active_slugs:
                    logger.info(f"REJECTED dedup: {slug[-30:]}")
                    return
                # Guard 3: Max open positions
                if len(self.active_slugs) >= self.MAX_OPEN_POSITIONS:
                    logger.info(f"REJECTED max {self.MAX_OPEN_POSITIONS} positions")
                    return
                self.signals_passed += 1
                if slug:
                    self.active_slugs.add(slug)
                score_val = filtered["score"]
                logger.info(f"SIGNAL PASSED: {sig_obj.direction} {sig_obj.outcome} @ {sig_obj.price:.2f} (score: {score_val:.2f}) [slug={slug}]")
                asyncio.create_task(self._execute_order(filtered, raw_signal))
            else:
                logger.info(f"Signal filtered: score {filtered['score']:.2f} < {self.filter.min_score:.2f}")
                
        except Exception as e:
            logger.error(f"Error processing signal: {e}")
    
    async def _execute_order(self, filtered: FilteredSignal, raw_signal: dict) -> None:
        """Execute order and track with exit manager + performance tracker"""
        try:
            # US-002: Low balance guard
            if not self.dry_run:
                import time as _t
                _now = _t.time()
                if _now - self._balance_cache_time > self.BALANCE_CACHE_TTL:
                    try:
                        _loop = asyncio.get_event_loop()
                        _bal_resp = await _loop.run_in_executor(
                            None,
                            lambda: self.executor.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                        )
                        if isinstance(_bal_resp, dict):
                            self._cached_balance = float(_bal_resp.get('balance', 0)) / 1e6
                        self._balance_cache_time = _now
                        self._low_balance = self._cached_balance < self.LOW_BALANCE_THRESHOLD
                        if self._low_balance:
                            logger.warning(f"LOW BALANCE: ${self._cached_balance:.2f} < ${self.LOW_BALANCE_THRESHOLD:.2f}")
                    except Exception as _bal_err:
                        logger.warning(f"Balance check failed: {_bal_err}")
                if self._low_balance:
                    _slug = raw_signal.get('slug', '')
                    logger.warning(f"SKIPPING BUY (low balance ${self._cached_balance:.2f}): {_slug[-30:]}")
                    if _slug and _slug in self.active_slugs:
                        self.active_slugs.discard(_slug)
                    return

            if self.dry_run:
                available = self.paper_trader.portfolio.cash
            else:
                # Live mode: query real USDC balance
                try:
                    loop = asyncio.get_event_loop()
                    balance_resp = await loop.run_in_executor(
                        None,
                        lambda: self.executor.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                    )
                    if isinstance(balance_resp, dict):
                        available = float(balance_resp.get('balance', 0)) / 1e6  # USDC 6 decimals
                    else:
                        available = 100.0
                    logger.info(f"Real USDC balance: ${available:.2f}")
                except Exception as e:
                    logger.warning(f"Balance check failed, using fallback: {e}")
                    available = 100.0
            # Calculate deployment ratio from internal state
            _open_positions = self.exit_manager.get_open_positions()
            _deployed_value = sum(p.entry_price * p.size for p in _open_positions)
            _portfolio_value = available + _deployed_value
            if _portfolio_value > 0:
                self._deployment_ratio = _deployed_value / _portfolio_value
            else:
                self._deployment_ratio = 0.0
            # Update peak equity (high water mark)
            if _portfolio_value > self._peak_equity:
                self._peak_equity = _portfolio_value

            result = await self.executor.execute_signal(raw_signal, filtered, available, cold_streak=self.db_cold_streak, deployment_ratio=self._deployment_ratio)
            
            if result and result.get("status") in ("FILLED", "DRY_RUN"):
                self.orders_executed += 1
                self._last_trade_time = datetime.now(timezone.utc)
                
                # Generate IDs
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                trade_id = f"TRADE_{ts}_{uuid.uuid4().hex[:6]}"
                token_id = raw_signal.get("asset", raw_signal.get("asset", "")) or trade_id
                entry_price = raw_signal.get("price", 0)
                size = result.get("size", raw_signal.get("usdc_size", 0))
                market_slug = raw_signal.get("slug", "")
                
                if self.dry_run:
                    self.paper_trader.place_order(filtered)
                
                # Record trade entry
                if self.perf_tracker_initialized:
                    entry_time_ts = int(datetime.now(timezone.utc).timestamp())
                    entry_amount = entry_price * size if entry_price and size else 0
                    # Map DB's outcome to our side: Up->YES, Down->NO
                    outcome = raw_signal.get("outcome", "Up")
                    side = "YES"  # Always YES - we buy the same token DB bought
                    trade_record = TradeRecord(
                        trade_id=trade_id,
                        token_id=token_id,
                        entry_time=entry_time_ts,
                        entry_price=entry_price,
                        entry_size=size,
                        side=side,
                        entry_amount=entry_amount,
                        slug=market_slug,
                        outcome=outcome
                    )
                    await self.perf_tracker.record_entry(trade_record)
                    logger.info(f"Recorded trade entry: {trade_id}")
                    logger.info(f"*** TRADE EXECUTED: {market_slug} | {outcome} @ {entry_price:.4f} | size={size} | order={result.get('order_id', '?')} ***")
                
                # Register with exit manager
                market_end = datetime.now(timezone.utc) + timedelta(hours=24)
                position = Position(
                    token_id=token_id,
                    entry_price=entry_price,
                    size=size,
                    entry_time=datetime.now(timezone.utc),
                    market_end_time=market_end,
                    current_price=entry_price
                )
                self.exit_manager.add_position(position)
                
                # Track mapping - store full signal data for pattern learning and resolution tracking
                self.token_to_trade[token_id] = trade_id
                self.trade_metadata[trade_id] = {
                    "token_id": token_id,
                    "entry_price": entry_price,
                    "size": size,
                    "slug": market_slug,
                    "price": raw_signal.get("price", 0),
                    "direction": raw_signal.get("direction", "UNKNOWN"),
                    "outcome": raw_signal.get("outcome", "Unknown"),
                    "usdc_size": raw_signal.get("usdc_size", 0)
                }
                logger.info(f"Position registered: {trade_id} ({token_id}) with slug: {market_slug}")
                # US-006: verify fill
                _oid = result.get('order_id', '')
                if _oid and not self.dry_run:
                    asyncio.create_task(self._verify_order_fill(_oid, market_slug))
                    
            else:
                # Order not filled or failed - free up the slug
                status = result.get("status", "NONE") if result else "NONE"
                slug = raw_signal.get("slug", "")
                logger.info(f"Order not filled (status={status}), freeing slug: {slug[-30:]}")
                if slug and slug in self.active_slugs:
                    self.active_slugs.discard(slug)
                    logger.info(f"Freed slug after failed order: {slug}")

        except Exception as e:
            logger.error(f"Execution error: {e}")
            # Remove slug from active set if execution failed entirely
            slug = raw_signal.get("slug", "")
            if slug and slug in self.active_slugs:
                self.active_slugs.discard(slug)
                logger.info(f"Removed failed slug from active set: {slug}")
    
    async def _verify_order_fill(self, order_id: str, slug: str) -> None:
        """US-006: Verify order filled after 30s"""
        try:
            await asyncio.sleep(30)
            loop = asyncio.get_event_loop()
            order = await loop.run_in_executor(
                None, lambda: self.executor.client.get_order(order_id)
            )
            status = order.get('status', 'unknown') if isinstance(order, dict) else 'unknown'
            if status in ('matched', 'filled'):
                logger.info(f"ORDER_FILL OK: {slug[-25:]} status={status}")
                self._unfilled_streak = 0
            else:
                self._unfilled_streak = getattr(self, '_unfilled_streak', 0) + 1
                logger.warning(f"ORDER_FILL WARN: {slug[-25:]} status={status} streak={self._unfilled_streak}")
                if self._unfilled_streak >= 3:
                    logger.error(f"ORDER_FILL ALERT: {self._unfilled_streak} consecutive unfilled!")
        except Exception as e:
            logger.info(f"Order fill check error: {e}")

    async def _execute_sell_exit(self, token_id: str, trade_id: str, raw_signal: dict) -> None:
        """Execute a SELL order to exit position, mirroring DB wallet's sell timing."""
        try:
            meta = self.trade_metadata.get(trade_id, {})
            entry_price = meta.get("entry_price", 0)
            position_size = meta.get("size", 0)
            slug = meta.get("slug", "")
            sell_price = float(raw_signal.get("price", 0))
    
            # Build sell signal with OUR position size
            sell_signal = dict(raw_signal)
            sell_signal["direction"] = "SELL"
            sell_signal["size_override"] = position_size
    
            if not self.dry_run:
                result = await self.executor.execute_signal(
                    sell_signal, {"passed": True, "score": 1.0}, 100.0
                )
            else:
                logger.info(f"[DRY RUN] Would SELL {position_size} @ {sell_price:.4f}")
                result = {"status": "DRY_RUN", "size": position_size}
    
            actual_exit_price = sell_price
            if result and result.get("status") in ("FILLED", "DRY_RUN"):
                pnl_usd = (actual_exit_price - entry_price) * position_size
                pnl_pct = ((actual_exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                logger.info(f"SELL SIGNAL EXIT: {slug[-30:]} | {entry_price:.4f} -> {actual_exit_price:.4f} | P&L: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
    
                # Record exit in DB (US-003)
                if self.perf_tracker_initialized:
                    await self.perf_tracker.record_exit(trade_id, actual_exit_price, "sell_signal")
    
                self.exits_triggered += 1
    
                # Cleanup state
                self.active_slugs.discard(slug)
                self.token_to_trade.pop(token_id, None)
                self.trade_metadata.pop(trade_id, None)
                self.exit_manager.remove_position(token_id)
                self.pending_sells.discard(token_id)
            else:
                logger.warning(f"SELL order failed for {slug[-30:]}: {result}")
                self.pending_sells.discard(token_id)
    
        except Exception as e:
            logger.error(f"SELL exit error for {trade_id}: {e}")
            self.pending_sells.discard(token_id)
    
    async def _handle_exit(self, token_id: str, exit_price: float, exit_reason: ExitReason) -> None:
        """Handle position exit"""
        try:
            # Try both string and original type for token_id lookup
            trade_id = self.token_to_trade.get(token_id) or self.token_to_trade.get(str(token_id))
            
            if not trade_id:
                logger.warning(f"No trade_id found for token {str(token_id)[:20]}... Available: {list(self.token_to_trade.keys())[:3]}")
                return
                
            if trade_id:
                self.exits_triggered += 1
                
                # Calculate P&L
                meta = self.trade_metadata.get(trade_id, {})
                entry_price = meta.get("entry_price", 0)
                size = meta.get("size", 0)
                pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                pnl_usd = (exit_price - entry_price) * size if entry_price > 0 else 0
                
                logger.info(f"EXIT: {trade_id} | {exit_reason.value} | Entry: {entry_price:.4f} -> Exit: {exit_price:.4f} | P&L: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
                
                # Record exit in performance tracker
                if self.perf_tracker_initialized:
                    await self.perf_tracker.record_exit(trade_id, exit_price, exit_reason.value)
                    
                    # Record outcome for pattern learning
                    self._pattern_learner.record_outcome(meta, pnl_usd)
                    self._pattern_learner.print_summary()
                
                # Cleanup
                slug = meta.get("slug", "")
                self.active_slugs.discard(slug)
                self.token_to_trade.pop(token_id, None)
                self.trade_metadata.pop(trade_id, None)
                self.exit_manager.remove_position(token_id)
                
        except Exception as e:
            logger.error(f"Error handling exit for {token_id}: {e}")
    
    async def _monitor_exits(self):
        """Background task to monitor positions for exits including market resolutions"""
        logger.info("Exit monitor started with live price feed and resolution tracking")
        while self.running:
            try:
                positions = self.exit_manager.get_open_positions()
                
                if positions:
                    # Fetch live prices for all positions
                    token_ids = [p.token_id for p in positions]
                    prices = await self.price_feed.get_prices(token_ids)
                    
                    for position in positions:
                        trade_id = self.token_to_trade.get(position.token_id)
                        meta = self.trade_metadata.get(trade_id, {})
                        slug = meta.get("slug", "")
                        
                        # Check if market has resolved
                        if slug:
                            resolution = await self.resolution_tracker.get_market_resolution(slug)
                            if resolution and resolution.get("resolved"):
                                # Market resolved - calculate actual P&L
                                market_outcome = resolution.get("outcome", "Unknown")
                                our_outcome = meta.get("outcome", "Unknown")
                                direction = meta.get("direction", "BUY")
                                
                                pnl = self.resolution_tracker.calculate_pnl(
                                    entry_price=position.entry_price,
                                    side=direction,
                                    outcome=market_outcome,
                                    our_outcome=our_outcome,
                                    size=position.size
                                )
                                
                                exit_price = pnl["exit_price"]
                                logger.info(f"RESOLUTION: {slug} resolved to '{market_outcome}', we bet '{our_outcome}', P&L: ${pnl['pnl_usd']:+.2f} ({pnl['pnl_pct']:+.2f}%)")
                                logger.info(f"Calling _handle_exit for token {str(position.token_id)[:20]}...")
                                
                                try:
                                    await self._handle_exit(position.token_id, exit_price, ExitReason.MARKET_RESOLVED)
                                    logger.info(f"Exit handled successfully")
                                except Exception as exit_err:
                                    logger.error(f"Exit handler error: {exit_err}")
                                continue
                        
                        # Check price-based exit conditions (with sanity guard)
                        live_price = prices.get(position.token_id)
                        if live_price and live_price > 0:
                            # Sanity check: reject prices < 30% of entry (bad feed)
                            if live_price < position.entry_price * 0.30:
                                if live_price > 0.02:  # Only log if not a resolved market (0.01)
                                    logger.info(f"Price sanity REJECT: {live_price:.4f} < 30% of entry {position.entry_price:.4f} for {slug[-25:]}")
                                continue
                            position.update_price(live_price)
                            
                            exit_reason = self.exit_manager.check_exit_conditions(position)
                            if exit_reason:
                                logger.info(f"Exit trigger: {exit_reason.value} for {slug[-25:]} @ {live_price:.4f}")
                                await self._handle_exit(position.token_id, live_price, exit_reason)
                        
            except Exception as e:
                logger.info(f"Exit monitor cycle: {e}")
            
            await asyncio.sleep(self.exit_manager.config.check_interval_secs)
    
    async def print_stats(self):
        """Print stats including performance metrics"""
        mode = "PAPER" if self.dry_run else "LIVE"
        logger.info(f"=== [{mode}] Stats ===")
        logger.info(f"Signals: {self.signals_received} received, {self.signals_passed} passed, {self.orders_executed} orders, {self.exits_triggered} exits")
        logger.info(f"Paper portfolio: cash=${self.paper_trader.portfolio.cash:.2f}, positions={len(self.paper_trader.portfolio.positions)}")
        logger.info(f"Exit manager: {len(self.exit_manager.positions)} open positions")
        
        if self.perf_tracker_initialized:
            stats = await self.perf_tracker.get_stats()
            total = stats.get("total_trades", 0)
            win_rate = stats.get("win_rate", 0)
            pnl = stats.get("total_pnl", 0)
            pf = stats.get("profit_factor", 0)
            logger.info(f"Performance: {total} trades, {win_rate:.1f}% win rate, ${pnl:.2f} P&L, {pf:.2f}x profit factor")
        
        exec_stats = self.executor.get_stats()
        logger.info(f"Executor: {exec_stats.get('total_orders', 0)} orders, {exec_stats.get('fill_rate', 0):.1f}% fill rate")
        if not self.dry_run:
            logger.info(f"Balance: ${self._cached_balance:.2f} USDC (low_balance={self._low_balance})")
    
    def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down signal bot...")
        self.running = False
    
    async def _learning_loop(self):
        """Autonomous learning: evaluate performance and adjust strategy every 15 min"""
        await asyncio.sleep(300)  # Wait 5 min before first eval (need data)
        while self.running:
            try:
                # Adaptive filter adjustment
                adj = self.filter.refresh_threshold()
                if adj.get("adjusted"):
                    logger.info(f"LEARNING: Filter {adj.get('action', 'adjusted')} -> min_score={self.filter.min_score:.2f}")
                else:
                    logger.info(f"LEARNING: Filter unchanged at {self.filter.min_score:.2f} ({adj.get('reason', '')})")
                
                # Pattern summary
                self._pattern_learner.print_summary()
                
                # Win rate tracking from performance.db
                if self.perf_tracker_initialized:
                    try:
                        # Query completed trades in last 4 hours
                        import time
                        now_ts = int(time.time())
                        four_hours_ago = now_ts - (4 * 3600)
                        
                        # Get database connection from performance tracker
                        conn = sqlite3.connect(self.perf_tracker.db_path)
                        cursor = conn.cursor()
                        
                        # Count completed trades (exit_time IS NOT NULL)
                        cursor.execute("""
                            SELECT COUNT(*) as total, 
                                   SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as wins
                            FROM trades
                            WHERE exit_time IS NOT NULL
                            AND entry_time > ?
                        """, (four_hours_ago,))
                        
                        result = cursor.fetchone()
                        total_trades = result[0] or 0
                        win_count = result[1] or 0
                        
                        if total_trades > 0:
                            win_rate = (win_count / total_trades) * 100
                            logger.info(f"DB Win Rate: {win_count}/{total_trades} trades = {win_rate:.1f}%")
                            
                            # Set cold streak flag based on win rate
                            if total_trades >= 10 and win_rate < 35:
                                self.db_cold_streak = True
                                logger.warning(f"COLD STREAK DETECTED: Win rate {win_rate:.1f}% < 35% threshold")
                            elif win_rate >= 45:
                                self.db_cold_streak = False
                                logger.info(f"RECOVERY: Win rate {win_rate:.1f}% >= 45%, exiting cold streak")
                        else:
                            logger.info(f"DB Win Rate: No completed trades in last 4 hours")
                        
                        conn.close()
                            
                    except Exception as e:
                        logger.error(f"Win rate tracking error: {e}")
                
                # Stats
                
                await self._cleanup_stale_state()
                # US-003: Clean stale state

                # === SELF-TUNING v1: Per-Bucket WR Logging (observation only) ===
                try:
                    three_days_ago = now_ts - (3 * 24 * 3600)
                    cursor2 = conn.cursor() if 'conn' not in dir() else sqlite3.connect(self.perf_tracker.db_path).cursor()
                    # Reconnect if conn was closed
                    _st_conn = sqlite3.connect(self.perf_tracker.db_path)
                    _st_cursor = _st_conn.cursor()

                    # Per entry-price bucket stats (3-day window)
                    buckets = [
                        ("0.60-0.65", 0.60, 0.65),
                        ("0.65-0.70", 0.65, 0.70),
                        ("0.70-0.80", 0.70, 0.80),
                        ("0.80-0.97", 0.80, 0.97),
                    ]
                    bucket_log = []
                    for bname, bmin, bmax in buckets:
                        _st_cursor.execute("""
                            SELECT COUNT(*) as total,
                                   COALESCE(SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END), 0) as wins
                            FROM trades
                            WHERE exit_time IS NOT NULL
                            AND entry_price >= ? AND entry_price < ?
                            AND entry_time > ?
                        """, (bmin, bmax, three_days_ago))
                        r = _st_cursor.fetchone()
                        bt, bw = r[0] or 0, r[1] or 0
                        bwr = (bw / bt * 100) if bt > 0 else 0
                        self._bucket_stats[bname] = {"total": bt, "wins": bw, "wr": round(bwr, 1)}
                        if bt > 0:
                            bucket_log.append(f"{bname}:{bw}/{bt}={bwr:.0f}%")

                    if bucket_log:
                        logger.info(f"BUCKET WR (3d): {' | '.join(bucket_log)}")

                    # Per-coin stats (3-day window, extract coin from slug)
                    coins = ["btc", "eth", "sol", "xrp"]
                    coin_log = []
                    for coin in coins:
                        _st_cursor.execute("""
                            SELECT COUNT(*) as total,
                                   COALESCE(SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END), 0) as wins
                            FROM trades
                            WHERE exit_time IS NOT NULL
                            AND slug LIKE ?
                            AND entry_time > ?
                        """, (f"{coin}-%", three_days_ago))
                        r = _st_cursor.fetchone()
                        ct, cw = r[0] or 0, r[1] or 0
                        cwr = (cw / ct * 100) if ct > 0 else 0
                        self._coin_stats[coin] = {"total": ct, "wins": cw, "wr": round(cwr, 1)}
                        if ct > 0:
                            coin_log.append(f"{coin}:{cw}/{ct}={cwr:.0f}%")

                    if coin_log:
                        logger.info(f"COIN WR (3d): {' | '.join(coin_log)}")

                    _st_conn.close()
                except Exception as st_err:
                    logger.warning(f"Self-tuning stats error: {st_err}")

                # === TRADE RATE MONITOR + ANTI-DEATH-SPIRAL RESET ===
                try:
                    # Count trades in last 4 hours
                    _st_conn2 = sqlite3.connect(self.perf_tracker.db_path)
                    _st_c2 = _st_conn2.cursor()
                    _st_c2.execute("SELECT COUNT(*) FROM trades WHERE entry_time > ?", (four_hours_ago,))
                    self._trades_last_4h = _st_c2.fetchone()[0] or 0
                    _st_conn2.close()

                    # Check for death spiral: no trades in 6 hours
                    _hours_since_trade = 0
                    if self._last_trade_time:
                        _hours_since_trade = (datetime.now(timezone.utc) - self._last_trade_time).total_seconds() / 3600
                    elif self._start_time:
                        _hours_since_trade = (datetime.now(timezone.utc) - self._start_time).total_seconds() / 3600

                    if _hours_since_trade >= 6 and self._trades_last_4h < 1:
                        # FORCE RESET: bot is stuck, loosen all parameters
                        old_min = self.MIN_ENTRY_PRICE
                        old_cold = self.db_cold_streak
                        self.MIN_ENTRY_PRICE = self.DEFAULT_MIN_ENTRY_PRICE
                        self.db_cold_streak = False
                        self._force_reset_count += 1
                        logger.warning(
                            f"ANTI-DEATH-SPIRAL RESET #{self._force_reset_count}: "
                            f"No trades in {_hours_since_trade:.1f}h. "
                            f"MIN_ENTRY_PRICE {old_min}->{self.MIN_ENTRY_PRICE}, "
                            f"cold_streak {old_cold}->False"
                        )
                    elif self._trades_last_4h < 2 and _hours_since_trade >= 4:
                        logger.warning(f"LOW TRADE RATE: {self._trades_last_4h} trades in 4h, {_hours_since_trade:.1f}h since last trade")
                    else:
                        logger.info(f"Trade rate: {self._trades_last_4h} trades/4h, last trade {_hours_since_trade:.1f}h ago")
                except Exception as tr_err:
                    logger.warning(f"Trade rate monitor error: {tr_err}")


                logger.info(f"LEARNING: Active slugs={len(self.active_slugs)}, Patterns={len(self._pattern_learner.patterns)}, ColdStreak={self.db_cold_streak}")
                
            except Exception as e:
                self.error_tracker.record_error(e, "learning_loop")
                logger.error(f"Learning loop error: {e}")
            
            await asyncio.sleep(900)  # Every 15 min


    async def _connection_watchdog(self):
        """US-004: Monitor signals, reconnect with exponential backoff"""
        import time as _tw
        self._last_signal_time = _tw.time()
        STALE_THRESHOLD = 120
        _backoff = 5.0
        _last_healthy = _tw.time()
        while self.running:
            try:
                await asyncio.sleep(30)
                now = _tw.time()
                gap = now - self._last_signal_time
                if gap > STALE_THRESHOLD:
                    self._reconnect_count += 1
                    logger.warning(f"WATCHDOG: No signals for {gap:.0f}s, backoff={_backoff:.0f}s (#{self._reconnect_count})")
                    try:
                        if hasattr(self.tracker, 'session') and self.tracker.session:
                            await self.tracker.session.close()
                            self.tracker.session = None
                            logger.info("WATCHDOG: Closed stale session")
                    except Exception as _re:
                        logger.warning(f"WATCHDOG: Session close error: {_re}")
                    self._last_signal_time = now
                    await asyncio.sleep(_backoff)
                    _backoff = min(_backoff * 2, 60.0)
                else:
                    if gap < 60 and (now - _last_healthy) > 300:
                        if _backoff > 5.0:
                            logger.info(f"WATCHDOG: Healthy 5min, backoff {_backoff:.0f}s -> 5s")
                        _backoff = 5.0
                        _last_healthy = now
                    elif gap > 60:
                        logger.info(f"WATCHDOG: Signal gap {gap:.0f}s")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WATCHDOG error: {e}")

    async def _cleanup_stale_state(self):
        """US-003: Clean stale entries from token_to_trade, trade_metadata, active_slugs"""
        try:
            open_token_ids = {p.token_id for p in self.exit_manager.get_open_positions()}
            
            # Clean token_to_trade entries with no matching position
            stale_tokens = [tid for tid in self.token_to_trade if tid not in open_token_ids]
            for tid in stale_tokens:
                trade_id = self.token_to_trade.pop(tid, None)
                if trade_id:
                    self.trade_metadata.pop(trade_id, None)
            
            # Clean active_slugs with no matching position
            position_slugs = set()
            for tid in open_token_ids:
                trade_id = self.token_to_trade.get(tid)
                if trade_id:
                    meta = self.trade_metadata.get(trade_id, {})
                    slug = meta.get('slug', '')
                    if slug:
                        position_slugs.add(slug)
            stale_slugs = self.active_slugs - position_slugs
            if stale_slugs:
                self.active_slugs -= stale_slugs
            
            # Clean pending_sells for tokens no longer tracked
            stale_sells = self.pending_sells - set(self.token_to_trade.keys())
            if stale_sells:
                self.pending_sells -= stale_sells
            
            total_cleaned = len(stale_tokens) + len(stale_slugs) + len(stale_sells)
            if total_cleaned > 0:
                logger.info(f"CLEANUP: Removed {len(stale_tokens)} tokens, {len(stale_slugs)} slugs, {len(stale_sells)} pending_sells")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
    
    async def _health_status_loop(self):
        """US-004: Log health status as JSON every 5 minutes"""
        await asyncio.sleep(60)  # Wait 1 min before first health log
        while self.running:
            try:
                now = datetime.now(timezone.utc)
                uptime = now - self._start_time
                uptime_mins = int(uptime.total_seconds() / 60)
                
                usdc_balance = self._cached_balance
                
                signals_5min = self.signals_received - self._last_health_signals
                self._last_health_signals = self.signals_received
                
                positions = self.exit_manager.get_open_positions()
                
                stuck = []
                for pos in positions:
                    age = now - pos.entry_time
                    if age.total_seconds() > 1800:
                        _tid = self.token_to_trade.get(pos.token_id, '')
                        _meta = self.trade_metadata.get(_tid, {})
                        stuck.append(_meta.get('slug', pos.token_id[-20:])[-30:])
                
                threshold = self.filter.min_score if hasattr(self.filter, 'min_score') else 0
                
                # Compute portfolio metrics for health log
                _h_deployed = sum(p.entry_price * p.size for p in positions)
                _h_portfolio = usdc_balance + _h_deployed
                _h_deploy_ratio = _h_deployed / _h_portfolio if _h_portfolio > 0 else 0.0
                if _h_portfolio > self._peak_equity:
                    self._peak_equity = _h_portfolio
                self._deployment_ratio = _h_deploy_ratio

                health = {
                    'type': 'HEALTH',
                    'ts': now.isoformat(),
                    'uptime_mins': uptime_mins,
                    'usdc': round(usdc_balance, 2),
                    'positions': len(positions),
                    'slugs': len(self.active_slugs),
                    'signals_5m': signals_5min,
                    'orders': self.orders_executed,
                    'exits': self.exits_triggered,
                    'stuck': stuck,
                    'threshold': round(threshold, 2),
                    'low_bal': self._low_balance,
                    'portfolio': round(_h_portfolio, 2),
                    'peak_equity': round(self._peak_equity, 2),
                    'drawdown_pct': round((1.0 - _h_portfolio / self._peak_equity) * 100, 1) if self._peak_equity > 0 else 0.0,
                    'deploy_ratio': round(_h_deploy_ratio * 100, 1),
                    'bucket_wr': self._bucket_stats,
                    'coin_wr': self._coin_stats,
                    'trades_4h': self._trades_last_4h,
                    'force_resets': self._force_reset_count,
                    'reconnects': self._reconnect_count,
                    'map_sz': len(self.token_to_trade),
                }
                
                # Self-tuning cycle (tuner has own 15-min cooldown)
                try:
                    self.self_tuner.run_cycle()
                    health['tuner_mults'] = dict(self.self_tuner._cached_multipliers)
                    health['tuner_kill'] = self.self_tuner.state.get('kill_switch_active', False)
                except Exception as e:
                    logger.error(f"Self-tuner cycle error: {e}")
                
                logger.info(f"HEALTH: {json.dumps(health)}")
                
                if stuck:
                    logger.warning(f"STUCK POSITIONS ({len(stuck)}): {stuck}")

                # US-005: Daily self-restart
                if uptime_mins > 1200:  # 20 hours
                    _opos = self.exit_manager.get_open_positions()
                    if len(_opos) == 0 or uptime_mins > 1440:  # Force at 24h
                        if len(_opos) > 0:
                            logger.warning(f"SELF-RESTART: FORCED at {uptime_mins//60}h with {len(_opos)} open positions")
                        else:
                            logger.info(f"SELF-RESTART: uptime={uptime_mins//60}h, clean (0 positions)")
                        self.running = False
                        self.error_tracker._save_errors()  # Flush before exit
                        import sys as _sys; _sys.exit(0)
                    else:
                        logger.info(f"SELF-RESTART: deferred, {len(_opos)} positions (uptime={uptime_mins//60}h)")

                
            except Exception as e:
                logger.error(f"Health status error: {e}")
            
            await asyncio.sleep(300)  # Every 5 minutes
    
    async def _redeem_loop(self):
        """US-005: Periodically redeem resolved positions"""
        await asyncio.sleep(120)  # Wait 2 min before first check
        while self.running:
            try:
                count = await self.position_redeemer.redeem_all()
                if count > 0:
                    logger.info(f"AUTO-CLAIM: Redeemed {count} resolved position(s)")
            except Exception as e:
                logger.error(f"Redeem loop error: {e}")
            await asyncio.sleep(600)  # Every 10 minutes
    
    async def run(self):
        """Main run loop with signal tracker"""
        mode = "PAPER" if self.dry_run else "LIVE"
        logger.info(f"Starting signal bot in {mode} mode...")
        logger.info(f"Starting capital: ${STARTING_CAPITAL}")
        logger.info(f"Min filter score: {MIN_FILTER_SCORE}")
        # Quick DB status
        try:
            _conn = sqlite3.connect(self.perf_tracker.db_path)
            _c = _conn.cursor()
            _c.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NULL")
            _open = _c.fetchone()[0]
            _c.execute("SELECT SUM(profit_loss) FROM trades WHERE exit_time IS NOT NULL")
            _pnl = _c.fetchone()[0] or 0
            _conn.close()
            logger.info(f"STATUS: open={_open} | slugs={len(self.active_slugs)} | pnl=${_pnl:+.2f} | signals={self.signals_received} passed={self.signals_passed} orders={self.orders_executed}")
        except Exception:
            pass
        
        await self.init_async()

        # US-003: Tell systemd we are ready
        notify_ready()
        logger.info("Sent READY=1 to systemd")
        
        # Intercept emit_signal to feed into our handler
        original_emit = self.tracker.emit_signal
        
        def intercepted_emit(trade):
            result = original_emit(trade)
            if result:
                direction = self.tracker.extract_direction(trade)
                outcome = self.tracker.extract_outcome(trade)
                market = trade.get("market", {})
                
                raw_signal = {
                    "tx_hash": trade.get("transactionHash", trade.get("txHash", "")),
                    "timestamp": trade.get("timestamp", ""),
                    "direction": direction,
                    "outcome": outcome,
                    "asset": trade.get("asset", ""),
                    "price": float(trade.get("price", 0)),
                    "usdc_size": float(trade.get("usdcSize", 0)),
                    "market_title": trade.get("title", "") or (market.get("title", "") if isinstance(market, dict) else ""),
                    "slug": trade.get("slug", trade.get("eventSlug", "")) or (market.get("slug", "") if isinstance(market, dict) else "")
                }
                self.handle_signal(raw_signal)
            return result
        
        self.tracker.emit_signal = intercepted_emit
        
        async def stats_loop():
            while self.running:
                await asyncio.sleep(60)
                await self.print_stats()
                notify_watchdog()
        
        stats_task = asyncio.create_task(stats_loop())
        learning_task = asyncio.create_task(self._learning_loop())
        watchdog_task = asyncio.create_task(self._connection_watchdog())
        health_task = asyncio.create_task(self._health_status_loop())
        redeem_task = asyncio.create_task(self._redeem_loop())
        exit_task = asyncio.create_task(self._monitor_exits())
        
        try:
            await self.tracker.run()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.running = False
            stats_task.cancel()
            exit_task.cancel()
            learning_task.cancel()
            watchdog_task.cancel()
            health_task.cancel()
            redeem_task.cancel()
            await self.price_feed.close()
            await self.resolution_tracker.close()
            await self.print_stats()
            logger.info("Signal bot stopped.")
    
    async def _periodic_stats(self):
        """Print stats periodically"""
        while self.running:
            try:
                await asyncio.sleep(60)
                await self.print_stats()
                notify_watchdog()  # Tell systemd we're alive
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Stats error: {e}")


async def main():
    """Main entry point"""
    dry_run = os.getenv("DRY_RUN", "true").lower() != "false"
    bot = SignalBot(dry_run=dry_run)
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
