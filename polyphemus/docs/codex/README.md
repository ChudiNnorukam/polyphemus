# Polyphemus Codex

Code-grounded knowledge graph that powers the librarian protocol (see
`~/.claude/skills/librarian/SKILL.md`). Each node maps a product-intent
concept (how Chudi talks about the bot) to a concrete `file:line` range
(how the code actually works). A drift detector re-hashes those ranges
so stale nodes get flagged before they mislead a reader.

## Layout

```
docs/codex/
  README.md          — this file
  domains.yaml       — the 16 domain keys (source: docs/GLOSSARY.md)
  INDEX.json         — generated term -> node map; do not hand-edit
  nodes/
    <id>.md          — one node per concept (YAML frontmatter + body)
```

## Node frontmatter

```yaml
---
id: <kebab-case>           # unique; also the filename stem
name: <Title Case>          # human label
domain: <domains.yaml key>  # one of the 16 domains
aliases: [...]              # synonyms, env-var names, common misspellings
code_refs:
  - path: <repo-relative>   # relative to polyphemus/ (codex_verify REPO_ROOT)
    lines: "<A-B>"          # 1-indexed inclusive
    sha256: null            # populated on first verify
related: [...]              # sibling node ids
parent_concepts: [...]
child_concepts: [...]
last_verified: null         # ISO8601 UTC; bumped by codex_verify on clean match
confidence: verified        # verified | inferred | target
---
```

## Node body sections

1. **What** — one paragraph in Chudi's vocabulary. Product-intent first.
2. **Where** — the `file:line` walk. 2-3 call sites max. No narration.
3. **When-to-touch** — user-visible trigger phrases that should route here.

## Authoring a new node

1. Pick an id (kebab-case; unique across nodes/).
2. Write the frontmatter with `sha256: null` and `last_verified: null`.
3. Write the three body sections.
4. `python3 tools/codex_verify.py --node <id>` — populates hashes, bumps `last_verified`.
5. `python3 tools/codex_verify.py --rebuild-index` — refreshes `INDEX.json`.

## Drift

`python3 tools/codex_verify.py --all` re-hashes every code_ref. If a
line range's current hash differs from the stored sha256, the tool
prints `DRIFT <id>: <path>:<lines>` and exits 1. The librarian
protocol must NOT act on a node with unresolved drift — either fix
the node (rename/move happened) or flag to Chudi (semantic shift).

## Scope

The codex is polyphemus-only. Paths are relative to the polyphemus/
repo root (the directory that owns this README). Do not cite files
outside this tree — if a concept lives upstream (e.g. LIFECYCLE.md at
the project root), cite the polyphemus mirror (e.g. `pre_deploy_check.py`)
or promote a summary into a markdown file inside `docs/`.
