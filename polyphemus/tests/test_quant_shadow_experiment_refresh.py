import json

from polyphemus.tools import quant_shadow_experiment_refresh as qsr


def test_recommendation_flags_conversion_starvation():
    summary = {
        "verdict": "NO-GO: shadow window immature",
        "duration_hours": 6.2,
    }
    deltas = {
        "completed_delta": 0,
        "candidates_delta": 24,
    }

    note = qsr.recommendation(summary, deltas)

    assert "throughput starvation" in note


def test_refresh_experiment_writes_shadow_artifacts(tmp_path, monkeypatch):
    experiment_dir = tmp_path / ".omc" / "experiments" / "xrp-5m-15m-fak-accumulator-shadow"
    experiment_dir.mkdir(parents=True)
    snapshots_path = tmp_path / "hourly_snapshots.vps.jsonl"
    rows = [
        {
            "captured_at": "2026-04-02T17:54:53+00:00",
            "captured_ts": 1000,
            "accumulator": {
                "assets": ["XRP"],
                "window_types": ["5m", "15m"],
                "entry_mode": "fak",
                "hedged_count": 15,
                "unwound_count": 5,
                "candidates_seen": 0,
                "scan_count": 666,
                "total_pnl": 4.83,
                "last_eval_block_reason": "",
                "circuit_tripped": False,
            },
            "status": {
                "effective_accumulator_dry_run": True,
                "accumulator_entry_mode": "fak",
            },
            "pipeline": {
                "stage": "accumulator_scanning",
                "summary": "scan_count=666, candidates_seen=0, hedged=15, unwound=5.",
                "stage_stops": [],
            },
        },
        {
            "captured_at": "2026-04-03T00:07:01+00:00",
            "captured_ts": 1000 + int(6.2 * 3600),
            "accumulator": {
                "assets": ["XRP"],
                "window_types": ["5m", "15m"],
                "entry_mode": "fak",
                "hedged_count": 15,
                "unwound_count": 5,
                "candidates_seen": 24,
                "scan_count": 18140,
                "total_pnl": 4.83,
                "last_eval_block_reason": "directional",
                "circuit_tripped": False,
            },
            "status": {
                "effective_accumulator_dry_run": True,
                "accumulator_entry_mode": "fak",
            },
            "pipeline": {
                "stage": "accumulator_scanning",
                "summary": "scan_count=18140, candidates_seen=24, hedged=15, unwound=5.",
                "stage_stops": [{"stage": "shadow", "status": "logged"}],
            },
        },
    ]
    snapshots_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(qsr, "ROOT", tmp_path)

    payload = qsr.refresh_experiment(
        "xrp-5m-15m-fak-accumulator-shadow",
        snapshots_path=snapshots_path,
        write_json=True,
    )

    evidence = (experiment_dir / "evidence_log.md").read_text(encoding="utf-8")
    review = (experiment_dir / "promotion_review.md").read_text(encoding="utf-8")
    status = json.loads((experiment_dir / "current_status.json").read_text(encoding="utf-8"))

    assert payload["gate_verdict"] == "NO-GO: shadow window immature"
    assert "completed_delta=0; candidates_delta=24" in evidence
    assert "throughput starvation" in review
    assert status["entry_mode"] == "fak"
