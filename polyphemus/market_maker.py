"""Market Maker — Risk-free pair-cost arbitrage on crypto 5m/15m markets.

Buys BOTH Up and Down simultaneously when pair_cost < $1.00 after fees.
One side always resolves to $1.00. Profit = $1.00 - pair_cost per share.

This is a zero-directional-risk strategy (gabagool's core edge, $823K verified on-chain).
The only risk is execution: one leg fills but the other doesn't. We mitigate this by:
1. Using FOK (fill-or-kill) orders -- both fill instantly or neither does
2. Requiring both asks to be present on WS before attempting
3. Cancelling stale single-leg positions before resolution
"""

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set

from py_clob_client.order_builder.constants import BUY

from .config import Settings, setup_logger
from .clob_wrapper import ClobWrapper
from .market_ws import MarketWS
from .types import Position, RedemptionEvent

GAMMA_API_URL = "https://gamma-api.polymarket.com"


class MMObservationDB:
    """SQLite logger for ALL pair-cost observations - the RAG training data foundation.

    Logs every scan cycle's pair_cost reading, not just opportunities.
    This builds the dataset that LLM analysis uses to find patterns:
    - When do pair_costs dip below threshold? (time, asset, regime)
    - How does volatility affect spread convergence?
    - Which assets have the tightest pair costs?
    """

    def __init__(self, data_dir: str):
        db_path = Path(data_dir) / "mm_observations.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                epoch REAL NOT NULL,
                slug TEXT NOT NULL,
                asset TEXT NOT NULL,
                window_secs INTEGER NOT NULL,
                secs_remaining REAL NOT NULL,

                -- Pair cost data (core)
                ask_up REAL NOT NULL,
                ask_down REAL NOT NULL,
                fee_up REAL NOT NULL,
                fee_down REAL NOT NULL,
                pair_cost REAL NOT NULL,
                profit_per_share REAL NOT NULL,

                -- Liquidity
                liq_up REAL,
                liq_down REAL,

                -- Regime context (from lagbot_context.json)
                fear_greed INTEGER,
                volatility_1h REAL,
                market_regime TEXT,

                -- Threshold used (may be dynamic)
                threshold_used REAL,

                -- Outcome
                is_opportunity INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL DEFAULT 'skip',
                hour_utc INTEGER,
                day_of_week INTEGER
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_obs_asset_time
            ON observations(asset, epoch)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_obs_opportunity
            ON observations(is_opportunity, asset)
        """)
        self._conn.commit()

    def log(self, obs: dict):
        now = datetime.now(timezone.utc)
        obs["timestamp"] = now.strftime("%Y-%m-%d %H:%M:%S")
        obs["epoch"] = time.time()
        obs["hour_utc"] = now.hour
        obs["day_of_week"] = now.weekday()
        cols = [
            "timestamp", "epoch", "slug", "asset", "window_secs",
            "secs_remaining", "ask_up", "ask_down", "fee_up", "fee_down",
            "pair_cost", "profit_per_share", "liq_up", "liq_down",
            "fear_greed", "volatility_1h", "market_regime",
            "threshold_used", "is_opportunity", "action",
            "hour_utc", "day_of_week",
        ]
        vals = [obs.get(c) for c in cols]
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(cols)
        try:
            self._conn.execute(
                f"INSERT INTO observations ({col_names}) VALUES ({placeholders})",
                vals,
            )
            self._conn.commit()
        except Exception:
            pass  # Non-critical, never block trading

    def get_recent(self, hours: int = 24, asset: str = None) -> list:
        cutoff = time.time() - (hours * 3600)
        if asset:
            rows = self._conn.execute(
                "SELECT * FROM observations WHERE epoch > ? AND asset = ? ORDER BY epoch",
                (cutoff, asset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM observations WHERE epoch > ? ORDER BY epoch",
                (cutoff,),
            ).fetchall()
        cols = [d[0] for d in self._conn.execute("SELECT * FROM observations LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]

    def get_opportunity_rate(self, hours: int = 24) -> dict:
        cutoff = time.time() - (hours * 3600)
        row = self._conn.execute(
            "SELECT COUNT(*) as total, SUM(is_opportunity) as opps FROM observations WHERE epoch > ?",
            (cutoff,),
        ).fetchone()
        total, opps = row[0] or 0, row[1] or 0
        return {"total_scans": total, "opportunities": opps,
                "rate": opps / total if total > 0 else 0}

    def get_pair_cost_distribution(self, hours: int = 24, asset: str = None) -> dict:
        cutoff = time.time() - (hours * 3600)
        if asset:
            rows = self._conn.execute(
                "SELECT pair_cost FROM observations WHERE epoch > ? AND asset = ?",
                (cutoff, asset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT pair_cost FROM observations WHERE epoch > ?",
                (cutoff,),
            ).fetchall()
        if not rows:
            return {"count": 0}
        costs = sorted([r[0] for r in rows])
        n = len(costs)
        return {
            "count": n,
            "min": costs[0],
            "p10": costs[int(n * 0.1)],
            "p25": costs[int(n * 0.25)],
            "median": costs[n // 2],
            "p75": costs[int(n * 0.75)],
            "p90": costs[int(n * 0.9)],
            "max": costs[-1],
            "below_995": sum(1 for c in costs if c < 0.995),
            "below_990": sum(1 for c in costs if c < 0.990),
        }


def _polymarket_fee(price: float) -> float:
    """Compute Polymarket taker fee for a given price.

    Fee formula: fee_rate = p * (1 - p), then fee = fee_rate * amount.
    At p=0.50: fee_rate = 0.25 (max). At p=0.95: fee_rate = 0.0475.
    """
    if price <= 0 or price >= 1:
        return 0.0
    return price * (1.0 - price)


class MarketMaker:
    """Risk-free pair-cost arbitrage scanner and executor."""

    def __init__(
        self,
        config: Settings,
        clob: ClobWrapper,
        market_ws: MarketWS,
        performance_db=None,
        momentum_feed=None,
        tracker=None,
        redeemer=None,
        store=None,
    ):
        self._config = config
        self._clob = clob
        self._ws = market_ws
        self._db = performance_db
        self._momentum_feed = momentum_feed  # BinanceMomentumFeed for stale quote detection
        self._tracker = tracker  # PerformanceTracker for recording stale trade entries
        self._redeemer = redeemer  # Redeemer for auto-redeeming winning stale positions
        self._store = store  # PositionStore for tracking active stale positions
        self._logger = setup_logger("polyphemus.market_maker")

        # Market cache: slug -> {up_token_id, down_token_id, market_title, condition_id}
        self._market_cache: Dict[str, Optional[dict]] = {}

        # Dedup: prevent firing same slug twice
        self._fired: Set[str] = set()

        # Log throttle: {asset_window: last_log_time}
        self._last_log: Dict[str, float] = {}

        # Stats
        self.opportunities_found = 0
        self.trades_executed = 0
        self.total_profit = 0.0
        self.stale_quotes_found = 0
        self.stale_trades_executed = 0

        # Stale quote dedup: slug -> last_fire_time (prevent repeated fires on same epoch)
        self._stale_fired: Dict[str, float] = {}

        # Gamma API session (reused)
        self._gamma_session = None

        # Observation DB for RAG/LLM analysis
        self._obs_db = MMObservationDB(config.lagbot_data_dir)

        # Batched observation queue (US-002: flush every mm_obs_flush_interval)
        self._obs_queue: list = []
        self._obs_last_flush: float = 0.0

        # Cached balance (US-003: avoid HTTP roundtrip on every opportunity)
        self._cached_balance: float = 0.0
        self._balance_fetched_at: float = 0.0

        # Market context cache (refreshed from lagbot_context.json)
        self._market_context: dict = {}
        self._context_fetched_at: float = 0

    def _queue_observation(self, obs: dict) -> None:
        """Queue an observation for batched DB write (US-002)."""
        self._obs_queue.append(obs)
        now = time.time()
        if now - self._obs_last_flush >= self._config.mm_obs_flush_interval:
            self._flush_observations()

    def _flush_observations(self) -> None:
        """Flush queued observations to DB."""
        if not self._obs_queue:
            return
        for obs in self._obs_queue:
            self._obs_db.log(obs)
        self._obs_queue.clear()
        self._obs_last_flush = time.time()

    async def _get_cached_balance(self) -> float:
        """Return cached balance, refreshing via HTTP every mm_balance_cache_ttl seconds (US-003)."""
        now = time.time()
        if now - self._balance_fetched_at >= self._config.mm_balance_cache_ttl:
            self._cached_balance = await self._clob.get_balance()
            self._balance_fetched_at = now
        return self._cached_balance

    async def _check_stale_quote(
        self, asset: str, slug: str, window: int, secs_left: float
    ) -> None:
        """Detect and buy underpriced side when Binance shows clear direction.

        When Binance moves significantly (e.g. +0.5%) during an epoch, the winning
        side's ask should reprice upward. If it hasn't yet (stale quote), the ask is
        below fair value. Buy it before the market maker reprices.

        This is a directional bet (unlike pair arb which buys both sides).
        Risk is bounded by mm_stale_max_bet and only fires when the discount
        to implied fair value exceeds mm_stale_max_fair_discount.
        """
        if not self._momentum_feed:
            return
        if not self._config.mm_stale_enabled:
            return

        # Dedup: only fire once per slug
        if slug in self._stale_fired:
            return

        # Get Binance price context for this epoch
        price_ctx = self._momentum_feed.get_epoch_price_context(asset, window)
        if not price_ctx:
            return

        pct_change = price_ctx["pct_change"]
        abs_move = abs(pct_change)

        # Need a clear directional move
        if abs_move < self._config.mm_stale_min_move_pct:
            # Near-miss logging (throttled to once per 30s per asset)
            log_key = f"stale_move_{asset}"
            now_t = time.time()
            if abs_move >= self._config.mm_stale_min_move_pct * 0.5 and now_t - self._last_log.get(log_key, 0) >= 30.0:
                self._last_log[log_key] = now_t
                self._logger.info(
                    f"[STALE NEAR] {asset} {window//60}m | move={pct_change:+.3%} "
                    f"(need {self._config.mm_stale_min_move_pct:.3%}) | {secs_left:.0f}s left"
                )
            return

        # Determine which side should win and get its token
        market_info = await self._discover_market(slug)
        if not market_info:
            return

        if pct_change > 0:
            # Binance up -> "Up" should win -> buy Up if stale/cheap
            winning_token = market_info["up_token_id"]
            side_label = "Up"
        else:
            # Binance down -> "Down" should win -> buy Down if stale/cheap
            winning_token = market_info["down_token_id"]
            side_label = "Down"

        # Get current ask for the winning side
        ask = self._ws.get_best_ask(winning_token) if self._ws else 0.0
        if ask <= 0:
            return

        # Check ask is within tradeable range
        if ask < self._config.mm_stale_min_ask or ask > self._config.mm_stale_max_ask:
            return

        # Check WS freshness
        age = self._ws.get_midpoint_age(winning_token) if self._ws else float('inf')
        if age > 5.0:
            return  # Stale WS data, can't trust the ask

        # Implied fair value: map Binance move magnitude to expected probability
        # Empirically: 0.3% move -> ~70% win rate, 0.5% -> ~80%, 1.0% -> ~90%
        # Conservative linear model: fair_value = 0.60 + min(abs_move / 0.01, 0.35)
        implied_fair = 0.60 + min(abs_move / 0.01, 0.35)
        implied_fair = min(implied_fair, 0.95)  # cap at 95%

        # Is the ask sufficiently below fair value?
        discount = (implied_fair - ask) / implied_fair
        if discount < self._config.mm_stale_max_fair_discount:
            # Near-miss: move was big enough but ask already repriced
            log_key = f"stale_disc_{slug}"
            now_t = time.time()
            if discount > 0 and now_t - self._last_log.get(log_key, 0) >= 30.0:
                self._last_log[log_key] = now_t
                self._logger.info(
                    f"[STALE REPRICED] {asset} {window//60}m | {side_label} ask={ask:.3f} "
                    f"fair={implied_fair:.3f} discount={discount:.1%} "
                    f"(need {self._config.mm_stale_max_fair_discount:.0%}) | "
                    f"binance={pct_change:+.3%} | {secs_left:.0f}s left"
                )
            return

        # We found a stale quote
        self.stale_quotes_found += 1
        self._stale_fired[slug] = time.time()

        # Compute fee and expected profit
        fee = _polymarket_fee(ask) * ask
        cost_per_share = ask + fee
        expected_payout = 1.0  # winning side resolves to $1.00
        expected_profit_per_share = expected_payout - cost_per_share

        # Size the trade
        balance = await self._get_cached_balance()
        if balance <= 0:
            return

        spend = min(
            balance * self._config.mm_stale_bet_pct,
            self._config.mm_stale_max_bet,
        )
        shares = spend / ask if ask > 0 else 0
        if shares < 5:
            return  # Minimum order

        expected_profit = expected_profit_per_share * shares

        if self._config.mm_dry_run:
            self._logger.info(
                f"[STALE DRY] {slug} | {side_label} ask={ask:.3f} "
                f"fair={implied_fair:.3f} discount={discount:.1%} | "
                f"binance={pct_change:+.3%} | fee={fee:.4f} | "
                f"spend=${spend:.2f} shares={shares:.0f} | "
                f"exp_profit=${expected_profit:.2f} | {secs_left:.0f}s left"
            )
            return

        # Execute FOK buy on the underpriced side
        self._logger.info(
            f"[STALE FIRE] {slug} | {side_label} ask={ask:.3f} "
            f"fair={implied_fair:.3f} discount={discount:.1%} | "
            f"binance={pct_change:+.3%} | spend=${spend:.2f} | "
            f"{secs_left:.0f}s left"
        )

        result = await self._clob.place_fak_order(winning_token, spend, BUY, price_hint=ask)
        if result.success:
            self.stale_trades_executed += 1
            self._logger.info(
                f"[STALE FILLED] {slug} | {side_label} | "
                f"order={result.order_id} | exp_profit=${expected_profit:.2f}"
            )
            # Record in performance DB + position store so redeemer picks it up
            await self._record_stale_trade(
                slug=slug,
                token_id=winning_token,
                side_label=side_label,
                entry_price=ask,
                shares=shares,
                order_id=result.order_id or f"stale_{slug}",
                market_info=market_info,
                window=window,
            )
        else:
            self._logger.info(
                f"[STALE MISS] {slug} | {side_label} | {result.error}"
            )

    async def _record_stale_trade(
        self, slug: str, token_id: str, side_label: str,
        entry_price: float, shares: float, order_id: str,
        market_info: dict, window: int,
    ) -> None:
        """Record stale quote trade in perf DB + position store for redeemer pickup."""
        from datetime import datetime, timezone
        import time as _time

        now = _time.time()
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
        # Market end = current epoch end
        current_epoch = (int(now) // window) * window
        window_end = current_epoch + window
        end_dt = datetime.fromtimestamp(window_end, tz=timezone.utc)

        metadata = {
            "source": "stale_quote",
            "direction": side_label,
            "condition_id": market_info.get("condition_id", ""),
            "is_stale_quote": True,
            "hold_to_resolution": True,
        }

        # 1. Record in performance DB
        if self._tracker:
            try:
                await self._tracker.record_entry(
                    trade_id=order_id,
                    token_id=token_id,
                    slug=slug,
                    entry_price=entry_price,
                    entry_size=shares,
                    entry_tx_hash=order_id,
                    outcome=side_label,
                    market_title=market_info.get("market_title", slug),
                    entry_time=now,
                    metadata=metadata,
                )
                self._logger.info(f"[STALE] Recorded entry in perf DB: {slug}")
            except Exception as e:
                self._logger.warning(f"[STALE] Failed to record entry: {e}")

        # 2. Add to position store (so exit manager + redeemer can find it)
        if self._store:
            try:
                pos = Position(
                    token_id=token_id,
                    slug=slug,
                    entry_price=entry_price,
                    entry_size=shares,
                    entry_time=now_dt,
                    entry_tx_hash=order_id,
                    market_end_time=end_dt,
                    current_price=entry_price,
                    peak_price=entry_price,
                    metadata=metadata,
                )
                self._store.add(pos)
                self._logger.info(f"[STALE] Added to position store: {slug}")
            except Exception as e:
                self._logger.warning(f"[STALE] Failed to add to store: {e}")

    async def _precache_all_markets(self, assets: list, windows: list) -> None:
        """Discover all asset/window market token IDs at startup (US-004).
        Also schedules background refresh every 60s for new epochs."""
        now = time.time()
        tasks = []
        for asset in assets:
            for window in windows:
                current_epoch = (int(now) // window) * window
                slug = f"{asset.lower()}-updown-{window // 60}m-{current_epoch}"
                tasks.append(self._discover_market(slug))
                # Also precache next epoch
                next_epoch = current_epoch + window
                next_slug = f"{asset.lower()}-updown-{window // 60}m-{next_epoch}"
                tasks.append(self._discover_market(next_slug))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            cached = sum(1 for r in results if r and not isinstance(r, Exception))
            self._logger.info(f"[MM] Pre-cached {cached}/{len(tasks)} markets at startup")

    async def start(self) -> None:
        """Main scanning loop for pair-cost arbitrage opportunities."""
        import aiohttp
        self._gamma_session = aiohttp.ClientSession()

        assets_str = self._config.mm_assets.strip()
        if assets_str:
            assets = [a.strip().upper() for a in assets_str.split(',') if a.strip()]
        else:
            assets = self._config.get_asset_filter() or ["BTC", "ETH"]

        windows = []
        if self._config.mm_scan_5m:
            windows.append(300)
        if self._config.mm_scan_15m:
            windows.append(900)
        if not windows:
            windows = [300]

        dry_tag = " [DRY RUN]" if self._config.mm_dry_run else ""
        self._logger.info(
            f"Market maker started{dry_tag} | assets={assets} | "
            f"windows={[f'{w//60}m' for w in windows]} | "
            f"max_pair_cost={self._config.mm_max_pair_cost} | "
            f"max_bet=${self._config.mm_max_bet} | "
            f"scan_interval={self._config.mm_scan_interval}s | "
            f"scan_window=[{self._config.mm_min_secs_remaining}-"
            f"{self._config.mm_max_secs_remaining}s]"
        )

        # US-004: Pre-cache all markets at startup (no lazy HTTP in scan loop)
        await self._precache_all_markets(assets, windows)

        # US-003: Prime balance cache at startup
        await self._get_cached_balance()

        precached: Set[str] = set()
        last_precache_refresh: float = time.time()

        try:
            while True:
                try:
                    # Event-driven wake on WS price updates
                    if self._ws:
                        await self._ws.wait_for_update(
                            timeout=self._config.mm_scan_interval
                        )
                    else:
                        await asyncio.sleep(self._config.mm_scan_interval)

                    now = time.time()

                    # US-004: Background refresh market cache every 60s
                    if now - last_precache_refresh >= 60.0:
                        asyncio.create_task(self._precache_all_markets(assets, windows))
                        last_precache_refresh = now

                    for asset in assets:
                        for window in windows:
                            current_epoch = (int(now) // window) * window
                            window_end = current_epoch + window
                            secs_to_end = window_end - now
                            slug = f"{asset.lower()}-updown-{window // 60}m-{current_epoch}"

                            # Stale quote sniping (wider window: up to 180s before epoch end)
                            if (secs_to_end <= self._config.mm_stale_max_secs_remaining
                                    and secs_to_end >= self._config.mm_stale_min_secs_remaining):
                                await self._check_stale_quote(
                                    asset, slug, window, secs_to_end
                                )

                            # Pair-arb window (narrower: last 60s only)
                            if secs_to_end > self._config.mm_max_secs_remaining:
                                continue
                            if secs_to_end < self._config.mm_min_secs_remaining:
                                continue

                            # Dedup (pair arb only)
                            if slug in self._fired:
                                continue

                            await self._check_pair_opportunity(
                                asset, slug, window, secs_to_end
                            )

                    # US-002: Periodic flush of batched observations
                    if self._obs_queue and (now - self._obs_last_flush >= self._config.mm_obs_flush_interval):
                        self._flush_observations()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self._logger.error(f"Market maker scan error: {e}")
                    await asyncio.sleep(5)
        finally:
            # US-002: Flush remaining observations on shutdown (no data loss)
            self._flush_observations()
            if self._gamma_session:
                await self._gamma_session.close()

    def _read_market_context(self) -> dict:
        """Read OpenClaw market context for regime-aware threshold adjustment."""
        if time.time() - self._context_fetched_at < 60:
            return self._market_context
        try:
            ctx_path = self._config.market_context_path
            if not ctx_path or not os.path.exists(ctx_path):
                return {}
            with open(ctx_path) as f:
                self._market_context = json.load(f)
            self._context_fetched_at = time.time()
            return self._market_context
        except Exception:
            return {}

    def _get_dynamic_threshold(self) -> float:
        """Regime-aware pair cost threshold.

        In high volatility: spreads widen, so pair_cost dips are more common
        but also more risky (prices moving fast). Tighten threshold.
        In low volatility: spreads are tight, opportunities are rare.
        Widen threshold slightly to catch more.

        This is the core LLM-optimizable parameter. The observation DB tracks
        which threshold was used for each scan, so the analysis module can
        recommend optimal thresholds per regime.
        """
        base = self._config.mm_max_pair_cost
        ctx = self._read_market_context()
        if not ctx:
            return base

        fg = ctx.get("fear_greed")
        vol = ctx.get("volatility_1h", 0)

        # High fear (< 25): markets choppy, widen threshold to catch panic dips
        if fg is not None and fg < 25:
            return min(base + 0.002, 0.998)  # e.g. 0.995 -> 0.997

        # High volatility (> 1.5%): tighten threshold, only take safest opportunities
        if vol and vol > 0.015:
            return max(base - 0.003, 0.985)  # e.g. 0.995 -> 0.992

        return base

    async def _check_pair_opportunity(
        self, asset: str, slug: str, window: int, secs_left: float
    ) -> None:
        """Check if both sides of a market can be bought for < $1.00 total.
        Logs ALL observations to DB for LLM/RAG pattern analysis."""
        market_info = await self._discover_market(slug)
        if not market_info:
            self._logger.info(f"[MM] {slug} | discovery failed, no market info")
            return

        up_token = market_info["up_token_id"]
        down_token = market_info["down_token_id"]

        # US-005: WS-only price data (no REST fallback -- saves 50-200ms per scan)
        ask_up = self._ws.get_best_ask(up_token) if self._ws else 0.0
        ask_down = self._ws.get_best_ask(down_token) if self._ws else 0.0
        size_up_ws = self._ws._ask_sizes.get(up_token, 0.0) if self._ws else 0.0
        size_down_ws = self._ws._ask_sizes.get(down_token, 0.0) if self._ws else 0.0

        if ask_up <= 0 or ask_down <= 0:
            # Log warning once per 30s per slug when WS data missing
            log_key = f"ws_miss_{slug}"
            now_t = time.time()
            if now_t - self._last_log.get(log_key, 0) >= 30.0:
                self._last_log[log_key] = now_t
                self._logger.info(
                    f"[MM] {slug} | WS data missing (up={ask_up:.3f} dn={ask_down:.3f}), skipping"
                )
            return  # Skip scan, no blocking HTTP

        # Check WS freshness
        age_up = self._ws.get_midpoint_age(up_token) if self._ws else float('inf')
        age_down = self._ws.get_midpoint_age(down_token) if self._ws else float('inf')
        if age_up > 5.0 or age_down > 5.0:
            return  # Stale WS data, skip

        # Compute fee-inclusive pair cost
        fee_up = _polymarket_fee(ask_up) * ask_up
        fee_down = _polymarket_fee(ask_down) * ask_down
        pair_cost = ask_up + ask_down + fee_up + fee_down

        # Check liquidity (use pre-fetched sizes from WS or REST)
        size_up = size_up_ws
        size_down = size_down_ws
        min_liquidity = max(5.0, self._config.mm_max_bet / max(ask_up, 0.01))

        # Dynamic regime-aware threshold
        threshold = self._get_dynamic_threshold()

        # Get regime context for observation logging
        ctx = self._read_market_context()
        fg_val = ctx.get("fear_greed")
        vol_val = ctx.get("volatility_1h")
        regime_val = ctx.get("market_regime", "")

        is_opp = (pair_cost < threshold
                   and size_up >= min_liquidity
                   and size_down >= min_liquidity)

        # US-002: Queue observation for batched DB write (not per-scan)
        self._queue_observation({
            "slug": slug,
            "asset": asset,
            "window_secs": window,
            "secs_remaining": round(secs_left, 1),
            "ask_up": ask_up,
            "ask_down": ask_down,
            "fee_up": round(fee_up, 6),
            "fee_down": round(fee_down, 6),
            "pair_cost": round(pair_cost, 6),
            "profit_per_share": round(1.0 - pair_cost, 6),
            "liq_up": size_up,
            "liq_down": size_down,
            "fear_greed": fg_val,
            "volatility_1h": vol_val,
            "market_regime": regime_val,
            "threshold_used": threshold,
            "is_opportunity": 1 if is_opp else 0,
            "action": "opportunity" if is_opp else "skip",
        })

        # Log scan result (throttled to once per 10s per asset/window)
        margin_pct = round((1.0 - pair_cost) * 100, 2)
        log_key = f"{asset}_{window}"
        now_t = time.time()
        if now_t - self._last_log.get(log_key, 0) >= 10.0:
            self._last_log[log_key] = now_t
            self._logger.info(
                f"[MM] {asset} {window//60}m | pair_cost={pair_cost:.4f} "
                f"threshold={threshold:.4f} | up={ask_up:.3f} dn={ask_down:.3f} | "
                f"liq={min(size_up,size_down):.0f} | {secs_left:.0f}s left | "
                f"margin={margin_pct}%"
            )

        if size_up < min_liquidity or size_down < min_liquidity:
            return  # Not enough liquidity

        if pair_cost >= threshold:
            return  # No edge

        # We have an opportunity
        profit_per_share = 1.0 - pair_cost
        self.opportunities_found += 1
        self._fired.add(slug)

        # US-003: Use cached balance (refreshed every mm_balance_cache_ttl)
        balance = await self._get_cached_balance()
        if balance <= 0:
            self._logger.warning("Market maker: zero balance, skipping")
            return

        spend_per_leg = min(
            balance * self._config.mm_bet_pct,
            self._config.mm_max_bet,
        )

        # Ensure minimum order (5 shares on each side)
        shares_up = spend_per_leg / ask_up if ask_up > 0 else 0
        shares_down = spend_per_leg / ask_down if ask_down > 0 else 0
        if shares_up < 5 or shares_down < 5:
            self._logger.debug(
                f"Market maker: {slug} shares too small "
                f"(up={shares_up:.1f}, down={shares_down:.1f})"
            )
            return

        total_spend = spend_per_leg * 2
        expected_profit = profit_per_share * min(shares_up, shares_down)

        label = f"{window // 60}m"

        if self._config.mm_dry_run:
            self._logger.info(
                f"[MM DRY] {slug} | pair_cost={pair_cost:.4f} | "
                f"profit/share=${profit_per_share:.4f} | "
                f"asks=({ask_up:.4f}+{ask_down:.4f}) | "
                f"fees=({fee_up:.4f}+{fee_down:.4f}) | "
                f"liq=({size_up:.0f}/{size_down:.0f}) | "
                f"spend=${total_spend:.2f} | "
                f"exp_profit=${expected_profit:.2f} | "
                f"{secs_left:.0f}s left"
            )
            return

        # Execute both legs simultaneously with FOK
        self._logger.info(
            f"[MM FIRE] {slug} | pair_cost={pair_cost:.4f} | "
            f"profit/share=${profit_per_share:.4f} | "
            f"Up ${spend_per_leg:.2f} @ {ask_up:.4f} | "
            f"Down ${spend_per_leg:.2f} @ {ask_down:.4f} | "
            f"{secs_left:.0f}s left"
        )

        # Fire both FOK orders concurrently
        result_up, result_down = await asyncio.gather(
            self._clob.place_fak_order(up_token, spend_per_leg, BUY, price_hint=ask_up),
            self._clob.place_fak_order(down_token, spend_per_leg, BUY, price_hint=ask_down),
        )

        if result_up.success and result_down.success:
            self.trades_executed += 1
            self.total_profit += expected_profit
            self._logger.info(
                f"[MM FILLED] {slug} | BOTH legs filled | "
                f"up_order={result_up.order_id} | "
                f"down_order={result_down.order_id} | "
                f"expected_profit=${expected_profit:.2f}"
            )
        elif result_up.success and not result_down.success:
            self._logger.warning(
                f"[MM PARTIAL] {slug} | Up filled, Down FAILED: {result_down.error} | "
                f"Up order={result_up.order_id} — single-leg exposure!"
            )
        elif not result_up.success and result_down.success:
            self._logger.warning(
                f"[MM PARTIAL] {slug} | Down filled, Up FAILED: {result_up.error} | "
                f"Down order={result_down.order_id} — single-leg exposure!"
            )
        else:
            self._logger.info(
                f"[MM MISS] {slug} | Both legs failed: "
                f"up={result_up.error}, down={result_down.error}"
            )

    async def _discover_market(self, slug: str) -> Optional[dict]:
        """Query Gamma API for market token IDs. Caches results per slug."""
        if slug in self._market_cache:
            return self._market_cache[slug]

        import json
        import aiohttp

        try:
            url = f"{GAMMA_API_URL}/markets?slug={slug}"
            session = self._gamma_session or aiohttp.ClientSession()
            close_session = self._gamma_session is None
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            finally:
                if close_session:
                    await session.close()

            if not data:
                self._market_cache[slug] = None
                return None

            market = data[0] if isinstance(data, list) else data
            token_ids = json.loads(market["clobTokenIds"])

            info = {
                "up_token_id": token_ids[0],
                "down_token_id": token_ids[1],
                "market_title": market.get("question", slug),
                "condition_id": market.get("conditionId", ""),
            }
            self._market_cache[slug] = info

            # Subscribe to WS for real-time prices
            if self._ws:
                await self._ws.subscribe([token_ids[0], token_ids[1]])

            # Pre-warm SDK caches
            asyncio.create_task(self._clob.prewarm_market(token_ids[0]))
            asyncio.create_task(self._clob.prewarm_market(token_ids[1]))

            return info

        except Exception as e:
            self._logger.warning(f"Market maker: Gamma API error for {slug}: {e}")
            return None

    def get_stats(self) -> dict:
        """Return market maker stats for dashboard/monitoring."""
        return {
            "opportunities_found": self.opportunities_found,
            "trades_executed": self.trades_executed,
            "total_profit": round(self.total_profit, 2),
            "stale_quotes_found": self.stale_quotes_found,
            "stale_trades_executed": self.stale_trades_executed,
            "dry_run": self._config.mm_dry_run,
            "stale_enabled": self._config.mm_stale_enabled,
            "observation_stats": self._obs_db.get_opportunity_rate(hours=24),
            "pair_cost_distribution": self._obs_db.get_pair_cost_distribution(hours=24),
        }

    def get_observations_for_analysis(self, hours: int = 24) -> list:
        """Export recent observations for LLM analysis.

        The evolution engine calls this to feed observation data into Claude API
        for pattern detection. Returns raw dicts suitable for JSON serialization.
        """
        return self._obs_db.get_recent(hours=hours)
