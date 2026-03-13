#!/usr/bin/env python3
"""Query the local Polyphemus KB using lexical retrieval and metadata filters."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from .kb_common import INDEX_PATH, SOURCE_CLASS_PRIORITY, dump_json, load_json, tokenize
except ImportError:  # pragma: no cover - script execution fallback
    from kb_common import INDEX_PATH, SOURCE_CLASS_PRIORITY, dump_json, load_json, tokenize


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="", help="Free-text query")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--source-class", default="", help="Optional source class filter")
    parser.add_argument("--trust-tier", default="", help="Optional trust tier filter")
    parser.add_argument("--topic", action="append", default=[], help="Optional topic filter")
    parser.add_argument("--list-docs", action="store_true", help="List top documents instead of passage hits")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def load_index() -> Dict[str, Any]:
    return load_json(INDEX_PATH)


def bm25ish_score(query_tokens: List[str], chunk: Dict[str, Any], inverted_index: Dict[str, Dict[str, int]], chunk_count: int) -> float:
    if not query_tokens:
        return 0.0
    score = 0.0
    for token in query_tokens:
        postings = inverted_index.get(token, {})
        term_freq = postings.get(chunk["chunk_id"], 0)
        if not term_freq:
            continue
        doc_freq = max(1, len(postings))
        idf = math.log((chunk_count + 1) / doc_freq)
        score += term_freq * idf
    score *= SOURCE_CLASS_PRIORITY.get(chunk.get("source_class", ""), 1.0)
    if chunk.get("trust_tier") == "T1":
        score *= 1.15
    return score


def matches_filters(chunk: Dict[str, Any], args: argparse.Namespace) -> bool:
    if args.source_class and chunk.get("source_class") != args.source_class:
        return False
    if args.trust_tier and chunk.get("trust_tier") != args.trust_tier:
        return False
    if args.topic:
        topics = set(chunk.get("topics", []))
        if not all(topic in topics for topic in args.topic):
            return False
    return True


def query_hits(args: argparse.Namespace) -> dict:
    index = load_index()
    query_tokens = tokenize(args.query)
    ranked: List[Dict[str, Any]] = []
    for chunk in index["chunks"]:
        if not matches_filters(chunk, args):
            continue
        score = bm25ish_score(query_tokens, chunk, index["inverted_index"], index["chunk_count"])
        if args.query and score <= 0:
            continue
        ranked.append({
            "document_id": chunk["document_id"],
            "score": round(score, 6),
            "matched_text": chunk["text"],
            "citation": chunk["citation"],
            "trust_tier": chunk["trust_tier"],
            "source_class": chunk["source_class"],
            "source_url": chunk.get("source_url", ""),
            "title": chunk["title"],
            "topics": chunk.get("topics", []),
        })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    if args.list_docs:
        docs = {}
        for hit in ranked:
            docs.setdefault(hit["document_id"], hit)
        hits = list(docs.values())[: args.limit]
    else:
        hits = ranked[: args.limit]
    return {
        "generated_at": index["generated_at"],
        "query": args.query,
        "count": len(hits),
        "hits": hits,
    }


def main() -> int:
    args = parse_args()
    payload = query_hits(args)
    if args.json_out:
        dump_json(args.json_out, payload)
    if args.print_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
