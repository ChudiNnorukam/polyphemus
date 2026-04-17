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
from .models import ExitSignal, MomentumResult, EXIT_CHECK_INTERVAL, PRICE_FEED_INTERVAL, ASSET_TO_BINANCE
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
from .binance_momentum import BinanceMomentumFeed, parse_strike_from_slug
from .dashboard import Dashboard
from .arb_engine import ArbEngine
from .accumulator import AccumulatorEngine
from .signal_logger import SignalLogger
from .evidence_verdict import BTC5MEvidenceEngine
from .trade_tracer import emit as _trace_emit, EventType as _ET
from .fill_router import route_dry_run_fill
from .signal_scorer import SignalScorer
from .fill_optimizer import FillOptimizer
from .regime_detector import RegimeDetector
from .accumulator_metrics import AccumulatorMetrics
from .gabagool_tracker import GabagoolTracker
from .tugao9_watcher import Tugao9Watcher
from .adaptive_tuner import AdaptiveTuner
from .market_ws import MarketWS
from .slack_notifier import SlackNotifier
from .chainlink_feed import ChainlinkFeed
from .market_maker import MarketMaker
from .ensemble_shadow import BTC5MEnsembleShadow
from .signal_pipeline import (
    build_entry_metadata,
    build_signal_log_features,
    normalize_signal,
)


def _format_guard_context(context: dict) -> str:
    """Render compact guard diagnostics for logs."""
    if not context:
        return ""
    ordered_keys = (
        "price",
        "min_price",
        "max_price",
        "directionality",
        "vol_1h",
        "trend_1h",
        "epoch_elapsed_secs",
        "epoch_max_elapsed_secs",
        "time_remaining_secs",
        "flat_vol_1h",
        "liq_volume_60s",
        "liq_bias",
        "funding_rate",
        "taker_delta",
        "cvd_agrees",
        "vpin_5m",
        "vpin_flow_opposes",
        "coinbase_premium_bps",
        "cb_premium_opposes",
    )
    parts = []
    for key in ordered_keys:
        if key in context:
            parts.append(f"{key}={context[key]}")
    for key in sorted(context):
        if key not in ordered_keys:
            parts.append(f"{key}={context[key]}")
    return ", ".join(parts)


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
        self._config_label = config.config_label or ""
        self._config_era = config.get_config_era_tag()
        self._instance_name = config.get_instance_name()

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

        # 5b. Epoch accumulator (ugag-style continuous buying)
        self._epoch_accum = None
        if config.accum_mode_enabled:
            from .epoch_accumulator import EpochAccumulator
            self._epoch_accum = EpochAccumulator(config, self._executor, market_ws=self._market_ws)
            self._logger.info(
                f"Epoch accumulator enabled | ${config.accum_bet_per_round}/round | "
                f"{config.accum_interval_secs}s interval | max {config.accum_max_rounds} rounds"
            )

        # 5c. Cheap side signal (buy whichever side is cheaper)
        self._cheap_side = None
        if getattr(config, 'cheap_side_enabled', False):
            from .cheap_side_signal import CheapSideSignal
            self._cheap_side = CheapSideSignal(
                config=config,
                clob=self._clob,
                on_signal=self._on_cheap_side_signal,
            )
            self._logger.info(
                f"Cheap side signal enabled | max_price=${config.cheap_side_max_price}"
            )

        # 6. Exit manager and handler
        self._exit_mgr = ExitManager(store=self._store, config=config)
        self._exit_handler = ExitHandler(clob=self._clob, config=config)

        # 7. Signal guard
        self._guard = SignalGuard(config=config, store=self._store)

        # 7b. Seed outcome gate with recent trade history
        try:
            import sqlite3
            _perf_conn = sqlite3.connect(self._db_path)
            _recent = _perf_conn.execute(
                "SELECT pnl FROM trades WHERE exit_time IS NOT NULL "
                "ORDER BY exit_time DESC LIMIT ?",
                (config.outcome_gate_window,)
            ).fetchall()
            _perf_conn.close()
            if _recent:
                # Reverse to oldest-first order
                outcomes_oldest_first = [row[0] > 0 for row in reversed(_recent)]
                self._guard.seed_outcomes(outcomes_oldest_first)
                self._guard.seed_markov_outcomes(outcomes_oldest_first)
                self._logger.info(
                    f"Outcome gate seeded with {len(_recent)} recent trades"
                )
        except Exception as seed_err:
            self._logger.warning(f"Outcome gate seed failed (non-fatal): {seed_err}")

        # 8. Performance tracker
        self._tracker = PerformanceTracker(self._db_path)
        if self._epoch_accum:
            self._epoch_accum._tracker = self._tracker

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
        if config.signal_mode == "binance_momentum":
            # Momentum mode: Binance prices are the PRIMARY signal source
            self._momentum_feed = BinanceMomentumFeed(
                config=config,
                clob=self._clob,
                on_signal=self._on_signal,
            )
            self._exit_mgr.set_momentum_feed(self._momentum_feed)
            self._executor._momentum_feed = self._momentum_feed
            if self._epoch_accum:
                self._epoch_accum._momentum_feed = self._momentum_feed
            if self._cheap_side:
                self._cheap_side._momentum_feed = self._momentum_feed
                self._cheap_side._market_ws = getattr(self._momentum_feed, '_market_ws', None)
            shadow = config.get_shadow_assets()
            self._logger.info(
                f"Signal mode: Binance momentum (primary)"
                f"{f' | shadow={shadow}' if shadow else ''}"
            )
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

        if config.signal_mode in ("binance_momentum", "noaa_weather"):
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

        # Link slack to health monitor for error rate alerts
        self._health.set_slack(self._slack)

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
            self._health.set_accumulator_engine(self._accumulator)
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
                    relayer_api_key=config.relayer_api_key,
                    relayer_api_key_address=config.relayer_api_key_address,
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

        # 13a-iv. Tugao9 copy-trade watcher
        self._tugao9_watcher = None
        if config.tugao9_watcher_enabled:
            self._tugao9_watcher = Tugao9Watcher(
                address=config.tugao9_address,
                poll_interval=config.tugao9_poll_interval,
                min_price=config.tugao9_min_price,
                max_price=config.tugao9_max_price,
                shadow=config.tugao9_shadow,
                on_signal=self._on_signal,
                momentum_feed=self._momentum_feed,
                allowed_assets=set(config.asset_filter),
            )
            self._logger.info(
                f"Tugao9 watcher ENABLED | shadow={config.tugao9_shadow} | "
                f"interval={config.tugao9_poll_interval}s"
            )

        # 13b. Data science modules (all optional, graceful degradation)
        self._signal_logger = None
        self._btc5m_evidence = None
        self._btc5m_ensemble_shadow = None
        self._signal_scorer = None
        self._fill_optimizer = None
        self._regime_detector = None

        if config.enable_signal_logging:
            self._signal_logger = SignalLogger(db_path=os.path.join(config.lagbot_data_dir, "signals.db"))
            self._logger.info("SignalLogger ENABLED")
            if config.enable_btc5m_evidence_verdicts:
                self._btc5m_evidence = BTC5MEvidenceEngine(
                    db_path=os.path.join(config.lagbot_data_dir, "signals.db"),
                    min_samples=config.btc5m_evidence_min_samples,
                )
                self._logger.info(
                    "BTC5MEvidenceEngine ENABLED (mode=%s, min_samples=%s)",
                    config.btc5m_evidence_mode,
                    config.btc5m_evidence_min_samples,
                )
            if config.enable_btc5m_ensemble_shadow:
                self._btc5m_ensemble_shadow = BTC5MEnsembleShadow(logger=self._logger)
                self._logger.info(
                    "BTC5MEnsembleShadow ENABLED (mode=%s)",
                    config.btc5m_ensemble_mode,
                )

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
        if self._accumulator:
            self._accumulator._perf_db = self._tracker.db
        self._health.set_pipeline_dbs(
            signal_logger=self._signal_logger,
            perf_db=self._tracker.db,
        )
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
            executor=self._executor,
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
        # Seed Slack stats from today's trades in DB (not zeros)
        try:
            import sqlite3 as _sql
            _db_path = os.path.join(self._config.lagbot_data_dir, "performance.db")
            _conn = _sql.connect(_db_path)
            _row = _conn.execute(
                "SELECT "
                "  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), "
                "  SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END), "
                "  COALESCE(SUM(pnl), 0) "
                "FROM trades WHERE date(entry_time, 'unixepoch') = date('now')"
            ).fetchone()
            _conn.close()
            _wins = _row[0] or 0
            _losses = _row[1] or 0
            _pnl = _row[2] or 0.0
            self._logger.info(f"PREFLIGHT [OK]   today's DB stats: {_wins}W {_losses}L, ${_pnl:.2f}")
        except Exception:
            _wins, _losses, _pnl = 0, 0, 0.0
        self._slack.seed_stats(_wins, _losses, _pnl, start_balance=balance)
        self._slack.notify_startup(
            open_positions=self._store.count_open(),
            balance=balance,
            dry_run=self._dry_run,
            active_assets=self._config.asset_filter,
            entry_mode=self._config.entry_mode,
            max_bet=self._config.max_bet,
            max_open_positions=self._config.max_open_positions,
            mid_price_stop_enabled=self._config.mid_price_stop_enabled,
            mid_price_stop_pct=self._config.mid_price_stop_pct,
        )

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

    async def _on_cheap_side_signal(self, signal: dict):
        """Handle a cheap-side signal by routing through the normal entry pipeline."""
        self._logger.info(
            f"Cheap side signal: {signal.get('slug')} {signal.get('outcome')} "
            f"@ ${signal.get('price', 0):.3f}"
        )
        # Route through the normal _on_signal which handles guard, accumulator, etc.
        await self._on_signal(signal)

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
            if self._config.mm_limit_enabled and self._market_maker:
                tasks.append(self._safe_task(self._market_maker.start_limit_mm(), "limit_mm"))
            elif self._config.mm_limit_enabled and not self._market_maker:
                # Limit MM needs a MarketMaker instance even if taker MM is disabled
                self._market_maker = MarketMaker(
                    config=self._config,
                    clob=self._clob,
                    market_ws=self._market_ws,
                    momentum_feed=self._momentum_feed,
                    tracker=self._tracker,
                    store=self._store,
                )
                tasks.append(self._safe_task(self._market_maker.start_limit_mm(), "limit_mm"))
            if self._config.mm_rebate_enabled:
                if self._market_maker:
                    tasks.append(self._safe_task(self._market_maker.start_rebate_mm(), "rebate_mm"))
                else:
                    self._market_maker = MarketMaker(
                        config=self._config,
                        clob=self._clob,
                        market_ws=self._market_ws,
                        momentum_feed=self._momentum_feed,
                        tracker=self._tracker,
                        store=self._store,
                    )
                    tasks.append(self._safe_task(self._market_maker.start_rebate_mm(), "rebate_mm"))
            if self._config.igoc_enabled:
                tasks.append(self._safe_task(self._igoc_signal_loop(), "igoc_signal_loop"))
            if self._config.flat_regime_rtds_enabled:
                tasks.append(self._safe_task(self._flat_regime_rtds_loop(), "flat_regime_rtds"))
            if self._tugao9_watcher:
                tasks.append(self._safe_task(self._tugao9_watcher.start(), "tugao9_watcher"))
            if self._cheap_side:
                tasks.append(self._safe_task(self._cheap_side.scan_loop(), "cheap_side_signal"))
            if self._signal_logger:
                tasks.append(self._safe_task(self._position_snapshot_loop(), "position_snapshots"))
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
                if (datetime.now(timezone.utc) - ctx_time).total_seconds() > 7200:
                    return {}
            return ctx
        except Exception:
            return {}

    def _enrich_signal_context(self, raw_signal: dict) -> tuple[dict, dict, object]:
        """Normalize a raw signal and enrich it with external market context."""
        envelope = normalize_signal(raw_signal)
        signal = envelope.signal

        market_context = self._read_market_context()
        if market_context:
            fg_val = market_context.get("fear_greed")
            if fg_val is not None:
                signal["fear_greed"] = fg_val
            market_regime = market_context.get("market_regime")
            if market_regime:
                signal["market_regime"] = market_regime
            asset_ctx = market_context.get(signal.get("asset", "BTC"), {})
            if asset_ctx.get("oi_change_pct") is not None:
                signal["oi_change_pct"] = asset_ctx.get("oi_change_pct")
            if asset_ctx.get("oi_trend"):
                signal["oi_trend"] = asset_ctx.get("oi_trend")

        regime = None
        if getattr(self, "_regime_detector", None):
            regime = self._regime_detector.get_regime(signal.get("asset", "BTC"))
            signal["regime"] = getattr(regime, "regime", "")
            signal["volatility_1h"] = getattr(regime, "volatility_1h", None)
            signal["trend_1h"] = getattr(regime, "trend_1h", None)

        if getattr(self, "_momentum_feed", None):
            asset = signal.get("asset", "BTC")
            binance_symbol = ASSET_TO_BINANCE.get(asset)
            if binance_symbol:
                signal["vpin_5m"] = self._momentum_feed.get_vpin(binance_symbol)
                signal["taker_delta"] = self._momentum_feed.get_taker_delta(binance_symbol, 300)

        return signal, market_context, regime

    def _mark_signal_stage(
        self,
        signal_id: int,
        stage: str,
        status: str,
        detail: str = "",
        **extra_updates,
    ) -> None:
        """Persist pipeline stage transitions when signal logging is enabled."""
        if not self._signal_logger or signal_id <= 0:
            return
        if hasattr(self._signal_logger, "mark_signal_stage"):
            self._signal_logger.mark_signal_stage(
                signal_id, stage, status, detail, **extra_updates
            )
            return
        updates = {
            "pipeline_stage": stage,
            "pipeline_status": status,
            "pipeline_detail": detail,
        }
        updates.update(extra_updates)
        self._signal_logger.update_signal(signal_id, updates)

    def _log_signal_observation(
        self,
        signal: dict,
        guard_result,
        *,
        market_context: dict,
        regime,
    ) -> tuple[int, object | None]:
        """Log the normalized signal once, then attach evidence annotations."""
        if not self._signal_logger:
            return -1, None

        signal_id = self._signal_logger.log_signal(
            build_signal_log_features(
                signal,
                guard_result,
                market_context=market_context,
                regime=regime,
                config_label=getattr(self, "_config_label", ""),
                config_era=getattr(self, "_config_era", ""),
                instance_name=getattr(self, "_instance_name", ""),
            )
        )
        if signal_id <= 0:
            return signal_id, None

        slug = signal.get("slug", "")
        parts = slug.rsplit("-", 1) if slug else []
        if len(parts) == 2 and parts[1].isdigit():
            epoch = int(parts[1])
            try:
                self._signal_logger.update_epoch_outcome(
                    epoch=epoch,
                    asset=signal.get("asset", ""),
                    window_secs=int(signal.get("market_window_secs") or 0),
                    bot_saw_signal=True,
                    bot_signal_source=signal.get("source", ""),
                )
            except Exception:
                pass

        evidence_engine = getattr(self, "_btc5m_evidence", None)
        if evidence_engine:
            evidence_verdict = evidence_engine.evaluate_signal(signal)
            if evidence_verdict:
                self._signal_logger.update_signal(
                    signal_id,
                    evidence_verdict.as_signal_updates(),
                )
                self._logger.info(
                    "BTC5m evidence verdict (%s): %s | %s",
                    evidence_verdict.verdict,
                    signal.get("slug", "unknown"),
                    evidence_verdict.reason,
                )
        ensemble_shadow = getattr(self, "_btc5m_ensemble_shadow", None)
        ensemble_verdict = None
        if ensemble_shadow:
            ensemble_verdict = ensemble_shadow.evaluate(
                signal,
                signal_id,
                signal_logger=self._signal_logger,
            )
            if ensemble_verdict:
                self._signal_logger.update_signal(signal_id, ensemble_verdict.as_signal_updates())
        return signal_id, ensemble_verdict

    def _ensemble_admission_enabled(self) -> bool:
        return bool(getattr(self._config, "btc5m_ensemble_admission_enabled", False))

    def _should_apply_ensemble_admission(self, signal: dict) -> bool:
        return (
            self._ensemble_admission_enabled()
            and signal.get("asset") == "BTC"
            and int(signal.get("market_window_secs") or 0) == 300
            and signal.get("source") == "binance_momentum"
            and not signal.get("shadow")
        )

    def _ensemble_admission_passed(self, ensemble_verdict) -> bool:
        return bool(ensemble_verdict and getattr(ensemble_verdict, "ensemble_selected", False))

    def _prepare_entry_signal(self, signal: dict) -> dict:
        """Attach stable metadata before handoff to the executor."""
        prepared = dict(signal)
        binance_price = prepared.get("binance_price", 0.0)
        if not binance_price and self._momentum_feed:
            binance_price = self._momentum_feed.get_latest_price(
                prepared.get("asset", "BTC")
            ) or 0.0

        prepared["metadata"] = build_entry_metadata(
            prepared,
            entry_binance_price=binance_price,
        )

        reversal_sources = (
            "binance_momentum", "binance_momentum_lag", "sharp_move",
            "oracle_flip", "reversal_short", "window_delta", "streak_contrarian",
        )
        if prepared.get("source") in reversal_sources:
            prepared["metadata"]["entry_momentum_direction"] = prepared.get(
                "outcome", ""
            ).lower()
            prepared["metadata"]["entry_momentum_ts"] = time.time()
            prepared["metadata"]["entry_binance_price"] = binance_price
        return prepared

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
            signal, market_context, regime = self._enrich_signal_context(signal)
            if signal.get("noise_flags"):
                self._logger.debug(
                    "Signal normalization flags for %s: %s",
                    signal.get("slug", "unknown"),
                    ",".join(signal["noise_flags"]),
                )

            result = self._guard.check(signal)
            # Inject Markov state into signal for downstream Kelly sizing.
            # getattr keeps minimal test fixtures working — any production
            # Settings instance has this attribute via config.Settings.
            if getattr(self._config, 'markov_kelly_enabled', False) and result.context:
                signal['markov_consec_w'] = result.context.get('markov_consec_w', 0)
                signal['markov_consec_l'] = result.context.get('markov_consec_l', 0)
            signal_id = -1
            if self._signal_logger:
                signal_id, ensemble_verdict = self._log_signal_observation(
                    signal,
                    result,
                    market_context=market_context,
                    regime=regime,
                )
            else:
                ensemble_verdict = None
            self._mark_signal_stage(
                signal_id,
                "guard",
                "passed" if result.passed else "filtered",
                ",".join(result.reasons) if result.reasons else "guard_passed",
            )

            # Shadow mode: log signal but don't execute
            if signal.get("shadow"):
                guard_status = "passed" if result.passed else f"rejected({','.join(result.reasons)})"
                context_str = _format_guard_context(result.context)
                if context_str:
                    guard_status = f"{guard_status} [{context_str}]"
                self._logger.info(
                    f"[SHADOW] {signal.get('slug')} {signal.get('outcome')} "
                    f"@ {signal.get('price', 0):.4f} | "
                    f"momentum={signal.get('momentum_pct', 0):+.3%} | "
                    f"guard={guard_status}"
                )
                if self._signal_logger and signal_id > 0:
                    self._signal_logger.update_signal(signal_id, {"outcome": "shadow"})
                self._mark_signal_stage(signal_id, "shadow", "logged", guard_status)
                return

            if not result.passed:
                context_str = _format_guard_context(result.context)
                reason_text = f"{result.reasons}"
                if context_str:
                    reason_text = f"{reason_text} | context: {context_str}"
                if self._signal_logger and signal_id > 0:
                    self._signal_logger.update_signal(signal_id, {"outcome": "filtered"})
                self._mark_signal_stage(signal_id, "guard", "filtered", reason_text)
                self._logger.info(
                    f"Signal rejected: {signal.get('slug', 'unknown')} "
                    f"Reasons: {reason_text}"
                )
                return

            if self._should_apply_ensemble_admission(signal):
                if not self._ensemble_admission_passed(ensemble_verdict):
                    detail = "shadow_ensemble_selected=0"
                    if self._signal_logger and signal_id > 0:
                        self._signal_logger.update_signal(signal_id, {"outcome": "ensemble_filtered"})
                    self._mark_signal_stage(signal_id, "ensemble_admission", "filtered", detail)
                    self._logger.info(
                        "BTC5m ensemble admission filtered: %s | %s",
                        signal.get("slug", "unknown"),
                        detail,
                    )
                    return
                self._mark_signal_stage(
                    signal_id,
                    "ensemble_admission",
                    "passed",
                    "shadow_ensemble_selected=1",
                )

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
                        detail = (
                            f"outcome={signal.get('outcome')} vs {momentum.direction} "
                            f"({momentum.momentum_pct:+.3%}) "
                            f"[{phase} | {lookback}m | thresh={threshold:.4f}]"
                        )
                        self._logger.info(
                            f"Momentum rejected: {signal.get('slug')} "
                            f"{detail}"
                        )
                        if self._signal_logger and signal_id > 0:
                            self._signal_logger.update_signal(signal_id, {"outcome": "binance_filtered"})
                        self._mark_signal_stage(signal_id, "momentum_confirmation", "filtered", detail)
                        return
                    self._momentum_stats["approved"] += 1
                    detail = (
                        f"{momentum.direction} ({momentum.momentum_pct:+.3%}, "
                        f"conf={momentum.confidence:.2f}) "
                        f"[{phase} | {lookback}m | thresh={threshold:.4f}]"
                    )
                    self._logger.info(
                        f"Momentum confirmed: {signal.get('slug')} "
                        f"{detail}"
                    )
                    self._mark_signal_stage(signal_id, "momentum_confirmation", "passed", detail)

            # 2b-ii. Epoch time gate: reject if insufficient time for order lifecycle
            # Order fill + settlement takes ~30s. Entries with <120s remaining
            # resolve in <90s hold time, which has 4.1% WR (flash trades).
            time_remaining = signal.get('time_remaining_secs', 999)
            min_exec_secs = self._config.min_execution_secs_remaining
            if signal.get('source') in ('cheap_side',) and time_remaining < min_exec_secs:
                detail = f"time_remaining={time_remaining:.0f}s < min={min_exec_secs}s"
                if self._signal_logger and signal_id > 0:
                    self._signal_logger.update_signal(signal_id, {"outcome": "epoch_time_filtered"})
                self._mark_signal_stage(signal_id, "epoch_time_gate", "filtered", detail)
                self._logger.info(
                    f"Epoch time gate: {signal.get('slug', '?')} | {detail}"
                )
                return

            # 2b-iii. Pre-entry adverse selection filter
            # Check if Binance price is moving AGAINST trade direction in last N seconds.
            # Favorable fills (Binance moving WITH trade) have 47.1% WR (break-even).
            # Adverse fills (Binance moving AGAINST) have 19.0% WR (disaster).
            if (self._config.adverse_precheck_enabled
                    and signal.get('source') in ('cheap_side',)
                    and self._momentum_feed
                    and asset in ASSET_TO_BINANCE):
                lookback = self._config.adverse_precheck_secs
                threshold = self._config.adverse_precheck_threshold
                velocity = self._momentum_feed.get_recent_velocity(asset, lookback_secs=lookback)
                if velocity is not None:
                    direction = signal.get('outcome', '')
                    is_adverse = (
                        (direction == 'Up' and velocity < -threshold) or
                        (direction == 'Down' and velocity > threshold)
                    )
                    detail = (
                        f"{direction} vs velocity={velocity:+.4%} "
                        f"(lookback={lookback}s, threshold={threshold:.4%})"
                    )
                    if is_adverse:
                        if self._signal_logger and signal_id > 0:
                            self._signal_logger.update_signal(signal_id, {"outcome": "adverse_precheck_filtered"})
                        self._mark_signal_stage(signal_id, "adverse_precheck", "filtered", detail)
                        self._logger.info(
                            f"ADVERSE PRECHECK SKIP | {signal.get('slug', '?')} | {detail}"
                        )
                        return
                    self._mark_signal_stage(signal_id, "adverse_precheck", "passed", detail)
                    self._logger.debug(f"Adverse precheck passed: {signal.get('slug', '?')} | {detail}")

            # 2c. Regime check (skip flat markets — not applicable to price arb or weather)
            if self._regime_detector and signal.get('source') not in ('pair_arb', 'noaa_weather', 'resolution_snipe', 'flat_regime_rtds', 'tugao9_copy'):
                if not self._regime_detector.should_trade(signal.get("asset", "BTC")):
                    if self._signal_logger and signal_id > 0:
                        self._signal_logger.update_signal(signal_id, {"outcome": "regime_filtered"})
                    self._mark_signal_stage(signal_id, "regime_check", "filtered", "flat_market")
                    self._logger.info(f"Regime filtered: {signal.get('slug', '?')} (flat market)")
                    return
                self._mark_signal_stage(
                    signal_id,
                    "regime_check",
                    "passed",
                    getattr(regime, "regime", "tradeable"),
                )

            # 2d. Signal scoring
            signal_score = None
            if self._signal_scorer:
                now_utc = datetime.now(timezone.utc)
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
                    self._mark_signal_stage(
                        signal_id,
                        "score",
                        "filtered",
                        f"{signal_score:.1f}<{self._signal_scorer._threshold}",
                    )
                    return
                self._logger.info(f"Signal score: {signal_score:.1f} for {signal.get('slug', '?')}")
                self._mark_signal_stage(
                    signal_id,
                    "score",
                    "passed",
                    f"{signal_score:.1f}>={self._signal_scorer._threshold}",
                )

            # 2b. Circuit breaker check (entries only — exits never blocked)
            # Bypass for reversal_short: the flip is triggered BY a loss, cooldown must not block it
            if self._trading_halted:
                self._mark_signal_stage(signal_id, "circuit_breaker", "halted", "startup_reconciliation_failed")
                self._logger.warning("Trading halted: startup reconciliation failed")
                return
            if signal.get('source') != 'reversal_short':
                allowed, cb_reason = self._circuit_breaker.is_trading_allowed()
                if not allowed:
                    self._mark_signal_stage(signal_id, "circuit_breaker", "blocked", cb_reason)
                    self._logger.warning(f"Circuit breaker blocked entry: {cb_reason}")
                    return
                self._mark_signal_stage(signal_id, "circuit_breaker", "passed", cb_reason or "allowed")

            # 3. Check if safe to trade
            if not await self._balance.is_safe_to_trade():
                self._mark_signal_stage(signal_id, "balance", "blocked", "unsafe_to_trade")
                self._logger.warning("Not safe to trade - balance too low or limit reached")
                return

            # 4. Get available capital (reserve accumulator share if enabled)
            if self._config.enable_accumulator:
                available = await self._balance.get_available_for_momentum()
            else:
                available = await self._balance.get_available()
            if available < self._config.min_bet:
                self._mark_signal_stage(
                    signal_id,
                    "sizing",
                    "blocked",
                    f"available={available:.2f}<min_bet={self._config.min_bet:.2f}",
                )
                self._logger.warning(
                    f"Insufficient capital: ${available:.2f} < ${self._config.min_bet:.2f}"
                )
                return

            # 5. Dry run check — create phantom position for data pipeline
            if self._dry_run:
                price = signal.get('price', 0)
                asset = signal.get('asset', '')
                projected = self._executor._calculate_size(price, available, asset, spread=signal.get("spread"), signal=signal)
                if price <= 0 or projected <= 0:
                    return

                # Generate phantom IDs
                phantom_id = f"dry_{signal.get('slug', 'unknown')}_{int(time.time())}"
                phantom_token = signal.get("token_id", phantom_id)
                size = projected / price if price > 0 else 0

                # Create phantom position in store — exit manager will track it
                from .models import Position
                phantom_pos = Position(
                    token_id=phantom_token,
                    slug=signal.get("slug", ""),
                    entry_price=price,
                    entry_size=size,
                    entry_time=datetime.now(timezone.utc),
                    entry_tx_hash=phantom_id,
                    current_price=price,
                    market_end_time=signal.get("market_end_time"),
                    metadata={
                        "asset": asset,
                        "direction": signal.get("outcome", ""),
                        "source": signal.get("source", "momentum"),
                        "signal_id": signal_id,
                        "dry_run": True,
                    },
                )
                self._store.add(phantom_pos)

                # Route the phantom through fill_router so the fill-model label
                # matches actual behavior (Phase 2 proper V2 wiring):
                #   maker entries with book state  → V2 probabilistic (may not fill)
                #   taker / FAK entries            → V1 instant (V2 is maker-only)
                #   maker entries missing bid/ask  → V1 instant (router auto-degrades)
                # ``signal.entry_mode_override`` wins over config default (cheap_side
                # forces "fak"). When the router reports filled=False, we skip phantom
                # creation entirely — that's the whole point of V2: an honest rest-model
                # reports unfilled orders as unfilled.
                _effective_entry_mode = signal.get("entry_mode_override") or self._config.entry_mode

                if _effective_entry_mode == "maker":
                    # V2 is a decay-over-time model: at elapsed=0, p_fill=0
                    # (see MakerFillModel.evaluate). The phantom path is a
                    # one-shot eval, not a poll loop, so we have to pass a
                    # representative rest duration. Cap at 30s to stay
                    # pessimistic — a maker order that hasn't filled in 30s
                    # on Polymarket is usually stale anyway. Use the signal's
                    # remaining market time as the ceiling so we never
                    # simulate resting past market close.
                    try:
                        _time_left = float(signal.get("time_remaining_secs") or 30.0)
                    except (TypeError, ValueError):
                        _time_left = 30.0
                    _sim_elapsed = min(max(_time_left, 0.0), 30.0)
                    _fr = route_dry_run_fill(
                        our_price=price,
                        best_bid=float(signal.get("best_bid") or 0),
                        best_ask=float(signal.get("best_ask") or 0),
                        qty=size,
                        elapsed_secs=_sim_elapsed,
                    )
                    if not _fr.filled:
                        _trace_emit(phantom_id, _ET.SIGNAL_FIRED, {
                            "source": signal.get("source"), "signal_id": signal_id,
                            "momentum_pct": signal.get("momentum_pct"),
                            "price": price, "size": size, "dry_run": True,
                        })
                        self._logger.info(
                            f"[DRY RUN] Phantom SKIPPED by V2: {signal.get('slug', 'unknown')} "
                            f"reason={_fr.fill_model_reason} price=${price:.4f} "
                            f"bid/ask=${_fr.book_spread_at_decision:.4f} spread"
                        )
                        # Remove the phantom position we added speculatively above —
                        # V2 said the resting order didn't fill, so no position exists.
                        self._store.remove(phantom_token)
                        return
                    _phantom_fill_model = _fr.fill_model
                    _phantom_fill_reason = _fr.fill_model_reason
                    _phantom_book_spread = _fr.book_spread_at_decision
                    _phantom_fill_price = _fr.fill_price
                    _phantom_fill_qty = _fr.fill_qty
                else:
                    # Taker / FAK: instant fill at signal price. V2 doesn't model
                    # takers — the fill model is for maker queue-position decay.
                    _phantom_fill_model = "v1_taker"
                    _phantom_fill_reason = "dry_run_phantom_taker"
                    _phantom_book_spread = signal.get("spread") or 0.0
                    _phantom_fill_price = price
                    _phantom_fill_qty = size

                _trace_emit(phantom_id, _ET.SIGNAL_FIRED, {
                    "source": signal.get("source"), "signal_id": signal_id,
                    "momentum_pct": signal.get("momentum_pct"),
                    "price": _phantom_fill_price, "size": _phantom_fill_qty, "dry_run": True,
                })
                await self._tracker.record_entry(
                    trade_id=phantom_id,
                    token_id=phantom_token,
                    slug=signal.get("slug", ""),
                    entry_price=_phantom_fill_price,
                    entry_size=_phantom_fill_qty,
                    entry_tx_hash=phantom_id,
                    outcome=signal.get("outcome", ""),
                    market_title=signal.get("market_title", ""),
                    entry_time=time.time(),
                    filter_score=signal_score,
                    metadata=phantom_pos.metadata,
                    fg_at_entry=signal.get("fear_greed"),
                    is_dry_run=True,
                    fill_model=_phantom_fill_model,
                    fill_model_reason=_phantom_fill_reason,
                    signal_id=signal_id if signal_id > 0 else None,
                    fill_latency_ms=0,
                    book_spread_at_entry=_phantom_book_spread,
                    entry_mode=_effective_entry_mode,
                )
                _trace_emit(phantom_id, _ET.ORDER_FILLED, {
                    "fill_price": _phantom_fill_price, "fill_size": _phantom_fill_qty,
                    "fill_model": _phantom_fill_model, "reason": _phantom_fill_reason,
                })

                # Update signal logger
                if self._signal_logger and signal_id > 0:
                    self._signal_logger.update_signal(signal_id, {
                        "outcome": "executed",
                        "entry_price": price,
                        "fill_mode": "dry_run",
                    })

                self._mark_signal_stage(
                    signal_id, "dry_run", "phantom_entry",
                    f"price={price:.4f}/size={size:.1f}/projected=${projected:.2f}",
                    outcome="dry_run",
                )
                self._logger.info(
                    f"[DRY RUN] Phantom entry: {signal.get('slug', 'unknown')} "
                    f"@ ${price:.4f} x {size:.1f} shares (tracked by exit manager)"
                )
                return

            # Inject ensemble score for Layer 1k sizing (None for non-BTC signals — fallback to neutral)
            if ensemble_verdict is not None:
                signal['ensemble_score'] = ensemble_verdict.score

            signal = self._prepare_entry_signal(signal)

            # 6. Execute buy (or start accumulation loop)
            if self._config.accum_mode_enabled and self._epoch_accum:
                slug = signal.get("slug", "")
                if not self._epoch_accum.is_accumulating(slug):
                    # Parse epoch end from slug
                    parts = slug.rsplit("-", 1)
                    epoch_end = 0
                    if len(parts) == 2 and parts[1].isdigit():
                        window = 900 if "15m" in slug else 300
                        epoch_end = int(parts[1]) + window
                    await self._epoch_accum.start_accumulation(
                        slug=slug,
                        direction=signal.get("outcome", "Up").lower(),
                        token_id=signal.get("token_id", ""),
                        asset=signal.get("asset", "BTC"),
                        epoch_end=epoch_end,
                        initial_price=signal.get("price", 0.5),
                    )
                    self._mark_signal_stage(signal_id, "execution", "accumulating", "accum_started")
                    return
                else:
                    self._logger.debug(f"Already accumulating {slug}, skipping")
                    return

            self._mark_signal_stage(signal_id, "execution", "attempted", self._config.entry_mode)
            _trace_emit(signal.get("token_id", ""), _ET.ORDER_PLACED, {
                "price": signal.get("price"), "size_usd_available": available,
                "entry_mode": self._config.entry_mode,
                "signal_id": signal_id,
            })
            exec_result = await self._executor.execute_buy(signal, available)

            if not exec_result.success:
                self._mark_signal_stage(
                    signal_id,
                    "execution",
                    "failed",
                    exec_result.error or exec_result.reason or "unknown_error",
                    outcome="execution_failed",
                )
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

            _trace_emit(exec_result.order_id, _ET.ORDER_FILLED, {
                "fill_price": exec_result.fill_price,
                "fill_size": exec_result.fill_size,
                "fill_time_ms": getattr(exec_result, 'fill_time_ms', None),
                "entry_mode": self._config.entry_mode,
            })
            # 7. Record to performance DB (include metadata for direction analysis).
            # v4 observability: every live fill gets ``fill_model="live"`` so the
            # attribution views can separate real fills from V1/V2 dry-run labels.
            # fill_latency_ms, entry_mode, signal_id, book_spread_at_entry populated
            # from context so one trade_id reconstructs the decision end-to-end.
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
                fg_at_entry=signal.get("fear_greed"),
                fill_model="live",
                signal_id=signal_id if signal_id > 0 else None,
                fill_latency_ms=getattr(exec_result, "fill_time_ms", None),
                book_spread_at_entry=signal.get("spread"),
                entry_mode=self._config.entry_mode,
            )

            # 7b. Adverse selection handled by position_executor._run_adverse_check (epoch-aware)

            # 7c. Slack notification (non-fatal)
            try:
                _bal = await self._balance.get_balance()
            except Exception:
                _bal = 0.0
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
                    entry_mode=self._config.entry_mode,
                    balance=_bal,
                )
            except Exception:
                pass

            # 8. Update signal logger with execution result
            if self._signal_logger and signal_id > 0:
                self._signal_logger.update_signal(signal_id, {
                    "outcome": "executed",
                    "entry_price": exec_result.fill_price,
                    "fill_mode": self._config.entry_mode,
                    "fill_time_ms": exec_result.fill_time_ms,
                })
                self._mark_signal_stage(
                    signal_id,
                    "execution",
                    "executed",
                    f"order_id={exec_result.order_id}",
                )
                # Store signal_id + trade context in position for exit tracking
                stored_pos = self._store.get(signal.get("token_id", ""))
                if stored_pos and stored_pos.metadata is not None:
                    stored_pos.metadata["signal_id"] = signal_id
                    stored_pos.metadata["asset"] = signal.get("asset", "")
                    stored_pos.metadata["direction"] = signal.get("outcome", "")

        except Exception as e:
            self._logger.exception(f"Error in _on_signal: {e}")
            self._health.record_error()

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

    async def _position_snapshot_loop(self):
        """Log midpoint snapshots for open positions every 30s.

        Enables mid-epoch exit research by tracking price trajectories.
        Data stored in signals.db position_snapshots table.
        """
        while True:
            await asyncio.sleep(30)
            try:
                for pos in list(self._store.get_open()):
                    if not pos.slug or not pos.entry_price:
                        continue
                    mid = None
                    spread = None
                    time_remaining = None
                    if self._market_ws:
                        mid = self._market_ws.get_midpoint(pos.token_id)
                        sp = self._market_ws.get_spread(pos.token_id)
                        if sp >= 0:
                            spread = sp
                    if pos.market_end_time:
                        time_remaining = int(pos.market_end_time.timestamp() - time.time())
                    secs_held = time.time() - pos.entry_time.timestamp() if pos.entry_time else None
                    meta = pos.metadata or {}
                    self._signal_logger.log_position_snapshot(
                        slug=pos.slug,
                        asset=meta.get("asset", ""),
                        direction=meta.get("direction", ""),
                        entry_price=pos.entry_price,
                        midpoint=mid,
                        spread=spread,
                        time_remaining_secs=time_remaining,
                        secs_held=secs_held,
                    )
            except Exception as e:
                self._logger.debug(f"Position snapshot error: {e}")

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
        """Run exit check loop forever.

        Event-driven: wakes on every WS price update rather than fixed timer.
        Falls back to EXIT_CHECK_INTERVAL polling if WS is unavailable.
        """
        try:
            while True:
                if self._market_ws:
                    await self._market_ws.wait_for_update(timeout=EXIT_CHECK_INTERVAL)
                else:
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

                    # Use WS midpoint (dict lookup, sub-ms) with REST fallback if stale.
                    # WS prices update every ~50-100ms; no REST round-trip needed here.
                    ws_prices = []
                    rest_needed = []
                    for pos in open_positions:
                        ws_price = self._market_ws.get_midpoint(pos.token_id) if self._market_ws else 0.0
                        if ws_price > 0:
                            ws_prices.append((pos, ws_price))
                        else:
                            rest_needed.append(pos)

                    # REST fallback only for tokens with stale/missing WS data
                    rest_results = []
                    if rest_needed:
                        rest_results = await asyncio.gather(
                            *[self._clob.get_midpoint(pos.token_id) for pos in rest_needed],
                            return_exceptions=True,
                        )

                    results = ws_prices + [
                        (pos, price) for pos, price in zip(rest_needed, rest_results)
                        if not isinstance(price, Exception)
                    ]

                    for pos, price in results:
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

            # 2. Dry run check — record real P&L from market price, no CLOB sell
            is_pair_arb = pos.metadata and isinstance(pos.metadata, dict) and pos.metadata.get("source") == "pair_arb"
            if self._dry_run and not is_pair_arb:
                dry_exit_price = exit_signal.exit_price or pos.current_price or pos.entry_price
                dry_pnl = (dry_exit_price - pos.entry_price) * pos.entry_size
                _trace_emit(pos.entry_tx_hash, _ET.EXIT_DECISION, {
                    "reason": exit_signal.reason,
                    "exit_price": dry_exit_price,
                    "pnl": dry_pnl, "dry_run": True,
                })
                self._logger.info(
                    f"[DRY RUN] Exit {pos.slug} "
                    f"@ ${dry_exit_price:.4f} pnl=${dry_pnl:+.4f} "
                    f"({exit_signal.reason})"
                )
                # Record to circuit breaker + outcome gate
                try:
                    self._circuit_breaker.record_trade_result(dry_pnl)
                except Exception:
                    pass
                try:
                    self._guard.record_outcome(dry_pnl > 0)
                    self._guard.record_markov_outcome(dry_pnl > 0)
                except Exception:
                    pass
                # Record exit to performance DB (same as live)
                try:
                    await self._tracker.record_exit(
                        trade_id=pos.entry_tx_hash,
                        exit_price=dry_exit_price,
                        exit_size=pos.entry_size,
                        exit_reason=exit_signal.reason,
                        exit_tx_hash=f"dry_exit_{int(time.time())}",
                        exit_time=time.time(),
                    )
                except Exception as db_err:
                    self._logger.warning(f"[DRY RUN] record_exit failed: {db_err}")
                    try:
                        self._tracker.db.force_close_trade(
                            slug=pos.slug,
                            exit_reason=exit_signal.reason,
                            exit_price=dry_exit_price,
                        )
                    except Exception:
                        pass
                # Update signal label (closes the feedback loop)
                try:
                    if self._signal_logger and pos.metadata:
                        ml_signal_id = pos.metadata.get("signal_id", -1)
                        if ml_signal_id and ml_signal_id > 0:
                            pnl_pct = (dry_exit_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                            self._signal_logger.update_signal(ml_signal_id, {
                                "exit_price": dry_exit_price,
                                "exit_reason": exit_signal.reason,
                                "pnl": dry_pnl,
                                "pnl_pct": pnl_pct,
                                "hold_secs": int(time.time() - pos.entry_time.timestamp()) if pos.entry_time else 0,
                                "is_win": 1 if dry_pnl > 0 else 0,
                            })
                except Exception:
                    pass
                # Clean up
                self._store.remove(exit_signal.token_id)
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
            _trace_emit(pos.entry_tx_hash, _ET.EXIT_FILLED, {
                "exit_price": exec_result.fill_price,
                "exit_size": exec_result.fill_size,
                "order_id": exec_result.order_id,
                "reason": exit_signal.reason,
            })

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

            # 5b. Record result to circuit breaker + outcome gate (non-fatal)
            try:
                exit_pnl = (exec_result.fill_price - pos.entry_price) * exec_result.fill_size
                self._circuit_breaker.record_trade_result(exit_pnl)
            except Exception as cb_err:
                self._logger.error(
                    f"Circuit breaker update failed for {pos.slug} (non-fatal): {cb_err}"
                )
            try:
                self._guard.record_outcome(exit_pnl > 0)
                self._guard.record_markov_outcome(exit_pnl > 0)
            except Exception:
                pass

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
            from .models import RedemptionEvent
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
                    "pipeline_stage": "reversal_short",
                    "pipeline_status": "dry_run",
                    "pipeline_detail": exit_signal.reason,
                })
            return

        self._logger.info(
            f"[REVERSAL_SHORT] {slug} FLIPPING to {opp_direction} "
            f"@ {opp_mid:.4f} | {secs_left:.0f}s left | "
            f"trigger={exit_signal.reason}"
        )
        await self._on_signal(rs_signal)

    async def _igoc_signal_loop(self):
        """IGOC (Imbalance-Gated Oracle Confirm) signal loop.

        Monitors for buy/sell imbalance opportunities gated by oracle direction confirmation.
        Only logs signals (shadow mode) when igoc_enabled=True.
        """
        if not self._config.igoc_enabled:
            return

        try:
            while True:
                # Event-driven: wake on every WS price update (50ms timeout fallback)
                if self._market_ws:
                    await self._market_ws.wait_for_update(timeout=0.05)
                else:
                    await asyncio.sleep(0.05)

                try:
                    now = datetime.now(timezone.utc)

                    # Skip if trading is halted
                    if self._trading_halted:
                        continue

                    # Get current active markets from market_ws subscriptions
                    if not self._market_ws or not self._market_ws._subscribed:
                        continue

                    for token_id in list(self._market_ws._subscribed):
                        # Get book depth (imbalance + freshness)
                        book_depth = self._market_ws.get_book_depth(token_id)
                        if not book_depth:
                            continue  # Stale or no data

                        imbalance = book_depth.get("imbalance", 0)
                        bid_qty = book_depth.get("bid_qty", 0)
                        ask_qty = book_depth.get("ask_qty", 0)

                        # Skip if imbalance doesn't meet threshold
                        if imbalance < self._config.igoc_imbalance_threshold and imbalance > (1 - self._config.igoc_imbalance_threshold):
                            continue  # Balanced, not imbalanced

                        # Resolve slug via momentum feed's market cache.
                        # py_clob_client get_market() takes condition_id, not token_id —
                        # calling with token_id returns {'error': 'not found'} with no slug key.
                        # Reverse-lookup token_id -> slug from _market_cache instead.
                        slug = ""
                        if self._momentum_feed:
                            for _slug, _info in self._momentum_feed._market_cache.items():
                                if _info.get("up_token_id") == token_id or _info.get("down_token_id") == token_id:
                                    slug = _slug
                                    break
                        if not slug:
                            continue

                        # Parse epoch and window
                        parts = slug.rsplit("-", 1)
                        if len(parts) != 2 or not parts[1].isdigit():
                            continue

                        epoch = int(parts[1])
                        window = 300 if "5m" in slug else 900
                        market_end = epoch + window
                        secs_left = market_end - time.time()

                        # Check secs_remaining range
                        if not (self._config.igoc_min_secs_remaining <= secs_left <= self._config.igoc_max_secs_remaining):
                            continue

                        # Get midpoint and check price range
                        midpoint = self._market_ws.get_midpoint(token_id)
                        if midpoint <= 0:
                            continue

                        if not (self._config.igoc_min_price <= midpoint <= self._config.igoc_max_price):
                            continue

                        # Determine direction from imbalance
                        # High imbalance = more buy pressure (buy more YES tokens)
                        # Low imbalance = more sell pressure (buy more NO tokens)
                        direction = "up" if imbalance >= 0.5 else "down"

                        # Check oracle direction confirmation
                        # Extract asset from slug: "btc-updown-5m-..." -> "BTC"
                        asset = slug.split('-')[0].upper() if slug else ""
                        if not asset or not self._chainlink:
                            continue

                        confirmed_direction = self._chainlink.get_direction_confirmed(
                            asset,
                            n=self._config.igoc_oracle_confirm_n
                        )

                        # Build IGOC signal
                        igoc_signal = {
                            "slug": slug,
                            "asset": asset,
                            "direction": direction,
                            "token_id": token_id,
                            "price": midpoint,
                            "source": "clob_imbalance",
                            "market_window_secs": window,
                            "time_remaining_secs": int(secs_left),
                            "imbalance": round(imbalance, 4),
                            "bid_qty": round(bid_qty, 2),
                            "ask_qty": round(ask_qty, 2),
                            "oracle_confirmed": confirmed_direction == direction,
                            "oracle_n": self._config.igoc_oracle_confirm_n,
                            "metadata": {
                                "source": "clob_imbalance",
                                "imbalance_threshold": self._config.igoc_imbalance_threshold,
                            },
                        }

                        # Pass through signal guard
                        guard_result = self._guard.check(igoc_signal)

                        # Log signal to signals.db even if oracle or guard filter it
                        if confirmed_direction != direction:
                            # Oracle disagrees with imbalance direction
                            igoc_signal["outcome"] = "filtered"
                            signal_id, _ = self._log_signal_observation(
                                igoc_signal,
                                guard_result,
                                market_context={},
                                regime=None,
                            )
                            self._logger.debug(
                                f"[IGOC FILTERED] {slug} {direction} @ {midpoint:.4f} | "
                                f"imbalance={imbalance:.4f} | "
                                f"oracle={confirmed_direction} (disagrees) | "
                                f"{secs_left:.0f}s left | signal_id={signal_id}"
                            )
                            continue

                        if not guard_result.passed:
                            # Guard filter rejected the signal
                            igoc_signal["outcome"] = "filtered"
                            signal_id, _ = self._log_signal_observation(
                                igoc_signal,
                                guard_result,
                                market_context={},
                                regime=None,
                            )
                            self._logger.debug(
                                f"[IGOC FILTERED] {slug} {direction} @ {midpoint:.4f} | "
                                f"guard_reasons={guard_result.reasons} | "
                                f"signal_id={signal_id}"
                            )
                            continue

                        # Log signal in shadow mode (no execution)
                        if self._config.igoc_shadow_only or self._dry_run:
                            signal_id, _ = self._log_signal_observation(
                                igoc_signal,
                                guard_result,
                                market_context={},
                                regime=None,
                            )
                            self._logger.info(
                                f"[IGOC SHADOW] {slug} {direction} @ {midpoint:.4f} | "
                                f"imbalance={imbalance:.4f} | "
                                f"oracle={confirmed_direction} | "
                                f"{secs_left:.0f}s left | signal_id={signal_id}"
                            )
                        else:
                            # IGOC live mode (currently disabled by default)
                            self._logger.info(
                                f"[IGOC LIVE] {slug} {direction} @ {midpoint:.4f} | "
                                f"imbalance={imbalance:.4f} | "
                                f"oracle={confirmed_direction} | "
                                f"{secs_left:.0f}s left"
                            )
                            await self._on_signal(igoc_signal)

                except Exception as e:
                    self._logger.debug(f"IGOC loop error: {e}")

        except asyncio.CancelledError:
            self._logger.info("IGOC signal loop cancelled")
            raise

    async def _flat_regime_rtds_loop(self) -> None:
        """Flat regime RTDS continuation signal.

        Fires when:
        - Market is in flat regime (vol < flat_regime_max_vol)
        - RTDS price clearly above/below strike (min 0.5% deviation)
        - Token midpoint is 0.30-0.80 (hasn't yet priced in the direction)
        - CLOB book imbalance confirms direction
        - T=45-180s remaining

        Shadow-only until validated. Promotes alongside IGOC.
        Break-even WR at entry 0.65: 21.8%. RTDS confirmation expected >> 21.8%.
        """
        cfg = self._config
        if not cfg.flat_regime_rtds_enabled:
            self._logger.info("flat_regime_rtds: disabled, skipping loop")
            return

        self._logger.info(
            f"flat_regime_rtds: loop started | shadow={cfg.flat_regime_rtds_shadow} | "
            f"price={cfg.flat_regime_rtds_min_price}-{cfg.flat_regime_rtds_max_price} | "
            f"secs={cfg.flat_regime_rtds_min_secs}-{cfg.flat_regime_rtds_max_secs} | "
            f"min_gap={cfg.flat_regime_rtds_min_gap}"
        )

        _rtds_fired: set = set()  # (slug, epoch_ts) already signaled — one per epoch per slug
        _rtds_blackout: set = set()
        if cfg.flat_regime_rtds_blackout_hours:
            _rtds_blackout = {int(h.strip()) for h in cfg.flat_regime_rtds_blackout_hours.split(',') if h.strip()}

        while True:
            try:
                await self._market_ws.wait_for_update(timeout=0.1)

                # Only fire in flat regime
                regime = self._regime_detector.get_regime() if self._regime_detector else None
                vol_1h = regime.volatility_1h if regime else None
                if vol_1h is not None and vol_1h >= cfg.flat_regime_max_vol:
                    continue

                # RTDS-specific blackout hours (independent of global BLACKOUT_HOURS)
                if _rtds_blackout and time.gmtime().tm_hour in _rtds_blackout:
                    continue

                # Scan all active markets
                for token_id in list(self._market_ws._subscribed):
                    try:
                        # Reverse lookup slug from market cache
                        slug = ""
                        if self._momentum_feed:
                            for _slug, _info in self._momentum_feed._market_cache.items():
                                if (_info.get("up_token_id") == token_id or
                                        _info.get("down_token_id") == token_id):
                                    slug = _slug
                                    break
                        if not slug:
                            continue

                        # Parse asset and epoch timestamp
                        asset = slug.split('-')[0].upper()
                        if asset not in ["BTC", "ETH", "SOL", "XRP"]:
                            continue
                        # Respect ASSET_FILTER / SHADOW_ASSETS
                        _shadow_assets = cfg.get_shadow_assets()
                        _is_shadow_asset = asset in _shadow_assets
                        if asset not in cfg.asset_filter and not _is_shadow_asset:
                            continue
                        slug_parts = slug.rsplit('-', 1)
                        if len(slug_parts) != 2 or not slug_parts[1].isdigit():
                            continue
                        epoch_ts = int(slug_parts[1])
                        window_secs_rtds = 300 if "5m" in slug else 900

                        # Get RTDS direction from chainlink epoch delta (current vs epoch open)
                        # epoch_ts is the epoch START (confirmed by market_end = epoch_ts + window)
                        delta_pct = None
                        if self._chainlink:
                            delta_pct = self._chainlink.get_epoch_delta_pct(
                                asset, epoch_ts, window_secs_rtds
                            )
                        if delta_pct is None:
                            continue
                        if abs(delta_pct) < cfg.flat_regime_rtds_rtds_min_pct:
                            continue  # RTDS too close to open, direction uncertain

                        direction = "up" if delta_pct > 0 else "down"

                        # Check token midpoint is in actionable range
                        midpoint = self._market_ws.get_midpoint(token_id)
                        if not midpoint:
                            continue

                        # For UP signal: token should be cheap (below 0.80) — hasn't priced in yet
                        # For DOWN signal: token should be expensive (above 0.20) — hasn't priced in yet
                        is_up_token = self._momentum_feed._market_cache.get(slug, {}).get("up_token_id") == token_id
                        if direction == "up" and not is_up_token:
                            continue
                        if direction == "down" and is_up_token:
                            continue

                        if not (cfg.flat_regime_rtds_min_price <= midpoint <= cfg.flat_regime_rtds_max_price):
                            continue

                        # Gap: how far is token from fair value (1.0 if RTDS confirms direction)
                        gap = (1.0 - midpoint) if direction == "up" else midpoint
                        if gap < cfg.flat_regime_rtds_min_gap:
                            continue

                        # Check time remaining
                        parts = slug.rsplit('-', 1)
                        secs_left = None
                        if len(parts) == 2 and parts[1].isdigit():
                            market_epoch = int(parts[1])
                            window = 300 if "5m" in slug else 900
                            market_end = market_epoch + window
                            secs_left = market_end - time.time()
                        if secs_left is None:
                            continue
                        if not (cfg.flat_regime_rtds_min_secs <= secs_left <= cfg.flat_regime_rtds_max_secs):
                            continue

                        # Check CLOB book imbalance confirmation
                        _book = self._market_ws.get_book_depth(token_id)
                        imbalance = _book.get("imbalance") if _book else 0.5
                        if direction == "up" and imbalance < cfg.flat_regime_rtds_imbalance_threshold:
                            continue
                        if direction == "down" and imbalance > (1.0 - cfg.flat_regime_rtds_imbalance_threshold):
                            continue

                        # Collect cross-feed data for signal logging
                        _cb_premium = None
                        _liq_vol = 0.0
                        _liq_bias_val = ""
                        _liq_conv = 0.0
                        if self._momentum_feed:
                            try:
                                _cb_premium = self._momentum_feed.get_coinbase_premium(asset)
                            except Exception:
                                pass
                        if self._regime_detector:
                            try:
                                _rs = self._regime_detector.get_state(asset)
                                _liq_vol = _rs.liq_volume_60s
                                _liq_bias_val = _rs.liq_bias
                                _liq_conv = self._regime_detector.get_liquidation_conviction(
                                    asset, direction.capitalize()
                                )
                            except Exception:
                                pass

                        # Build signal dict matching the standard signal format
                        signal = {
                            "source": "flat_regime_rtds",
                            "shadow": cfg.flat_regime_rtds_shadow or _is_shadow_asset,
                            "slug": slug,
                            "token_id": token_id,
                            "asset": asset,
                            "outcome": direction,
                            "price": midpoint,
                            "direction": direction.upper(),
                            "momentum_pct": delta_pct,
                            "time_remaining_secs": secs_left,
                            "volatility_1h": vol_1h,
                            "rtds_price": self._chainlink.get_current_price(asset) if self._chainlink else None,
                            "strike": self._chainlink.get_window_open_price(epoch_ts, window_secs_rtds, asset) if self._chainlink else None,
                            "rtds_gap_pct": round(delta_pct * 100, 2),
                            "token_gap_pp": round(gap * 100, 2),
                            "book_imbalance": round(imbalance, 3),
                            "coinbase_premium_bps": _cb_premium,
                            "liq_volume_60s": _liq_vol,
                            "liq_bias": _liq_bias_val,
                            "liq_conviction": _liq_conv,
                        }

                        # Throttle: one signal per epoch per slug (prevents 10x/sec spam)
                        _fire_key = (slug, epoch_ts)
                        if _fire_key in _rtds_fired:
                            continue
                        _rtds_fired.add(_fire_key)

                        mode = "SHADOW" if cfg.flat_regime_rtds_shadow else "LIVE"
                        self._logger.info(
                            f"[FLAT_REGIME_RTDS {mode}] {slug} {direction.upper()} @ {midpoint:.3f} | "
                            f"epoch_delta={delta_pct:+.2%} | "
                            f"gap={gap:.2%} secs={secs_left:.0f} imbalance={imbalance:.3f} vol={vol_1h or 'N/A'}"
                        )

                        await self._on_signal(signal)

                        # --- Phase Gate hedge leg ---
                        # After main leg fires, check if opposite side token is cheap enough
                        # to buy a small hedge (temporal pair-cost arbitrage)
                        if cfg.phase_gate_hedge_enabled and not cfg.flat_regime_rtds_shadow:
                            try:
                                market_info = self._momentum_feed._market_cache.get(slug) if self._momentum_feed else None
                                if market_info:
                                    opp_key = "down_token_id" if direction.lower() == "up" else "up_token_id"
                                    opp_direction = "Down" if direction.lower() == "up" else "Up"
                                    opp_token_id = market_info.get(opp_key)
                                    if opp_token_id:
                                        opp_mid = 0.0
                                        if self._market_ws:
                                            opp_mid = self._market_ws.get_midpoint(opp_token_id)
                                        if opp_mid <= 0:
                                            try:
                                                opp_mid = await self._clob.get_midpoint(opp_token_id)
                                            except Exception:
                                                pass
                                        if 0 < opp_mid <= cfg.phase_gate_hedge_max_price:
                                            hedge_signal = {
                                                "source": "phase_gate_hedge",
                                                "shadow": False,
                                                "slug": slug,
                                                "token_id": opp_token_id,
                                                "asset": asset,
                                                "outcome": opp_direction,
                                                "price": opp_mid,
                                                "direction": opp_direction.upper(),
                                                "time_remaining_secs": int(secs_left),
                                                "condition_id": market_info.get("condition_id", ""),
                                                "metadata": {
                                                    "source": "phase_gate_hedge",
                                                    "main_direction": direction.upper(),
                                                    "main_entry_price": midpoint,
                                                    "pair_cost": round(midpoint + opp_mid, 4),
                                                },
                                            }
                                            self._logger.info(
                                                f"[PHASE_GATE_HEDGE] {slug} {opp_direction} @ {opp_mid:.4f} | "
                                                f"pair_cost={midpoint + opp_mid:.4f} secs={secs_left:.0f}"
                                            )
                                            await self._on_signal(hedge_signal)
                                        else:
                                            self._logger.debug(
                                                f"Phase gate hedge skip: opp_mid={opp_mid:.4f} "
                                                f"> max {cfg.phase_gate_hedge_max_price}"
                                            )
                            except Exception as he:
                                self._logger.debug(f"Phase gate hedge error: {he}")

                    except Exception as e:
                        self._logger.debug(f"flat_regime_rtds scan error for {token_id}: {e}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.warning(f"flat_regime_rtds loop error: {e}")
                await asyncio.sleep(1.0)


async def main():
    """Main entry point for the bot."""
    config = Settings()
    bot = SignalBot(config=config)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
