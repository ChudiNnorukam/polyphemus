import json
from pathlib import Path

from polyphemus.tools import quant_candidate_refresh as qcr


def test_confidence_label_thresholds():
    assert qcr.confidence_label(10) == "ANECDOTAL n=10"
    assert qcr.confidence_label(30) == "LOW n=30"


def test_refresh_candidate_writes_files(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    experiment_dir = tmp_path / ".omc" / "experiments" / "btc-5m-ensemble-selected-live-v1"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "evidence_log.md").write_text("", encoding="utf-8")
    (experiment_dir / "promotion_review.md").write_text("", encoding="utf-8")

    gate = {
        "config_era": "727771dc4073",
        "blockers": [
            "emmanuel: runtime 16.7h < 48.0h",
            "emmanuel: unexplained pipeline stall windows 38 > 1",
        ],
        "comparison": {
            "ensemble_selected_live_v1": {
                "trades": 10,
                "avg_net_live": 1.162355065790085,
                "live_roi": 0.05811775328950425,
                "live_max_drawdown": 32.33158472615508,
            }
        },
    }
    shadow = {"elapsed_hours": 16.666666666666668}
    decision = {"generated_at": "2026-03-13T05:03:37.340228+00:00", "verdict": "NO-GO"}
    (runtime_dir / "go_live_gate_status.json").write_text(json.dumps(gate), encoding="utf-8")
    (runtime_dir / "shadow_window_status.json").write_text(json.dumps(shadow), encoding="utf-8")
    (runtime_dir / "decision_memo.json").write_text(json.dumps(decision), encoding="utf-8")

    monkeypatch.setattr(qcr, "ROOT", tmp_path)
    monkeypatch.setattr(qcr, "RUNTIME_DIR", runtime_dir)

    payload = qcr.refresh_candidate("btc-5m-ensemble-selected-live-v1", write_json=True)

    assert payload["gate_verdict"] == "NO-GO"
    evidence = (experiment_dir / "evidence_log.md").read_text(encoding="utf-8")
    review = (experiment_dir / "promotion_review.md").read_text(encoding="utf-8")
    status = json.loads((experiment_dir / "current_status.json").read_text(encoding="utf-8"))
    assert "ANECDOTAL n=10" in evidence
    assert "`NO-GO`" in review
    assert status["config_era"] == "727771dc4073"
