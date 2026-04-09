from polyphemus.tools import quant_refresh_pipeline as qrp


def test_main_runs_candidate_refresh(monkeypatch, capsys):
    calls = []

    def fake_run_script(script_name, *extra_args):
        calls.append((script_name, extra_args))
        return {"script": script_name}

    def fake_refresh_candidate(slug, write_json=False):
        assert slug == "btc-5m-ensemble-selected-live-v1"
        assert write_json is True
        return {
            "gate_verdict": "NO-GO",
            "shadow_elapsed_hours": 16.7,
            "comparison": {"trades": 10, "win_rate": 0.7, "avg_net_live": 1.16},
            "blockers": ["runtime too short"],
        }

    monkeypatch.setattr(qrp, "run_script", fake_run_script)
    monkeypatch.setattr(qrp.quant_candidate_refresh, "refresh_candidate", fake_refresh_candidate)
    monkeypatch.setattr(
        "sys.argv",
        ["quant_refresh_pipeline.py", "--print-json"],
    )

    rc = qrp.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert '"candidate_slug": "btc-5m-ensemble-selected-live-v1"' in out
    assert '"gate_verdict": "NO-GO"' in out
    assert calls[0][0] == "refresh_vps_caches.py"
    assert calls[1][0] == "agent_bootstrap.py"


def test_main_supports_skip_flags(monkeypatch, capsys):
    def fake_refresh_candidate(slug, write_json=False):
        return {"gate_verdict": "NO-GO", "comparison": {}, "blockers": []}

    monkeypatch.setattr(qrp.quant_candidate_refresh, "refresh_candidate", fake_refresh_candidate)
    monkeypatch.setattr(
        "sys.argv",
        ["quant_refresh_pipeline.py", "--skip-vps-refresh", "--skip-bootstrap", "--print-json"],
    )

    rc = qrp.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert '"skipped": true' in out
