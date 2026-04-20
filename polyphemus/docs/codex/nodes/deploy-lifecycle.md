---
id: deploy-lifecycle
name: Deploy Lifecycle
domain: infrastructure
aliases:
- LIFECYCLE
- predeploy
- pre-deploy check
- systemd restart
- scp deploy
- open positions check
code_refs:
- path: pre_deploy_check.py
  lines: 1-15
  sha256: f43b07b55f0c07be
- path: CLAUDE.md
  lines: 7-18
  sha256: ebd7d1c8c15357fb
- path: SHADOW_DEPLOY_CHECKLIST.md
  lines: 1-40
  sha256: f83718e2f915f5bd
related:
- mtc-gate
- infrastructure
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: verified
---

## What

The deploy lifecycle is the end-to-end discipline that separates a
code change on Chudi's laptop from a live change on emmanuel. Every
step exists because skipping it cost real money: schema mismatches,
circuit-breaker poisoning, swallowed exceptions, orphan trades, config
drift, and the famous $212 restart-with-open-positions incident. The
rule is: every deploy phase produces an artifact before the next
phase starts, and "I think it works" is never artifact.

Mechanical sequence for code-only deploys:
1. `systemctl stop lagbot@<instance>` (not when positions are open)
2. `scp` all modified files together (partial deploys cause AttributeError)
3. `rm -rf __pycache__` on the VPS
4. `py_compile` check for syntax
5. `systemctl start`
6. `journalctl --since '60 seconds ago'` — grep for Traceback, not
   just `is-active`

For `.env` changes: `cp .env .env.bak.$(date +%s)` before any `sed -i`
(enforced by the `block_unbacked_env_sed.sh` hook). `.env` changes are
per-instance; the shared codebase means one code deploy hits all
instances.

## Where

- Programmatic pre/post deploy checks at
  [pre_deploy_check.py:1-15](pre_deploy_check.py#L1-L15) — module
  docstring enumerates the losses that motivated each check.
- Claude adapter rules (required startup flow + hard rules) at
  [CLAUDE.md:7-18](CLAUDE.md#L7-L18) — read repo contract, check gate
  state, never silently mutate live systems.
- Shadow deploy checklist at
  [SHADOW_DEPLOY_CHECKLIST.md:1-40](SHADOW_DEPLOY_CHECKLIST.md#L1-L40)
  — manual fallback when predeploy.sh automation can't be used.

## When-to-touch

Route here when the user says:

- "deploy" / "ship it" / "push to emmanuel"
- ".env change" / "update the trading param"
- "restart lagbot" / "systemctl" / "service is down"
- "partial deploy" / "AttributeError after restart"
- "check open positions before restart"
- anything involving `scp`, `py_compile`, `journalctl`, or the
  post-deploy 60-second window
