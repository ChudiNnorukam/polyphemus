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
        self._dashboard_build_id = f"{int(os.path.getmtime(__file__))}-{os.getpid()}"

    def _get_accumulator_targets(self) -> tuple[list[str], list[str]]:
        assets = [a.strip().upper() for a in self._config.accum_assets.split(",") if a.strip()]
        windows = [w.strip() for w in self._config.accum_window_types.split(",") if w.strip()]
        return assets, windows

    def _use_accumulator_pipeline_summary(self) -> bool:
        if not self._accumulator_engine or not bool(getattr(self._config, "enable_accumulator", False)):
            return False
        assets, windows = self._get_accumulator_targets()
        return bool(assets) and (assets != ["BTC"] or windows != ["5m"])

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
        accum_stats = self._accumulator_engine.stats if self._accumulator_engine else {}
        data = {
            "status": "running",
            "state": "running",
            "uptime_hours": round(uptime_h, 2),
            "errors": self._health._error_count,
            "binance_connected": (
                (self._binance_feed is not None and not self._binance_feed.circuit_open)
                or (self._momentum_feed is not None and getattr(self._momentum_feed, '_consecutive_failures', 0) == 0)
            ),
            "dry_run": bool(self._dry_run),
            "enable_accumulator": bool(self._accumulator_engine is not None),
            "accum_dry_run": bool(self._config.accum_dry_run),
            "effective_accumulator_dry_run": bool(
                accum_stats.get("effective_accumulator_dry_run", self._dry_run)
            ),
            "accum_mode_enabled": bool(getattr(self._config, "accum_mode_enabled", False)),
            "accumulator_state": accum_stats.get("state", "disabled"),
            "accumulator_assets": accum_stats.get("assets", []),
            "accumulator_window_types": accum_stats.get("window_types", []),
            "accumulator_active_positions": accum_stats.get("active_positions", 0),
            "accumulator_circuit_tripped": bool(accum_stats.get("circuit_tripped", False)),
            "accumulator_entry_mode": accum_stats.get("entry_mode", "unknown"),
            "accumulator_daily_loss_limit": accum_stats.get("daily_loss_limit", 0.0),
            "accumulator_total_pnl": accum_stats.get("total_pnl", 0.0),
            "open_positions": self._store.count_open(),
            "balance": round(self._balance._cached_balance, 2),
            "timestamp": time.time(),
            "dashboard_build_id": self._dashboard_build_id,
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
                if m:
                    readings[asset] = {
                        "direction": m.direction,
                        "momentum_pct": round(m.momentum_pct * 100, 4),
                        "confidence": round(m.confidence, 3),
                        "age_secs": round(m.age_secs, 1),
                    }
                else:
                    readings[asset] = {"direction": "UNKNOWN", "momentum_pct": 0, "confidence": 0, "age_secs": 0}
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
        payload = {"enabled": True, **self._accumulator_engine.stats}
        payload["settlements"] = self._get_recent_accumulator_settlements(
            runtime_stats=payload,
            limit=20,
        )
        return web.json_response(payload)

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
                    {expr("evidence_match_level", "''")},
                    {expr("shadow_current_guarded", "0")},
                    {expr("shadow_ensemble_candidate", "0")},
                    {expr("shadow_ensemble_selected", "0")},
                    {expr("shadow_ensemble_score", "0")},
                    {expr("shadow_ensemble_reason", "''")},
                    {expr("config_era", "''")}
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

    def _normalize_accumulator_settlement(self, settlement: dict) -> dict | None:
        """Normalize runtime settlement rows into a stable dashboard shape."""
        if not settlement:
            return None
        slug = settlement.get("slug")
        timestamp = settlement.get("timestamp")
        if not slug or timestamp is None:
            return None
        try:
            pnl = float(settlement.get("pnl", 0.0) or 0.0)
        except Exception:
            pnl = 0.0
        return {
            "slug": slug,
            "exit_reason": settlement.get("exit_reason", "unknown"),
            "matched": float(settlement.get("matched", 0.0) or 0.0),
            "up_qty": float(settlement.get("up_qty", 0.0) or 0.0),
            "down_qty": float(settlement.get("down_qty", 0.0) or 0.0),
            "up_avg": float(settlement.get("up_avg", 0.0) or 0.0),
            "down_avg": float(settlement.get("down_avg", 0.0) or 0.0),
            "pair_cost": float(settlement.get("pair_cost", 0.0) or 0.0),
            "pnl": pnl,
            "timestamp": float(timestamp),
        }

    def _trade_to_accumulator_settlement(self, trade: dict) -> dict | None:
        """Project a closed accumulator trade row into dashboard settlement shape."""
        if not trade or trade.get("exit_time") is None:
            return None

        slug = trade.get("slug") or ""
        if "-updown-" not in slug:
            return None

        exit_reason = str(trade.get("exit_reason") or "")
        strategy = str(trade.get("strategy") or "")
        allowed_reasons = {
            "hedged_settlement",
            "orphaned_settlement",
            "held_to_settlement",
            "sellback",
            "unwound",
            "forced_hold_clob_unindexed",
            "forced_hold_sell_failed",
        }
        if strategy != "pair_arb" and exit_reason not in allowed_reasons:
            return None

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
        try:
            pnl_value = float(pnl or 0.0)
        except Exception:
            pnl_value = 0.0

        entry_size = float(trade.get("entry_size", 0.0) or 0.0)
        up_qty = float(metadata.get("up_qty", 0.0) or 0.0)
        down_qty = float(metadata.get("down_qty", 0.0) or 0.0)
        if not up_qty and not down_qty and entry_size > 0:
            if (trade.get("outcome") or "").upper() == "PAIR":
                up_qty = entry_size
                down_qty = entry_size
            else:
                up_qty = entry_size

        return {
            "slug": slug,
            "exit_reason": exit_reason or "unknown",
            "matched": entry_size if (trade.get("outcome") or "").upper() == "PAIR" else 0.0,
            "up_qty": up_qty,
            "down_qty": down_qty,
            "up_avg": float(metadata.get("up_price", 0.0) or 0.0),
            "down_avg": float(metadata.get("down_price", 0.0) or 0.0),
            "pair_cost": float(
                metadata.get("pair_cost", trade.get("entry_price", 0.0)) or 0.0
            ),
            "pnl": pnl_value,
            "timestamp": float(trade.get("exit_time") or trade.get("entry_time") or 0.0),
        }

    def _get_recent_accumulator_settlements(
        self,
        runtime_stats: dict | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Return recent accumulator settlements, rehydrating from performance.db after restarts."""
        runtime_rows = []
        if runtime_stats is not None:
            runtime_rows = list(runtime_stats.get("settlements") or [])
        elif self._accumulator_engine:
            runtime_rows = list(getattr(self._accumulator_engine, "_settlements", []) or [])

        settlements: list[dict] = []
        seen: set[tuple[str, str, int, int]] = set()

        def add_row(row: dict | None) -> None:
            normalized = self._normalize_accumulator_settlement(row) if row else None
            if not normalized:
                return
            key = (
                normalized["slug"],
                normalized["exit_reason"],
                int(normalized["timestamp"]),
                int(round(normalized["pnl"] * 10000)),
            )
            if key in seen:
                return
            seen.add(key)
            settlements.append(normalized)

        for row in runtime_rows:
            add_row(row)

        need_db_backfill = (
            len(settlements) < limit
            and self._perf_db is not None
            and hasattr(self._perf_db, "get_recent_trades")
        )
        if need_db_backfill:
            for trade in self._perf_db.get_recent_trades(limit=max(limit * 5, 50)):
                add_row(self._trade_to_accumulator_settlement(trade))
                if len(settlements) >= limit:
                    break

        settlements.sort(key=lambda row: float(row.get("timestamp", 0.0)))
        return settlements[-limit:]

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
                if "shadow_ensemble_selected" in signal_columns:
                    counts[f"ensemble_selected_{label}"] = int(query_scalar(
                        sig_conn,
                        """
                        SELECT COUNT(*)
                        FROM signals
                        WHERE asset = 'BTC' AND market_window_secs = 300
                          AND epoch >= ?
                          AND COALESCE(shadow_ensemble_selected, 0) = 1
                        """,
                        (cutoff,),
                    ) or 0)
                else:
                    counts[f"ensemble_selected_{label}"] = 0
                if "shadow_current_guarded" in signal_columns:
                    counts[f"current_guarded_{label}"] = int(query_scalar(
                        sig_conn,
                        """
                        SELECT COUNT(*)
                        FROM signals
                        WHERE asset = 'BTC' AND market_window_secs = 300
                          AND epoch >= ?
                          AND COALESCE(shadow_current_guarded, 0) = 1
                        """,
                        (cutoff,),
                    ) or 0)
                else:
                    counts[f"current_guarded_{label}"] = 0

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
            accum_stats = self._accumulator_engine.stats if self._accumulator_engine else {}
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
            if accum_stats.get("circuit_tripped", False):
                stage = "circuit_breaker"
                headline = "Accumulator circuit breaker is active"
                summary = (
                    f"New entries are halted because realized accumulator P&L is "
                    f"${float(accum_stats.get('total_pnl', 0.0)):.2f} versus the daily loss limit "
                    f"${float(accum_stats.get('daily_loss_limit', 0.0)):.2f}. "
                    "Trade silence is expected until operator review and reset."
                )
            elif self._use_accumulator_pipeline_summary():
                assets, windows_cfg = self._get_accumulator_targets()
                target_label = f"{'/'.join(assets)} {'/'.join(windows_cfg)}".strip()
                active_positions = int(accum_stats.get("active_positions", 0) or 0)
                scan_count = int(accum_stats.get("scan_count", 0) or 0)
                candidates_seen = int(accum_stats.get("candidates_seen", 0) or 0)
                last_block = str(accum_stats.get("last_eval_block_reason", "") or "").strip()
                if active_positions > 0:
                    stage = "accumulating"
                    headline = f"Accumulator active on {target_label}"
                    summary = (
                        f"{active_positions} active paired position(s); "
                        f"entry_mode={accum_stats.get('entry_mode', 'unknown')}."
                    )
                elif scan_count == 0:
                    stage = "starting"
                    headline = f"Accumulator starting on {target_label}"
                    summary = "No shadow scans recorded yet."
                else:
                    stage = "accumulator_scanning"
                    headline = f"Accumulator scanning {target_label} markets"
                    summary = (
                        f"scan_count={scan_count}, candidates_seen={candidates_seen}, "
                        f"hedged={int(accum_stats.get('hedged_count', 0) or 0)}, "
                        f"unwound={int(accum_stats.get('unwound_count', 0) or 0)}."
                    )
                    if last_block:
                        summary = f"{summary} Last eval block: {last_block}."
            elif counts["decisions_15m"] == 0:
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
                "accumulator_circuit_tripped": bool(accum_stats.get("circuit_tripped", False)),
                "accumulator_total_pnl": float(accum_stats.get("total_pnl", 0.0)),
                "accumulator_daily_loss_limit": float(accum_stats.get("daily_loss_limit", 0.0)),
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
        html = DASHBOARD_HTML.replace("__DASHBOARD_BUILD_ID__", self._dashboard_build_id)
        return web.Response(text=html, content_type="text/html")


# ── Embedded HTML Dashboard ──────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Polyphemus Shadow Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;width:100%;overflow-x:hidden}
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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;line-height:1.5;padding-bottom:40px}
a{color:var(--blue);text-decoration:none}
.wrap{max-width:1400px;margin:0 auto;padding:12px 16px}

header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--border);padding:10px 0 12px;margin-bottom:16px;z-index:100;display:flex;justify-content:space-between;align-items:center;gap:12px}
.header-left{display:flex;align-items:center;gap:8px}
header h1{font-size:14px;font-weight:700;color:var(--bright)}
.badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-family:var(--mono);padding:2px 8px;border:1px solid var(--border);border-radius:12px;color:var(--dim)}
.badge .dot{width:5px;height:5px;border-radius:50%;display:inline-block}
.badge .dot.on{background:var(--green)}
.badge .dot.off{background:var(--red)}
.header-right{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--dim)}
.theme-btn{background:none;border:1px solid var(--border);border-radius:4px;padding:2px 8px;cursor:pointer;font-size:10px;color:var(--dim);min-height:28px}
.theme-btn:hover{color:var(--bright)}

.grid{display:grid;gap:12px;margin-bottom:12px}
.g1{grid-template-columns:1fr}
.g2{grid-template-columns:1fr 1fr}
.g3{grid-template-columns:1fr 1fr 1fr}
.g4{grid-template-columns:repeat(4,1fr)}
@media(max-width:1200px){.g4{grid-template-columns:repeat(2,1fr)}.g3{grid-template-columns:1fr 1fr}}
@media(max-width:768px){.g2,.g3,.g4{grid-template-columns:1fr}}

.card{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:12px}
.card-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--dim);margin-bottom:10px}
.card-sub{font-size:9px;color:var(--dim);margin-bottom:4px}

.hero-metric{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.hero-item{flex:1;text-align:center;padding:8px}
.hero-val{font-family:var(--mono);font-size:20px;font-weight:700;line-height:1;margin-bottom:2px}
.hero-val.green{color:var(--green)}
.hero-val.red{color:var(--red)}
.hero-lbl{font-size:9px;color:var(--dim);text-transform:uppercase;font-weight:600}

.pos-card{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:10px}
.pos-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:6px}
.pos-market{font-family:var(--mono);font-size:11px;font-weight:600;color:var(--bright)}
.countdown{font-family:var(--mono);font-size:16px;font-weight:700;color:var(--amber);min-width:45px;text-align:right}
.countdown.settling{color:var(--green)}
.pos-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:10px}
.pos-stat{display:flex;justify-content:space-between}
.pos-stat-lbl{color:var(--dim)}
.pos-stat-val{font-family:var(--mono);font-weight:600;color:var(--bright)}

.chart-wrap{position:relative;height:120px;margin-bottom:6px}

table{width:100%;border-collapse:collapse;font-size:10px}
th{text-align:left;padding:6px 4px;color:var(--dim);font-size:9px;text-transform:uppercase;border-bottom:1px solid var(--border);font-weight:700}
td{padding:6px 4px;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:10px;color:var(--text)}
tr:hover td{background:var(--hover)}

.tag{display:inline-block;padding:1px 6px;border-radius:8px;font-size:8px;font-weight:600;text-transform:uppercase}
.tag.hedged{background:#3fb95018;color:var(--green)}
.tag.orphan{background:#f8514918;color:var(--red)}
.tag.unwound{background:#d2992218;color:var(--amber)}
.tag.allow{background:#3fb95018;color:var(--green)}
.tag.block{background:#f8514918;color:var(--red)}
.tag.open{background:#58a6ff20;color:var(--blue)}

.row{display:flex;justify-content:space-between;align-items:baseline;padding:4px 0;font-size:10px}
.row .lbl{color:var(--dim)}
.row .val{font-family:var(--mono);font-weight:600;color:var(--bright)}

.kv{display:grid;gap:6px;font-size:10px}
.kv>div{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.kv .label{color:var(--dim)}
.kv .val{font-family:var(--mono);font-weight:600;color:var(--bright)}

.empty{text-align:center;padding:20px;color:var(--dim);font-size:10px}

.momentum-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(60px,1fr));gap:6px}
.m-item{text-align:center;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px}
.m-asset{font-size:8px;color:var(--dim);font-weight:700;margin-bottom:2px}
.m-dir{font-family:var(--mono);font-size:14px;font-weight:700;color:var(--bright)}
.m-dir.UP{color:var(--green)}
.m-dir.DOWN{color:var(--red)}
.m-pct{font-family:var(--mono);font-size:8px;color:var(--dim);margin-top:2px}

.analytics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:12px}
.stat-box{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;text-align:center}
.stat-box-val{font-family:var(--mono);font-size:18px;font-weight:700;line-height:1;margin-bottom:4px}
.stat-box-lbl{font-size:8px;color:var(--dim);text-transform:uppercase;font-weight:600}

.collapsible{cursor:pointer;user-select:none;padding:8px;background:var(--hover);border:1px solid var(--border);border-radius:4px;margin-bottom:8px}
.collapsible::before{content:'▼';display:inline-block;margin-right:6px;transition:transform 0.2s}
.collapsible.closed::before{transform:rotate(-90deg)}
.collapsible-content{display:none}
.collapsible.closed .collapsible-content{display:none}
.collapsible:not(.closed) .collapsible-content{display:block}

.green{color:var(--green)!important}
.red{color:var(--red)!important}
.blue{color:var(--blue)!important}
.amber{color:var(--amber)!important}
.dim{color:var(--dim)!important}

footer{text-align:center;padding:8px;color:var(--dim);font-size:8px;margin-top:24px;border-top:1px solid var(--border)}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="header-left">
    <h1>Polyphemus</h1>
    <span class="badge"><span class="dot on" id="dot"></span><span id="status-text">...</span></span>
  </div>
  <div class="header-right">
    <span id="refresh">--</span>
    <button class="theme-btn" onclick="toggleTheme()">Light</button>
  </div>
</header>

<!-- Hero metrics row -->
<div class="grid g4">
  <div class="card">
    <div class="card-title">Balance</div>
    <div class="hero-metric">
      <div class="hero-item">
        <div class="hero-val" id="balance">--</div>
        <div class="hero-lbl">Wallet</div>
      </div>
    </div>
    <div class="row" style="font-size:9px"><span class="lbl">Deployed</span><span class="val blue" id="deployed">--</span></div>
    <div class="row" style="font-size:9px"><span class="lbl">Available</span><span class="val" id="available">--</span></div>
  </div>

  <div class="card">
    <div class="card-title">Session P&L</div>
    <div class="hero-val" id="accum-pnl" style="margin-bottom:8px">--</div>
    <div class="row" style="font-size:9px"><span class="lbl">Win Rate</span><span class="val" id="win-rate">--</span></div>
    <div class="row" style="font-size:9px"><span class="lbl">Hedge Rate</span><span class="val blue" id="hedge-rate">--</span></div>
  </div>

  <div class="card">
    <div class="card-title">Cycles/Hour</div>
    <div class="hero-val" id="cycles-per-hour" style="margin-bottom:8px">--</div>
    <div class="row" style="font-size:9px"><span class="lbl">Completed</span><span class="val" id="completed-cycles">--</span></div>
    <div class="row" style="font-size:9px"><span class="lbl">Hedged</span><span class="val blue" id="hedged-cycles">--</span></div>
  </div>

  <div class="card">
    <div class="card-title">System</div>
    <div class="row" style="font-size:9px"><span class="lbl">Status</span><span class="val" id="system-status">--</span></div>
    <div class="row" style="font-size:9px"><span class="lbl">Uptime</span><span class="val" id="uptime">--</span></div>
    <div class="row" style="font-size:9px"><span class="lbl">Errors</span><span class="val" id="errors">0</span></div>
    <div class="row" style="font-size:9px"><span class="lbl">Binance</span><span class="val" id="binance-status">--</span></div>
  </div>
</div>

<!-- Active positions -->
<div class="grid g1">
  <div class="card">
    <div class="card-title">Active Positions</div>
    <div id="positions-container"><p class="empty">Scanning...</p></div>
  </div>
</div>

<!-- Live activity -->
<div class="analytics-grid">
  <div class="stat-box">
    <div class="stat-box-val" id="scan-count">--</div>
    <div class="stat-box-lbl">Scan Count</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="candidates-seen">--</div>
    <div class="stat-box-lbl">Candidates Seen</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="last-candidate-time">--</div>
    <div class="stat-box-lbl">Last Candidate</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="last-block-short">--</div>
    <div class="stat-box-lbl">Last Block</div>
  </div>
</div>

<!-- Cumulative P&L chart -->
<div class="grid g1">
  <div class="card">
    <div class="card-title">Cumulative P&L</div>
    <div class="chart-wrap"><canvas id="pnl-chart"></canvas></div>
  </div>
</div>

<!-- Walk-forward analytics -->
<div class="analytics-grid">
  <div class="stat-box">
    <div class="stat-box-val" id="hedge-rate-5m">--</div>
    <div class="stat-box-lbl">Hedge Rate 5m</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="hedge-rate-15m">--</div>
    <div class="stat-box-lbl">Hedge Rate 15m</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="rolling-wr">--</div>
    <div class="stat-box-lbl">Rolling WR (20)</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="rolling-pnl">--</div>
    <div class="stat-box-lbl">Avg PnL (20)</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="avg-fill-time">--</div>
    <div class="stat-box-lbl">Avg Fill Time</div>
  </div>
  <div class="stat-box">
    <div class="stat-box-val" id="sellback-rate">--</div>
    <div class="stat-box-lbl">Sellback %</div>
  </div>
</div>

<!-- P&L by hour -->
<div class="grid g1">
  <div class="card">
    <div class="card-title">P&L by Hour (UTC)</div>
    <table id="hourly-table">
      <tr><th>Hour</th><th>Trades</th><th>WR</th><th>P&L</th></tr>
    </table>
  </div>
</div>

<!-- Settlement history -->
<div class="grid g1">
  <div class="card">
    <div class="card-title">Settlement History</div>
    <table id="settlements-table">
      <tr><th>Time</th><th>Asset</th><th>Window</th><th>Type</th><th>P&L</th></tr>
    </table>
  </div>
</div>

<div class="grid g2">
  <div class="card">
    <div class="card-title">Recent Backend Alerts</div>
    <table id="alerts-table">
      <tr><th>When</th><th>Message</th></tr>
    </table>
  </div>
  <div class="card">
    <div class="card-title">Recent Closed Trades</div>
    <table id="recent-trades-table">
      <tr><th>Time</th><th>Asset</th><th>Window</th><th>Reason</th><th>P&L</th></tr>
    </table>
  </div>
</div>

<!-- Momentum + Advanced sections -->
<div class="grid g2">
  <div class="card">
    <div class="card-title">Momentum Feed</div>
    <div class="momentum-grid" id="momentum-grid"></div>
  </div>
  <div class="card">
    <div class="card-title">Runtime Detail</div>
    <div class="collapsible closed" id="advanced-toggle">
      <span style="font-size:11px;font-weight:600">Pipeline, runtime, and guard detail</span>
      <div class="collapsible-content" style="padding-top:10px;font-size:10px">
        <div id="advanced-content"><p class="empty">Loading...</p></div>
      </div>
    </div>
  </div>
</div>

<footer>Polyphemus Shadow Dashboard · build <span id="dashboard-build">__DASHBOARD_BUILD_ID__</span></footer>
</div>

<script>
const $ = id => document.getElementById(id);
const fmt = (n,d=2) => n != null ? Number(n).toFixed(d) : '--';
const pnlSign = n => n > 0 ? '+$'+fmt(n) : n < 0 ? '-$'+fmt(Math.abs(n)) : '$0.00';
const CURRENT_DASHBOARD_BUILD_ID = "__DASHBOARD_BUILD_ID__";
let lastUpdate = Date.now();
let pnlChart = null;

function slugEpoch(slug){const m=slug.match(/(\\d{10})$/);return m?parseInt(m[1]):0}
function slugTime(slug){const ep=slugEpoch(slug);if(!ep)return slug;const d=new Date(ep*1000);return d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true,timeZone:'UTC'})}
function slugAsset(slug){const m=slug.match(/^([a-z]+)-/);return m?m[1].toUpperCase():'--'}
function slugWindow(slug){const m=slug.match(/(5m|15m|1h)/);return m?m[1]:'--'}
function fmtTimestamp(ts){if(!ts)return '--';const d=new Date(ts*1000);return d.toLocaleString('en-US',{timeZone:'UTC',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',hour12:true})}
function fmtAgo(ts){if(!ts)return 'never';const secs=Math.max(0,Math.floor(Date.now()/1000-ts));if(secs<60)return secs+'s';if(secs<3600)return(secs/60).toFixed(0)+'m';return(secs/3600).toFixed(0)+'h'}
function escapeHtml(str){return String(str||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}
function cycleType(reason){
  const r=(reason||'').toLowerCase();
  if(r.includes('hedged'))return 'HEDGED';
  if(r.includes('sellback')||r.includes('unwound'))return 'SELLBACK';
  if(r.includes('orphan')||r.includes('held_to_settlement')||r.includes('forced_hold'))return 'ORPHAN';
  return 'UNKNOWN';
}
function normalizeTradeToSettlement(trade){
  if(!trade||trade.open||!trade.slug)return null;
  const reason=trade.exit_reason||'';
  const allowed=['hedged_settlement','orphaned_settlement','held_to_settlement','sellback','unwound','forced_hold_clob_unindexed','forced_hold_sell_failed'];
  if(!allowed.includes(reason))return null;
  return {
    slug:trade.slug,
    exit_reason:reason,
    matched:0,
    up_qty:0,
    down_qty:0,
    up_avg:0,
    down_avg:0,
    pair_cost:Number(trade.entry_price||0),
    pnl:Number(trade.pnl||0),
    timestamp:Number(trade.exit_time||trade.entry_time||0),
  };
}
function settlementsFromTrades(trades){
  return (trades||[]).map(normalizeTradeToSettlement).filter(Boolean).sort((a,b)=>(a.timestamp||0)-(b.timestamp||0));
}

function toggleTheme(){
  const el=document.documentElement;
  el.classList.toggle('light');
  el.classList.toggle('dark');
  localStorage.setItem('lagbot-theme',el.classList.contains('light')?'light':'dark');
}
(()=>{const s=localStorage.getItem('lagbot-theme');if(s==='light'){document.documentElement.classList.add('light');document.documentElement.classList.remove('dark')}})();

function getC(){const s=getComputedStyle(document.documentElement);return{green:s.getPropertyValue('--green').trim(),red:s.getPropertyValue('--red').trim(),blue:s.getPropertyValue('--blue').trim(),dim:s.getPropertyValue('--dim').trim(),border:s.getPropertyValue('--border').trim()}}

function initChart(){const c=getC();pnlChart=new Chart($('pnl-chart').getContext('2d'),{type:'line',data:{labels:[],datasets:[{data:[],borderColor:c.green,backgroundColor:c.green+'18',fill:true,tension:0.3,pointRadius:2,pointHoverRadius:4,borderWidth:2}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{grid:{color:c.border+'60'},ticks:{color:c.dim,font:{size:9,family:'var(--mono)'},callback:v=>'$'+v}}}}});}

async function fetchAll(){
  try{
    const[status,balance,momentum,accum,gabagool,tuning,signals,evidence,trades,filters,errors,pipeline]=await Promise.all([
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

    const setts=((accum.settlements&&accum.settlements.length)?accum.settlements:settlementsFromTrades(trades.trades||[]));
    const hedgedCnt=Number(accum.hedged_count||0)||setts.filter(s=>(s.exit_reason||'').includes('hedged')).length;
    const orphanedCnt=Number(accum.orphaned_count||0)||setts.filter(s=>cycleType(s.exit_reason)==='ORPHAN').length;
    const unwoundCnt=Number(accum.unwound_count||0)||setts.filter(s=>cycleType(s.exit_reason)==='SELLBACK').length;
    const totalSettled=(hedgedCnt+orphanedCnt+unwoundCnt)||setts.length;
    const winCnt=setts.filter(s=>s.pnl>0).length;
    const apnl=accum.total_pnl||0;

    const accumTripped=!!status.accumulator_circuit_tripped;
    const effectiveDryRun=!!(status.effective_accumulator_dry_run ?? status.dry_run);
    const serverBuildId=status.dashboard_build_id||CURRENT_DASHBOARD_BUILD_ID;
    if(serverBuildId!==CURRENT_DASHBOARD_BUILD_ID){
      window.location.reload();
      return;
    }
    const systemState=accumTripped?'HALTED':(status.errors>5?'ERROR':(effectiveDryRun?'SHADOW':'LIVE'));
    $('status-text').textContent=systemState;
    $('dot').className='dot '+((status.errors>5||accumTripped)?'off':'on');
    $('balance').textContent='$'+fmt(balance.balance||0,0);
    $('deployed').textContent='$'+fmt(balance.deployed||0,0);
    $('available').textContent='$'+fmt(balance.available||0,0);

    const apnlEl=$('accum-pnl');
    apnlEl.textContent=pnlSign(apnl);
    apnlEl.className='hero-val '+(apnl>=0?'green':'red');

    const wr=totalSettled>0?(winCnt/totalSettled*100).toFixed(0)+'%':'--';
    $('win-rate').textContent=wr;
    $('win-rate').className='val '+(winCnt/Math.max(totalSettled,1)>=0.5?'green':'red');

    const hedgeRate=totalSettled>0?(hedgedCnt/totalSettled*100).toFixed(0)+'%':'--';
    $('hedge-rate').textContent=hedgeRate;
    $('hedge-rate').className='val blue';

    const hrs=status.uptime_hours||0;
    $('cycles-per-hour').textContent=totalSettled>0?(totalSettled/Math.max(hrs||1,0.25)).toFixed(2):'0.00';
    $('completed-cycles').textContent=String(totalSettled);
    $('hedged-cycles').textContent=String(hedgedCnt);
    $('uptime').textContent=hrs>=24?fmt(hrs/24,1)+'d':fmt(hrs,1)+'h';
    $('errors').textContent=status.errors||0;
    $('errors').className='val '+(status.errors>0?'red':'green');
    $('binance-status').textContent=status.binance_connected?'OK':'OFFLINE';
    $('binance-status').className='val '+(status.binance_connected?'green':'red');
    $('system-status').textContent=systemState;
    $('system-status').className='val '+(accumTripped?'amber':(status.errors>5?'red':'green'));
    $('dashboard-build').textContent=serverBuildId;

    // Positions
    const positions=accum.positions||[];
    const posCont=$('positions-container');
    if(positions.length>0){
      let posHtml='';
      for(const p of positions){
        const ep=slugEpoch(p.slug);
        let cntdn='--';
        if(ep>0){
          const secs=Math.max(0,ep+300-Math.floor(Date.now()/1000));
          if(secs>0)cntdn=Math.floor(secs/60)+':'+ String(secs%60).padStart(2,'0');
          else cntdn='SETTLING';
        }
        const profit=p.pair_cost>0?(1.0-p.pair_cost):0;
        posHtml+='<div class="pos-card"><div class="pos-header">'
          +'<span class="pos-market">'+slugTime(p.slug)+' '+slugAsset(p.slug)+'</span>'
          +'<span class="countdown '+(cntdn==='SETTLING'?'settling':'')+'">'+cntdn+'</span></div>'
          +'<div class="pos-stats">'
          +'<div class="pos-stat"><span class="pos-stat-lbl">UP/DOWN:</span><span class="pos-stat-val">'+fmt(p.up_qty,0)+'/'+fmt(p.down_qty,0)+'</span></div>'
          +'<div class="pos-stat"><span class="pos-stat-lbl">Pair Cost:</span><span class="pos-stat-val">$'+fmt(p.pair_cost,3)+'</span></div>'
          +'<div class="pos-stat"><span class="pos-stat-lbl">Profit/sh:</span><span class="pos-stat-val '+(profit>0?'green':'red')+'">$'+fmt(profit,4)+'</span></div>'
          +'<div class="pos-stat"><span class="pos-stat-lbl">State:</span><span class="pos-stat-val">'+p.state+'</span></div>'
          +'</div></div>';
      }
      posCont.innerHTML=posHtml;
    }else{
      if(accum.circuit_tripped){
        posCont.innerHTML='<p class="empty amber">Accumulator halted by circuit breaker. Session P&L '
          +pnlSign(accum.total_pnl||0)+' is below the daily limit $'+fmt(accum.daily_loss_limit||0,2)
          +'. No new entries are expected until operator reset.</p>';
      }else{
        posCont.innerHTML='<p class="empty">Scanning for opportunities...</p>';
      }
    }

    // Live activity
    const lastCandidateSlug=accum.last_candidate_slug||'';
    const lastCandidateTime=lastCandidateSlug?slugTime(lastCandidateSlug)+' '+slugAsset(lastCandidateSlug)+' '+slugWindow(lastCandidateSlug):'--';
    const lastBlock=(accum.last_eval_block_reason||'').trim();
    const lastBlockShort=lastBlock?(lastBlock.length>36?lastBlock.slice(0,36)+'…':lastBlock):'clear';
    $('scan-count').textContent=String(accum.scan_count||0);
    $('candidates-seen').textContent=String(accum.candidates_seen||0);
    $('last-candidate-time').textContent=lastCandidateTime;
    $('last-candidate-time').className='stat-box-val '+(lastCandidateSlug?'blue':'dim');
    $('last-block-short').textContent=lastBlockShort;
    $('last-block-short').className='stat-box-val '+(lastBlock?'amber':'green');

    // Chart
    if(pnlChart&&setts.length){
      let cum=0;const labels=[],data=[];
      for(const s of setts){cum+=s.pnl||0;labels.push(slugTime(s.slug));data.push(+cum.toFixed(2));}
      pnlChart.data.labels=labels;
      pnlChart.data.datasets[0].data=data;
      const c=getC();
      pnlChart.data.datasets[0].borderColor=cum>=0?c.green:c.red;
      pnlChart.data.datasets[0].backgroundColor=(cum>=0?c.green:c.red)+'18';
      pnlChart.update('none');
    }else if(pnlChart){
      pnlChart.data.labels=[];
      pnlChart.data.datasets[0].data=[];
      pnlChart.update('none');
    }

    // Analytics: hedge rate by window, rolling metrics, etc
    const setts5m=setts.filter(s=>slugWindow(s.slug)==='5m');
    const setts15m=setts.filter(s=>slugWindow(s.slug)==='15m');
    const hedged5m=setts5m.filter(s=>(s.exit_reason||'').includes('hedged')).length;
    const hedged15m=setts15m.filter(s=>(s.exit_reason||'').includes('hedged')).length;

    $('hedge-rate-5m').textContent=setts5m.length>0?(hedged5m/setts5m.length*100).toFixed(0)+'%':'--';
    $('hedge-rate-15m').textContent=setts15m.length>0?(hedged15m/setts15m.length*100).toFixed(0)+'%':'--';

    // Rolling 20
    const last20=setts.slice(-20);
    if(last20.length>0){
      const rollWin=last20.filter(s=>s.pnl>0).length;
      const rollPnl=last20.reduce((a,s)=>a+(s.pnl||0),0);
      $('rolling-wr').textContent=(rollWin/last20.length*100).toFixed(0)+'%';
      $('rolling-wr').className='stat-box-val '+(rollWin/last20.length>=0.5?'green':'red');
      $('rolling-pnl').textContent=pnlSign(rollPnl/last20.length);
      $('rolling-pnl').className='stat-box-val '+(rollPnl>=0?'green':'red');
    }

    // Avg fill time (rough: time from settlement to next settle if hedged)
    let totalFillTime=0,fillCount=0;
    for(let i=0;i<setts.length-1;i++){
      if((setts[i].exit_reason||'').includes('hedged')){
        const diff=(setts[i+1].timestamp||setts[i].timestamp)-(setts[i].timestamp||0);
        if(diff>0){totalFillTime+=diff;fillCount++}
      }
    }
    $('avg-fill-time').textContent=fillCount>0?fmt(totalFillTime/fillCount,0)+'s':'--';

    // Sellback rate
    const sellbacks=unwoundCnt;
    const sellbackRate=totalSettled>0?(sellbacks/totalSettled*100).toFixed(0)+'%':'--';
    $('sellback-rate').textContent=sellbackRate;

    // Hourly P&L table
    const hourlyBuckets={};
    for(const s of setts){
      const epoch=slugEpoch(s.slug);
      if(epoch){
        const d=new Date(epoch*1000);
        const hour=d.getUTCHours();
        if(!hourlyBuckets[hour])hourlyBuckets[hour]={trades:0,pnl:0,wins:0};
        hourlyBuckets[hour].trades++;
        hourlyBuckets[hour].pnl+=s.pnl||0;
        if(s.pnl>0)hourlyBuckets[hour].wins++;
      }
    }
    let hourlyHtml='<tr><th>Hour (UTC)</th><th>Trades</th><th>WR</th><th>P&L</th></tr>';
    let renderedHourly=false;
    for(let h=0;h<24;h++){
      if(hourlyBuckets[h]){
        renderedHourly=true;
        const hb=hourlyBuckets[h];
        const wr=(hb.wins/hb.trades*100).toFixed(0);
        hourlyHtml+='<tr><td>'+String(h).padStart(2,'0')+':00</td><td>'+hb.trades+'</td><td class="'+(wr>=50?'green':'red')+'">'+wr+'%</td><td class="'+(hb.pnl>=0?'green':'red')+'">'+pnlSign(hb.pnl)+'</td></tr>';
      }
    }
    if(!renderedHourly){
      hourlyHtml+='<tr><td colspan="4" class="dim">No completed cycles yet</td></tr>';
    }
    $('hourly-table').innerHTML=hourlyHtml;

    // Settlement table
    let settHtml='<tr><th>Time</th><th>Asset</th><th>Window</th><th>Type</th><th>P&L</th></tr>';
    if(!setts.length){
      settHtml+='<tr><td colspan="5" class="dim">No settlements loaded</td></tr>';
    }else{
      for(const s of setts.slice(-15).reverse()){
        const type=cycleType(s.exit_reason);
        settHtml+='<tr><td>'+fmtTimestamp(s.timestamp)+'</td><td>'+slugAsset(s.slug)+'</td><td>'+slugWindow(s.slug)+'</td>'
          +'<td>'+type+'</td><td class="'+(s.pnl>=0?'green':'red')+'">'+pnlSign(s.pnl)+'</td></tr>';
      }
    }
    $('settlements-table').innerHTML=settHtml;

    let alertHtml='<tr><th>When</th><th>Message</th></tr>';
    const alertRows=errors.errors||[];
    if(!alertRows.length){
      alertHtml+='<tr><td colspan="2" class="dim">No recent backend alerts</td></tr>';
    }else{
      for(const line of alertRows.slice().reverse()){
        const split=line.split(' polyphemus.');
        const when=split[0]||'recent';
        const message=split.length>1?split.slice(1).join(' polyphemus.'):line;
        alertHtml+='<tr><td>'+escapeHtml(when)+'</td><td style="white-space:normal;word-break:break-word">'+escapeHtml(message)+'</td></tr>';
      }
    }
    $('alerts-table').innerHTML=alertHtml;

    let tradeHtml='<tr><th>Time</th><th>Asset</th><th>Window</th><th>Reason</th><th>P&L</th></tr>';
    const closedTrades=(trades.trades||[]).filter(t=>!t.open);
    if(!closedTrades.length){
      tradeHtml+='<tr><td colspan="5" class="dim">No recent closed trades</td></tr>';
    }else{
      for(const trade of closedTrades.slice(0,12)){
        tradeHtml+='<tr><td>'+fmtTimestamp(trade.exit_time||trade.entry_time)+'</td><td>'+slugAsset(trade.slug)+'</td><td>'+slugWindow(trade.slug)+'</td>'
          +'<td>'+escapeHtml(trade.exit_reason||'closed')+'</td><td class="'+((trade.pnl||0)>=0?'green':'red')+'">'+(trade.pnl!=null?pnlSign(trade.pnl):'--')+'</td></tr>';
      }
    }
    $('recent-trades-table').innerHTML=tradeHtml;

    // Momentum
    const mGrid=$('momentum-grid');
    mGrid.innerHTML='';
    const readings=momentum.readings||{};
    for(const asset of Object.keys(readings).sort()){
      const r=readings[asset];
      const arrow=r.direction==='UP'?'↑':r.direction==='DOWN'?'↓':'−';
      mGrid.innerHTML+='<div class="m-item"><div class="m-asset">'+asset+'</div><div class="m-dir '+r.direction+'">'+arrow+'</div><div class="m-pct">'+(r.momentum_pct>=0?'+':'')+fmt(r.momentum_pct,2)+'%</div></div>';
    }

    // Advanced section
    let advHtml='<div class="kv">'
      +'<div><span class="label">Hedged Count</span><span class="val green">'+hedgedCnt+'</span></div>'
      +'<div><span class="label">Orphaned Count</span><span class="val amber">'+orphanedCnt+'</span></div>'
      +'<div><span class="label">Sellback Count</span><span class="val red">'+unwoundCnt+'</span></div>'
      +'<div><span class="label">Total Settled</span><span class="val">'+totalSettled+'</span></div>'
      +'<div><span class="label">Circuit Breaker</span><span class="val '+(accum.circuit_tripped?'amber':'green')+'">'+(accum.circuit_tripped?'TRIPPED':'ARMED')+'</span></div>'
      +'<div><span class="label">Entry Mode</span><span class="val">'+(accum.entry_mode||'unknown').toUpperCase()+'</span></div>'
      +'<div><span class="label">Pipeline</span><span class="val '+(pipeline.stage==='circuit_breaker'?'amber':'')+'">'+(pipeline.headline||'--')+'</span></div>'
      +'<div><span class="label">Last Candidate Slug</span><span class="val">'+(lastCandidateSlug||'--')+'</span></div>'
      +'<div><span class="label">Last Eval Block</span><span class="val amber">'+(lastBlock||'clear')+'</span></div>'
      +'</div>';
    $('advanced-content').innerHTML=advHtml;

    lastUpdate=Date.now();
  }catch(e){console.error('Fetch error:',e)}
}

function updateRefresh(){
  const s=Math.floor((Date.now()-lastUpdate)/1000);
  $('refresh').textContent=s<3?'now':s+'s';
}

function tickCountdown(){
  document.querySelectorAll('.countdown').forEach(cd=>{
    if(cd.classList.contains('settling'))return;
    const txt=cd.textContent;
    const m=txt.match(/(\\d+):(\\d+)/);
    if(!m)return;
    let secs=parseInt(m[1])*60+parseInt(m[2])-1;
    if(secs<=0){cd.textContent='SETTLING';cd.classList.add('settling');return}
    cd.textContent=Math.floor(secs/60)+':'+String(secs%60).padStart(2,'0');
  });
}

document.addEventListener('click',e=>{
  if(e.target.id==='advanced-toggle'||e.target.closest('#advanced-toggle')){
    $('advanced-toggle').classList.toggle('closed');
  }
});

initChart();
fetchAll();
setInterval(fetchAll,5000);
setInterval(updateRefresh,1000);
setInterval(tickCountdown,1000);
</script>
</body>
</html>"""
