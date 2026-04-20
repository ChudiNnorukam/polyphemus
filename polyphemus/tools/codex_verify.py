"""codex_verify — read-only drift detector for the librarian codex.

The codex lives at polyphemus/docs/codex/ and is the graph that powers the
librarian protocol (see ~/.claude/skills/librarian/SKILL.md). Each node has
YAML frontmatter listing code_refs[{path, lines, sha256}]. This tool:

  * --all           Walk every node, re-hash code_refs, report drift.
  * --node <id>     Verify one node by id.
  * --rebuild-index Regenerate INDEX.json from nodes/*.md frontmatter.

Exit codes:
  0  clean (or hashes populated on first verify)
  1  drift detected (a code_ref's current content hash != stored sha256)
  2  usage/config error (missing file, malformed frontmatter, unknown node)

Design notes:
  * Never writes outside docs/codex/. Safe for any hook or pre-commit.
  * On first verify of a node with sha256==null, populates the hash and
    bumps last_verified. This lets seed nodes be hand-authored without
    computing hashes by hand.
  * Paths in code_refs are repo-relative (from the polyphemus/ root so the
    tool is portable to any checkout).
  * Uses only stdlib + PyYAML (already a polyphemus dependency).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CODEX_DIR = REPO_ROOT / "docs" / "codex"
NODES_DIR = CODEX_DIR / "nodes"
INDEX_PATH = CODEX_DIR / "INDEX.json"


@dataclass
class CodeRef:
    path: str
    lines: str
    sha256: str | None = None


@dataclass
class Node:
    id: str
    name: str
    domain: str
    file: Path
    frontmatter: dict = field(default_factory=dict)
    body: str = ""
    code_refs: list[CodeRef] = field(default_factory=list)


def _parse_node(md_path: Path) -> Node:
    """Split a node markdown file into frontmatter + body and hydrate."""
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{md_path}: missing YAML frontmatter")
    _, fm_raw, body = text.split("---\n", 2)
    fm = yaml.safe_load(fm_raw) or {}
    for required in ("id", "name", "domain"):
        if required not in fm:
            raise ValueError(f"{md_path}: frontmatter missing required field '{required}'")
    refs = []
    for r in fm.get("code_refs") or []:
        refs.append(
            CodeRef(
                path=r["path"],
                lines=r["lines"],
                sha256=r.get("sha256"),
            )
        )
    return Node(
        id=fm["id"],
        name=fm["name"],
        domain=fm["domain"],
        file=md_path,
        frontmatter=fm,
        body=body,
        code_refs=refs,
    )


def _read_line_range(path: Path, lines_spec: str) -> str | None:
    """Return the substring of `path` for lines_spec 'A-B' (1-indexed, inclusive).

    Returns None if the file is missing or the range is out of bounds.
    """
    if not path.exists():
        return None
    try:
        start_s, end_s = lines_spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        raise ValueError(f"invalid lines spec {lines_spec!r}; expected 'A-B'")
    with path.open("r", encoding="utf-8") as fh:
        all_lines = fh.readlines()
    if start < 1 or end > len(all_lines) or start > end:
        return None
    return "".join(all_lines[start - 1 : end])


def _hash_range(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _write_node(node: Node, new_fm: dict) -> None:
    """Rewrite a node's frontmatter preserving body."""
    rendered = yaml.safe_dump(new_fm, sort_keys=False, default_flow_style=False)
    node.file.write_text(f"---\n{rendered}---\n{node.body}", encoding="utf-8")


def _verify_node(node: Node, *, populate_missing: bool = True) -> tuple[bool, list[str]]:
    """Verify all code_refs in a node. Return (clean, messages)."""
    messages = []
    clean = True
    updated_refs = []
    for ref in node.code_refs:
        abs_path = REPO_ROOT / ref.path
        content = _read_line_range(abs_path, ref.lines)
        if content is None:
            messages.append(f"MISSING {node.id}: {ref.path}:{ref.lines}")
            clean = False
            updated_refs.append({"path": ref.path, "lines": ref.lines, "sha256": ref.sha256})
            continue
        current = _hash_range(content)
        if ref.sha256 is None:
            if populate_missing:
                messages.append(f"POPULATED {node.id}: {ref.path}:{ref.lines} sha256={current}")
                updated_refs.append({"path": ref.path, "lines": ref.lines, "sha256": current})
            else:
                messages.append(f"UNHASHED {node.id}: {ref.path}:{ref.lines}")
                clean = False
                updated_refs.append({"path": ref.path, "lines": ref.lines, "sha256": None})
        elif current != ref.sha256:
            messages.append(
                f"DRIFT {node.id}: {ref.path}:{ref.lines} "
                f"stored={ref.sha256} current={current}"
            )
            clean = False
            updated_refs.append({"path": ref.path, "lines": ref.lines, "sha256": ref.sha256})
        else:
            updated_refs.append({"path": ref.path, "lines": ref.lines, "sha256": current})
    if clean and populate_missing:
        new_fm = dict(node.frontmatter)
        new_fm["code_refs"] = updated_refs
        new_fm["last_verified"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_node(node, new_fm)
    return clean, messages


def _load_all_nodes() -> list[Node]:
    if not NODES_DIR.is_dir():
        print(f"codex_verify: nodes dir missing at {NODES_DIR}", file=sys.stderr)
        sys.exit(2)
    out = []
    for md in sorted(NODES_DIR.glob("*.md")):
        try:
            out.append(_parse_node(md))
        except ValueError as e:
            print(f"codex_verify: {e}", file=sys.stderr)
            sys.exit(2)
    return out


def cmd_verify_all() -> int:
    nodes = _load_all_nodes()
    overall_clean = True
    for node in nodes:
        clean, msgs = _verify_node(node)
        for m in msgs:
            print(m)
        if not clean:
            overall_clean = False
    if overall_clean:
        print(f"OK {len(nodes)} nodes clean")
        return 0
    return 1


def cmd_verify_one(node_id: str) -> int:
    nodes = _load_all_nodes()
    for node in nodes:
        if node.id == node_id:
            clean, msgs = _verify_node(node)
            for m in msgs:
                print(m)
            return 0 if clean else 1
    print(f"codex_verify: unknown node id {node_id!r}", file=sys.stderr)
    return 2


def cmd_rebuild_index() -> int:
    """Regenerate INDEX.json from nodes/*.md frontmatter."""
    nodes = _load_all_nodes()
    domain_map: dict[str, list[str]] = {}
    node_map: dict[str, dict] = {}
    for node in nodes:
        fm = node.frontmatter
        node_map[node.id] = {
            "file": f"nodes/{node.file.name}",
            "domain": node.domain,
            "name": node.name,
            "aliases": fm.get("aliases", []),
            "related": fm.get("related", []),
            "confidence": fm.get("confidence", "inferred"),
            "last_verified": fm.get("last_verified"),
        }
        domain_map.setdefault(node.domain, []).append(node.id)
    index = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nodes": node_map,
        "domains": domain_map,
    }
    INDEX_PATH.write_text(json.dumps(index, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"REBUILT {INDEX_PATH} ({len(node_map)} nodes, {len(domain_map)} domains)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="codex_verify", description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="verify every node")
    g.add_argument("--node", metavar="ID", help="verify one node by id")
    g.add_argument("--rebuild-index", action="store_true", help="regenerate INDEX.json")
    args = p.parse_args(argv)
    if args.rebuild_index:
        return cmd_rebuild_index()
    if args.all:
        return cmd_verify_all()
    return cmd_verify_one(args.node)


if __name__ == "__main__":
    sys.exit(main())
