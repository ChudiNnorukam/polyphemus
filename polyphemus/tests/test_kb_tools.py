from pathlib import Path
from types import SimpleNamespace

from polyphemus.tools import kb_decision_memo, kb_query, run_agent_evals
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

    assert payload["count"] == 2
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
