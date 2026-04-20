# Polyphemus Decision Principles

This file encodes the operator's (Chudi's) values for **decision-mode picks** in polyphemus. When the librarian has to choose between plausible options, this is the first authority it consults. Fallback order: node bodies → `~/.claude/rules/chudi-frame.md` → node confidence labels.

Scope: polyphemus only. Other repos have their own PRINCIPLES.md if adopted.

Every principle below is grounded in either (a) a named bug/precedent with a measured cost, or (b) an explicit statement in existing node bodies, MEMORY.md, or chudi-frame. None are fabricated.

---

## P1 — Observability at write time, not derivable at read time

**Rule:** If a debugger needs a field to reconstruct a decision, write it at decision time. Never rely on log-grep reconstruction.

**Rationale:** Apr 10 2026 precedent cost ~$85 because `accum_metrics.db` excluded sellback cycles — the data existed at write time but wasn't captured. MEMORY.md: "Fabricated WR has cost $927+."

**Applies to:** trade-tracer, adverse-selection, fill-models, accumulator-state.

**Overrides:** none. Load-bearing project value.

---

## P2 — Safety gates compound; they don't replace

**Rule:** When deciding between "tighten an existing gate" and "add another gate with a different window," prefer adding. Multiple gates catch what single gates miss.

**Rationale:** Markov (streak, 1+ losses), outcome (rolling WR, ~3+ outcomes), circuit (cumulative PnL), MTC (deploy-time walk-forward). Each exists because each catches what the others don't. Node `outcome-gate.md`: "Two gates, different jobs… rejection reasons stack."

**Applies to:** markov-gate, outcome-gate, circuit-breaker, mtc-gate.

**Overrides:** when an existing gate is demonstrably broken (false-positive rate >30% in the last 7d), fix or remove it before layering another on top.

---

## P3 — Prefer robust-under-estimation-error over theoretically-optimal

**Rule:** When a formula has a "correct" solution that amplifies estimation error (full-Kelly, raw win rate, exact probability), prefer a dampened version (half-Kelly + haircut, Wilson lower bound, floor).

**Rationale:** Node `kelly-haircut.md`: "Full-Kelly is theoretically optimal but empirically brutal when your probability estimate is wrong; the bot never uses it raw." emmanuel's `.env`: 0.15 haircut, 0.10 hard cap. Node `mtc-gate.md`: Wilson lower bound is the primary WR gate, not raw WR.

**Applies to:** kelly-haircut, mtc-gate, entry-band.

**Overrides:** when the input is sha256-exact or oracle-verified (e.g., resolution price), use the exact value — no dampening needed.

---

## P4 — Infra failures ≠ strategy failures

**Rule:** Metric counters (consecutive unwinds, loss streak, circuit PnL, outcome-gate rolling WR) must only increment on **real strategy outcomes**. Network timeouts, DB hiccups, restart gaps, config-parse errors don't count.

**Rationale:** Bugs #46/#47 cost $150+ because infra failures incremented the circuit breaker. Node `circuit-breaker.md`: "Bug #46/#47 taught us infra failures must NOT increment `_consecutive_unwinds`." MEMORY.md: "Deployment-disrupted trades are NOT strategy losses."

**Applies to:** circuit-breaker, accumulator-state, outcome-gate, markov-gate, mtc-gate.

**Overrides:** none.

---

## P5 — Statistical floor: n≥30 + Wilson lower bound

**Rule:** Never ship a go/no-go verdict on fewer than 30 samples. Never trust a raw win rate — always cite the Wilson 95% lower bound alongside.

**Rationale:** MTC gate R3 uses Wilson lower bound precisely because small-sample overconfidence is how backtests become live losses. Node `mtc-gate.md` explicitly cites `n >= 30` and `wilson_lo >= breakeven_ratio` as gating rules. The Plan's "answer the friend's hypothesis" active sequencing requires n≥30 for the 93-97 band verdict.

**Applies to:** mtc-gate, entry-band, adverse-selection.

**Overrides:** when the decision is a **kill**, not a launch (e.g., cheap_side 0-for-16 at expected p≈0.5 has Wilson_lo ≈ 0 — kill without n≥30 is valid). Launches need the floor; kills can skip it when the signal is overwhelming.

---

## P6 — Preserve existing systems unless a concrete defect is named

**Rule:** Don't replace on taste. Replacement requires one of: (a) a named bug, (b) a measured cost, (c) an explicit operator request for replacement (not just "make it better").

**Rationale:** `~/.claude/rules/chudi-frame.md`: "Preserve existing systems unless replacement is justified by a concrete defect." Restarting-from-scratch discards institutional knowledge baked into gate ordering, bug-fix patches, and edge-case handlers.

**Applies to:** all nodes (cross-cutting).

**Overrides:** when the existing system's measured maintenance cost (bug count, hours of drift reconciliation) exceeds the replacement cost. Must be measured, not felt.

---

## P7 — Reversibility is a first-class constraint

**Rule:** When two options solve the same problem, pick the more reversible one unless the operator explicitly said "deep fix," "root cause," or "all the way."

**Rationale:** chudi-frame: "Prefer reversible assumptions over clarification. Reversible = can be undone with a single edit or revert." A reversible fix that works is better than an irreversible fix that works — you can always escalate; you can't always undo.

**Applies to:** all nodes.

**Overrides:** (a) when the reversible fix is known to mask the real problem (duct tape over a crack); (b) when the operator said "deep fix" / "root cause" / "finally kill this."

---

## P8 — Deploy discipline is non-negotiable

**Rule:** Every trading-code change follows `LIFECYCLE.md` phases. No skipping. Specifically: check open positions before restart, post-deploy `journalctl` for 60s, backup `.env` before any `sed -i`, deploy all modified `.py` files together.

**Rationale:** $1,323 in preventable losses Feb-Mar 2026 before LIFECYCLE was written. Partial deploys caused 3 separate AttributeError crashes. `is-active` alone is NOT enough (missed Traceback once, cost $212).

**Applies to:** deploy-lifecycle, trade-tracer, fill-models, and any node whose `code_refs` live in production runtime code.

**Overrides:** operator explicit opt-out, stated per-deploy. Standing opt-out is not honored.

---

## Conflict resolution

When principles conflict within this file, resolve in this order:

1. **P1 (observability)** outranks **P7 (reversibility)** — data loss compounds across deploys; a reversible fix that drops data taints every subsequent analysis.
2. **P4 (infra ≠ strategy)** outranks **P2 (compound gates)** — don't add more gates if the existing ones are miscounting. Fix the ledger first.
3. **P5 (n≥30)** outranks **P3 (robust-under-error)** — if you don't have the sample, dampening doesn't help; you're guessing with a fancier number.
4. **P8 (deploy discipline)** is a floor: never overridden by another principle in this file.

When two principles not listed above conflict, fall through to `chudi-frame.md` universal stance. If still unresolved, surface the conflict to the operator.

---

## When this file disagrees with a fresh operator statement

If a decision-mode pick is forced toward option X by this file, but the operator said something in the last ~5 turns that points toward option Y, surface the conflict explicitly:

> P7 says prefer reversible; you said "deep fix" 3 turns ago. Picking the irreversible option — confirm?

The operator's **fresh statement beats the written principle** when the conflict is explicit and the operator is asked to confirm. Do not silently apply one over the other.

---

## How to extend

After any decision-mode pick that crystallizes a principle not yet written down:

1. Append a new section with the next `P<n>` number.
2. Cite the node(s) and/or precedent that grounds it.
3. If the new principle overrides an existing one, add a line to **Conflict resolution** above.
4. Commit as a separate change from the triggering work — principles are meta-code; they shouldn't travel with feature commits.

This file sharpens with use. Empty principles are worse than missing ones.
