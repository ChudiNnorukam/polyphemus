from polyphemus.tools.shadow_eval_report import (
    confidence_label,
    compute_summary,
    render_report,
    verdict,
)
from polyphemus.tools.shadow_eval_snapshot import build_snapshot


def test_confidence_label_thresholds():
    assert confidence_label(0) == "ANECDOTAL n=0"
    assert confidence_label(29) == "ANECDOTAL n=29"
    assert confidence_label(30) == "LOW n=30"
    assert confidence_label(107) == "MODERATE n=107"


def test_verdict_requires_window_and_sample_and_positive_expectancy():
    assert verdict(12, 40, 1.0) == "NO-GO: shadow window immature"
    assert verdict(72, 10, 1.0) == "NO-GO: insufficient completed opportunity count"
    assert verdict(72, 40, 0.0) == "NO-GO: non-positive expectancy"
    assert verdict(72, 40, 0.25) == "CANARY-ELIGIBLE: narrow live review only"


def test_compute_summary_uses_latest_snapshot_counts():
    rows = [
        {
            "captured_ts": 1000,
            "accumulator": {"hedged_count": 2, "unwound_count": 1, "total_pnl": 1.5},
        },
        {
            "captured_ts": 1000 + 72 * 3600,
            "accumulator": {"hedged_count": 20, "unwound_count": 15, "total_pnl": 10.5},
        },
    ]

    summary = compute_summary(rows)

    assert summary["duration_hours"] == 72
    assert summary["completed"] == 35
    assert round(summary["expectancy"], 4) == 0.3
    assert summary["verdict"] == "CANARY-ELIGIBLE: narrow live review only"


def test_render_report_prefers_runtime_slice_over_stale_config():
    summary = {
        "verdict": "NO-GO: shadow window immature",
        "confidence": "ANECDOTAL n=20",
        "duration_hours": 0.5,
        "completed": 20,
        "total_pnl": 4.83,
        "expectancy": 0.2415,
        "latest": {
            "captured_at": "2026-04-02T17:58:03.844188+00:00",
            "config": {"accum_assets": "SOL,XRP", "accum_window_types": "5m"},
            "status": {
                "status": "running",
                "accumulator_entry_mode": "fak",
                "effective_accumulator_dry_run": True,
            },
            "accumulator": {
                "assets": ["XRP"],
                "window_types": ["5m", "15m"],
                "active_positions": 0,
                "scan_count": 844,
                "candidates_seen": 0,
                "hedged_count": 15,
                "unwound_count": 5,
                "orphaned_count": 11,
                "circuit_tripped": False,
            },
            "pipeline": {
                "stage": "accumulator_scanning",
                "headline": "Accumulator scanning XRP 5m/15m markets",
                "summary": "scan_count=844, candidates_seen=0, hedged=15, unwound=5.",
            },
        },
    }

    report = render_report("polyphemus", summary)

    assert "- Target slice: `XRP` / `5m,15m`" in report
    assert "- Effective dry run: `True`" in report


def test_build_snapshot_prefers_runtime_api_state(monkeypatch):
    monkeypatch.setattr(
        "polyphemus.tools.shadow_eval_snapshot.resolve_instance_paths",
        lambda instance: (None, None),
    )
    monkeypatch.setattr(
        "polyphemus.tools.shadow_eval_snapshot.read_env",
        lambda path: {
            "DRY_RUN": "true",
            "ACCUM_DRY_RUN": "true",
            "ACCUM_ENTRY_MODE": "maker",
            "ACCUM_ASSETS": "SOL,XRP",
            "ACCUM_WINDOW_TYPES": "5m",
            "DASHBOARD_PORT": "8083",
        },
    )
    monkeypatch.setattr(
        "polyphemus.tools.shadow_eval_snapshot.read_service_env",
        lambda instance: {
            "ACCUM_ENTRY_MODE": "maker",
            "ACCUM_ASSETS": "SOL,XRP",
            "ACCUM_WINDOW_TYPES": "5m",
        },
    )

    responses = {
        "http://127.0.0.1:8083/api/status": {
            "dry_run": True,
            "accum_dry_run": True,
            "effective_accumulator_dry_run": True,
            "accumulator_entry_mode": "fak",
            "accumulator_assets": ["XRP"],
            "accumulator_window_types": ["5m", "15m"],
        },
        "http://127.0.0.1:8083/api/accumulator": {
            "assets": ["XRP"],
            "window_types": ["5m", "15m"],
            "entry_mode": "fak",
            "accum_dry_run": True,
            "effective_accumulator_dry_run": True,
        },
        "http://127.0.0.1:8083/api/pipeline": {},
        "http://127.0.0.1:8083/api/balance": {},
    }
    monkeypatch.setattr(
        "polyphemus.tools.shadow_eval_snapshot.fetch_json",
        lambda url: responses[url],
    )

    snapshot = build_snapshot("polyphemus", "http://127.0.0.1:8083")

    assert snapshot["config"]["accum_entry_mode"] == "fak"
    assert snapshot["config"]["accum_assets"] == "XRP"
    assert snapshot["config"]["accum_window_types"] == "5m,15m"
    assert snapshot["config"]["effective_accumulator_dry_run"] == "true"
    assert snapshot["config_debug"]["env_file"]["accum_assets"] == "SOL,XRP"
