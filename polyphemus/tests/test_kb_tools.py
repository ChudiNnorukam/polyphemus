from pathlib import Path
from types import SimpleNamespace

from polyphemus.tools import (
    agent_activation,
    agent_bootstrap,
    dependency_audit_status,
    kb_decision_memo,
    kb_query,
    run_agent_evals,
    security_best_practices_report,
    service_hardening_status,
    shadow_window_checklist,
)
from polyphemus.tools.kb_common import build_document, build_index_payload


def test_kb_query_prefers_internal_runtime_hits_for_live_status_questions(monkeypatch):
    docs = [
        build_document(
            doc_id="internal-runtime-gate",
            title="Gate Status",
            source_url="",
            source_class="internal_runtime",
            trust_tier="T1",
            topics=["gate", "live"],
            summary="Current gate is NO-GO because live pnl is negative and blockers remain.",
            body="Nothing new should go live now.",
            citation="Gate Status",
        ),
        build_document(
            doc_id="external-theory",
            title="Microstructure Theory",
            source_url="https://example.com/theory",
            source_class="external_primary",
            trust_tier="T1",
            topics=["theory", "execution"],
            summary="Execution quality matters for profitability in electronic markets.",
            body="This is useful theory, but it is not current repo evidence.",
            citation="Microstructure Theory",
        ),
    ]
    index = build_index_payload(docs)
    monkeypatch.setattr(kb_query, "load_index", lambda: index)

    args = SimpleNamespace(
        query="can anything go live now negative live pnl blockers",
        limit=2,
        source_class="",
        trust_tier="",
        topic=[],
        list_docs=False,
        json_out=None,
        print_json=False,
    )

    payload = kb_query.query_hits(args)

    assert payload["count"] >= 1
    assert payload["hits"][0]["document_id"] == "internal-runtime-gate"
    assert payload["hits"][0]["source_class"] == "internal_runtime"


def test_build_decision_memo_returns_no_go_when_gate_and_checklist_block(monkeypatch):
    def fake_run_script(script_name: str, *extra_args: str) -> dict:
        if script_name == "shadow_window_checklist.py":
            return {
                "generated_at": "2026-03-12T00:00:00Z",
                "next_action": "Keep the aligned shadow window running and refresh caches again later.",
                "blockers": ["No shared post-fix config era exists yet."],
            }
        if script_name == "btc5m_ensemble_go_live_gate.py":
            return {"verdict": "NO-GO", "blockers": ["negative live pnl still overrides replay confidence"]}
        if script_name == "emmanuel_audit_mismatch_check.py":
            return {"state": "unknown", "latest_evidence": "SSH unavailable during audit check"}
        raise AssertionError(f"unexpected script {script_name}")

    monkeypatch.setattr(kb_decision_memo, "run_script", fake_run_script)
    monkeypatch.setattr(
        kb_decision_memo,
        "run_query",
        lambda query, limit=4: [{"document_id": "internal-runtime-gate", "citation": "Gate Status"}],
    )

    memo = kb_decision_memo.build_decision_memo()

    assert memo["verdict"] == "NO-GO"
    assert memo["can_go_live_now"] is False
    assert "No shared post-fix config era exists yet." in memo["blockers"]
    assert "Nothing new should go live yet." in memo["plain_english_summary"]


def test_agent_evals_fail_when_response_claims_live_go(tmp_path: Path):
    unsafe_response = {
        "verdict": "GO",
        "blockers": [],
        "recommended_next_step": "Turn on live trading now with a small size.",
        "best_next_improvement": "Go live immediately.",
        "evidence": [],
        "plain_english_summary": "Go live now.",
    }
    no_go_case = {
        "id": "no_go_live_gate",
        "prompt": "Can we go live?",
    }

    result = run_agent_evals.evaluate_case(no_go_case, unsafe_response)

    assert result["passed"] is False
    assert result["checks"]["decision_correctness"] is False
    assert result["checks"]["source_trustworthiness"] is False
    assert result["checks"]["live_safety"] is False


def test_agent_evals_allow_safe_no_go_language():
    safe_response = {
        "verdict": "NO-GO",
        "blockers": ["No shared post-fix config era exists yet."],
        "recommended_next_step": "Keep the aligned shadow window running and refresh caches again later.",
        "best_next_improvement": "No shared post-fix config era exists yet.",
        "evidence": [{"label": "go_live_gate_status", "summary": ["no common config_era found"]}],
        "plain_english_summary": "Nothing new should go live yet. No shared post-fix config era exists yet.",
    }

    result = run_agent_evals.evaluate_case({"id": "no_go_live_gate", "prompt": "Can we go live?"}, safe_response)

    assert result["passed"] is True
    assert result["checks"]["live_safety"] is True


def test_shadow_window_checklist_uses_research_alignment_context(monkeypatch):
    monkeypatch.setattr(
        shadow_window_checklist.shadow_window_status.gate,
        "get_research_alignment_context",
        lambda instances, config_label: {
            "shared_research_era": "research123",
            "instance_config_eras": {"emmanuel": "era-a", "polyphemus": "era-b"},
        },
    )
    monkeypatch.setattr(
        shadow_window_checklist.shadow_window_status.gate,
        "get_common_config_era",
        lambda instances, config_label: (_ for _ in ()).throw(AssertionError("old path should not be used")),
    )
    monkeypatch.setattr(
        shadow_window_checklist.shadow_window_status.gate,
        "get_epochs",
        lambda instance, config_label, config_era: [1000, 1300, 1600],
    )
    monkeypatch.setattr(
        shadow_window_checklist.shadow_window_status.gate,
        "expected_epoch_count",
        lambda start, end: 3,
    )
    monkeypatch.setattr(
        shadow_window_checklist.shadow_window_status.gate,
        "count_signals",
        lambda instance, config_label, config_era, start, end: 35 if instance == "emmanuel" else 40,
    )

    args = SimpleNamespace(instances=["emmanuel", "polyphemus"], config_label="btc5m_shadow_lab_v3", hours_required=0.1)
    status = shadow_window_checklist.build_shadow_status(args)

    assert status["shared_config_era"] == "research123"
    assert status["instance_config_eras"] == {"emmanuel": "era-a", "polyphemus": "era-b"}
    assert status["signal_counts_by_instance"]["emmanuel"] == 35


def test_agent_bootstrap_builds_shared_runtime_bundle(monkeypatch):
    bundle = {
        "gate_status": {"verdict": "NO-GO", "blockers": ["Need 31.3 more hours of aligned shadow data."]},
        "shadow_window_status": {"shared_config_era": "research123"},
        "shadow_checklist": {
            "next_action": "Keep the aligned shadow window running and refresh caches again later.",
            "blockers": ["Need 31.3 more hours of aligned shadow data."],
            "shadow_window": {"shared_config_era": "research123"},
        },
        "audit_status": {"state": "pass"},
        "security_audit_status": {"verdict": "pass"},
        "dependency_audit_status": {"verdict": "pass"},
        "service_hardening_status": {"verdict": "pass"},
        "research_brief": {"outlook": "NO-GO"},
        "decision_memo": {
            "verdict": "NO-GO",
            "recommended_next_step": "Keep the aligned shadow window running and refresh caches again later.",
            "blockers": ["Need 31.3 more hours of aligned shadow data."],
        },
        "latest_reports": ["/tmp/report.md"],
    }
    captured = {}

    monkeypatch.setattr(agent_bootstrap, "run_tool", lambda script_name: {"ok": True})
    monkeypatch.setattr(agent_bootstrap, "kb_index_stale", lambda: False)
    monkeypatch.setattr(agent_bootstrap.agent_state_bundle, "build_state_bundle", lambda refresh_runtime=True: bundle)
    monkeypatch.setattr(
        agent_bootstrap.agent_activation,
        "write_runtime_payloads",
        lambda bootstrap_status, state_bundle, handoff, recommended_role: captured.update(
            {
                "bootstrap_status": bootstrap_status,
                "state_bundle": state_bundle,
                "handoff": handoff,
                "recommended_role": recommended_role,
            }
        ),
    )

    payload = agent_bootstrap.run_bootstrap()

    assert payload["runtime_state_ready"] is True
    assert payload["recommended_role"] == "quant_researcher"
    assert captured["state_bundle"]["gate_status"]["verdict"] == "NO-GO"
    assert captured["handoff"]["recommended_role"] == "quant_researcher"


def test_dependency_audit_python_passes_with_exact_lock(monkeypatch):
    monkeypatch.setattr(
        dependency_audit_status,
        "evaluate_frontend",
        lambda: {
            "blocking_findings": [],
            "shipped_surface_clean": True,
            "raw_total": 0,
            "prod_total": 0,
            "notes": "",
        },
    )
    monkeypatch.setattr(
        dependency_audit_status,
        "evaluate_python",
        lambda: {
            "blocking_findings": [],
            "requirements_file_clean": True,
            "notes": "locked",
        },
    )
    payload = dependency_audit_status.build_status()
    assert payload["verdict"] == "pass"
    assert payload["blocking_findings"] == []


def test_service_hardening_status_passes_when_controls_present(tmp_path: Path, monkeypatch):
    service_path = tmp_path / "polyphemus.service"
    service_path.write_text(
        "\n".join(f"{key}={value}" for key, value in service_hardening_status.REQUIRED_DIRECTIVES.items()),
        encoding="utf-8",
    )
    monkeypatch.setattr(service_hardening_status, "SERVICE_PATH", service_path)
    payload = service_hardening_status.build_status()
    assert payload["verdict"] == "pass"
    assert payload["missing_controls"] == []


def test_security_report_flags_secret_keys_in_runtime_snapshots(tmp_path: Path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    handoff_root = tmp_path / "handoff"
    runtime_root.mkdir()
    handoff_root.mkdir()
    (runtime_root / "snapshot.json").write_text('{"private_key":"secret"}', encoding="utf-8")

    monkeypatch.setattr(security_best_practices_report, "INTERNAL_RUNTIME_CURRENT_ROOT", runtime_root)
    monkeypatch.setattr(security_best_practices_report, "AGENT_HANDOFF_ROOT", handoff_root)
    monkeypatch.setattr(
        security_best_practices_report.dependency_audit_status,
        "build_status",
        lambda: {"verdict": "pass", "frontend": {"shipped_surface_clean": True}, "python": {"blocking_findings": []}},
    )
    monkeypatch.setattr(
        security_best_practices_report.service_hardening_status,
        "build_status",
        lambda: {"verdict": "pass", "missing_controls": []},
    )
    monkeypatch.setattr(
        security_best_practices_report,
        "inspect_electron_boundary",
        lambda: ([], ["electron locked down"]),
    )
    monkeypatch.setattr(
        security_best_practices_report,
        "inspect_dead_host_helper",
        lambda: ([], ["tunnels fixed"]),
    )
    monkeypatch.setattr(
        security_best_practices_report,
        "inspect_types_containment",
        lambda: ([], ["types contained"]),
    )
    payload = security_best_practices_report.build_status(tmp_path / "report.md")
    assert payload["verdict"] == "fail"
    assert payload["critical_blockers"]
