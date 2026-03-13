#!/usr/bin/env python3
"""Normalize internal reports and runtime snapshots into the repo-local KB."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

try:
    from .kb_common import (
        INTERNAL_REPORT_DOCS,
        INTERNAL_REPORT_ROOT,
        INTERNAL_RUNTIME_ROOT,
        PROJECT_ROOT,
        build_document,
        dump_json,
        ensure_kb_dirs,
        now_iso,
        read_text,
        slugify,
    )
except ImportError:  # pragma: no cover - script execution fallback
    from kb_common import (
        INTERNAL_REPORT_DOCS,
        INTERNAL_REPORT_ROOT,
        INTERNAL_RUNTIME_ROOT,
        PROJECT_ROOT,
        build_document,
        dump_json,
        ensure_kb_dirs,
        now_iso,
        read_text,
        slugify,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "dario_output")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def summarize_markdown(text: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    summary = " ".join(lines[:3])[:500]
    lessons = []
    for line in lines:
        if line.startswith("- ") or line.startswith("* "):
            lessons.append(line[2:].strip())
        if len(lessons) >= 4:
            break
    return summary, lessons


def run_json_script(script_name: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        json_path = Path(handle.name)
    cmd = [
        "python3",
        str(PROJECT_ROOT / "tools" / script_name),
        "--json-out",
        str(json_path),
        "--print-json",
    ]
    subprocess.run(cmd, cwd=str(PROJECT_ROOT.parent), capture_output=True, text=True, check=True)
    return json.loads(json_path.read_text(encoding="utf-8"))


def ingest_internal(report_dir: Path) -> dict:
    ensure_kb_dirs()
    report_docs = []
    for path in sorted(report_dir.glob("*.md")):
        text = read_text(path)
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
        summary, lessons = summarize_markdown(text)
        document = build_document(
            doc_id=f"internal-report-{slugify(path.stem)}",
            title=title or path.stem,
            source_url="",
            source_class="internal_report",
            trust_tier="T1",
            topics=["internal-report", "analysis"],
            summary=summary,
            actionable_lessons=lessons,
            anti_patterns=["Do not treat a historical report as fresher than current runtime status."],
            body=text,
            source_path=str(path.relative_to(PROJECT_ROOT)),
            citation=f"{path.name}",
        )
        dump_json(INTERNAL_REPORT_DOCS / f"{document['id']}.json", document)
        report_docs.append(document["id"])

    for repo_path in [
        PROJECT_ROOT / "PROGRESS.md",
        PROJECT_ROOT / "CLAUDE_MEMORY_RECONCILIATION.md",
        PROJECT_ROOT / "BTC5M_BOT_IMPROVEMENT_PLAN.md",
    ]:
        if not repo_path.exists():
            continue
        text = read_text(repo_path)
        title = text.splitlines()[0].lstrip("# ").strip() if text else repo_path.stem
        summary, lessons = summarize_markdown(text)
        document = build_document(
            doc_id=f"internal-report-{slugify(repo_path.stem)}",
            title=title or repo_path.stem,
            source_url="",
            source_class="internal_report",
            trust_tier="T1",
            topics=["internal-report", "repo-memory"],
            summary=summary,
            actionable_lessons=lessons,
            anti_patterns=["Do not override fresher runtime facts with stale notes."],
            body=text,
            source_path=str(repo_path.relative_to(PROJECT_ROOT)),
            citation=repo_path.name,
        )
        dump_json(INTERNAL_REPORT_DOCS / f"{document['id']}.json", document)
        report_docs.append(document["id"])

    runtime_payloads = {
        "shadow_window_status": run_json_script("shadow_window_status.py"),
        "shadow_window_checklist": run_json_script("shadow_window_checklist.py"),
        "go_live_gate_status": run_json_script("btc5m_ensemble_go_live_gate.py"),
        "audit_mismatch_status": run_json_script("emmanuel_audit_mismatch_check.py"),
    }
    for name, payload in runtime_payloads.items():
        doc = build_document(
            doc_id=f"runtime-{name}",
            title=name.replace("_", " ").title(),
            source_url="",
            source_class="internal_runtime",
            trust_tier="T1",
            topics=["runtime", "current-state"],
            summary=json.dumps(payload, sort_keys=True)[:500],
            actionable_lessons=[f"Use {name} as current-state evidence before making any live recommendation."],
            anti_patterns=["Do not treat runtime snapshots as permanent truth; they must be refreshed."],
            body=json.dumps(payload, indent=2, sort_keys=True),
            source_path=f"kb/internal/runtime/{name}.json",
            citation=name,
        )
        dump_json(INTERNAL_RUNTIME_ROOT / f"{name}.json", doc)

    bundle = {
        "generated_at": now_iso(),
        "report_doc_count": len(report_docs),
        "runtime_doc_count": len(runtime_payloads),
        "report_docs": report_docs,
        "runtime_docs": list(runtime_payloads.keys()),
    }
    dump_json(INTERNAL_REPORT_ROOT / "bundle.json", bundle)
    return bundle


def main() -> int:
    args = parse_args()
    payload = ingest_internal(args.report_dir)
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
