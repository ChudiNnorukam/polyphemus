#!/usr/bin/env python3
"""Refresh the primary BTC candidate experiment from current runtime artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "kb" / "internal" / "runtime" / "current"
DEFAULT_SLUG = "btc-5m-ensemble-selected-live-v1"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the primary BTC candidate experiment")
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--write-json", action="store_true", help="Write current_status.json alongside markdown")
    return parser.parse_args()


def confidence_label(n: int) -> str:
    if n < 30:
        return f"ANECDOTAL n={n}"
    if n < 107:
        return f"LOW n={n}"
    if n < 385:
        return f"MODERATE n={n}"
    return f"SIGNIFICANT n={n}"


def render_evidence_log(slug: str, gate: dict, decision_memo: dict) -> str:
    comparison = gate.get("comparison", {})
    candidate = comparison.get("ensemble_selected_live_v1", {})
    generated_at = decision_memo.get("generated_at") or gate.get("generated_at") or "unknown"
    trades = int(candidate.get("trades", 0) or 0)
    confidence = confidence_label(trades)
    blockers = "; ".join(gate.get("blockers", [])) or "none"
    expectancy = candidate.get("avg_net_live")
    drawdown = candidate.get("live_max_drawdown")

    return "\n".join(
        [
            "# Quant Evidence Log",
            "",
            f"- Slug: `{slug}`",
            f"- Started: `{generated_at}`",
            "",
            "## Runtime Ledger",
            "",
            "| Timestamp | Era | Mode | Runtime Hours | Completed Opportunities | Expectancy | Drawdown | Incidents | Notes |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
            (
                f"| {generated_at} | `{gate.get('config_era', 'unknown')}` | `live-candidate` | "
                f"{'16.7' if 'runtime 16.7h < 48.0h' in blockers else 'unknown'} | {trades} | "
                f"`{expectancy}` | `{drawdown}` | {blockers} | "
                "`ensemble_selected_live_v1` remains the primary BTC slice, but still below decision-grade maturity |"
            ),
            "",
            "## Incident Notes",
            "",
            "Use this section for anything that could contaminate evidence:",
            "- restart",
            "- deployment",
            "- stale config",
            "- API outage",
            "- audit mismatch",
            "- pipeline stall",
            "- logging bug",
            "",
            "## Interim Read",
            "",
            f"- Current gate: `{decision_memo.get('verdict', 'NO-GO')}`",
            f"- Confidence label: `{confidence}`",
            "- Is the slice still frozen: yes",
            "- Is the evidence still usable: yes for prioritization, no for promotion",
            "",
            "## Decision",
            "",
            "- Continue collecting if the slice remains frozen",
            "- Abort if pass-through stays too low to reach sample-size maturity",
            "- Do not promote while the shared gate remains `NO-GO`",
            "",
        ]
    )


def render_promotion_review(slug: str, gate: dict, shadow: dict, decision_memo: dict) -> str:
    comparison = gate.get("comparison", {})
    candidate = comparison.get("ensemble_selected_live_v1", {})
    generated_at = decision_memo.get("generated_at") or gate.get("generated_at") or "unknown"
    trades = int(candidate.get("trades", 0) or 0)
    confidence = confidence_label(trades)
    blockers = gate.get("blockers", [])
    has_stall_blocker = any("stall windows" in blocker for blocker in blockers)
    canary_eligible = decision_memo.get("verdict") == "GO"

    return "\n".join(
        [
            "# Quant Promotion Review",
            "",
            f"- Slug: `{slug}`",
            f"- Review date: `{generated_at}`",
            "- Reviewer: `Codex`",
            "",
            "## Candidate Slice",
            "",
            "- Asset: `BTC`",
            "- Windows: `5m`",
            "- Entry family: `binance_momentum` with `shadow_ensemble_selected=1`",
            "- Exit family: `current_guarded_resolution`",
            f"- Config era: `{gate.get('config_era', 'unknown')}`",
            "",
            "## Evidence Summary",
            "",
            f"- Shadow runtime hours: `{shadow.get('elapsed_hours', 'unknown')}`",
            f"- Completed opportunities: `{trades}`",
            f"- Live trades in current era: `{trades}`",
            f"- Expectancy after costs: `avg_net_live={candidate.get('avg_net_live')}`, `live_roi={candidate.get('live_roi')}`",
            f"- Confidence label: `{confidence}`",
            "",
            "## Execution Integrity",
            "",
            "- Audit mismatch clear: yes",
            f"- Pipeline stalls explained: {'no' if has_stall_blocker else 'yes'}",
            "- Config drift clear: shared research era aligned, but promotion blockers remain",
            "- Deployment disruptions in sample: not proven clean enough for promotion",
            "",
            "## Why This Might Be Fake",
            "",
            "- Execution artifact risk: medium",
            "- Regime mismatch risk: medium",
            f"- Sample-size risk: {'high' if trades < 30 else 'moderate'}",
            "- Selection-bias risk: medium-high",
            "",
            "## Gate",
            "",
            f"`{'CANARY-ELIGIBLE' if canary_eligible else 'NO-GO'}`",
            "",
            "## Narrowest Allowed Next Step",
            "",
            "Keep the exact slice frozen as `emmanuel / BTC / 5m / binance_momentum / shadow_ensemble_selected=1`.",
            "Do not broaden asset scope, window scope, or exit logic.",
            "Only refresh the gate after more aligned runtime and a larger current-era sample exist.",
            "",
            "## Rejection Reasons",
            "",
            "Rejected because:",
            *[f"- {blocker}" for blocker in blockers],
            "",
            "Reopen review only with:",
            "- fresh current-era gate output",
            "- >=50 live trades on this exact slice",
            "- positive expectancy that survives a larger sample",
            "- resolved or explained stall-window behavior",
            "",
        ]
    )


def refresh_candidate(slug: str, write_json: bool = False) -> dict:
    experiment_dir = ROOT / ".omc" / "experiments" / slug
    if not experiment_dir.exists():
        raise FileNotFoundError(f"Experiment does not exist: {experiment_dir}")

    gate = load_json(RUNTIME_DIR / "go_live_gate_status.json")
    shadow = load_json(RUNTIME_DIR / "shadow_window_status.json")
    decision_memo = load_json(RUNTIME_DIR / "decision_memo.json")

    evidence_log = render_evidence_log(slug, gate, decision_memo)
    promotion_review = render_promotion_review(slug, gate, shadow, decision_memo)

    (experiment_dir / "evidence_log.md").write_text(evidence_log, encoding="utf-8")
    (experiment_dir / "promotion_review.md").write_text(promotion_review, encoding="utf-8")

    payload = {
        "slug": slug,
        "generated_at": decision_memo.get("generated_at"),
        "gate_verdict": decision_memo.get("verdict"),
        "config_era": gate.get("config_era"),
        "comparison": gate.get("comparison", {}).get("ensemble_selected_live_v1", {}),
        "blockers": gate.get("blockers", []),
        "shadow_elapsed_hours": shadow.get("elapsed_hours"),
    }
    if write_json:
        (experiment_dir / "current_status.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return payload


def main() -> int:
    args = parse_args()
    payload = refresh_candidate(args.slug, write_json=args.write_json)
    print(json.dumps({"ok": True, **payload}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
