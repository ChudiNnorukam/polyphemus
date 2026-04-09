"""Operator tooling tests for truthful accumulator diagnostics."""

import asyncio
import json
import sqlite3
import sys
import types
from types import SimpleNamespace

from aiohttp.test_utils import make_mocked_request

from polyphemus.dashboard import DASHBOARD_HTML, Dashboard
from polyphemus.health_monitor import HealthMonitor
import polyphemus.pre_deploy_check as pre_deploy_check


def _install_rich_stub():
    if "rich" in sys.modules:
        return
    rich = types.ModuleType("rich")
    rich_live = types.ModuleType("rich.live")
    rich_layout = types.ModuleType("rich.layout")
    rich_table = types.ModuleType("rich.table")
    rich_panel = types.ModuleType("rich.panel")
    rich_text = types.ModuleType("rich.text")
    rich_box = types.ModuleType("rich.box")

    rich_live.Live = type("Live", (), {})
    rich_layout.Layout = type("Layout", (), {})
    rich_table.Table = type("Table", (), {})
    rich_panel.Panel = type("Panel", (), {})
    rich_text.Text = type("Text", (), {"from_markup": staticmethod(lambda text: text)})
    rich_box.SIMPLE = "SIMPLE"

    sys.modules["rich"] = rich
    sys.modules["rich.live"] = rich_live
    sys.modules["rich.layout"] = rich_layout
    sys.modules["rich.table"] = rich_table
    sys.modules["rich.panel"] = rich_panel
    sys.modules["rich.text"] = rich_text
    sys.modules["rich.box"] = rich_box


_install_rich_stub()

from polyphemus.tools import bot_monitor, session_state


def test_dashboard_status_exposes_accumulator_runtime_flags(tmp_path):
    dashboard = Dashboard(
        config=SimpleNamespace(accum_dry_run=True, accum_mode_enabled=True),
        store=SimpleNamespace(count_open=lambda: 4),
        balance=SimpleNamespace(_cached_balance=123.45),
        health=SimpleNamespace(get_uptime_hours=lambda: 1.25, _error_count=0),
        guard=SimpleNamespace(),
        perf_db=None,
        dry_run=True,
        accumulator_engine=SimpleNamespace(stats={
            "state": "idle",
            "active_positions": 0,
            "effective_accumulator_dry_run": True,
            "circuit_tripped": True,
            "entry_mode": "fak",
            "daily_loss_limit": -50.0,
            "total_pnl": -50.82,
        }),
    )

    response = asyncio.run(dashboard._handle_status(make_mocked_request("GET", "/api/status")))
    payload = json.loads(response.text)

    assert payload["dry_run"] is True
    assert payload["enable_accumulator"] is True
    assert payload["accum_dry_run"] is True
    assert payload["effective_accumulator_dry_run"] is True
    assert payload["accum_mode_enabled"] is True
    assert payload["accumulator_state"] == "idle"
    assert payload["accumulator_circuit_tripped"] is True
    assert payload["accumulator_entry_mode"] == "fak"
    assert payload["accumulator_daily_loss_limit"] == -50.0
    assert payload["accumulator_total_pnl"] == -50.82
    assert payload["open_positions"] == 4


def test_dashboard_pipeline_summary_reports_accumulator_circuit_breaker(tmp_path):
    signals_db = tmp_path / "signals.db"
    perf_db = tmp_path / "performance.db"

    sig_conn = sqlite3.connect(signals_db)
    sig_conn.execute(
        """
        CREATE TABLE signals (
            asset TEXT,
            market_window_secs INTEGER,
            epoch REAL,
            source TEXT,
            guard_passed INTEGER,
            outcome TEXT,
            guard_reasons TEXT,
            slug TEXT,
            midpoint REAL,
            time_remaining_secs INTEGER
        )
        """
    )
    sig_conn.commit()
    sig_conn.close()

    trade_conn = sqlite3.connect(perf_db)
    trade_conn.execute("CREATE TABLE trades (slug TEXT, entry_time REAL)")
    trade_conn.commit()
    trade_conn.close()

    dashboard = Dashboard(
        config=SimpleNamespace(accum_dry_run=False, accum_mode_enabled=True),
        store=SimpleNamespace(count_open=lambda: 0),
        balance=SimpleNamespace(_cached_balance=321.0),
        health=SimpleNamespace(get_uptime_hours=lambda: 3.0, _error_count=0),
        guard=SimpleNamespace(),
        perf_db=SimpleNamespace(db_path=str(perf_db)),
        signal_logger=SimpleNamespace(_db_path=str(signals_db)),
        accumulator_engine=SimpleNamespace(stats={
            "circuit_tripped": True,
            "total_pnl": -50.82,
            "daily_loss_limit": -50.0,
            "entry_mode": "fak",
        }),
    )

    summary = dashboard._get_pipeline_summary()

    assert summary["stage"] == "circuit_breaker"
    assert "circuit breaker" in summary["headline"].lower()
    assert "-50.82" in summary["summary"]
    assert summary["accumulator_circuit_tripped"] is True
    assert summary["accumulator_total_pnl"] == -50.82
    assert summary["accumulator_daily_loss_limit"] == -50.0


def test_dashboard_pipeline_summary_prefers_accumulator_targets_for_non_btc_shadow(tmp_path):
    signals_db = tmp_path / "signals.db"
    perf_db = tmp_path / "performance.db"

    sig_conn = sqlite3.connect(signals_db)
    sig_conn.execute(
        """
        CREATE TABLE signals (
            asset TEXT,
            market_window_secs INTEGER,
            epoch REAL,
            source TEXT,
            guard_passed INTEGER,
            outcome TEXT,
            guard_reasons TEXT,
            slug TEXT,
            midpoint REAL,
            time_remaining_secs INTEGER
        )
        """
    )
    sig_conn.commit()
    sig_conn.close()

    trade_conn = sqlite3.connect(perf_db)
    trade_conn.execute("CREATE TABLE trades (slug TEXT, entry_time REAL)")
    trade_conn.commit()
    trade_conn.close()

    dashboard = Dashboard(
        config=SimpleNamespace(
            accum_dry_run=True,
            accum_mode_enabled=False,
            enable_accumulator=True,
            accum_assets="XRP",
            accum_window_types="5m,15m",
        ),
        store=SimpleNamespace(count_open=lambda: 0),
        balance=SimpleNamespace(_cached_balance=321.0),
        health=SimpleNamespace(get_uptime_hours=lambda: 3.0, _error_count=0),
        guard=SimpleNamespace(),
        perf_db=SimpleNamespace(db_path=str(perf_db)),
        signal_logger=SimpleNamespace(_db_path=str(signals_db)),
        accumulator_engine=SimpleNamespace(stats={
            "entry_mode": "fak",
            "scan_count": 56,
            "candidates_seen": 0,
            "hedged_count": 15,
            "unwound_count": 5,
            "active_positions": 0,
            "circuit_tripped": False,
            "assets": ["XRP"],
            "window_types": ["5m", "15m"],
            "last_eval_block_reason": "",
        }),
    )

    summary = dashboard._get_pipeline_summary()

    assert summary["stage"] == "accumulator_scanning"
    assert "xrp 5m/15m" in summary["headline"].lower()
    assert "scan_count=56" in summary["summary"]


def test_dashboard_accumulator_endpoint_rehydrates_settlements_from_perf_db():
    perf_db = SimpleNamespace(
        get_recent_trades=lambda limit=50: [
            {
                "slug": "xrp-updown-5m-1775067000",
                "exit_time": 1775067241.502221,
                "entry_time": 1775067030.04191,
                "entry_price": 0.98,
                "exit_reason": "hedged_settlement",
                "pnl": 0.259499,
                "outcome": "PAIR",
                "entry_size": 10.0,
                "strategy": "pair_arb",
                "metadata": json.dumps({
                    "up_qty": 10,
                    "down_qty": 10,
                    "up_price": 0.49,
                    "down_price": 0.49,
                    "pair_cost": 0.98,
                }),
            },
            {
                "slug": "xrp-updown-5m-1775080200",
                "exit_time": 1775080282.7926733,
                "entry_time": 1775080272.612892,
                "entry_price": 0.53,
                "exit_reason": "sellback",
                "pnl": -4.198302,
                "outcome": "ORPHAN",
                "entry_size": 0.0,
                "strategy": "pair_arb",
                "metadata": json.dumps({
                    "up_qty": 10,
                    "down_qty": 0,
                    "up_price": 0.53,
                    "pair_cost": 0.53,
                }),
            },
        ]
    )
    dashboard = Dashboard(
        config=SimpleNamespace(accum_dry_run=True, accum_mode_enabled=True),
        store=SimpleNamespace(count_open=lambda: 0),
        balance=SimpleNamespace(_cached_balance=100.0),
        health=SimpleNamespace(get_uptime_hours=lambda: 2.0, _error_count=0),
        guard=SimpleNamespace(),
        perf_db=perf_db,
        dry_run=True,
        accumulator_engine=SimpleNamespace(stats={
            "state": "idle",
            "hedged_count": 1,
            "orphaned_count": 0,
            "unwound_count": 1,
            "settlements": [],
        }),
    )

    response = asyncio.run(
        dashboard._handle_accumulator(make_mocked_request("GET", "/api/accumulator"))
    )
    payload = json.loads(response.text)

    assert payload["enabled"] is True
    assert len(payload["settlements"]) == 2
    assert [row["exit_reason"] for row in payload["settlements"]] == [
        "hedged_settlement",
        "sellback",
    ]
    assert payload["settlements"][0]["pair_cost"] == 0.98
    assert payload["settlements"][1]["timestamp"] == 1775080282.7926733


def test_dashboard_html_uses_shadow_dashboard_branding():
    assert "Polyphemus Shadow Dashboard" in DASHBOARD_HTML
    assert "Recent Backend Alerts" in DASHBOARD_HTML
    assert "Recent Closed Trades" in DASHBOARD_HTML


def test_health_monitor_pipeline_watchdog_reports_accumulator_circuit_breaker(tmp_path):
    import sqlite3
    from unittest.mock import MagicMock

    signals_db = tmp_path / "signals.db"
    perf_db = tmp_path / "performance.db"

    sig_conn = sqlite3.connect(signals_db)
    sig_conn.execute(
        """
        CREATE TABLE signals (
            asset TEXT,
            market_window_secs INTEGER,
            epoch REAL,
            source TEXT,
            guard_passed INTEGER,
            outcome TEXT,
            guard_reasons TEXT,
            slug TEXT,
            midpoint REAL,
            time_remaining_secs INTEGER
        )
        """
    )
    sig_conn.commit()
    sig_conn.close()

    trade_conn = sqlite3.connect(perf_db)
    trade_conn.execute("CREATE TABLE trades (slug TEXT, entry_time REAL)")
    trade_conn.commit()
    trade_conn.close()

    monitor = HealthMonitor(
        config=SimpleNamespace(lagbot_data_dir=str(tmp_path)),
        store=SimpleNamespace(count_open=lambda: 0),
    )
    monitor.set_pipeline_dbs(
        signal_logger=SimpleNamespace(_db_path=str(signals_db)),
        perf_db=SimpleNamespace(db_path=str(perf_db)),
    )
    monitor.set_accumulator_engine(SimpleNamespace(stats={
        "circuit_tripped": True,
        "total_pnl": -50.82,
        "daily_loss_limit": -50.0,
        "entry_mode": "fak",
    }))
    monitor._logger = MagicMock()

    monitor._check_pipeline_watchdog()

    assert monitor._logger.warning.called
    assert "accumulator circuit breaker active" in monitor._logger.warning.call_args[0][0].lower()


def test_health_monitor_pipeline_watchdog_skips_btc_stall_for_non_btc_accumulator_shadow(tmp_path):
    signals_db = tmp_path / "signals.db"
    perf_db = tmp_path / "performance.db"

    sig_conn = sqlite3.connect(signals_db)
    sig_conn.execute(
        """
        CREATE TABLE signals (
            asset TEXT,
            market_window_secs INTEGER,
            epoch REAL,
            source TEXT,
            guard_passed INTEGER,
            outcome TEXT,
            guard_reasons TEXT,
            slug TEXT,
            midpoint REAL,
            time_remaining_secs INTEGER
        )
        """
    )
    sig_conn.commit()
    sig_conn.close()

    trade_conn = sqlite3.connect(perf_db)
    trade_conn.execute("CREATE TABLE trades (slug TEXT, entry_time REAL)")
    trade_conn.commit()
    trade_conn.close()

    monitor = HealthMonitor(
        config=SimpleNamespace(
            lagbot_data_dir=str(tmp_path),
            enable_accumulator=True,
            accum_assets="XRP",
            accum_window_types="5m,15m",
        ),
        store=SimpleNamespace(count_open=lambda: 0),
    )
    monitor.set_pipeline_dbs(
        signal_logger=SimpleNamespace(_db_path=str(signals_db)),
        perf_db=SimpleNamespace(db_path=str(perf_db)),
    )
    monitor.set_accumulator_engine(SimpleNamespace(stats={
        "circuit_tripped": False,
        "scan_count": 56,
    }))

    class LoggerStub:
        def __init__(self):
            self.messages = []

        def warning(self, msg, *args):
            if args:
                msg = msg % args
            self.messages.append(msg)

    logger = LoggerStub()
    monitor._logger = logger

    monitor._check_pipeline_watchdog()

    assert logger.messages == []


def test_session_state_uses_dashboard_port_from_env():
    base = session_state.get_dashboard_base({
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "8083",
    })

    assert base == "http://127.0.0.1:8083"


def test_pre_deploy_check_flags_accumulator_dry_run_mismatch(monkeypatch):
    env_blob = "\n".join([
        "DRY_RUN=true",
        "ENABLE_ARB=false",
        "ENABLE_ACCUMULATOR=true",
        "ENABLE_PAIR_ARB=false",
        "ACCUM_DRY_RUN=false",
        "ACCUM_MAX_PAIR_COST=0.95",
        "DASHBOARD_PORT=8083",
    ])

    monkeypatch.setattr(pre_deploy_check, "ssh", lambda *args, **kwargs: env_blob)
    pre_deploy_check.results.clear()

    pre_deploy_check.check_config_drift("polyphemus")

    assert any(
        level == pre_deploy_check.FAIL and "ACCUM_DRY_RUN=false" in msg
        for level, _, msg in pre_deploy_check.results
    )


def test_pre_deploy_check_uses_accumulator_runtime_for_circuit_breaker(monkeypatch):
    env_blob = "\n".join([
        "ENABLE_ACCUMULATOR=true",
        "MAX_DAILY_LOSS=15",
        "DASHBOARD_PORT=8083",
        "KILL_SWITCH_PATH=/tmp/polyphemus/KILL_SWITCH",
    ])

    def fake_ssh(cmd, *args, **kwargs):
        if "cat /opt/lagbot/instances/polyphemus/.env" in cmd:
            return env_blob
        if "curl -fsS http://127.0.0.1:8083/api/accumulator" in cmd:
            return json.dumps({"total_pnl": 0.0})
        if "test -f /tmp/polyphemus/KILL_SWITCH" in cmd:
            return ""
        raise AssertionError(f"unexpected ssh command: {cmd}")

    monkeypatch.setattr(pre_deploy_check, "ssh", fake_ssh)
    pre_deploy_check.results.clear()

    pre_deploy_check.check_circuit_breaker("polyphemus")

    assert any(
        level == pre_deploy_check.PASS and check == "circuit_breaker"
        for level, check, _ in pre_deploy_check.results
    )


def test_pre_deploy_check_scopes_startup_errors_to_current_service_start(monkeypatch):
    def fake_ssh(cmd, *args, **kwargs):
        if "systemctl show lagbot@polyphemus -p ActiveEnterTimestamp --value" in cmd:
            return "Wed 2026-04-01 03:02:24 UTC"
        if "date -d 'Wed 2026-04-01 03:02:24 UTC' +%s" in cmd:
            return "1775012544"
        if "--since '@1775012544' --until '@1775012634'" in cmd:
            return ""
        raise AssertionError(f"unexpected ssh command: {cmd}")

    monkeypatch.setattr(pre_deploy_check, "ssh", fake_ssh)
    pre_deploy_check.results.clear()

    pre_deploy_check.check_startup_errors("polyphemus")

    assert any(
        level == pre_deploy_check.PASS and check == "startup_errors"
        for level, check, _ in pre_deploy_check.results
    )


def test_pre_deploy_check_treats_older_shared_state_as_stale_residue(monkeypatch):
    env_blob = "ENABLE_ACCUMULATOR=true"

    def fake_ssh(cmd, *args, **kwargs):
        if "cat /opt/lagbot/instances/polyphemus/.env" in cmd:
            return env_blob
        if "test -f /opt/lagbot/instances/polyphemus/data/circuit_breaker.json" in cmd:
            return "yes"
        if "test -f /opt/lagbot/data/circuit_breaker.json" in cmd:
            return "yes"
        if "systemctl show lagbot@polyphemus -p ActiveEnterTimestamp --value" in cmd:
            return "Wed 2026-04-01 03:02:24 UTC"
        if "date -d 'Wed 2026-04-01 03:02:24 UTC' +%s" in cmd:
            return "1775012544"
        if "stat -c %Y /opt/lagbot/data/circuit_breaker.json" in cmd:
            return "1775007600"
        if "stat -c %Y /opt/lagbot/instances/polyphemus/data/circuit_breaker.json" in cmd:
            return "1775012545"
        raise AssertionError(f"unexpected ssh command: {cmd}")

    monkeypatch.setattr(pre_deploy_check, "ssh", fake_ssh)
    pre_deploy_check.results.clear()

    pre_deploy_check.check_accumulator_state_storage("polyphemus")

    assert any(
        level == pre_deploy_check.INFO and check == "accum_state_storage"
        for level, check, _ in pre_deploy_check.results
    )


def test_bot_monitor_loads_instance_ports_from_env(monkeypatch):
    monkeypatch.setattr(
        bot_monitor,
        "_read_env",
        lambda path: {"DASHBOARD_PORT": "9001"} if path.endswith("polyphemus/.env") else {},
    )

    bots = bot_monitor._load_bots()

    polyphemus = next(bot for bot in bots if bot["instance"] == "polyphemus")
    assert polyphemus["port"] == 9001
