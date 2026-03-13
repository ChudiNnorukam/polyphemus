# Agent Handoff

Use this playbook whenever Codex, Claude Opus, or Claude Sonnet switches responsibility inside this repo.

## When to hand off

- The next task belongs to a different specialist role.
- The current agent is missing current runtime truth.
- A fresh bootstrap changed the blockers or next action.

## What must be preserved

- Current gate verdict.
- Current audit status.
- Current shadow checklist and next action.
- Recommended next role.
- Latest relevant evidence paths.

## What must be revalidated

- Bootstrap freshness.
- Audit status if it is `unknown`.
- Gate blockers before any live-trading discussion.
- Any claim derived from stale chat memory rather than current runtime JSON.

## What no agent may assume

- That prior chat confidence outweighs the current state bundle.
- That a replay result alone justifies live trading.
- That Codex and Claude may maintain separate truths for this repo.
