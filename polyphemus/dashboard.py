"""
Polyphemus Dashboard — Embedded web server for real-time bot monitoring.

Runs an aiohttp web server inside the bot process, providing:
- JSON API endpoints for accumulator state, balance, and momentum
- Single-page HTML dashboard with auto-refresh
- Zero extra dependencies (uses existing aiohttp)
- Direct access to live state (no intermediary files)

Access: http://<vps-ip>:8080 or via SSH tunnel: ssh -L 8080:localhost:8080 root@<vps>
"""

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from typing import TYPE_CHECKING

from aiohttp import web

from .config import setup_logger
from .types import ASSET_TO_BINANCE

if TYPE_CHECKING:
    from .config import Settings
    from .position_store import PositionStore
    from .balance_manager import BalanceManager
    from .health_monitor import HealthMonitor
    from .signal_guard import SignalGuard
    from .binance_feed import BinanceFeed
    from .performance_db import PerformanceDB
    from .arb_engine import ArbEngine
    from .accumulator import AccumulatorEngine


class Dashboard:
    """Embedded aiohttp web server for accumulator bot monitoring."""

    def __init__(
        self,
        config: "Settings",
        store: "PositionStore",
        balance: "BalanceManager",
        health: "HealthMonitor",
        guard: "SignalGuard",
        perf_db: "PerformanceDB",
        binance_feed: "BinanceFeed | None" = None,
        momentum_feed=None,
        momentum_stats: dict | None = None,
        dry_run: bool = False,
        arb_engine: "ArbEngine | None" = None,
        accumulator_engine: "AccumulatorEngine | None" = None,
        signal_logger=None,
        signal_scorer=None,
        fill_optimizer=None,
        regime_detector=None,
        gabagool_tracker=None,
        adaptive_tuner=None,
        executor=None,
    ):
        self._config = config
        self._store = store
        self._balance = balance
        self._health = health
        self._guard = guard
        self._perf_db = perf_db
        self._binance_feed = binance_feed
        self._momentum_feed = momentum_feed
        self._momentum_stats = momentum_stats or {}
        self._dry_run = dry_run
        self._arb_engine = arb_engine
        self._accumulator_engine = accumulator_engine
        self._signal_logger = signal_logger
        self._signal_scorer = signal_scorer
        self._fill_optimizer = fill_optimizer
        self._regime_detector = regime_detector
        self._gabagool_tracker = gabagool_tracker
        self._adaptive_tuner = adaptive_tuner
        self._executor = executor
        self._logger = setup_logger("polyphemus.dashboard")

    async def start(self) -> None:
        """Start the dashboard web server."""
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/api/balance", self._handle_balance)
        app.router.add_get("/api/momentum", self._handle_momentum)
        app.router.add_get("/api/accumulator", self._handle_accumulator)
        app.router.add_get("/api/gabagool", self._handle_gabagool)
        app.router.add_get("/api/accumulator/tuning", self._handle_tuning)
        app.router.add_get("/api/signals", self._handle_signals)
        app.router.add_get("/api/evidence", self._handle_evidence)
        app.router.add_get("/api/trades", self._handle_trades)
        app.router.add_get("/api/filters", self._handle_filters)
        app.router.add_get("/api/errors", self._handle_errors)
        app.router.add_get("/api/pipeline", self._handle_pipeline)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(
            runner,
            self._config.dashboard_host,
            self._config.dashboard_port,
        )
        await site.start()
        self._logger.info(
            f"Dashboard running on "
            f"http://{self._config.dashboard_host}:{self._config.dashboard_port}"
        )

        # Keep running forever
        while True:
            await asyncio.sleep(3600)

    # ── API Handlers ─────────────────────────────────────────────

    async def _handle_status(self, request: web.Request) -> web.Response:
        uptime_h = self._health.get_uptime_hours()
        data = {
            "status": "running",
            "uptime_hours": round(uptime_h, 2),
            "errors": self._health._error_count,
            "binance_connected": (
                (self._binance_feed is not None and not self._binance_feed.circuit_open)
                or (self._momentum_feed is not None and self._momentum_feed._consecutive_failures == 0)
            ),
            "timestamp": time.time(),
        }
        return web.json_response(data)

    async def _handle_balance(self, request: web.Request) -> web.Response:
        balance = self._balance._cached_balance
        # For accumulator-only bot, deployed is just the deployed capital (not signal positions)
        deployed = 0.0
        if self._accumulator_engine and hasattr(self._accumulator_engine, 'stats'):
            accum_stats = self._accumulator_engine.stats
            for cp in accum_stats.get("positions", []):
                pair_cost = cp.get("pair_cost", 0.0)
                qty = max(cp.get("up_qty", 0.0), cp.get("down_qty", 0.0))
                deployed += pair_cost * qty
        available = max(0.0, balance - deployed)
        data = {
            "balance": round(balance, 2),
            "deployed": round(deployed, 2),
            "available": round(available, 2),
        }
        return web.json_response(data)

    async def _handle_momentum(self, request: web.Request) -> web.Response:
        readings = {}
        if self._binance_feed:
            for asset, symbol in ASSET_TO_BINANCE.items():
                m = self._binance_feed.get_momentum(asset)
                readings[asset] = {
                    "direction": m.direction,
                    "momentum_pct": round(m.momentum_pct * 100, 4),
                    "confidence": round(m.confidence, 3),
                    "age_secs": round(m.age_secs, 1),
                }
        elif self._momentum_feed:
            now = time.time()
            window = self._config.momentum_window_secs
            threshold = self._config.momentum_trigger_pct
            for asset, symbol in ASSET_TO_BINANCE.items():
                buf = self._momentum_feed._price_buffers.get(symbol)
                if buf and len(buf) > 1:
                    cutoff = now - window
                    oldest_price = None
                    for ts, price in buf:
                        if ts >= cutoff:
                            oldest_price = price
                            break
                    current_price = buf[-1][1]
                    if oldest_price and oldest_price > 0:
                        pct = (current_price - oldest_price) / oldest_price
                        direction = "UP" if pct > threshold else ("DOWN" if pct < -threshold else "NEUTRAL")
                    else:
                        pct, direction = 0.0, "UNKNOWN"
                    readings[asset] = {
                        "direction": direction,
                        "momentum_pct": round(pct * 100, 4),
                        "confidence": round(min(abs(pct) / threshold, 1.0), 3),
                        "age_secs": round(now - buf[-1][0], 1),
                    }
                else:
                    readings[asset] = {
                        "direction": "UNKNOWN",
                        "momentum_pct": 0,
                        "confidence": 0,
                        "age_secs": 0,
                    }
        enabled = self._binance_feed is not None or self._momentum_feed is not None
        circuit_open = False
        if self._binance_feed:
            circuit_open = self._binance_feed.circuit_open
        elif self._momentum_feed:
            circuit_open = self._momentum_feed._consecutive_failures >= 3
        data = {
            "readings": readings,
            "enabled": enabled,
            "circuit_open": circuit_open,
        }
        return web.json_response(data)

    async def _handle_accumulator(self, request: web.Request) -> web.Response:
        if not self._accumulator_engine:
            return web.json_response({"enabled": False})
        return web.json_response({"enabled": True, **self._accumulator_engine.stats})

    async def _handle_gabagool(self, request: web.Request) -> web.Response:
        if not self._gabagool_tracker:
            return web.json_response({"enabled": False})
        return web.json_response({"enabled": True, **self._gabagool_tracker.stats})

    async def _handle_tuning(self, request: web.Request) -> web.Response:
        if not self._adaptive_tuner:
            return web.json_response({"enabled": False})
        return web.json_response({"enabled": True, **self._adaptive_tuner.get_state()})

    async def _handle_signals(self, request: web.Request) -> web.Response:
        """Return recent signal decisions from signals.db."""
        if not self._signal_logger:
            return web.json_response({"enabled": False, "signals": []})
        return web.json_response({
            "enabled": True,
            "signals": self._get_recent_signals(limit=20),
        })

    async def _handle_evidence(self, request: web.Request) -> web.Response:
        """Return BTC 5m evidence verdict summary from recent signal history."""
        if not self._signal_logger:
            return web.json_response({"enabled": False})
        return web.json_response({
            "enabled": bool(getattr(self._config, "enable_btc5m_evidence_verdicts", False)),
            **self._get_evidence_summary(hours=24),
        })

    async def _handle_trades(self, request: web.Request) -> web.Response:
        """Return recent trades from performance.db."""
        trades = []
        if self._perf_db:
            trades = self._get_recent_trades(limit=20)
        return web.json_response({
            "enabled": self._perf_db is not None,
            "trades": trades,
        })

    async def _handle_filters(self, request: web.Request) -> web.Response:
        """Return recent BTC 5m filter/rejection breakdowns."""
        if not self._signal_logger:
            return web.json_response({"enabled": False})
        return web.json_response({
            "enabled": True,
            **self._get_filter_summary(hours=24),
        })

    async def _handle_errors(self, request: web.Request) -> web.Response:
        """Return a small recent error stream for the active lagbot unit."""
        errors = self._get_recent_error_lines(minutes=30, limit=20)
        return web.json_response({
            "enabled": True,
            "unit": self._detect_systemd_unit() or "unknown",
            "errors": errors,
        })

    async def _handle_pipeline(self, request: web.Request) -> web.Response:
        """Return BTC 5m pipeline watchdog summary."""
        return web.json_response({
            "enabled": bool(self._signal_logger and self._perf_db),
            **self._get_pipeline_summary(),
        })

    def _get_recent_signals(self, limit: int = 20) -> list[dict]:
        """Fetch recent signals with outcome and evidence fields."""
        db_path = getattr(self._signal_logger, "_db_path", None)
        if not db_path:
            return []

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()
            }

            def expr(name: str, default_sql: str = "NULL") -> str:
                return name if name in columns else f"{default_sql} AS {name}"

            rows = conn.execute(
                f"""
                SELECT
                    timestamp,
                    epoch,
                    slug,
                    asset,
                    direction,
                    {expr("source", "''")},
                    {expr("midpoint")},
                    {expr("entry_price")},
                    {expr("time_remaining_secs")},
                    {expr("outcome", "''")},
                    {expr("guard_reasons", "''")},
                    {expr("evidence_verdict", "''")},
                    {expr("evidence_r8_label", "''")},
                    {expr("evidence_reason", "''")},
                    {expr("evidence_match_level", "''")}
                FROM signals
                ORDER BY epoch DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _get_evidence_summary(self, hours: int = 24) -> dict:
        """Aggregate recent BTC 5m evidence verdict counts and reason prefixes."""
        db_path = getattr(self._signal_logger, "_db_path", None)
        if not db_path:
            return {
                "window_hours": hours,
                "signals_scanned": 0,
                "verdict_counts": {},
                "reason_counts": [],
            }

        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()
            }
            if "evidence_verdict" not in columns:
                return {
                    "window_hours": hours,
                    "signals_scanned": 0,
                    "verdict_counts": {},
                    "reason_counts": [],
                }

            count_row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300 AND epoch >= ?
                """,
                (cutoff,),
            ).fetchone()
            verdict_rows = conn.execute(
                """
                SELECT COALESCE(evidence_verdict, '') AS verdict, COUNT(*) AS count
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                  AND epoch >= ? AND COALESCE(evidence_verdict, '') != ''
                GROUP BY COALESCE(evidence_verdict, '')
                ORDER BY count DESC
                """,
                (cutoff,),
            ).fetchall()
            reason_rows = conn.execute(
                """
                SELECT evidence_reason
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                  AND epoch >= ? AND COALESCE(evidence_reason, '') != ''
                ORDER BY epoch DESC
                LIMIT 50
                """,
                (cutoff,),
            ).fetchall()

            verdict_counts = {row["verdict"]: row["count"] for row in verdict_rows}
            reason_counts: dict[str, int] = {}
            for row in reason_rows:
                prefix = row["evidence_reason"].split("|", 1)[0].strip()
                if not prefix:
                    continue
                reason_counts[prefix] = reason_counts.get(prefix, 0) + 1

            recent_rows = conn.execute(
                """
                SELECT epoch, slug, evidence_verdict, evidence_r8_label, evidence_reason
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                  AND epoch >= ? AND COALESCE(evidence_verdict, '') != ''
                ORDER BY epoch DESC
                LIMIT 8
                """,
                (cutoff,),
            ).fetchall()
            recent = [dict(row) for row in recent_rows]
            return {
                "window_hours": hours,
                "signals_scanned": int(count_row["total"] or 0),
                "verdict_counts": verdict_counts,
                "reason_counts": [
                    {"reason": key, "count": value}
                    for key, value in sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:6]
                ],
                "recent": recent,
            }
        finally:
            conn.close()

    def _get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Fetch recent trades with normalized source and PnL fields."""
        trades = []
        for trade in self._perf_db.get_recent_trades(limit=limit):
            metadata = {}
            raw_metadata = trade.get("metadata")
            if raw_metadata:
                try:
                    metadata = json.loads(raw_metadata)
                except Exception:
                    metadata = {}
            pnl = trade.get("pnl")
            if pnl is None:
                pnl = trade.get("profit_loss")
            trades.append({
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "slug": trade.get("slug"),
                "outcome": trade.get("outcome"),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("exit_price"),
                "exit_reason": trade.get("exit_reason"),
                "source": metadata.get("source", ""),
                "pnl": pnl,
                "open": trade.get("exit_time") is None,
            })
        return trades

    def _get_filter_summary(self, hours: int = 24) -> dict:
        """Aggregate recent BTC 5m filtered/rejected signals."""
        db_path = getattr(self._signal_logger, "_db_path", None)
        if not db_path:
            return {
                "window_hours": hours,
                "filtered_signals": 0,
                "reason_counts": [],
                "price_buckets": [],
                "time_buckets": [],
                "recent": [],
            }

        cutoff = time.time() - (hours * 3600)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT epoch, slug, asset, source, outcome, guard_reasons,
                       entry_price, midpoint, time_remaining_secs
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300 AND epoch >= ?
                ORDER BY epoch DESC
                """,
                (cutoff,),
            ).fetchall()
            reason_counts: dict[str, int] = {}
            price_buckets: dict[str, int] = {}
            time_buckets: dict[str, int] = {}
            recent = []
            filtered_count = 0

            for row in rows:
                outcome = (row["outcome"] or "").lower()
                reasons = [part.strip() for part in (row["guard_reasons"] or "").split(",") if part.strip()]
                is_filtered = (
                    "filtered" in outcome
                    or outcome in {"shadow", "missed"}
                    or bool(reasons)
                )
                if not is_filtered:
                    continue

                filtered_count += 1
                price = row["entry_price"] if row["entry_price"] is not None else row["midpoint"]
                p_bucket = self._price_bucket(price)
                t_bucket = self._time_bucket(row["time_remaining_secs"])
                price_buckets[p_bucket] = price_buckets.get(p_bucket, 0) + 1
                time_buckets[t_bucket] = time_buckets.get(t_bucket, 0) + 1

                if not reasons:
                    reason_counts["unspecified_filter"] = reason_counts.get("unspecified_filter", 0) + 1
                else:
                    for reason in reasons:
                        reason_counts[reason] = reason_counts.get(reason, 0) + 1

                if len(recent) < 12:
                    recent.append({
                        "epoch": row["epoch"],
                        "slug": row["slug"],
                        "source": row["source"] or "",
                        "outcome": row["outcome"] or "",
                        "guard_reasons": row["guard_reasons"] or "",
                        "price_bucket": p_bucket,
                        "time_bucket": t_bucket,
                    })

            return {
                "window_hours": hours,
                "filtered_signals": filtered_count,
                "reason_counts": [
                    {"reason": key, "count": value}
                    for key, value in sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:8]
                ],
                "price_buckets": [
                    {"bucket": key, "count": value}
                    for key, value in sorted(price_buckets.items(), key=lambda item: item[0])
                ],
                "time_buckets": [
                    {"bucket": key, "count": value}
                    for key, value in sorted(time_buckets.items(), key=lambda item: item[0])
                ],
                "recent": recent,
            }
        finally:
            conn.close()

    def _get_pipeline_summary(self) -> dict:
        """Summarize where the BTC 5m pipeline is currently stopping."""
        db_path = getattr(self._signal_logger, "_db_path", None)
        perf_db_path = getattr(self._perf_db, "db_path", None)
        if not db_path or not perf_db_path:
            return {
                "stage": "unknown",
                "headline": "Pipeline data unavailable",
                "summary": "signals.db or performance.db is not wired",
            }

        now = time.time()
        sig_conn = sqlite3.connect(str(db_path))
        sig_conn.row_factory = sqlite3.Row
        trade_conn = sqlite3.connect(str(perf_db_path))
        trade_conn.row_factory = sqlite3.Row
        try:
            def query_scalar(conn, sql: str, params=()):
                row = conn.execute(sql, params).fetchone()
                if row is None:
                    return None
                return row[0]

            signal_columns = {
                row["name"]
                for row in sig_conn.execute("PRAGMA table_info(signals)").fetchall()
            }

            windows = {
                "15m": now - 900,
                "1h": now - 3600,
                "6h": now - 21600,
            }

            counts = {}
            for label, cutoff in windows.items():
                counts[f"decisions_{label}"] = int(query_scalar(
                    sig_conn,
                    """
                    SELECT COUNT(*)
                    FROM signals
                    WHERE asset = 'BTC' AND market_window_secs = 300
                      AND epoch >= ?
                    """,
                    (cutoff,),
                ) or 0)
                counts[f"momentum_{label}"] = int(query_scalar(
                    sig_conn,
                    """
                    SELECT COUNT(*)
                    FROM signals
                    WHERE asset = 'BTC' AND market_window_secs = 300
                      AND source = 'binance_momentum'
                      AND epoch >= ?
                    """,
                    (cutoff,),
                ) or 0)
                counts[f"passed_{label}"] = int(query_scalar(
                    sig_conn,
                    """
                    SELECT COUNT(*)
                    FROM signals
                    WHERE asset = 'BTC' AND market_window_secs = 300
                      AND source = 'binance_momentum'
                      AND epoch >= ?
                      AND guard_passed = 1
                    """,
                    (cutoff,),
                ) or 0)
                counts[f"filtered_{label}"] = int(query_scalar(
                    sig_conn,
                    """
                    SELECT COUNT(*)
                    FROM signals
                    WHERE asset = 'BTC' AND market_window_secs = 300
                      AND source = 'binance_momentum'
                      AND epoch >= ?
                      AND COALESCE(outcome, '') = 'filtered'
                    """,
                    (cutoff,),
                ) or 0)
                counts[f"trades_{label}"] = int(query_scalar(
                    trade_conn,
                    """
                    SELECT COUNT(*)
                    FROM trades
                    WHERE slug LIKE 'btc-updown-5m-%'
                      AND entry_time >= ?
                    """,
                    (cutoff,),
                ) or 0)

            last_btc_decision = query_scalar(
                sig_conn,
                """
                SELECT MAX(epoch)
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                """,
            )
            last_btc_momentum = query_scalar(
                sig_conn,
                """
                SELECT MAX(epoch)
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                  AND source = 'binance_momentum'
                """,
            )
            last_btc_pass = query_scalar(
                sig_conn,
                """
                SELECT MAX(epoch)
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                  AND source = 'binance_momentum'
                  AND guard_passed = 1
                """,
            )
            last_trade = query_scalar(
                trade_conn,
                """
                SELECT MAX(entry_time)
                FROM trades
                WHERE slug LIKE 'btc-updown-5m-%'
                """,
            )

            blocker_rows = sig_conn.execute(
                """
                SELECT guard_reasons, COUNT(*) AS n
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                  AND source = 'binance_momentum'
                  AND epoch >= ?
                  AND COALESCE(guard_reasons, '') != ''
                GROUP BY guard_reasons
                ORDER BY n DESC
                LIMIT 5
                """,
                (windows["6h"],),
            ).fetchall()
            blockers = [
                {"reason": row["guard_reasons"], "count": row["n"]}
                for row in blocker_rows
                if row["guard_reasons"]
            ]

            recent_pass_rows = sig_conn.execute(
                """
                SELECT epoch, slug, source, midpoint, time_remaining_secs
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                  AND source = 'binance_momentum'
                  AND guard_passed = 1
                ORDER BY epoch DESC
                LIMIT 5
                """
            ).fetchall()
            recent_passes = [dict(row) for row in recent_pass_rows]
            stage_stops = []
            if {"pipeline_stage", "pipeline_status"}.issubset(signal_columns):
                stage_stop_rows = sig_conn.execute(
                    """
                    SELECT
                        COALESCE(pipeline_stage, 'unknown') AS stage,
                        COALESCE(pipeline_status, 'unknown') AS status,
                        COUNT(*) AS n
                    FROM signals
                    WHERE asset = 'BTC' AND market_window_secs = 300
                      AND epoch >= ?
                    GROUP BY COALESCE(pipeline_stage, 'unknown'), COALESCE(pipeline_status, 'unknown')
                    ORDER BY n DESC
                    LIMIT 8
                    """,
                    (windows["6h"],),
                ).fetchall()
                stage_stops = [dict(row) for row in stage_stop_rows]
            retry_stats = (
                self._executor.get_entry_retry_stats()
                if self._executor and hasattr(self._executor, "get_entry_retry_stats")
                else {}
            )
            retry_skip_reasons = [
                {"reason": reason, "count": count}
                for reason, count in sorted(
                    retry_stats.get("retry_skip_reasons", {}).items(),
                    key=lambda item: item[1],
                    reverse=True,
                )[:5]
            ]

            stage = "healthy"
            headline = "BTC pipeline is flowing"
            summary = "Recent BTC decisions, passed signals, and trades are present."
            if counts["decisions_15m"] == 0:
                stage = "stalled"
                headline = "No BTC 5m decision in 15 minutes"
                summary = "Market discovery or signal generation may be stalled."
            elif counts["momentum_1h"] == 0:
                stage = "starved"
                headline = "No BTC momentum candidate in 1 hour"
                summary = "The entry source is not generating BTC 5m candidates."
            elif counts["momentum_1h"] > 0 and counts["passed_1h"] == 0:
                stage = "guard_blocked"
                headline = "BTC candidates are dying at the guard stage"
                summary = f"{counts['momentum_1h']} BTC momentum candidates in the last hour, 0 passed."
            elif counts["passed_6h"] > 0 and counts["trades_6h"] == 0:
                stage = "execution_gap"
                headline = "Signals passed but no BTC trade executed in 6 hours"
                summary = "A handoff or execution path may be missing."
            elif counts["trades_6h"] == 0:
                stage = "trade_silent"
                headline = "No executed BTC trade in 6 hours"
                summary = "The bot is alive, but the live BTC path has not produced a fill recently."
            if retry_stats.get("placement_failures", 0) > 0 or retry_stats.get("fill_timeouts", 0) > 0:
                summary = (
                    f"{summary} Placement failures={retry_stats.get('placement_failures', 0)}, "
                    f"fill timeouts={retry_stats.get('fill_timeouts', 0)}, "
                    f"retry recovered={retry_stats.get('retry_recovered', 0)}."
                )

            return {
                "stage": stage,
                "headline": headline,
                "summary": summary,
                "last_btc_decision_ts": last_btc_decision,
                "last_btc_momentum_ts": last_btc_momentum,
                "last_btc_pass_ts": last_btc_pass,
                "last_trade_ts": last_trade,
                "counts": counts,
                "blockers": blockers,
                "recent_passes": recent_passes,
                "passed_btc_candidates": counts.get("passed_6h", 0),
                "placement_failures": retry_stats.get("placement_failures", 0),
                "fill_timeouts": retry_stats.get("fill_timeouts", 0),
                "retry_recovered": retry_stats.get("retry_recovered", 0),
                "retry_skip_reasons": retry_skip_reasons,
                "stage_stops": stage_stops,
            }
        finally:
            sig_conn.close()
            trade_conn.close()

    def _detect_systemd_unit(self) -> str | None:
        """Best-effort detection of the running lagbot systemd unit name."""
        try:
            with open("/proc/self/cgroup", "r", encoding="utf-8") as handle:
                for line in handle:
                    if ".service" not in line:
                        continue
                    parts = [part for part in line.strip().split("/") if part]
                    for part in reversed(parts):
                        if part.endswith(".service") and "lagbot@" in part:
                            return part
        except Exception:
            return None
        return None

    def _get_recent_error_lines(self, minutes: int = 30, limit: int = 20) -> list[str]:
        """Return recent error/exception lines from journald for this unit."""
        unit = self._detect_systemd_unit()
        if not unit:
            return []
        try:
            result = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    unit,
                    "--since",
                    f"{minutes} minutes ago",
                    "--no-pager",
                    "-o",
                    "short-iso",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return []
            lines = []
            for line in result.stdout.splitlines():
                if any(token in line for token in ("[ERROR]", "[WARNING]", "[CRITICAL]", "Traceback", "Exception")):
                    lines.append(line.strip())
            return lines[-limit:]
        except Exception:
            return []

    def _price_bucket(self, price) -> str:
        """Bucket entry or midpoint prices for dashboard summaries."""
        if price is None:
            return "unknown"
        if price < 0.40:
            return "0.00-0.39"
        if price < 0.60:
            return "0.40-0.59"
        if price < 0.80:
            return "0.60-0.79"
        return "0.80-1.00"

    def _time_bucket(self, secs) -> str:
        """Bucket time remaining for dashboard summaries."""
        if secs is None:
            return "unknown"
        if secs < 60:
            return "<60s"
        if secs < 120:
            return "60-119s"
        if secs < 180:
            return "120-179s"
        return "180s+"

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")


# ── Embedded HTML Dashboard ──────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lagbot</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0d1117;--card:#161b22;--border:#21262d;--hover:#1c2128;
  --text:#c9d1d9;--dim:#484f58;--bright:#f0f6fc;
  --green:#3fb950;--red:#f85149;--blue:#58a6ff;--amber:#d29922;--purple:#bc8cff;
  --shadow:0 1px 3px rgba(0,0,0,0.3);
  --mono:'SF Mono',SFMono-Regular,'Cascadia Code','JetBrains Mono',Consolas,monospace;
}
.light{
  --bg:#f6f8fa;--card:#ffffff;--border:#d0d7de;--hover:#f3f4f6;
  --text:#24292f;--dim:#656d76;--bright:#1f2328;
  --green:#1a7f37;--red:#cf222e;--blue:#0969da;--amber:#9a6700;--purple:#8250df;
  --shadow:0 1px 3px rgba(27,31,36,0.12);
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:14px;line-height:1.5}
a{color:var(--blue);text-decoration:none}
.wrap{max-width:1200px;margin:0 auto;padding:16px 20px}

/* Header */
header{display:flex;justify-content:space-between;align-items:center;padding:12px 0 20px;border-bottom:1px solid var(--border);margin-bottom:20px}
.header-left{display:flex;align-items:center;gap:12px}
header h1{font-size:16px;font-weight:600;color:var(--bright)}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-family:var(--mono);padding:2px 10px;border:1px solid var(--border);border-radius:16px;color:var(--dim)}
.badge .dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.badge .dot.on{background:var(--green);box-shadow:0 0 4px var(--green)}
.badge .dot.off{background:var(--red)}
.badge .dot.warn{background:var(--amber)}
.header-right{display:flex;align-items:center;gap:10px}
.header-right .ago{font-size:11px;color:var(--dim);font-family:var(--mono)}
.theme-btn{background:none;border:1px solid var(--border);border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;color:var(--dim)}
.theme-btn:hover{color:var(--bright);border-color:var(--bright)}

/* Grid */
.grid{display:grid;gap:12px;margin-bottom:12px}
.g4{grid-template-columns:repeat(4,1fr)}
.g2{grid-template-columns:1fr 1fr}
.g1{grid-template-columns:1fr}
@media(max-width:900px){.g4,.g2{grid-template-columns:1fr 1fr}}
@media(max-width:540px){.g4,.g2,.g1{grid-template-columns:1fr}}

/* Cards */
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}
.card-title{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;color:var(--dim);margin-bottom:12px}

/* Metrics */
.big-num{font-family:var(--mono);font-size:28px;font-weight:700;color:var(--bright);letter-spacing:-0.5px}
.big-num.sm{font-size:22px}
.big-sub{font-size:12px;color:var(--dim);font-family:var(--mono);margin-top:2px;margin-bottom:10px}
.row{display:flex;justify-content:space-between;align-items:baseline;padding:4px 0}
.row .lbl{color:var(--dim);font-size:12px}
.row .val{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--bright)}
.green{color:var(--green) !important}.red{color:var(--red) !important}
.blue{color:var(--blue) !important}.amber{color:var(--amber) !important}.purple{color:var(--purple) !important}.dim{color:var(--dim) !important}

/* State pill */
.state-pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px;font-family:var(--mono)}
.state-pill.hedged{background:#3fb95020;color:var(--green)}
.state-pill.accumulating{background:#58a6ff20;color:var(--blue)}
.state-pill.settling{background:#d2992220;color:var(--amber)}
.state-pill.scanning,.state-pill.idle{background:#484f5820;color:var(--dim)}
.state-pill.active{background:#3fb95020;color:var(--green)}

/* Countdown */
.countdown{font-family:var(--mono);font-size:20px;font-weight:700;color:var(--amber);text-align:center;padding:6px 0}

/* Chart */
.chart-wrap{position:relative;height:140px}

/* Table */
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 8px;color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid var(--border);font-weight:600}
td{padding:6px 8px;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:11px;color:var(--text)}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--hover)}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px}
.tag.hedged{background:#3fb95018;color:var(--green)}
.tag.orphan{background:#f8514918;color:var(--red)}
.tag.unwound{background:#d2992218;color:var(--amber)}
.tag.allow,.tag.executed,.tag.closed{background:#3fb95018;color:var(--green)}
.tag.shadow,.tag.open{background:#58a6ff20;color:var(--blue)}
.tag.block,.tag.filtered{background:#f8514918;color:var(--red)}
.tag.unknown{background:#484f5820;color:var(--dim)}
.tag.active{background:#d2992218;color:var(--amber)}
.kv{display:grid;gap:6px}
.kv div{display:flex;justify-content:space-between;gap:12px}
.kv .label{color:var(--dim);font-size:12px}
.empty{text-align:center;padding:20px;color:var(--dim);font-size:12px}

/* Momentum */
.momentum-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:8px}
.m-item{text-align:center;padding:10px 6px;background:var(--bg);border-radius:6px;border:1px solid var(--border)}
.m-item .m-asset{font-size:10px;color:var(--dim);font-weight:700;letter-spacing:0.5px;margin-bottom:2px}
.m-item .m-dir{font-family:var(--mono);font-size:16px;font-weight:700}
.m-item .m-pct{font-family:var(--mono);font-size:10px;color:var(--dim);margin-top:1px}
.m-dir.UP{color:var(--green)}.m-dir.DOWN{color:var(--red)}.m-dir.NEUTRAL,.m-dir.UNKNOWN{color:var(--dim)}

/* Separator */
.sep{height:1px;background:var(--border);margin:8px 0}

footer{text-align:center;padding:16px 0;color:var(--dim);font-size:10px;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="header-left">
    <h1>Lagbot</h1>
    <span class="badge"><span class="dot" id="dot"></span><span id="status-text">...</span></span>
    <span class="badge" id="mode-badge"></span>
  </div>
  <div class="header-right">
    <span class="ago" id="refresh">--</span>
    <button class="theme-btn" id="theme-toggle" onclick="toggleTheme()">Light</button>
  </div>
</header>

<!-- Row 1: Key numbers -->
<div class="grid g4">
  <div class="card">
    <div class="card-title">CLOB Balance</div>
    <div class="big-num" id="balance">--</div>
    <div class="big-sub" id="balance-sub"></div>
    <div class="row"><span class="lbl">Deployed</span><span class="val blue" id="deployed">--</span></div>
    <div class="row"><span class="lbl">Available</span><span class="val" id="available">--</span></div>
  </div>

  <div class="card">
    <div class="card-title">Session P&amp;L</div>
    <div class="big-num" id="accum-pnl">--</div>
    <div class="big-sub" id="pnl-sub"></div>
    <div class="row"><span class="lbl">Win Rate</span><span class="val" id="win-rate">--</span></div>
    <div class="row"><span class="lbl">Avg Profit</span><span class="val" id="avg-profit">--</span></div>
    <div class="row"><span class="lbl">Fill Rate</span><span class="val" id="fill-rate">--</span></div>
  </div>

  <div class="card">
    <div class="card-title">Current Cycle</div>
    <div id="cycle-content"><p class="empty">Scanning...</p></div>
  </div>

  <div class="card">
    <div class="card-title">System</div>
    <div class="row"><span class="lbl">Uptime</span><span class="val" id="uptime">--</span></div>
    <div class="row"><span class="lbl">Errors</span><span class="val" id="errors">0</span></div>
    <div class="row"><span class="lbl">Binance WS</span><span class="val" id="binance-status">--</span></div>
    <div class="sep"></div>
    <div class="row"><span class="lbl">Scans</span><span class="val" id="scan-count">0</span></div>
    <div class="row"><span class="lbl">Hedged</span><span class="val green" id="accum-hedged">0</span></div>
    <div class="row"><span class="lbl">Orphaned</span><span class="val red" id="accum-orphaned">0</span></div>
    <div class="row"><span class="lbl">Unwound</span><span class="val amber" id="accum-unwound">0</span></div>
  </div>
</div>

<!-- Row 2: Chart + Momentum -->
<div class="grid g2">
  <div class="card">
    <div class="card-title">Cumulative P&amp;L</div>
    <div class="chart-wrap"><canvas id="pnl-chart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title">Momentum Feed</div>
    <div class="momentum-grid" id="momentum-grid"></div>
  </div>
</div>

<!-- Row 3: Settlements -->
<div class="grid g1">
  <div class="card">
    <div class="card-title">Settlement Log</div>
    <div id="settlements-container"><p class="empty">No settlements yet</p></div>
  </div>
</div>

<!-- Row 4: Learning Stack -->
<div class="grid g2">
  <div class="card">
    <div class="card-title">Gabagool Tracker</div>
    <div id="gabagool-container"><p class="empty">Loading...</p></div>
  </div>
  <div class="card">
    <div class="card-title">Adaptive Tuner</div>
    <div id="tuning-container"><p class="empty">Loading...</p></div>
  </div>
</div>

<!-- Row 5: Evidence + Trades -->
<div class="grid g2">
  <div class="card">
    <div class="card-title">BTC Pipeline Watchdog</div>
    <div id="pipeline-container"><p class="empty">Loading...</p></div>
  </div>
  <div class="card">
    <div class="card-title">BTC 5m Evidence</div>
    <div id="evidence-container"><p class="empty">Loading...</p></div>
  </div>
</div>

<!-- Row 6: Trades + Filters -->
<div class="grid g2">
  <div class="card">
    <div class="card-title">Recent Trades</div>
    <div id="recent-trades-container"><p class="empty">Loading...</p></div>
  </div>
  <div class="card">
    <div class="card-title">Why Signals Were Rejected</div>
    <div id="filters-container"><p class="empty">Loading...</p></div>
  </div>
</div>

<!-- Row 7: Recent Error Stream -->
<div class="grid g1">
  <div class="card">
    <div class="card-title">Recent Error Stream</div>
    <div id="errors-container"><p class="empty">Loading...</p></div>
  </div>
</div>

<!-- Row 8: Recent Signals -->
<div class="grid g1">
  <div class="card">
    <div class="card-title">Recent Signal Decisions</div>
    <div id="recent-signals-container"><p class="empty">Loading...</p></div>
  </div>
</div>

<footer>Lagbot Live Observability</footer>
</div>

<script>
const $ = id => document.getElementById(id);
const fmt = (n,d=2) => n != null ? Number(n).toFixed(d) : '--';
const pnlSign = n => n > 0 ? '+$'+fmt(n) : n < 0 ? '-$'+fmt(Math.abs(n)) : '$0.00';
let lastUpdate = Date.now();
let pnlChart = null;

// Parse epoch from slug like "btc-updown-5m-1771050900"
function slugEpoch(slug) {
  const m = slug.match(/(\\d{10})$/);
  return m ? parseInt(m[1]) : 0;
}
function slugTime(slug) {
  const ep = slugEpoch(slug);
  if (!ep) return slug;
  const d = new Date(ep * 1000);
  return d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true,timeZone:'UTC'});
}
function fmtTimestamp(ts) {
  if (!ts) return '--';
  const d = new Date(ts * 1000);
  return d.toLocaleString('en-US',{timeZone:'UTC',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',hour12:true});
}
function fmtAgo(ts) {
  if(!ts) return 'never';
  const secs = Math.max(0, Math.floor(Date.now()/1000 - ts));
  if(secs < 60) return secs+'s ago';
  if(secs < 3600) return (secs/60).toFixed(1)+'m ago';
  return (secs/3600).toFixed(1)+'h ago';
}
function tagCls(value) {
  const v = (value || 'unknown').toLowerCase();
  if(v.includes('filtered')) return 'block';
  if(['allow','executed','closed'].includes(v)) return 'allow';
  if(['shadow','open'].includes(v)) return 'shadow';
  if(['block','filtered'].includes(v)) return 'block';
  if(['active'].includes(v)) return 'active';
  if(['healthy'].includes(v)) return 'allow';
  if(['guard_blocked','stalled','starved','execution_gap','trade_silent'].includes(v)) return 'block';
  return 'unknown';
}
function clipReason(text, maxLen=72) {
  if(!text) return '--';
  const base = text.split('|')[0].trim();
  return base.length > maxLen ? base.slice(0, maxLen-1) + '…' : base;
}

function toggleTheme(){
  const el = document.documentElement;
  const isLight = el.classList.contains('light');
  el.classList.toggle('dark', isLight);
  el.classList.toggle('light', !isLight);
  localStorage.setItem('lagbot-theme', isLight ? 'dark' : 'light');
  $('theme-toggle').textContent = isLight ? 'Light' : 'Dark';
  if(pnlChart) updateChartColors();
}
(function(){
  const s = localStorage.getItem('lagbot-theme');
  if(s==='light'){
    document.documentElement.classList.remove('dark');
    document.documentElement.classList.add('light');
    $('theme-toggle').textContent = 'Dark';
  }
})();

function getC(){
  const s = getComputedStyle(document.documentElement);
  return {green:s.getPropertyValue('--green').trim(),red:s.getPropertyValue('--red').trim(),
    blue:s.getPropertyValue('--blue').trim(),dim:s.getPropertyValue('--dim').trim(),
    border:s.getPropertyValue('--border').trim(),text:s.getPropertyValue('--text').trim()};
}

function initChart(){
  const c = getC();
  pnlChart = new Chart($('pnl-chart').getContext('2d'), {
    type:'line',
    data:{labels:[],datasets:[{data:[],borderColor:c.green,backgroundColor:c.green+'18',fill:true,tension:0.3,pointRadius:2,pointHoverRadius:4,borderWidth:2}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
      scales:{x:{display:false},y:{grid:{color:c.border+'60'},ticks:{color:c.dim,font:{size:10,family:'var(--mono)'},callback:v=>'$'+v}}}}
  });
}
function updateChartColors(){
  const c=getC();
  pnlChart.options.scales.y.grid.color=c.border+'60';
  pnlChart.options.scales.y.ticks.color=c.dim;
  pnlChart.update('none');
}

async function fetchAll(){
  try{
    const [status,balance,momentum,accum,gabagool,tuning,signals,evidence,trades,filters,errors,pipeline] = await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/balance').then(r=>r.json()),
      fetch('/api/momentum').then(r=>r.json()),
      fetch('/api/accumulator').then(r=>r.json()),
      fetch('/api/gabagool').then(r=>r.json()).catch(()=>({enabled:false})),
      fetch('/api/accumulator/tuning').then(r=>r.json()).catch(()=>({enabled:false})),
      fetch('/api/signals').then(r=>r.json()).catch(()=>({enabled:false,signals:[]})),
      fetch('/api/evidence').then(r=>r.json()).catch(()=>({enabled:false,verdict_counts:{},reason_counts:[],recent:[]})),
      fetch('/api/trades').then(r=>r.json()).catch(()=>({enabled:false,trades:[]})),
      fetch('/api/filters').then(r=>r.json()).catch(()=>({enabled:false,filtered_signals:0,reason_counts:[],price_buckets:[],time_buckets:[],recent:[]})),
      fetch('/api/errors').then(r=>r.json()).catch(()=>({enabled:false,unit:'unknown',errors:[]})),
      fetch('/api/pipeline').then(r=>r.json()).catch(()=>({enabled:false,stage:'unknown',headline:'Pipeline unavailable',summary:'No pipeline data'})),
    ]);

    // Status
    const dot=$('dot'), stxt=$('status-text');
    if(status.errors>5){dot.className='dot off';stxt.textContent='ERROR';}
    else{dot.className='dot on';stxt.textContent='LIVE';}

    // Mode badge
    const st = accum.state||'idle';
    $('mode-badge').innerHTML = '<span class="state-pill '+st+'">'+st+'</span>';

    // Balance
    $('balance').textContent = '$'+fmt(balance.balance||0);
    const dep = balance.deployed||0;
    $('deployed').textContent = '$'+fmt(dep);
    $('available').textContent = '$'+fmt(balance.available||0);

    // Session P&L
    const apnl = accum.total_pnl||0;
    const apnlEl = $('accum-pnl');
    apnlEl.textContent = pnlSign(apnl);
    apnlEl.className = 'big-num ' + (apnl >= 0 ? 'green' : 'red');

    const setts = accum.settlements||[];
    const totalSettled = setts.length;
    const hedgedCnt = setts.filter(s=>(s.exit_reason||'').includes('hedged')).length;
    const orphanCnt = setts.filter(s=>(s.exit_reason||'').includes('orphan')).length;
    const unwoundCnt = setts.filter(s=>(s.exit_reason||'').includes('unwound')).length;
    const winCnt = setts.filter(s=>s.pnl>0).length;
    const wr = totalSettled > 0 ? (winCnt/totalSettled*100).toFixed(0)+'%' : '--';
    const avgP = totalSettled > 0 ? pnlSign(apnl/totalSettled) : '--';
    const placed = accum.orders_placed||0;
    const filled = accum.orders_filled||0;
    const fr = placed > 0 ? (filled/placed*100).toFixed(0)+'%' : '--';

    $('pnl-sub').textContent = totalSettled+' settled / '+hedgedCnt+'W '+orphanCnt+'O '+unwoundCnt+'U';
    $('win-rate').textContent = wr;
    $('win-rate').className = 'val '+(winCnt/Math.max(totalSettled,1)>=0.5?'green':'red');
    $('avg-profit').textContent = avgP;
    $('avg-profit').className = 'val '+(apnl>=0?'green':'red');
    $('fill-rate').textContent = fr + ' ('+filled+'/'+placed+')';
    $('fill-rate').className = 'val '+(filled/Math.max(placed,1)>=0.5?'green':'amber');

    // Counters
    $('accum-hedged').textContent = accum.hedged_count||0;
    $('accum-orphaned').textContent = accum.orphaned_count||0;
    $('accum-unwound').textContent = accum.unwound_count||0;
    $('scan-count').textContent = accum.scan_count||0;

    // Current cycle(s)
    const cycleEl = $('cycle-content');
    const positions = accum.positions || [];
    if(positions.length > 0){
      let cycleHtml = '<div class="row"><span class="lbl">Slots</span><span class="val blue">'+positions.length+'/'+(accum.max_concurrent||1)+'</span></div><div class="sep"></div>';
      for(const cp of positions){
        const profit = cp.pair_cost > 0 ? (1.0 - cp.pair_cost) : 0;
        const ep = slugEpoch(cp.slug);
        let countdownHtml = '';
        if(ep > 0){
          const secsLeft = ep + 300 - Math.floor(Date.now()/1000);
          if(secsLeft > 0){
            const mm = Math.floor(secsLeft/60), ss = secsLeft%60;
            countdownHtml = '<div class="countdown">'+mm+':'+String(ss).padStart(2,'0')+'</div>';
          } else {
            countdownHtml = '<div class="countdown green">SETTLING</div>';
          }
        }
        cycleHtml += countdownHtml
          +'<div class="row"><span class="lbl">Market</span><span class="val">'+slugTime(cp.slug)+' <span class="state-pill '+cp.state+'">'+cp.state+'</span></span></div>'
          +'<div class="row"><span class="lbl">Shares</span><span class="val">'+fmt(cp.up_qty,1)+' / '+fmt(cp.down_qty,1)+'</span></div>'
          +'<div class="row"><span class="lbl">Pair Cost</span><span class="val blue">$'+fmt(cp.pair_cost,4)+'</span></div>'
          +'<div class="row"><span class="lbl">Profit/share</span><span class="val '+(profit>0?'green':'red')+'">$'+fmt(profit,4)+'</span></div>'
          +'<div class="row"><span class="lbl">Reprices</span><span class="val">'+cp.reprice_count+'/15</span></div>'
          +(cp.is_hedged?'<div style="text-align:center;margin-top:4px"><span class="state-pill hedged">HEDGED</span></div>':'')
          +'<div class="sep"></div>';
      }
      cycleEl.innerHTML = cycleHtml;
    } else {
      cycleEl.innerHTML = '<p class="empty">Scanning for opportunities...</p>';
    }

    // System
    const hrs = status.uptime_hours||0;
    $('uptime').textContent = hrs >= 24 ? fmt(hrs/24,1)+'d' : fmt(hrs,1)+'h';
    const errEl = $('errors');
    errEl.textContent = status.errors||0;
    errEl.className = 'val '+(status.errors>0?'red':'green');
    const bEl = $('binance-status');
    bEl.textContent = status.binance_connected?'Connected':'Offline';
    bEl.className = 'val '+(status.binance_connected?'green':'red');

    // Chart
    if(pnlChart && setts.length){
      let cum=0;const labels=[],data=[];
      for(const s of setts){cum+=s.pnl||0;labels.push(slugTime(s.slug));data.push(+cum.toFixed(2));}
      pnlChart.data.labels=labels;
      pnlChart.data.datasets[0].data=data;
      const c=getC();
      pnlChart.data.datasets[0].borderColor=cum>=0?c.green:c.red;
      pnlChart.data.datasets[0].backgroundColor=(cum>=0?c.green:c.red)+'18';
      pnlChart.update('none');
    }

    // Momentum — dynamic from API keys
    const mGrid = $('momentum-grid');
    mGrid.innerHTML = '';
    const readings = momentum.readings||{};
    for(const asset of Object.keys(readings).sort()){
      const r = readings[asset];
      const arrow = r.direction==='UP'?'&#8593;':r.direction==='DOWN'?'&#8595;':'&#8212;';
      mGrid.innerHTML += '<div class="m-item"><div class="m-asset">'+asset+'</div>'
        +'<div class="m-dir '+r.direction+'">'+arrow+' '+r.direction+'</div>'
        +'<div class="m-pct">'+(r.momentum_pct>=0?'+':'')+fmt(r.momentum_pct,3)+'%</div></div>';
    }

    // Settlement table
    const sCont = $('settlements-container');
    if(!setts.length){
      sCont.innerHTML = '<p class="empty">No settlements yet</p>';
    } else {
      let h = '<table><tr><th>Time</th><th>Market</th><th>Type</th><th>Shares</th><th>Pair Cost</th><th>P&amp;L</th></tr>';
      for(const s of setts.slice().reverse()){
        const reason = s.exit_reason||'';
        const shares = s.matched > 0 ? fmt(s.matched,0) : fmt(Math.max(s.up_qty,s.down_qty),0);
        const typeLabel = reason.includes('hedged')?'HEDGED':reason.includes('orphan')?'ORPHAN':'UNWOUND';
        const tagCls = reason.includes('hedged')?'hedged':reason.includes('orphan')?'orphan':'unwound';
        const timeStr = fmtTimestamp(s.timestamp);
        h += '<tr><td>'+timeStr+'</td><td>'+slugTime(s.slug)+'</td>'
          +'<td><span class="tag '+tagCls+'">'+typeLabel+'</span></td>'
          +'<td>'+shares+'</td>'
          +'<td>'+(s.pair_cost>0?'$'+fmt(s.pair_cost,4):'--')+'</td>'
          +'<td class="'+(s.pnl>=0?'green':'red')+'">'+pnlSign(s.pnl)+'</td></tr>';
      }
      sCont.innerHTML = h+'</table>';
    }

    // Gabagool tracker
    const gCont = $('gabagool-container');
    if(gabagool.enabled && gCont){
      const g = gabagool;
      const pc = g.pair_cost||{};
      const pnl = g.pnl||{};
      const lastSeen = g.last_seen > 0 ? new Date(g.last_seen*1000).toLocaleTimeString() : 'never';
      const activeClass = g.active_now ? 'green' : 'dim';
      const assetStr = Object.entries(g.asset_distribution||{}).map(([k,v])=>k+': '+v+'%').join(', ')||'--';
      const sideRange = g.side_price_range||[0,0];
      gCont.innerHTML = '<div class="kv">'
        +'<div><span class="label">Status</span><span class="'+activeClass+'">'+(g.active_now?'ACTIVE':'IDLE')+'</span></div>'
        +'<div><span class="label">Last Seen</span><span>'+lastSeen+'</span></div>'
        +'<div><span class="label">Tracked / Pairs</span><span>'+g.total_tracked+' / '+g.total_pairs+'</span></div>'
        +'<div><span class="label">Trades/Hour</span><span>'+fmt(g.trades_per_hour,1)+'</span></div>'
        +'<div><span class="label">Pair Cost</span><span>'+(pc.median>0?'$'+fmt(pc.median,4)+' ('+fmt(pc.min,3)+'-'+fmt(pc.max,3)+')':'--')+'</span></div>'
        +'<div><span class="label">Fill Rate</span><span>'+(g.fill_rate>0?g.fill_rate+'%':'--')+'</span></div>'
        +'<div><span class="label">Fill Gap</span><span>'+(g.avg_fill_gap_secs>0?fmt(g.avg_fill_gap_secs,1)+'s (max '+fmt(g.max_fill_gap_secs,0)+'s)':'--')+'</span></div>'
        +'<div><span class="label">Side Range</span><span>'+(sideRange[1]>0?'$'+fmt(sideRange[0],3)+'-$'+fmt(sideRange[1],3):'--')+'</span></div>'
        +'<div><span class="label">PnL</span><span class="'+(pnl.total>=0?'green':'red')+'">'+(pnl.total!=null?pnlSign(pnl.total)+' ($'+fmt(pnl.hourly||0,2)+'/hr)':'--')+'</span></div>'
        +'<div><span class="label">Assets</span><span>'+assetStr+'</span></div>'
        +'<div><span class="label">Window</span><span>'+(g.preferred_window||'--')+'</span></div>'
        +'<div><span class="label">Avg Size</span><span>'+fmt(g.avg_size_per_side,0)+' shares</span></div>'
        +'</div>';
    } else if(gCont) {
      gCont.innerHTML = '<p class="empty">Tracker disabled</p>';
    }

    // Adaptive tuner
    const tCont = $('tuning-container');
    if(tuning.enabled && tCont){
      const t = tuning;
      const frozen = t.frozen ? '<span class="red">FROZEN: '+t.frozen_reason+'</span>' : '<span class="green">ACTIVE</span>';
      let oh = '<div class="kv">'
        +'<div><span class="label">Status</span>'+frozen+'</div>'
        +'<div><span class="label">Tune Cycles</span><span>'+t.tune_count+'</span></div>';
      // Current overrides
      const ov = t.current_overrides||{};
      const ovKeys = Object.keys(ov);
      if(ovKeys.length>0){
        oh += '<div><span class="label">Overrides</span><span>'+ovKeys.map(k=>k.replace('accum_','')+': '+ov[k]).join(', ')+'</span></div>';
      } else {
        oh += '<div><span class="label">Overrides</span><span class="dim">none (using defaults)</span></div>';
      }
      // Hourly stats
      const hs = t.hourly_stats||{};
      oh += '<div><span class="label">Hedge Rate (1h)</span><span>'+(hs.hedge_rate!=null?Math.round(hs.hedge_rate*100)+'%':'--')+'</span></div>'
        +'<div><span class="label">PnL (1h)</span><span class="'+(hs.total_pnl>=0?'green':'red')+'">'+pnlSign(hs.total_pnl||0)+'</span></div>'
        +'</div>';
      // Recent adjustments
      const adjs = t.last_5_adjustments||[];
      if(adjs.length>0){
        oh += '<div style="margin-top:8px;font-size:11px;color:var(--dim)">';
        for(const a of adjs.slice().reverse()){
          const ts = new Date(a.ts*1000).toLocaleTimeString();
          oh += '<div>'+ts+' '+a.param.replace('accum_','')+': '+a.old+' &rarr; '+a.new+' ('+a.reason+')</div>';
        }
        oh += '</div>';
      }
      tCont.innerHTML = oh;
    } else if(tCont) {
      tCont.innerHTML = '<p class="empty">Tuner disabled</p>';
    }

    // Pipeline watchdog
    const pCont = $('pipeline-container');
    if(pCont){
      const counts = pipeline.counts || {};
      const blockers = pipeline.blockers || [];
      const passes = pipeline.recent_passes || [];
      const retrySkips = pipeline.retry_skip_reasons || [];
      const stageStops = pipeline.stage_stops || [];
      let ph = '<div class="kv">'
        +'<div><span class="label">Stage</span><span><span class="tag '+tagCls(pipeline.stage)+'">'+(pipeline.stage||'unknown')+'</span></span></div>'
        +'<div><span class="label">Headline</span><span>'+(pipeline.headline || '--')+'</span></div>'
        +'<div><span class="label">Summary</span><span>'+(pipeline.summary || '--')+'</span></div>'
        +'<div><span class="label">Last BTC decision</span><span>'+fmtAgo(pipeline.last_btc_decision_ts)+'</span></div>'
        +'<div><span class="label">Last BTC momentum signal</span><span>'+fmtAgo(pipeline.last_btc_momentum_ts)+'</span></div>'
        +'<div><span class="label">Last BTC guard pass</span><span>'+fmtAgo(pipeline.last_btc_pass_ts)+'</span></div>'
        +'<div><span class="label">Last BTC trade</span><span>'+fmtAgo(pipeline.last_trade_ts)+'</span></div>'
        +'<div><span class="label">Passed BTC candidates</span><span>'+(pipeline.passed_btc_candidates||0)+'</span></div>'
        +'<div><span class="label">Placement failures</span><span>'+(pipeline.placement_failures||0)+'</span></div>'
        +'<div><span class="label">Fill timeouts</span><span>'+(pipeline.fill_timeouts||0)+'</span></div>'
        +'<div><span class="label">Retry recovered</span><span>'+(pipeline.retry_recovered||0)+'</span></div>'
        +'<div><span class="label">Decisions 15m / 1h</span><span>'+(counts.decisions_15m||0)+' / '+(counts.decisions_1h||0)+'</span></div>'
        +'<div><span class="label">Momentum 1h</span><span>'+(counts.momentum_1h||0)+'</span></div>'
        +'<div><span class="label">Passed 1h / 6h</span><span>'+(counts.passed_1h||0)+' / '+(counts.passed_6h||0)+'</span></div>'
        +'<div><span class="label">Trades 1h / 6h</span><span>'+(counts.trades_1h||0)+' / '+(counts.trades_6h||0)+'</span></div>'
        +'</div>';
      if(blockers.length){
        ph += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Top BTC Guard Blockers (6h)</div>';
        for(const row of blockers){
          ph += '<div class="row"><span class="lbl">'+clipReason(row.reason, 46)+'</span><span class="val">'+row.count+'</span></div>';
        }
        ph += '</div>';
      }
      if(passes.length){
        ph += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Recent Passed BTC Signals</div><table><tr><th>Time</th><th>Price</th><th>Left</th></tr>';
        for(const row of passes){
          ph += '<tr><td>'+slugTime(row.slug||'')+'</td><td>'+(row.midpoint!=null?'$'+fmt(row.midpoint,3):'--')+'</td><td>'+(row.time_remaining_secs!=null?row.time_remaining_secs+'s':'--')+'</td></tr>';
        }
        ph += '</table></div>';
      }
      if(retrySkips.length){
        ph += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Retry Skip Reasons</div>';
        for(const row of retrySkips){
          ph += '<div class="row"><span class="lbl">'+clipReason(row.reason, 46)+'</span><span class="val">'+row.count+'</span></div>';
        }
        ph += '</div>';
      }
      if(stageStops.length){
        ph += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Pipeline Stage Mix (6h)</div>';
        for(const row of stageStops){
          ph += '<div class="row"><span class="lbl">'+clipReason((row.stage||'unknown')+' / '+(row.status||'unknown'), 46)+'</span><span class="val">'+row.n+'</span></div>';
        }
        ph += '</div>';
      }
      pCont.innerHTML = ph;
    }

    // Evidence summary
    const eCont = $('evidence-container');
    if(eCont){
      const verdicts = evidence.verdict_counts || {};
      const recentVerdicts = evidence.recent || [];
      const reasons = evidence.reason_counts || [];
      const counts = Object.keys(verdicts).length
        ? Object.entries(verdicts).map(([k,v]) => '<span class="tag '+tagCls(k)+'">'+k+': '+v+'</span>').join(' ')
        : '<span class="dim">no verdicts yet</span>';
      let eh = '<div class="kv">'
        +'<div><span class="label">Mode</span><span>'+(evidence.enabled ? 'shadow' : 'disabled')+'</span></div>'
        +'<div><span class="label">Signals (24h)</span><span>'+ (evidence.signals_scanned || 0) +'</span></div>'
        +'<div><span class="label">Verdicts</span><span>'+counts+'</span></div>'
        +'</div>';
      if(reasons.length){
        eh += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Top Reasons</div>';
        for(const item of reasons){
          eh += '<div class="row"><span class="lbl">'+clipReason(item.reason, 44)+'</span><span class="val">'+item.count+'</span></div>';
        }
        eh += '</div>';
      }
      if(recentVerdicts.length){
        eh += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Recent Verdicts</div>'
          +'<table><tr><th>Time</th><th>Verdict</th><th>R8</th><th>Reason</th></tr>';
        for(const row of recentVerdicts){
          eh += '<tr><td>'+slugTime(row.slug)+'</td>'
            +'<td><span class="tag '+tagCls(row.evidence_verdict)+'">'+(row.evidence_verdict||'--')+'</span></td>'
            +'<td>'+(row.evidence_r8_label||'--')+'</td>'
            +'<td>'+clipReason(row.evidence_reason, 52)+'</td></tr>';
        }
        eh += '</table></div>';
      }
      eCont.innerHTML = eh;
    }

    // Recent trades
    const trCont = $('recent-trades-container');
    if(trCont){
      const rows = trades.trades || [];
      if(!rows.length){
        trCont.innerHTML = '<p class="empty">No recent trades</p>';
      } else {
        let h = '<table><tr><th>Entry</th><th>Market</th><th>Source</th><th>Status</th><th>P&amp;L</th></tr>';
        for(const row of rows.slice(0, 12)){
          const status = row.open ? 'open' : 'closed';
          const reason = row.open ? 'open' : (row.exit_reason || 'closed');
          h += '<tr><td>'+fmtTimestamp(row.entry_time)+'</td>'
            +'<td>'+slugTime(row.slug||'')+'</td>'
            +'<td>'+(row.source || '--')+'</td>'
            +'<td><span class="tag '+tagCls(status)+'">'+reason+'</span></td>'
            +'<td class="'+((row.pnl||0)>=0?'green':'red')+'">'+(row.pnl != null ? pnlSign(row.pnl) : '--')+'</td></tr>';
        }
        h += '</table>';
        trCont.innerHTML = h;
      }
    }

    // Filter reasons / rejection panel
    const fCont = $('filters-container');
    if(fCont){
      const reasons = filters.reason_counts || [];
      const priceBuckets = filters.price_buckets || [];
      const timeBuckets = filters.time_buckets || [];
      const recentFiltered = filters.recent || [];
      let fh = '<div class="kv">'
        +'<div><span class="label">Filtered signals (24h)</span><span>'+(filters.filtered_signals || 0)+'</span></div>'
        +'<div><span class="label">Top price buckets</span><span>'+(priceBuckets.length ? priceBuckets.map(b=>b.bucket+': '+b.count).join(', ') : '--')+'</span></div>'
        +'<div><span class="label">Top time buckets</span><span>'+(timeBuckets.length ? timeBuckets.map(b=>b.bucket+': '+b.count).join(', ') : '--')+'</span></div>'
        +'</div>';
      if(reasons.length){
        fh += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Top Rejection Reasons</div>';
        for(const item of reasons){
          fh += '<div class="row"><span class="lbl">'+item.reason+'</span><span class="val">'+item.count+'</span></div>';
        }
        fh += '</div>';
      }
      if(recentFiltered.length){
        fh += '<div style="margin-top:10px"><div class="card-title" style="margin-bottom:8px">Recent Filtered Signals</div>'
          +'<table><tr><th>Time</th><th>Source</th><th>Outcome</th><th>Bucket</th><th>Reason</th></tr>';
        for(const row of recentFiltered.slice(0,8)){
          fh += '<tr><td>'+slugTime(row.slug||'')+'</td>'
            +'<td>'+(row.source || '--')+'</td>'
            +'<td><span class="tag '+tagCls(row.outcome || 'filtered')+'">'+(row.outcome || 'filtered')+'</span></td>'
            +'<td>'+row.price_bucket+' / '+row.time_bucket+'</td>'
            +'<td>'+clipReason(row.guard_reasons, 46)+'</td></tr>';
        }
        fh += '</table></div>';
      }
      fCont.innerHTML = fh;
    }

    // Recent error stream
    const erCont = $('errors-container');
    if(erCont){
      const rows = errors.errors || [];
      if(!rows.length){
        erCont.innerHTML = '<div class="kv"><div><span class="label">Unit</span><span>'+(errors.unit || '--')+'</span></div></div><p class="empty">No recent error lines</p>';
      } else {
        let eh = '<div class="kv"><div><span class="label">Unit</span><span>'+(errors.unit || '--')+'</span></div></div>'
          +'<div style="margin-top:10px"><table><tr><th>Recent Error Lines</th></tr>';
        for(const line of rows.slice().reverse()){
          eh += '<tr><td style="white-space:normal;word-break:break-word;font-family:var(--mono)">'+line.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')+'</td></tr>';
        }
        eh += '</table></div>';
        erCont.innerHTML = eh;
      }
    }

    // Recent signals
    const sigCont = $('recent-signals-container');
    if(sigCont){
      const rows = signals.signals || [];
      if(!rows.length){
        sigCont.innerHTML = '<p class="empty">No signal history available</p>';
      } else {
        let h = '<table><tr><th>Time</th><th>Asset</th><th>Source</th><th>Outcome</th><th>Price</th><th>Left</th><th>Evidence</th><th>Reason</th></tr>';
        for(const row of rows.slice(0, 14)){
          const price = row.entry_price != null ? row.entry_price : row.midpoint;
          const outcomeTag = row.outcome || 'unknown';
          const evidenceTag = row.evidence_verdict || '--';
          const reason = row.evidence_reason || row.guard_reasons || '--';
          h += '<tr><td>'+slugTime(row.slug||'')+'</td>'
            +'<td>'+(row.asset || '--')+'</td>'
            +'<td>'+(row.source || '--')+'</td>'
            +'<td><span class="tag '+tagCls(outcomeTag)+'">'+outcomeTag+'</span></td>'
            +'<td>'+(price != null ? '$'+fmt(price,3) : '--')+'</td>'
            +'<td>'+(row.time_remaining_secs != null ? row.time_remaining_secs+'s' : '--')+'</td>'
            +'<td>'+(row.evidence_verdict ? '<span class="tag '+tagCls(evidenceTag)+'">'+evidenceTag+'</span>' : '--')+'</td>'
            +'<td>'+clipReason(reason, 54)+'</td></tr>';
        }
        h += '</table>';
        sigCont.innerHTML = h;
      }
    }

    lastUpdate = Date.now();
  }catch(e){console.error('Fetch error:',e);}
}

function updateRefresh(){
  const s = Math.floor((Date.now()-lastUpdate)/1000);
  $('refresh').textContent = s<3?'just now':s+'s ago';
}

// Countdown ticker (updates every second without refetch)
function tickCountdown(){
  document.querySelectorAll('.countdown').forEach(cd => {
    if(cd.classList.contains('green')) return;
    const txt = cd.textContent;
    const m = txt.match(/(\\d+):(\\d+)/);
    if(!m) return;
    let secs = parseInt(m[1])*60 + parseInt(m[2]) - 1;
    if(secs <= 0){ cd.textContent = 'SETTLING'; cd.classList.add('green'); return; }
    cd.textContent = Math.floor(secs/60)+':'+String(secs%60).padStart(2,'0');
  });
}

initChart();
fetchAll();
setInterval(fetchAll, 5000);
setInterval(updateRefresh, 1000);
setInterval(tickCountdown, 1000);
</script>
</body>
</html>"""
