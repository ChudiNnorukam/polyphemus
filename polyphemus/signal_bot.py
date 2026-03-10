"""
Main orchestrator for Polyphemus Polymarket Trading Bot.

Coordinates all modules: signal feed, position execution, exit management,
performance tracking, balance management, and health monitoring.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from .config import Settings, setup_logger
from .types import ExitSignal, MomentumResult, EXIT_CHECK_INTERVAL, PRICE_FEED_INTERVAL, ASSET_TO_BINANCE
from .position_store import PositionStore
from .clob_wrapper import ClobWrapper
from .position_executor import PositionExecutor
from .exit_manager import ExitManager
from .exit_handler import ExitHandler
from .self_tuner import SelfTuner
from .signal_feed import SignalFeed, PollingSignalFeed
from .signal_guard import SignalGuard
from .performance_tracker import PerformanceTracker
from .balance_manager import BalanceManager
from .health_monitor import HealthMonitor
from .binance_feed import BinanceFeed
from .binance_momentum import BinanceMomentumFeed
from .dashboard import Dashboard
from .arb_engine import ArbEngine
from .accumulator import AccumulatorEngine
from .signal_logger import SignalLogger
from .signal_scorer import SignalScorer
from .fill_optimizer import FillOptimizer
from .regime_detector import RegimeDetector
from .accumulator_metrics import AccumulatorMetrics
from .gabagool_tracker import GabagoolTracker
from .adaptive_tuner import AdaptiveTuner
from .market_ws import MarketWS
from .telegram_approver import TelegramApprover
from .slack_notifier import SlackNotifier
from .chainlink_feed import ChainlinkFeed
from .market_maker import MarketMaker


class SignalBot:
    """Main orchestrator for Polyphemus Polymarket Trading Bot."""

    def __init__(
        self,
        config: Settings,
        dry_run: bool = False,
        mock_file: str = None,
    ):
        """Initialize all components of the bot.

        Args:
            config: Settings instance with all configuration
            dry_run: If True, log but don't execute real orders
            mock_file: Optional path to mock signal file for testing
        """
        self._config = config
        self._dry_run = dry_run or config.dry_run
        self._mock_file = mock_file
        self._logger = setup_logger("polyphemus.bot")
        self._db_path = os.path.join(config.lagbot_data_dir, "performance.db")
        self._tuning_state_path = os.path.join(config.lagbot_data_dir, "tuning_state.json")

        # 1. Position store
        self._store = PositionStore()

        # 2. CLOB client setup
        clob_creds = ApiCreds(
            api_key=config.clob_api_key,
            api_secret=config.clob_secret,
            api_passphrase=config.clob_passphrase,
        )
        clob_client = ClobClient(
            host="https://clob.polymarket.com",
            key=config.private_key,
            chain_id=config.polygon_chain_id,
            creds=clob_creds,
            signature_type=config.signature_type,
            funder=config.wallet_address,
        )

        # 3. CLOB wrapper
        self._clob = ClobWrapper(clob_client, self._logger)

        # 3b. MarketWS (real-time midpoint feed via CLOB WebSocket)
        self._market_ws = MarketWS()
        self._clob.set_market_ws(self._market_ws)

        # 4. Self-tuner (if enabled)
        self._tuner = None
        if config.enable_self_tuning:
            self._tuner = SelfTuner(self._tuning_state_path)

        # 5. Position executor
        self._executor = PositionExecutor(
            clob=self._clob,
            store=self._store,
            config=config,
            tuner=self._tuner,
        )

        # 6. Exit manager and handler
        self._exit_mgr = ExitManager(store=self._store, config=config)
        self._exit_handler = ExitHandler(clob=self._clob, config=config)

        # 7. Signal guard
        self._guard = SignalGuard(config=config, store=self._store)

        # 8. Performance tracker
        self._tracker = PerformanceTracker(self._db_path)

        # 9. Balance manager
        self._balance = BalanceManager(
            clob=self._clob,
            store=self._store,
            config=config,
        )

        # 9b. Circuit breaker (entries only — exits never blocked)
        from .circuit_breaker import KillSwitch, DailyLossMonitor, StreakTracker, CircuitBreaker
        self._circuit_breaker = CircuitBreaker(
            kill_switch=KillSwitch(config.kill_switch_path),
            loss_monitor=DailyLossMonitor(self._tracker.db, config.max_daily_loss),
            streak_tracker=StreakTracker(
                max_consecutive=config.max_consecutive_losses,
                cooldown_mins=config.loss_cooldown_mins,
                state_path=os.path.join(config.lagbot_data_dir, "streak_state.json"),
            ),
            logger=self._logger,
            post_loss_cooldown_mins=config.post_loss_cooldown_mins,
        )
        self._trading_halted = False  # Set True if live reconciliation fails

        # 10. Health monitor (with guard for filter metrics + halt callback)
        self._health = HealthMonitor(
            config=config, store=self._store, guard=self._guard,
            halt_callback=self._on_invariant_halt,
        )

        # 11. Signal feed (created last so health monitor is ready)
        self._feed = None
        self._momentum_feed = None
        self._weather_feed = None
        if config.signal_mode == "binance_momentum":
            # Momentum mode: Binance prices are the PRIMARY signal source
            self._momentum_feed = BinanceMomentumFeed(
                config=config,
                clob=self._clob,
                on_signal=self._on_signal,
            )
            self._exit_mgr.set_momentum_feed(self._momentum_feed)
            shadow = config.get_shadow_assets()
            self._logger.info(
                f"Signal mode: Binance momentum (primary)"
                f"{f' | shadow={shadow}' if shadow else ''}"
            )
        elif config.signal_mode == "noaa_weather":
            from .weather_feed import WeatherFeed
            self._weather_feed = WeatherFeed(
                config=config,
                clob=self._clob,
                on_signal=self._on_signal,
                db=self._tracker.db,
            )
            self._logger.info("Signal mode: NOAA weather arb")

        # 11b. Chainlink oracle feed (wired to momentum feed + exit manager)
        self._chainlink = None
        if config.oracle_enabled and self._momentum_feed:
            self._chainlink = ChainlinkFeed(config=config)
            self._chainlink.set_on_direction_cross(
                self._momentum_feed._on_oracle_direction_cross
            )
            self._momentum_feed.set_chainlink_feed(self._chainlink)
            self._exit_mgr.set_chainlink_feed(self._chainlink)
            self._health.set_pipeline_feeds(
                chainlink_feed=self._chainlink,
                momentum_feed=self._momentum_feed,
            )
            self._logger.info("ChainlinkFeed wired (RTDS + Alchemy)")
        elif config.oracle_enabled:
            self._logger.warning("ORACLE_ENABLED=true but no momentum feed — ChainlinkFeed skipped")

        # 11c. Market maker (pair-cost arbitrage scanner + stale quote sniping)
        self._market_maker = None
        if config.enable_market_maker:
            self._market_maker = MarketMaker(
                config=config,
                clob=self._clob,
                market_ws=self._market_ws,
                momentum_feed=self._momentum_feed,
                tracker=self._tracker,
                store=self._store,
            )
            self._logger.info(
                f"MarketMaker wired (dry_run={config.mm_dry_run}, "
                f"interval={config.mm_scan_interval}s)"
            )

        # Telegram approval gate (weather mode only; no-op if token not configured)
        self._telegram = TelegramApprover(
            config=config,
            on_execute=self._execute_weather_signal,
        )
        if self._telegram.enabled:
            self._logger.info("Telegram approval gate ENABLED")
        elif config.signal_mode in ("binance_momentum", "noaa_weather"):
            self._logger.info("Signal feed: disabled (dedicated feed active for signal_mode=%s)", config.signal_mode)
        else:
            # Copy-trade mode (existing behavior)
            if config.signal_feed_mode == "polling":
                self._feed = PollingSignalFeed(
                    config=config,
                    store=self._store,
                    on_signal=self._on_signal,
                    mock_file=mock_file,
                )
                self._logger.info("Signal feed: REST polling mode")
            else:
                self._feed = SignalFeed(
                    config=config,
                    store=self._store,
                    on_signal=self._on_signal,
                    mock_file=mock_file,
                )
                self._logger.info("Signal feed: RTDS WebSocket mode")

        # Slack trade notifier (no-op if no credentials set)
        # Derive instance name from data dir path: /opt/lagbot/instances/emmanuel/data -> emmanuel
        data_dir = config.lagbot_data_dir
        instance_name = Path(data_dir).parent.name if "/instances/" in data_dir else "lagbot"
        self._slack = SlackNotifier(
            webhook_url=config.slack_webhook_url,
            instance_name=instance_name,
            bot_token=config.slack_bot_token,
            channel_id=config.slack_channel_id,
        )

        # Link signal feed to health monitor
        if self._feed:
            self._health.signal_feed = self._feed

        # 12. Binance momentum confirmation feed (skip if signal IS momentum mode)
        self._binance_feed = None
        self._momentum_stats = {"approved": 0, "rejected": 0, "bypassed": 0}
        if config.signal_mode != "binance_momentum" and config.enable_binance_confirmation:
            self._binance_feed = BinanceFeed(config)
            self._logger.info("Binance momentum confirmation ENABLED")

        # 13. Arbitrage engine (if enabled)
        self._arb_engine = None
        if config.enable_arb and not config.enable_accumulator:
            self._arb_engine = ArbEngine(
                clob=self._clob,
                balance=self._balance,
                config=config,
            )
            self._logger.info("Arbitrage engine ENABLED")
        elif config.enable_arb and config.enable_accumulator:
            self._logger.warning("Cannot enable both arb and accumulator — disabling arb")

        # 13a. Accumulator engine (if enabled, mutually exclusive with arb)
        self._accumulator = None
        if config.enable_accumulator:
            self._accumulator = AccumulatorEngine(
                clob=self._clob,
                balance=self._balance,
                store=self._store,
                config=config,
            )
            self._logger.info("Accumulator engine ENABLED")

        # 13a-ii. Auto-redeemer (for accumulator settlement + post-exit CTF redemption)
        self._redeemer = None
        if config.enable_auto_redemption:
            private_key = os.getenv("PRIVATE_KEY", "")
            wallet_addr = os.getenv("WALLET_ADDRESS", "")
            rpc_url = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")
            if private_key and wallet_addr:
                from .redeemer import Redeemer
                self._redeemer = Redeemer(
                    private_key=private_key,
                    wallet_address=wallet_addr,
                    rpc_url=rpc_url,
                    builder_api_key=config.builder_api_key,
                    builder_secret=config.builder_secret,
                    builder_passphrase=config.builder_passphrase,
                    signature_type=config.signature_type,
                    data_dir=config.lagbot_data_dir,
                )
                self._redeemer.set_position_store(self._store)
                self._redeemer.set_slack(self._slack)
                self._redeemer.set_db(self._tracker.db)
                if self._accumulator:
                    self._accumulator.set_redeemer(self._redeemer)
                self._health.redeemer = self._redeemer
                self._logger.info("Auto-redeemer ENABLED")
                # Wire redeemer into market maker for stale quote position tracking
                if self._market_maker:
                    self._market_maker._redeemer = self._redeemer

        # 13a-iii. Accumulator learning stack (metrics → tracker → tuner)
        self._accum_metrics = None
        self._gabagool_tracker = None
        self._adaptive_tuner = None
        if self._accumulator:
            self._accum_metrics = AccumulatorMetrics(os.path.join(config.lagbot_data_dir, "accum_metrics.db"))
            self._accumulator.set_metrics(self._accum_metrics)

            self._gabagool_tracker = GabagoolTracker()
            self._adaptive_tuner = AdaptiveTuner(
                metrics=self._accum_metrics,
                tracker=self._gabagool_tracker,
                config=config,
                state_path=os.path.join(config.lagbot_data_dir, "adaptive_state.json"),
            )
            self._accumulator.set_adaptive_tuner(self._adaptive_tuner)
            self._logger.info("Learning stack ENABLED (metrics + gabagool + tuner)")

        # 13b. Data science modules (all optional, graceful degradation)
        self._signal_logger = None
        self._signal_scorer = None
        self._fill_optimizer = None
        self._regime_detector = None

        if config.enable_signal_logging:
            self._signal_logger = SignalLogger(db_path=os.path.join(config.lagbot_data_dir, "signals.db"))
            self._logger.info("SignalLogger ENABLED")

        if config.enable_regime_detection:
            self._regime_detector = RegimeDetector()
            self._logger.info("RegimeDetector ENABLED")

        if config.enable_fill_optimizer:
            self._fill_optimizer = FillOptimizer(db_path=os.path.join(config.lagbot_data_dir, "fill_optimizer.db"))
            self._logger.info("FillOptimizer ENABLED")

        if config.enable_signal_scoring and self._signal_logger:
            self._signal_scorer = SignalScorer(
                signal_logger=self._signal_logger,
                model_path=os.path.join(config.lagbot_data_dir, "signal_model.pkl"),
                mode=config.signal_score_mode,
                threshold=config.signal_score_threshold,
            )
            self._logger.info(f"SignalScorer ENABLED (mode={config.signal_score_mode})")

        # Wire data science modules to existing components
        if self._fill_optimizer:
            self._executor._fill_optimizer = self._fill_optimizer
        self._executor._performance_db = self._tracker.db  # Kelly sizing (no-op if disabled)
        if self._regime_detector and self._momentum_feed:
            self._momentum_feed._regime_detector = self._regime_detector
        if self._momentum_feed:
            self._momentum_feed.set_market_ws(self._market_ws)
            if self._signal_logger:
                self._momentum_feed._signal_logger = self._signal_logger

        # 14. Dashboard
        self._dashboard = Dashboard(
            config=config,
            store=self._store,
            balance=self._balance,
            health=self._health,
            guard=self._guard,
            perf_db=self._tracker.db,
            binance_feed=self._binance_feed,
            momentum_feed=self._momentum_feed,
            momentum_stats=self._momentum_stats,
            dry_run=self._dry_run,
            arb_engine=self._arb_engine,
            accumulator_engine=self._accumulator,
            signal_logger=self._signal_logger,
            signal_scorer=self._signal_scorer,
            fill_optimizer=self._fill_optimizer,
            regime_detector=self._regime_detector,
            gabagool_tracker=self._gabagool_tracker,
            adaptive_tuner=self._adaptive_tuner,
        )

        self._logger.info("SignalBot initialized successfully")

    async def _run_preflight(self) -> None:
        """API connectivity and sanity checks before entering the trading loop.

        Checks:
          1. CLOB API reachable (ping /ok)
          2. Wallet balance visible (log balance)
          3. Binance WS host reachable (TCP connect)

        Calls sys.exit(1) on critical failure in live mode. Warns in dry-run mode.
        """
        ok = True
        self._logger.info("--- PREFLIGHT CHECKS ---")

        # 1. CLOB API
        clob_up = await self._clob.ping()
        if not clob_up:
            self._logger.critical("PREFLIGHT [FAIL] CLOB API unreachable")
            ok = False

        # 2. Wallet balance
        balance = await self._clob.get_balance()
        if balance < 1.0 and not self._dry_run:
            self._logger.critical(f"PREFLIGHT [FAIL] wallet balance ${balance:.2f} < $1 minimum")
            ok = False
        else:
            self._logger.info(f"PREFLIGHT [OK]   wallet balance ${balance:.2f}")
        self._slack.seed_stats(0, 0, 0.0, start_balance=balance)

        # 3. Binance WS host TCP reachability (skip for modes that don't use Binance)
        needs_binance = (
            self._config.signal_mode == "binance_momentum"
            or self._config.enable_binance_confirmation
        )
        if needs_binance:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("stream.binance.com", 9443, ssl=True),
                    timeout=8.0,
                )
                writer.close()
                await writer.wait_closed()
                self._logger.info("PREFLIGHT [OK]   Binance WS host reachable")
            except Exception as e:
                self._logger.critical(f"PREFLIGHT [FAIL] Binance WS unreachable: {e}")
                ok = False
        else:
            self._logger.info("PREFLIGHT [SKIP] Binance WS (not needed for this signal mode)")

        self._logger.info("--- END PREFLIGHT ---")
        if not ok:
            if self._dry_run:
                self._logger.warning("PREFLIGHT: Some checks failed (dry-run — continuing with degraded connectivity)")
            else:
                self._logger.critical("PREFLIGHT: Critical failures detected. Aborting startup.")
                sys.exit(1)

    async def start(self):
        """Start the bot with all concurrent tasks."""
        try:
            self._logger.info("Starting SignalBot...")

            # 0. API connectivity preflight (before any CLOB calls)
            await self._run_preflight()

            # 1. Create data directory
            os.makedirs(self._config.lagbot_data_dir, exist_ok=True)

            # 2. Load positions from DB
            count = self._store.load_from_db(self._db_path)
            self._logger.info(f"Loaded {count} positions from DB")

            # 3. Purge stale trades from DB (markets ended >10 min ago)
            stale_count = 0
            for pos in list(self._store.get_open()):
                # Skip accumulator positions — managed by AccumulatorEngine
                if pos.metadata and pos.metadata.get("is_accumulator"):
                    continue
                # Skip weather positions — no epoch in slug, redeemer handles resolution
                if pos.metadata and pos.metadata.get("is_weather"):
                    continue
                try:
                    # Parse epoch from slug: e.g., btc-updown-5m-1770944400
                    parts = pos.slug.rsplit('-', 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        market_epoch = int(parts[1])
                        window = 300 if '5m' in pos.slug else 900
                        market_end = market_epoch + window
                        if time.time() > market_end + 600:  # ended 10+ min ago
                            mins_ago = (time.time() - market_end) / 60
                            # Check share balance to determine win/loss
                            # Shares > 0 after resolution = WIN (shares worth $1.00)
                            # Shares = 0 = LOSS (worthless)
                            try:
                                shares = await self._clob.get_share_balance(pos.token_id)
                                if shares >= 5.0:
                                    # WIN - shares resolved to $1.00, queue for redemption
                                    exit_price = 1.0
                                    self._logger.info(
                                        f"Purging stale WIN: {pos.slug} "
                                        f"(market ended {mins_ago:.0f} min ago, "
                                        f"{shares:.1f} shares to redeem)"
                                    )
                                    self._enqueue_redemption(pos)
                                else:
                                    exit_price = 0.0
                                    self._logger.info(
                                        f"Purging stale LOSS: {pos.slug} "
                                        f"(market ended {mins_ago:.0f} min ago, "
                                        f"0 shares remaining)"
                                    )
                            except Exception as bal_err:
                                exit_price = 0.0
                                self._logger.warning(
                                    f"Share balance check failed for {pos.slug}: {bal_err}, "
                                    f"assuming loss (redeemer will correct if win)"
                                )
                            self._store.remove(pos.token_id)
                            self._tracker.db.force_close_trade(
                                slug=pos.slug,
                                exit_reason='market_resolved',
                                exit_price=exit_price,
                            )
                            stale_count += 1
                            continue
                except Exception as e:
                    self._logger.warning(f"Stale check failed for {pos.slug}: {e}")

            if stale_count > 0:
                self._logger.info(f"Startup: purged {stale_count} stale positions (expired markets)")

            # 4. Reconcile remaining positions against CLOB share holdings
            # Only ghost-cleanup positions whose market has ENDED. Active markets
            # are left alone — the exit_manager will handle them normally.
            ghost_count = 0
            resumed_count = 0
            for pos in list(self._store.get_open()):
                # Skip accumulator positions — managed by AccumulatorEngine
                if pos.metadata and pos.metadata.get("is_accumulator"):
                    continue
                # Skip weather positions — weather markets resolve over 24-48h,
                # not within the 5-minute CLOB settlement window used by this check
                if pos.metadata and pos.metadata.get("is_weather"):
                    continue

                # Check if market is still active
                market_still_active = False
                try:
                    parts = pos.slug.rsplit('-', 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        market_epoch = int(parts[1])
                        window = 300 if '5m' in pos.slug else 900
                        market_end = market_epoch + window
                        market_still_active = time.time() < market_end
                except Exception:
                    pass

                if market_still_active:
                    # Market is live — resume monitoring, don't ghost-cleanup
                    resumed_count += 1
                    self._logger.info(
                        f"Resuming active position: {pos.slug} | "
                        f"entry={pos.entry_price:.4f} | size={pos.entry_size:.1f} shares"
                    )
                    continue

                try:
                    shares = await self._clob.get_share_balance(pos.token_id)
                    if shares < 5.0:  # MIN_SHARES_FOR_SELL
                        self._logger.warning(
                            f"Ghost position detected: {pos.slug} | "
                            f"store={pos.entry_size:.2f} shares, clob={shares:.2f} | "
                            f"Purging from store and DB"
                        )
                        self._store.remove(pos.token_id)
                        self._tracker.db.force_close_trade(
                            slug=pos.slug,
                            exit_reason='ghost_cleanup',
                            exit_price=0.0,
                        )
                        ghost_count += 1
                except Exception as e:
                    self._logger.warning(f"Failed to check shares for {pos.slug}: {e}")

            total_purged = stale_count + ghost_count
            remaining = len(self._store.get_open())
            if total_purged > 0 or resumed_count > 0:
                self._logger.info(
                    f"Reconciliation: purged {stale_count} stale + {ghost_count} ghost = "
                    f"{total_purged} positions | {resumed_count} resumed | {remaining} active remain"
                )
            else:
                self._logger.info(f"Reconciliation: all {remaining} positions verified on CLOB")

            # 5. Reconcile wallet
            reconcile_result = await self._balance.reconcile_at_startup()
            if not reconcile_result:
                if self._config.dry_run:
                    self._logger.warning("Wallet reconciliation incomplete (dry run — continuing)")
                else:
                    self._logger.critical(
                        "LIVE MODE: Startup reconciliation FAILED. "
                        "Bot running in DEGRADED mode (exits only, no new trades)."
                    )
                    self._trading_halted = True

            # 5b. CLOB↔DB trade audit (live mode only, skip if already halted or accumulator enabled)
            # Accumulator trades don't record to signal_bot DB, so ratio check is invalid when active.
            if not self._dry_run and not self._trading_halted and not self._config.enable_accumulator:
                passed, msg = await self._balance.reconcile_trades(self._tracker.db)
                if not passed:
                    self._logger.critical(f"CLOB↔DB trade audit FAILED: {msg}")
                    self._trading_halted = True
                else:
                    self._logger.info(f"CLOB↔DB trade audit: {msg}")

            # 6. Notify systemd ready
            self._health.notify_ready()

            # 7. Start concurrent tasks
            tasks = []
            if self._feed:
                tasks.append(self._feed.start())
            if self._momentum_feed:
                tasks.append(self._momentum_feed.start())
            if self._weather_feed:
                tasks.append(self._weather_feed.start())
            if self._telegram.enabled:
                tasks.append(self._safe_task(self._telegram.start(), "telegram_approver"))
            # Critical tasks — crash the bot if they fail
            tasks.extend([
                self._exit_loop(),
                self._price_feed_loop(),
                self._health.start_watchdog_loop(),
            ])
            # Non-critical tasks — log errors but don't crash the bot
            tasks.extend([
                self._safe_task(self._market_ws.start(), "market_ws"),
                self._safe_task(self._health.start_health_log_loop(balance_manager=self._balance), "health_log"),
                self._safe_task(self._health.check_daily_restart(), "daily_restart"),
                self._safe_task(self._dashboard.start(), "dashboard"),
            ])
            if self._feed:
                tasks.append(self._safe_task(self._feed.start_stale_watchdog(), "stale_watchdog"))
            if self._binance_feed:
                tasks.append(self._safe_task(self._binance_feed.start(), "binance_feed"))
            if self._arb_engine:
                tasks.append(self._safe_task(self._arb_engine.start(), "arb_engine"))
            if self._accumulator:
                tasks.append(self._safe_task(self._accumulator.start(), "accumulator"))
            if self._redeemer:
                tasks.append(self._safe_task(self._redeemer.start(), "redeemer"))
            if self._signal_scorer:
                tasks.append(self._safe_task(self._scorer_retrain_loop(), "scorer_retrain"))
            if self._gabagool_tracker:
                tasks.append(self._safe_task(self._gabagool_tracker.start(), "gabagool_tracker"))
            if self._adaptive_tuner:
                tasks.append(self._safe_task(self._adaptive_tuner.start(), "adaptive_tuner"))
            if self._chainlink:
                tasks.append(self._safe_task(self._chainlink.start(), "chainlink_feed"))
            if self._config.enable_pair_arb and self._momentum_feed:
                tasks.append(self._safe_task(self._momentum_feed.pair_arb_scan_loop(), "pair_arb_scan"))
            if self._config.pair_arb_near_res_enabled and self._momentum_feed:
                tasks.append(self._safe_task(self._momentum_feed.near_res_pair_arb_loop(), "near_res_pair_arb"))
            if self._market_maker:
                tasks.append(self._safe_task(self._market_maker.start(), "market_maker"))
            await asyncio.gather(*tasks)

        except KeyboardInterrupt:
            self._logger.info("Received SIGINT, shutting down gracefully")
            sys.exit(0)
        except Exception as e:
            self._logger.exception(f"Fatal error in start: {e}")
            self._health.record_error()
            sys.exit(1)

    def _read_market_context(self) -> dict:
        """Read OpenClaw market context (F&G, OI trends). Returns {} on any failure."""
        try:
            ctx_path = self._config.market_context_path
            if not ctx_path or not os.path.exists(ctx_path):
                return {}
            with open(ctx_path) as f:
                ctx = json.load(f)
            # Stale check: skip if older than 30 min
            updated = ctx.get("updated_at", "")
            if updated:
                from datetime import datetime as dt
                ctx_time = dt.strptime(updated, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - ctx_time).total_seconds() > 1800:
                    return {}
            return ctx
        except Exception:
            return {}

    def _on_invariant_halt(self, reason: str):
        """Called by HealthMonitor when a CRITICAL invariant is violated."""
        self._trading_halted = True
        self._logger.critical(f"TRADING HALTED by invariant: {reason}")

    async def _on_signal(self, signal: dict):
        """Process incoming signal from WebSocket or mock feed.

        Args:
            signal: Raw signal dict with keys: token_id, price, slug, market_title,
                   usdc_size, direction, outcome, asset, tx_hash, timestamp
        """
        try:
            self._logger.debug(f"Received signal: {signal.get('slug', 'unknown')}")

            # 0b. Inject F&G from market context into signal for guard check
            mkt_ctx_guard = self._read_market_context()
            if mkt_ctx_guard:
                fg_val = mkt_ctx_guard.get("fear_greed")
                if fg_val is not None:
                    signal["fear_greed"] = fg_val

            # 0c. Inject regime data (volatility_1h, trend_1h) for whipsaw guard
            if self._regime_detector:
                regime = self._regime_detector.get_regime(signal.get("asset", "BTC"))
                signal["volatility_1h"] = regime.volatility_1h
                signal["trend_1h"] = regime.trend_1h

            # 1. Run through signal guard
            result = self._guard.check(signal)

            # 1b. Log signal features (captures ALL signals for ML training)
            signal_id = -1
            if self._signal_logger:
                slug_val = signal.get("slug", "")
                log_features = {
                    "slug": slug_val,
                    "asset": signal.get("asset", ""),
                    "direction": signal.get("outcome", ""),
                    "token_id": signal.get("token_id", ""),
                    "midpoint": signal.get("price", 0.0),
                    "momentum_pct": signal.get("momentum_pct", 0.0),
                    "market_window_secs": signal.get("market_window_secs", 0),
                    "guard_passed": 1 if result.passed else 0,
                    "guard_reasons": ",".join(result.reasons) if result.reasons else "",
                    "source": signal.get("source", ""),
                    "spread": signal.get("spread"),
                    "book_depth_bid": signal.get("best_bid"),
                    "book_depth_ask": signal.get("best_ask"),
                    "book_imbalance": signal.get("book_imbalance"),
                }
                # Compute time_remaining from slug if not in signal
                time_remaining = signal.get("time_remaining_secs", 0)
                if time_remaining == 0 and slug_val:
                    parts = slug_val.rsplit('-', 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        from .types import parse_window_from_slug
                        epoch = int(parts[1])
                        window = parse_window_from_slug(slug_val)
                        time_remaining = max(0, int(epoch + window - time.time()))
                log_features["time_remaining_secs"] = time_remaining
                # Add regime features if available
                if self._regime_detector:
                    regime = self._regime_detector.get_regime(signal.get("asset", "BTC"))
                    log_features["regime"] = regime.regime
                    log_features["volatility_1h"] = regime.volatility_1h
                    log_features["trend_1h"] = regime.trend_1h
                # Add OpenClaw market context (F&G, OI) if available
                mkt_ctx = self._read_market_context()
                if mkt_ctx:
                    log_features["fear_greed"] = mkt_ctx.get("fear_greed")
                    log_features["market_regime"] = mkt_ctx.get("market_regime", "")
                    asset_ctx = mkt_ctx.get(signal.get("asset", "BTC"), {})
                    log_features["oi_change_pct"] = asset_ctx.get("oi_change_pct")
                    log_features["oi_trend"] = asset_ctx.get("oi_trend", "")
                signal_id = self._signal_logger.log_signal(log_features)

            # Shadow mode: log signal but don't execute
            if signal.get("shadow"):
                guard_status = "passed" if result.passed else f"rejected({','.join(result.reasons)})"
                self._logger.info(
                    f"[SHADOW] {signal.get('slug')} {signal.get('outcome')} "
                    f"@ {signal.get('price', 0):.4f} | "
                    f"momentum={signal.get('momentum_pct', 0):+.3%} | "
                    f"guard={guard_status}"
                )
                if self._signal_logger and signal_id > 0:
                    self._signal_logger.update_signal(signal_id, {"outcome": "shadow"})
                return

            if not result.passed:
                if self._signal_logger and signal_id > 0:
                    self._signal_logger.update_signal(signal_id, {"outcome": "filtered"})
                self._logger.info(
                    f"Signal rejected: {signal.get('slug', 'unknown')} "
                    f"Reasons: {result.reasons}"
                )
                return

            # 2. Binance momentum confirmation (skip if signal IS from momentum feed)
            asset = signal.get('asset', '')
            is_momentum_signal = signal.get('source') in ('binance_momentum', 'binance_momentum_lag', 'window_delta', 'pair_arb', 'noaa_weather', 'resolution_snipe', 'sharp_move')
            if self._binance_feed and asset in ASSET_TO_BINANCE and not is_momentum_signal:
                if self._binance_feed.in_grace_period():
                    self._momentum_stats["bypassed"] += 1
                elif self._binance_feed.circuit_open:
                    self._momentum_stats["bypassed"] += 1
                    self._logger.warning("Binance circuit open, bypassing momentum check")
                else:
                    # Adaptive lookback and threshold based on market age
                    slug = signal.get('slug', '')
                    parts = slug.rsplit('-', 1) if slug else []
                    lookback = self._config.momentum_candles
                    threshold = self._config.min_momentum_pct
                    phase = "early"
                    if len(parts) == 2 and parts[1].isdigit():
                        market_epoch = int(parts[1])
                        elapsed = time.time() - market_epoch
                        if elapsed < 180:  # 0-3 min
                            lookback = self._config.momentum_candles
                            threshold = self._config.momentum_threshold_early
                            phase = "early"
                        elif elapsed < 360:  # 3-6 min
                            lookback = self._config.momentum_candles
                            threshold = self._config.momentum_threshold_mid
                            phase = "mid"
                        else:  # 6+ min
                            lookback = self._config.momentum_candles_late
                            threshold = self._config.momentum_threshold_late
                            phase = "late"

                    momentum = self._binance_feed.get_momentum(
                        asset, lookback=lookback, threshold=threshold
                    )
                    if not self._momentum_confirms(signal, momentum):
                        self._momentum_stats["rejected"] += 1
                        self._logger.info(
                            f"Momentum rejected: {signal.get('slug')} "
                            f"outcome={signal.get('outcome')} vs {momentum.direction} "
                            f"({momentum.momentum_pct:+.3%}) "
                            f"[{phase} | {lookback}m | thresh={threshold:.4f}]"
                        )
                        if self._signal_logger and signal_id > 0:
                            self._signal_logger.update_signal(signal_id, {"outcome": "binance_filtered"})
                        return
                    self._momentum_stats["approved"] += 1
                    self._logger.info(
                        f"Momentum confirmed: {signal.get('slug')} "
                        f"{momentum.direction} ({momentum.momentum_pct:+.3%}, "
                        f"conf={momentum.confidence:.2f}) "
                        f"[{phase} | {lookback}m | thresh={threshold:.4f}]"
                    )

            # 2c. Regime check (skip flat markets — not applicable to price arb or weather)
            if self._regime_detector and signal.get('source') not in ('pair_arb', 'noaa_weather', 'resolution_snipe'):
                if not self._regime_detector.should_trade(signal.get("asset", "BTC")):
                    if self._signal_logger and signal_id > 0:
                        self._signal_logger.update_signal(signal_id, {"outcome": "regime_filtered"})
                    self._logger.info(f"Regime filtered: {signal.get('slug', '?')} (flat market)")
                    return

            # 2d. Signal scoring
            signal_score = None
            if self._signal_scorer:
                now_utc = datetime.now(timezone.utc)
                regime = self._regime_detector.get_regime(signal.get("asset", "BTC")) if self._regime_detector else None
                features = self._signal_scorer.build_feature_dict(
                    momentum_pct=signal.get("momentum_pct", 0.0),
                    midpoint=signal.get("price", 0.0),
                    spread=signal.get("spread") or 0.0,
                    book_imbalance=signal.get("book_imbalance") or 0.0,
                    time_remaining_secs=signal.get("time_remaining_secs", 0),
                    hour_utc=now_utc.hour,
                    day_of_week=now_utc.weekday(),
                    volatility_1h=regime.volatility_1h if regime else 0.0,
                    trend_1h=regime.trend_1h if regime else 0.0,
                    asset=signal.get("asset", ""),
                    direction=signal.get("outcome", ""),
                    market_window_secs=signal.get("market_window_secs", 900),
                )
                signal_score = self._signal_scorer.score(features)
                if self._signal_logger and signal_id > 0:
                    self._signal_logger.update_signal(signal_id, {
                        "signal_score": signal_score,
                        "score_threshold": self._signal_scorer._threshold,
                    })
                if not self._signal_scorer.should_trade(signal_score):
                    if self._signal_logger and signal_id > 0:
                        self._signal_logger.update_signal(signal_id, {"outcome": "score_filtered"})
                    self._logger.info(
                        f"Score filtered: {signal.get('slug', '?')} "
                        f"score={signal_score:.1f} < {self._signal_scorer._threshold}"
                    )
                    return
                self._logger.info(f"Signal score: {signal_score:.1f} for {signal.get('slug', '?')}")

            # 2b. Circuit breaker check (entries only — exits never blocked)
            # Bypass for reversal_short: the flip is triggered BY a loss, cooldown must not block it
            if self._trading_halted:
                self._logger.warning("Trading halted: startup reconciliation failed")
                return
            if signal.get('source') != 'reversal_short':
                allowed, cb_reason = self._circuit_breaker.is_trading_allowed()
                if not allowed:
                    self._logger.warning(f"Circuit breaker blocked entry: {cb_reason}")
                    return

            # 3. Check if safe to trade
            if not await self._balance.is_safe_to_trade():
                self._logger.warning("Not safe to trade - balance too low or limit reached")
                return

            # 4. Get available capital (reserve accumulator share if enabled)
            if self._config.enable_accumulator:
                available = await self._balance.get_available_for_momentum()
            else:
                available = await self._balance.get_available()
            if available < self._config.min_bet:
                self._logger.warning(
                    f"Insufficient capital: ${available:.2f} < ${self._config.min_bet:.2f}"
                )
                return

            # 5. Dry run check
            if self._dry_run:
                price = signal.get('price', 0)
                asset = signal.get('asset', '')
                projected = self._executor._calculate_size(price, available, asset, spread=signal.get("spread"))
                self._logger.info(
                    f"[DRY RUN] Would execute BUY: {signal.get('slug', 'unknown')} "
                    f"@ ${price:.4f} (projected ${projected:.2f} / ${available:.2f} avail)"
                )
                return

            # 5b. Telegram approval gate (weather signals only)
            if signal.get('source') == 'noaa_weather' and self._telegram.enabled:
                await self._telegram.submit(signal)
                return  # execution happens via _execute_weather_signal callback

            # 5c. Populate entry metadata for ALL signal types (direction, source, asset)
            signal.setdefault("metadata", {})
            signal["metadata"]["direction"] = signal.get("outcome", "").lower()
            signal["metadata"]["source"] = signal.get("source", "")
            signal["metadata"]["asset"] = signal.get("asset", "")
            signal["metadata"]["entry_price_at_signal"] = signal.get("price", 0.0)

            # 5d. Extra momentum fields for reversal exit (all Binance-sourced entries)
            reversal_sources = ('binance_momentum', 'binance_momentum_lag', 'sharp_move',
                                'oracle_flip', 'reversal_short', 'window_delta', 'streak_contrarian')
            if signal.get('source') in reversal_sources and self._momentum_feed:
                signal["metadata"]["entry_momentum_direction"] = signal.get("outcome", "").lower()
                signal["metadata"]["entry_momentum_ts"] = time.time()
                bp = signal.get("binance_price", 0.0)
                if not bp:
                    bp = self._momentum_feed.get_latest_price(signal.get("asset", "BTC")) or 0.0
                signal["metadata"]["entry_binance_price"] = bp

            # 6. Execute buy
            exec_result = await self._executor.execute_buy(signal, available)

            if not exec_result.success:
                self._logger.error(
                    f"Buy execution failed for {signal.get('slug', 'unknown')}: "
                    f"{exec_result.error}"
                )
                self._health.record_error()
                return

            self._logger.info(
                f"Buy executed: {signal.get('slug', 'unknown')} "
                f"Order: {exec_result.order_id} "
                f"@ ${exec_result.fill_price:.4f} x {exec_result.fill_size:.1f}"
            )

            # 7. Record to performance DB (include metadata for direction analysis)
            await self._tracker.record_entry(
                trade_id=exec_result.order_id,
                token_id=signal.get("token_id", ""),
                slug=signal.get("slug", ""),
                entry_price=exec_result.fill_price,
                entry_size=exec_result.fill_size,
                entry_tx_hash=exec_result.order_id,
                outcome=signal.get("outcome", ""),
                market_title=signal.get("market_title", ""),
                entry_time=time.time(),
                filter_score=signal_score,
                metadata=signal.get("metadata"),
            )

            # 7b. Slack notification (non-fatal)
            try:
                self._slack.notify_entry(
                    slug=signal.get("slug", ""),
                    asset=signal.get("asset", ""),
                    direction=signal.get("outcome", ""),
                    entry_price=exec_result.fill_price,
                    size_usd=exec_result.fill_price * exec_result.fill_size,
                    shares=exec_result.fill_size,
                    momentum_pct=signal.get("momentum_pct", 0.0),
                    source=signal.get("source", ""),
                    secs_left=signal.get("time_remaining_secs", 0),
                )
            except Exception:
                pass

            # 8. Update signal logger with execution result
            if self._signal_logger and signal_id > 0:
                self._signal_logger.update_signal(signal_id, {
                    "outcome": "executed",
                    "entry_price": exec_result.fill_price,
                    "fill_mode": self._config.entry_mode,
                })
                # Store signal_id + trade context in position for exit tracking
                stored_pos = self._store.get(signal.get("token_id", ""))
                if stored_pos and stored_pos.metadata is not None:
                    stored_pos.metadata["signal_id"] = signal_id
                    stored_pos.metadata["asset"] = signal.get("asset", "")
                    stored_pos.metadata["direction"] = signal.get("outcome", "")

        except Exception as e:
            self._logger.exception(f"Error in _on_signal: {e}")
            self._health.record_error()

    async def _execute_weather_signal(self, signal: dict) -> None:
        """Called by TelegramApprover when user taps Approve. Executes the weather buy."""
        try:
            available = await self._balance.get_available()
            if available < self._config.min_bet:
                self._logger.warning(
                    f"Telegram-approved signal skipped: insufficient capital "
                    f"${available:.2f} < ${self._config.min_bet:.2f}"
                )
                return

            exec_result = await self._executor.execute_buy(signal, available)
            if not exec_result.success:
                self._logger.error(
                    f"Telegram-approved buy failed for {signal.get('slug', '?')}: "
                    f"{exec_result.error}"
                )
                raise RuntimeError(exec_result.error)

            self._logger.info(
                f"Telegram-approved buy executed: {signal.get('slug', '?')} "
                f"@ ${exec_result.fill_price:.4f} x {exec_result.fill_size:.1f}"
            )

            await self._tracker.record_entry(
                trade_id=exec_result.order_id,
                token_id=signal.get("token_id", ""),
                slug=signal.get("slug", ""),
                entry_price=exec_result.fill_price,
                entry_size=exec_result.fill_size,
                entry_tx_hash=exec_result.order_id,
                outcome=signal.get("outcome", ""),
                market_title=signal.get("market_title", ""),
                entry_time=time.time(),
                filter_score=None,
            )
        except Exception as e:
            self._logger.exception(f"_execute_weather_signal error: {e}")
            raise

    def _momentum_confirms(self, signal: dict, momentum: MomentumResult) -> bool:
        """Check if Binance momentum confirms the signal direction.

        Args:
            signal: Signal dict with 'outcome' key
            momentum: MomentumResult from BinanceFeed

        Returns:
            True if momentum aligns with trade direction
        """
        outcome = signal.get('outcome', '').lower()
        if momentum.direction in ("UNKNOWN", "NEUTRAL"):
            return False
        if outcome == "down" and momentum.direction == "DOWN":
            return True
        if outcome == "up" and momentum.direction == "UP":
            return True
        return False

    async def _scorer_retrain_loop(self):
        """Periodically retrain the signal scorer with new labeled data."""
        while True:
            await asyncio.sleep(3600)  # Check every hour
            try:
                self._signal_scorer.maybe_retrain()
            except Exception as e:
                self._logger.warning(f"Scorer retrain error: {e}")

    async def _safe_task(self, coro, name: str):
        """Wrap a non-critical async task with error handling.

        If the task crashes, log the error but don't kill the entire bot.
        Only use for non-critical tasks (dashboard, health logging, arb engine).
        Critical tasks (exit_loop, price_feed, signal feed) should NOT use this.
        """
        try:
            await coro
        except asyncio.CancelledError:
            raise  # Let cancellation propagate
        except Exception as e:
            self._logger.error(f"Non-critical task '{name}' crashed: {e}", exc_info=True)

    async def _exit_loop(self):
        """Run exit check loop forever."""
        try:
            while True:
                await asyncio.sleep(EXIT_CHECK_INTERVAL)
                try:
                    now = datetime.now(timezone.utc)
                    exit_signals = self._exit_mgr.check_all(now)

                    for exit_signal in exit_signals:
                        await self._handle_exit(exit_signal)

                except Exception as e:
                    self._logger.exception(f"Error in exit loop: {e}")
                    self._health.record_error()

        except asyncio.CancelledError:
            self._logger.info("Exit loop cancelled")
            raise

    async def _price_feed_loop(self):
        """Update current_price for all open positions periodically.

        Fetches all prices in parallel via asyncio.gather to avoid
        blocking the exit loop if one API call is slow.
        """
        try:
            while True:
                await asyncio.sleep(PRICE_FEED_INTERVAL)
                try:
                    open_positions = [
                        p for p in self._store.get_open()
                        if not (p.metadata and p.metadata.get("is_accumulator"))
                    ]
                    if not open_positions:
                        continue

                    # Fetch all prices in parallel
                    results = await asyncio.gather(
                        *[self._clob.get_midpoint(pos.token_id) for pos in open_positions],
                        return_exceptions=True,
                    )

                    for pos, price in zip(open_positions, results):
                        if isinstance(price, Exception):
                            self._logger.warning(
                                f"Price fetch exception for {pos.slug}: {type(price).__name__}"
                            )
                            continue
                        if price > 0:
                            new_peak = max(price, pos.peak_price or pos.entry_price)
                            self._store.update(pos.token_id, current_price=price, peak_price=new_peak)
                            self._logger.debug(
                                f"Price updated: {pos.slug} "
                                f"entry={pos.entry_price:.4f} current={price:.4f} peak={new_peak:.4f}"
                            )
                except Exception as e:
                    self._logger.warning(f"Price feed error: {e}")

        except asyncio.CancelledError:
            self._logger.info("Price feed loop cancelled")
            raise

    async def _handle_exit(self, exit_signal: ExitSignal):
        """Handle a single exit signal.

        Args:
            exit_signal: ExitSignal with token_id, reason, and optional exit_price
        """
        try:
            # 1. Get position
            pos = self._store.get(exit_signal.token_id)
            if not pos:
                self._logger.warning(
                    f"Position not found for exit: {exit_signal.token_id}"
                )
                return

            self._logger.info(
                f"Exiting position: {pos.slug} "
                f"Reason: {exit_signal.reason}"
            )

            # 2. Dry run check
            if self._dry_run:
                self._logger.info(
                    f"[DRY RUN] Would exit {pos.slug} "
                    f"@ ${exit_signal.exit_price or pos.current_price:.4f} "
                    f"({exit_signal.reason})"
                )
                # Record to circuit breaker so post-loss cooldown works in paper mode
                try:
                    dry_exit_price = exit_signal.exit_price or pos.current_price or pos.entry_price
                    dry_pnl = (dry_exit_price - pos.entry_price) * pos.entry_size
                    self._circuit_breaker.record_trade_result(dry_pnl)
                except Exception:
                    pass
                # Still record as exit to advance state
                self._exit_mgr.complete_exit(exit_signal.token_id)
                return

            # 3. Execute exit
            exec_result = await self._exit_handler.execute_exit(pos, exit_signal)

            if not exec_result.success:
                # Bug #41 fix: 0 shares on CLOB = market resolved, don't retry
                if exec_result.error == "insufficient_shares":
                    self._logger.warning(
                        f"0 shares on CLOB for {pos.slug} — "
                        f"treating as resolved (outcome unknown until redemption)"
                    )
                    # Record with exit_price=0.0 (unknown outcome).
                    # Redeemer will update pnl if it successfully redeems (= win).
                    # If no redemption happens, pnl stays 0.0 (= loss, close enough).
                    try:
                        self._tracker.db.force_close_trade(
                            slug=pos.slug,
                            exit_reason='insufficient_shares',
                            exit_price=0.0,
                        )
                    except Exception as db_err:
                        self._logger.error(
                            f"force_close_trade failed for {pos.slug}: {db_err}"
                        )
                    # Record to circuit breaker: estimate PnL from current price
                    try:
                        est_price = pos.current_price or pos.entry_price
                        est_pnl = (est_price - pos.entry_price) * pos.entry_size
                        self._circuit_breaker.record_trade_result(est_pnl)
                    except Exception:
                        pass
                    self._store.remove(exit_signal.token_id)
                    self._exit_mgr.complete_exit(exit_signal.token_id)
                    self._enqueue_redemption(pos)
                    return

                self._logger.error(
                    f"Exit execution failed for {pos.slug}: {exec_result.error}"
                )
                self._exit_mgr.fail_exit(exit_signal.token_id)
                self._health.record_error()
                return

            self._logger.info(
                f"Exit executed: {pos.slug} "
                f"Order: {exec_result.order_id} "
                f"@ ${exec_result.fill_price:.4f} x {exec_result.fill_size:.1f}"
            )

            # 4. Record exit to performance DB (non-fatal — cleanup MUST run)
            try:
                await self._tracker.record_exit(
                    trade_id=pos.entry_tx_hash,
                    exit_price=exec_result.fill_price,
                    exit_size=exec_result.fill_size,
                    exit_reason=exit_signal.reason,
                    exit_tx_hash=exec_result.order_id,
                    exit_time=time.time(),
                )
            except Exception as db_err:
                self._logger.warning(
                    f"record_exit failed for {pos.slug} (falling back to force_close): {db_err}"
                )
                # Fallback: directly close in DB by slug (handles ghost positions)
                try:
                    self._tracker.db.force_close_trade(
                        slug=pos.slug,
                        exit_reason=exit_signal.reason,
                        exit_price=exec_result.fill_price,
                    )
                except Exception as fc_err:
                    self._logger.error(f"force_close_trade also failed for {pos.slug}: {fc_err}")

            # 5. Update self-tuner (if enabled, non-fatal)
            # Only update for entries in valid bucket range (Bug #36 fix)
            try:
                if self._tuner and 0.50 <= pos.entry_price < 0.80:
                    balance = await self._balance.get_balance()
                    bucket = '0.65-0.70' if pos.entry_price < 0.70 else '0.70-0.80'
                    pnl = (
                        (exec_result.fill_price - pos.entry_price)
                        * exec_result.fill_size
                    )
                    won = pnl > 0
                    self._tuner.update_state(bucket, won, balance)
                    self._logger.debug(
                        f"Self-tuner updated: {bucket} won={won} balance=${balance:.2f}"
                    )
                elif self._tuner:
                    self._logger.debug(
                        f"Skipping tuner: entry_price={pos.entry_price:.3f} "
                        f"outside bucket range [0.50, 0.80)"
                    )
            except Exception as tuner_err:
                self._logger.error(
                    f"Self-tuner update failed for {pos.slug} (non-fatal): {tuner_err}"
                )

            # 5b. Record result to circuit breaker (non-fatal)
            try:
                exit_pnl = (exec_result.fill_price - pos.entry_price) * exec_result.fill_size
                self._circuit_breaker.record_trade_result(exit_pnl)
            except Exception as cb_err:
                self._logger.error(
                    f"Circuit breaker update failed for {pos.slug} (non-fatal): {cb_err}"
                )

            # 5c. Update signal logger with exit data (non-fatal)
            try:
                if self._signal_logger:
                    ml_signal_id = pos.metadata.get("signal_id", -1) if pos.metadata else -1
                    if ml_signal_id > 0:
                        exit_pnl = (exec_result.fill_price - pos.entry_price) * exec_result.fill_size
                        exit_pnl_pct = (exec_result.fill_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                        self._signal_logger.update_signal(ml_signal_id, {
                            "exit_price": exec_result.fill_price,
                            "exit_reason": exit_signal.reason,
                            "pnl": exit_pnl,
                            "pnl_pct": exit_pnl_pct,
                            "hold_secs": int(time.time() - pos.entry_time.timestamp()),
                            "is_win": 1 if exit_pnl > 0 else 0,
                        })
            except Exception as sl_err:
                self._logger.error(f"Signal logger exit update failed (non-fatal): {sl_err}")

            # 5d. Slack exit notification (non-fatal)
            try:
                exit_pnl = (exec_result.fill_price - pos.entry_price) * exec_result.fill_size
                hold_secs = time.time() - pos.entry_time.timestamp()
                slug_parts = pos.slug.rsplit("-", 2) if pos.slug else []
                asset = pos.metadata.get("asset", "") if pos.metadata else ""
                direction = pos.metadata.get("direction", "") if pos.metadata else ""
                if not asset and len(slug_parts) >= 3:
                    asset = slug_parts[0].upper()
                if not direction and len(slug_parts) >= 2:
                    direction = slug_parts[-2].capitalize()
                self._slack.notify_exit(
                    slug=pos.slug,
                    asset=asset,
                    direction=direction,
                    entry_price=pos.entry_price,
                    exit_price=exec_result.fill_price,
                    shares=exec_result.fill_size,
                    pnl=exit_pnl,
                    exit_reason=exit_signal.reason,
                    hold_secs=hold_secs,
                )
                # Update balance for session line (best-effort)
                try:
                    bal = await self._balance.get_balance()
                    self._slack.update_balance(bal)
                except Exception:
                    pass
            except Exception:
                pass

            # 5e. Update fill optimizer profit (non-fatal)
            try:
                if self._fill_optimizer:
                    fo_pnl = (exec_result.fill_price - pos.entry_price) * exec_result.fill_size
                    self._fill_optimizer.update_profit(pos.slug, fo_pnl)
            except Exception as fo_err:
                self._logger.error(f"Fill optimizer profit update failed (non-fatal): {fo_err}")

            # 5f. Reversal short: flip to opposite side after reversal exit (non-fatal)
            try:
                await self._try_reversal_short(pos, exit_signal)
            except Exception as rs_err:
                self._logger.error(f"Reversal short failed for {pos.slug} (non-fatal): {rs_err}")

            # 6. Remove from position store (MUST run after successful SELL)
            self._store.remove(exit_signal.token_id)

            # 7. Complete exit in exit manager (MUST run)
            self._exit_mgr.complete_exit(exit_signal.token_id)

            # 8. Enqueue auto-redemption for resolved markets (non-fatal)
            if exit_signal.reason in ('market_resolved', 'max_hold', 'time_exit'):
                self._enqueue_redemption(pos)

        except Exception as e:
            self._logger.exception(f"Error in _handle_exit: {e}")
            self._health.record_error()

    def _enqueue_redemption(self, pos):
        """Enqueue CTF token redemption if redeemer is active and condition_id is known."""
        if not self._redeemer:
            return
        condition_id = (pos.metadata or {}).get("condition_id", "")
        if not condition_id:
            self._logger.debug(f"No condition_id for {pos.slug}, skipping auto-redeem")
            return
        try:
            from .types import RedemptionEvent
            self._redeemer.enqueue(RedemptionEvent(
                condition_id=condition_id,
                slug=pos.slug,
                winning_side="",  # unknown at exit time
                shares=pos.entry_size,
                settled_at=time.time(),
            ))
            self._logger.info(f"Auto-redeem queued: {pos.slug} ({pos.entry_size:.1f} shares)")
        except Exception as e:
            self._logger.error(f"Failed to enqueue redemption for {pos.slug}: {e}")

    async def _try_reversal_short(self, pos, exit_signal):
        """After a reversal exit, create a signal to buy the opposite side token.

        Only fires on oracle_reversal or binance_reversal exits.
        Uses the same market_cache + market_ws pattern as oracle_flip.
        """
        # Gate 1: only on reversal exits
        if exit_signal.reason not in ('oracle_reversal', 'binance_reversal'):
            return

        # Gate 2: config enabled
        if not self._config.reversal_short_enabled:
            return

        # Gate 3: anti-loop -- never flip a flip
        source = (pos.metadata or {}).get("source", "")
        if source == "reversal_short":
            self._logger.debug(f"Skipping reversal_short for {pos.slug}: already a reversal_short position")
            return

        # Gate 4: need momentum feed for market cache
        if not self._momentum_feed or not hasattr(self._momentum_feed, '_market_cache'):
            self._logger.debug("Reversal short: no momentum feed or market cache")
            return

        # Gate 5: time remaining
        now = time.time()
        if pos.market_end_time:
            secs_left = pos.market_end_time.timestamp() - now
        else:
            self._logger.debug(f"Reversal short: no market_end_time for {pos.slug}")
            return

        if secs_left < self._config.reversal_short_min_secs_remaining:
            self._logger.debug(
                f"Reversal short skip: {secs_left:.0f}s left < "
                f"{self._config.reversal_short_min_secs_remaining}s min"
            )
            return

        # Lookup opposite token from market cache
        slug = pos.slug
        market_info = self._momentum_feed._market_cache.get(slug)
        if not market_info:
            self._logger.debug(f"Reversal short: no market_cache entry for {slug}")
            return

        # Determine opposite direction
        entry_direction = (pos.metadata or {}).get("direction", "").lower()
        if entry_direction == "down":
            opp_direction = "Up"
            opp_key = "up_token_id"
        elif entry_direction == "up":
            opp_direction = "Down"
            opp_key = "down_token_id"
        else:
            self._logger.debug(f"Reversal short: unknown direction '{entry_direction}' for {slug}")
            return

        opp_token_id = market_info.get(opp_key)
        if not opp_token_id:
            self._logger.debug(f"Reversal short: no {opp_key} in market_cache for {slug}")
            return

        # Get midpoint of opposite token (market_ws first, clob fallback)
        opp_mid = 0.0
        if self._market_ws:
            opp_mid = self._market_ws.get_midpoint(opp_token_id)
        if opp_mid <= 0:
            try:
                opp_mid = await self._clob.get_midpoint(opp_token_id)
            except Exception:
                pass

        # Gate 6: price range
        if (opp_mid <= 0
                or opp_mid > self._config.reversal_short_max_down_price
                or opp_mid < self._config.reversal_short_min_down_price):
            self._logger.debug(
                f"Reversal short skip: opp_mid={opp_mid:.4f} "
                f"outside [{self._config.reversal_short_min_down_price}, "
                f"{self._config.reversal_short_max_down_price}]"
            )
            return

        asset = (pos.metadata or {}).get("asset", "")
        window = (pos.metadata or {}).get("market_window_secs", 300)

        rs_signal = {
            "slug": slug,
            "token_id": opp_token_id,
            "outcome": opp_direction,
            "midpoint": opp_mid,
            "price": opp_mid,
            "source": "reversal_short",
            "asset": asset,
            "market_window_secs": window,
            "time_remaining_secs": int(secs_left),
            "condition_id": market_info.get("condition_id", ""),
            "metadata": {
                "source": "reversal_short",
                "triggered_by": exit_signal.reason,
                "original_direction": entry_direction,
                "original_entry_price": pos.entry_price,
            },
        }

        if self._config.reversal_short_dry_run:
            potential_shares = self._config.reversal_short_max_bet / opp_mid if opp_mid > 0 else 0
            self._logger.info(
                f"[REVERSAL_SHORT DRY] {slug} flip to {opp_direction} "
                f"@ {opp_mid:.4f} | {secs_left:.0f}s left | "
                f"trigger={exit_signal.reason} | "
                f"projected_shares={potential_shares:.0f} "
                f"max_bet=${self._config.reversal_short_max_bet:.0f}"
            )
            # Log to signal_logger for tracking
            if self._signal_logger:
                rs_signal["dry_run"] = True
                self._signal_logger.log_signal({
                    "slug": slug,
                    "asset": asset,
                    "direction": opp_direction,
                    "token_id": opp_token_id,
                    "midpoint": opp_mid,
                    "source": "reversal_short",
                    "outcome": "dry_run",
                    "dry_run": 1,
                })
            return

        self._logger.info(
            f"[REVERSAL_SHORT] {slug} FLIPPING to {opp_direction} "
            f"@ {opp_mid:.4f} | {secs_left:.0f}s left | "
            f"trigger={exit_signal.reason}"
        )
        await self._on_signal(rs_signal)


async def main():
    """Main entry point for the bot."""
    config = Settings()
    bot = SignalBot(config=config)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
