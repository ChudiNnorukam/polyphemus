#!/usr/bin/env python3
"""Shared helpers for the Polyphemus KB, retrieval, and decision tools."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
KB_ROOT = PROJECT_ROOT / "kb"
EXTERNAL_PRIMARY_ROOT = KB_ROOT / "external" / "primary"
EXTERNAL_PRIMARY_DOCS = EXTERNAL_PRIMARY_ROOT / "docs"
INTERNAL_REPORT_ROOT = KB_ROOT / "internal" / "reports"
INTERNAL_REPORT_DOCS = INTERNAL_REPORT_ROOT / "docs"
INTERNAL_RUNTIME_ROOT = KB_ROOT / "internal" / "runtime"
INTERNAL_RUNTIME_CURRENT_ROOT = INTERNAL_RUNTIME_ROOT / "current"
PLAYBOOK_ROOT = KB_ROOT / "playbooks"
INDEX_ROOT = KB_ROOT / "index"
INDEX_PATH = INDEX_ROOT / "kb_index.json"
AGENT_ROOT = PROJECT_ROOT / "agent"
AGENT_CONTRACT_ROOT = AGENT_ROOT / "contract"
AGENT_HANDOFF_ROOT = AGENT_ROOT / "handoff"

SOURCE_CLASS_PRIORITY = {
    "internal_runtime": 4.0,
    "internal_report": 3.0,
    "playbook": 2.0,
    "external_primary": 1.0,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "document"


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]{2,}", text.lower())


def normalize_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_text(path: Path) -> str:
    return normalize_text(path.read_text(encoding="utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def ensure_kb_dirs() -> None:
    for path in [
        EXTERNAL_PRIMARY_ROOT,
        EXTERNAL_PRIMARY_DOCS,
        INTERNAL_REPORT_ROOT,
        INTERNAL_REPORT_DOCS,
        INTERNAL_RUNTIME_ROOT,
        INTERNAL_RUNTIME_CURRENT_ROOT,
        PLAYBOOK_ROOT,
        INDEX_ROOT,
        AGENT_CONTRACT_ROOT,
        AGENT_HANDOFF_ROOT,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def build_document(
    *,
    doc_id: str,
    title: str,
    source_url: str,
    source_class: str,
    trust_tier: str,
    published_at: str = "",
    topics: Iterable[str] = (),
    summary: str = "",
    actionable_lessons: Iterable[str] = (),
    anti_patterns: Iterable[str] = (),
    body: str = "",
    source_path: str = "",
    citation: str = "",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    summary_text = normalize_text(summary)
    body_text = normalize_text(body)
    return {
        "id": doc_id,
        "title": title,
        "source_url": source_url,
        "source_class": source_class,
        "trust_tier": trust_tier,
        "published_at": published_at,
        "topics": list(topics),
        "summary": summary_text,
        "actionable_lessons": list(actionable_lessons),
        "anti_patterns": list(anti_patterns),
        "body": body_text,
        "source_path": source_path,
        "citation": citation or title,
        "metadata": metadata or {},
        "updated_at": now_iso(),
    }


def load_all_documents() -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for root in [EXTERNAL_PRIMARY_DOCS, INTERNAL_REPORT_DOCS, INTERNAL_RUNTIME_ROOT]:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            try:
                payload = load_json(path)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, list):
                docs.extend(payload)
            elif isinstance(payload, dict) and "id" in payload:
                docs.append(payload)
    for path in sorted(PLAYBOOK_ROOT.rglob("*.md")):
        text = read_text(path)
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
        docs.append(build_document(
            doc_id=f"playbook-{slugify(path.stem)}",
            title=title,
            source_url="",
            source_class="playbook",
            trust_tier="T2",
            topics=["playbook", "operations"],
            summary="\n".join(text.splitlines()[1:4]).strip(),
            body=text,
            source_path=str(path.relative_to(PROJECT_ROOT)),
            citation=title,
        ))
    return docs


def paragraph_chunks(document: Dict[str, Any], max_chars: int = 900) -> List[Dict[str, Any]]:
    pieces: List[str] = []
    for field in [
        document.get("summary", ""),
        "\n".join(document.get("actionable_lessons", [])),
        "\n".join(document.get("anti_patterns", [])),
        document.get("body", ""),
    ]:
        field_text = normalize_text(field)
        if field_text:
            pieces.extend([part.strip() for part in field_text.split("\n\n") if part.strip()])

    chunks: List[Dict[str, Any]] = []
    current: List[str] = []
    current_length = 0
    chunk_index = 0
    for piece in pieces:
        if current and current_length + len(piece) + 2 > max_chars:
            text = "\n\n".join(current)
            chunks.append({"chunk_id": f"{document['id']}#{chunk_index}", "text": text})
            chunk_index += 1
            current = [piece]
            current_length = len(piece)
            continue
        current.append(piece)
        current_length += len(piece) + 2
    if current:
        text = "\n\n".join(current)
        chunks.append({"chunk_id": f"{document['id']}#{chunk_index}", "text": text})
    return chunks


def build_index_payload(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    doc_map = {document["id"]: document for document in documents}
    chunks: List[Dict[str, Any]] = []
    inverted: Dict[str, Dict[str, int]] = {}
    for document in documents:
        for chunk in paragraph_chunks(document):
            tokens = tokenize(chunk["text"])
            if not tokens:
                continue
            chunk_payload = {
                "chunk_id": chunk["chunk_id"],
                "document_id": document["id"],
                "tokens": tokens,
                "text": chunk["text"],
                "source_class": document["source_class"],
                "trust_tier": document["trust_tier"],
                "topics": document.get("topics", []),
                "title": document["title"],
                "citation": document.get("citation", document["title"]),
                "source_url": document.get("source_url", ""),
            }
            chunks.append(chunk_payload)
            term_counts: Dict[str, int] = {}
            for token in tokens:
                term_counts[token] = term_counts.get(token, 0) + 1
            for token, count in term_counts.items():
                postings = inverted.setdefault(token, {})
                postings[chunk_payload["chunk_id"]] = count
    return {
        "generated_at": now_iso(),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "documents": doc_map,
        "chunks": chunks,
        "inverted_index": inverted,
    }
