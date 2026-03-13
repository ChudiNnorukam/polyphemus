# Polyphemus Claude Adapter

Claude should use the same repo contract and current-state bundle as Codex.

## Required startup flow

1. Read [agent/contract/repo_agent_contract.json](/Users/chudinnorukam/Projects/business/polyphemus/agent/contract/repo_agent_contract.json).
2. Read [agent/policy/live_trading_policy.md](/Users/chudinnorukam/Projects/business/polyphemus/agent/policy/live_trading_policy.md).
3. Run `python3 /Users/chudinnorukam/Projects/business/polyphemus/tools/agent_bootstrap.py --print-json` before making strategy or live-trading recommendations.
4. Use [agent/handoff/current_state.json](/Users/chudinnorukam/Projects/business/polyphemus/agent/handoff/current_state.json) and [agent/handoff/next_role.json](/Users/chudinnorukam/Projects/business/polyphemus/agent/handoff/next_role.json) for seamless handoff with Codex or another Claude session.

## Hard rules

- Do not invent a private repo memory model when the bootstrap bundle exists.
- Do not recommend live trading while the current gate is `NO-GO` or audit state is not current.
- Do not silently mutate live systems during activation.

This file is an adapter only. Shared operational truth lives in the repo contract and runtime bundle.
