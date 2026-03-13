# Polyphemus Agent Adapter

This repo uses a shared cross-agent contract so Codex and Claude read the same state, policy, and handoff bundle.

## Required startup flow

1. Read [agent/contract/repo_agent_contract.json](/Users/chudinnorukam/Projects/business/polyphemus/agent/contract/repo_agent_contract.json).
2. Read [agent/policy/live_trading_policy.md](/Users/chudinnorukam/Projects/business/polyphemus/agent/policy/live_trading_policy.md).
3. Run `python3 /Users/chudinnorukam/Projects/business/polyphemus/tools/agent_bootstrap.py --print-json` before making strategy or live-trading recommendations.
4. Use [agent/handoff/current_state.json](/Users/chudinnorukam/Projects/business/polyphemus/agent/handoff/current_state.json) and [agent/handoff/next_role.json](/Users/chudinnorukam/Projects/business/polyphemus/agent/handoff/next_role.json) as the current shared truth for agent switching.

## Hard rules

- Default posture is `shadow first`.
- Never recommend or enable live trading while the current gate is `NO-GO`.
- Never let stale memory override the current bootstrap bundle.
- Never auto-run SSH cache refresh or live config mutation as part of activation.

Codex should treat this file as a thin adapter. The contract file is the canonical source of truth.
