#!/usr/bin/env python3
"""Build the local lexical KB index for RAG-style retrieval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .kb_common import INDEX_PATH, build_index_payload, dump_json, ensure_kb_dirs, load_all_documents
except ImportError:  # pragma: no cover - script execution fallback
    from kb_common import INDEX_PATH, build_index_payload, dump_json, ensure_kb_dirs, load_all_documents


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def build_index() -> dict:
    ensure_kb_dirs()
    documents = load_all_documents()
    index = build_index_payload(documents)
    dump_json(INDEX_PATH, index)
    return {
        "generated_at": index["generated_at"],
        "document_count": index["document_count"],
        "chunk_count": index["chunk_count"],
        "index_path": str(INDEX_PATH),
    }


def main() -> int:
    args = parse_args()
    payload = build_index()
    if args.json_out:
        dump_json(Path(args.json_out), payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
