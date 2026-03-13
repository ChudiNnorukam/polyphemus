#!/usr/bin/env python3
"""Ingest pinned external primary sources into the repo-local KB."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .kb_common import (
        EXTERNAL_PRIMARY_DOCS,
        EXTERNAL_PRIMARY_ROOT,
        PROJECT_ROOT,
        build_document,
        dump_json,
        ensure_kb_dirs,
        load_json,
        now_iso,
    )
except ImportError:  # pragma: no cover - script execution fallback
    from kb_common import (
        EXTERNAL_PRIMARY_DOCS,
        EXTERNAL_PRIMARY_ROOT,
        PROJECT_ROOT,
        build_document,
        dump_json,
        ensure_kb_dirs,
        load_json,
        now_iso,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=EXTERNAL_PRIMARY_ROOT / "source_manifest.json",
        help="Pinned source manifest JSON",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def ingest_external(manifest_path: Path) -> dict:
    ensure_kb_dirs()
    entries = load_json(manifest_path)
    documents = []
    for entry in entries:
        document = build_document(
            doc_id=entry["id"],
            title=entry["title"],
            source_url=entry["canonical_url"],
            source_class="external_primary",
            trust_tier=entry["trust_tier"],
            published_at=entry.get("published_at", ""),
            topics=entry.get("topic_tags", []),
            summary=entry.get("summary", ""),
            actionable_lessons=entry.get("actionable_lessons", []),
            anti_patterns=entry.get("anti_misuse_notes", []),
            body=(
                "Actionable lessons:\n- "
                + "\n- ".join(entry.get("actionable_lessons", []))
                + "\n\nWhat it does not justify:\n- "
                + "\n- ".join(entry.get("anti_misuse_notes", []))
            ),
            source_path=str(manifest_path.relative_to(PROJECT_ROOT)),
            citation=f"{entry['title']} ({entry['canonical_url']})",
            metadata={"source_type": entry["source_type"]},
        )
        doc_path = EXTERNAL_PRIMARY_DOCS / f"{document['id']}.json"
        dump_json(doc_path, document)
        documents.append(document)

    bundle = {
        "generated_at": now_iso(),
        "document_count": len(documents),
        "documents": [document["id"] for document in documents],
    }
    dump_json(EXTERNAL_PRIMARY_ROOT / "bundle.json", bundle)
    return bundle


def main() -> int:
    args = parse_args()
    payload = ingest_external(args.manifest)
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        import json
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        import json
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
