# Security Best Practices Report

- Generated: `2026-03-13T05:11:36.310890+00:00`
- Verdict: `pass`
- Safe To Run Shadow: `True`
- Eligible For Narrow Live Rollout: `False`

## Critical

- none

## High

- none

## Moderate

- none

## Fixed In This Pass

- Electron Python tool execution is restricted to an explicit allowlist.
- Electron report opening is constrained to approved repo roots.
- Electron BrowserWindow security flags stay on the safe baseline.
- Tunnel helper no longer references dead or backup hosts.
- types.py shadowing risk is contained by deterministic parent-directory quality-gate invocation.
- Frontend production dependency audit is clean; remaining advisories are build/dev-only.
- Python deploy path is exact-pinned through requirements-lock.txt.
- Local systemd unit includes the hardening baseline required for shadow-safe deployment.

## Dependency Posture

- Frontend shipped-surface clean: `True`
- Frontend raw findings: `5`
- Frontend prod findings: `0`
- Python pinned deploy path clean: `True`

## Service Hardening

- Service verdict: `pass`
- Missing controls: `none`
