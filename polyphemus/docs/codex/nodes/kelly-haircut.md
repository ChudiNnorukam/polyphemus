---
id: kelly-haircut
name: Kelly & Haircut
domain: sizing-gating
aliases:
- kelly_fraction
- half_kelly
- fractional_kelly
- MARKOV_KELLY_HAIRCUT
- MARKOV_KELLY_MAX_BET_PCT
- kelly sizing
- fractional kelly
code_refs:
- path: prediction_markets/shared/kelly.py
  lines: 10-38
  sha256: 18925baf8bb85aac
- path: prediction_markets/shared/kelly.py
  lines: 41-50
  sha256: da86bb6a54b9dc90
related:
- markov-gate
- circuit-breaker
- mtc-gate
parent_concepts: []
child_concepts: []
last_verified: '2026-04-20T05:02:09Z'
confidence: inferred
---

## What

Kelly is the "how big a bet" formula. Given a true probability and
the market price, full-Kelly `f* = (Q - P) / (1 + Q)` says the
growth-optimal fraction of bankroll — where Q is true-probability odds
and P is market-implied odds. Full-Kelly is theoretically optimal but
empirically brutal when your probability estimate is wrong; the bot
never uses it raw.

Instead we scale it twice. `half_kelly` sacrifices ~25% of long-run
growth but massively reduces drawdowns and tolerates probability
estimation error. On top of that, emmanuel's `.env` applies a
conservative `MARKOV_KELLY_HAIRCUT=0.15` (bet 15% of Kelly) and a
hard cap `MARKOV_KELLY_MAX_BET_PCT=0.10` (never more than 10% of
bankroll in one bet). The "haircut" in the node name is that
compound reduction from theoretical to what actually goes on the
wire.

## Where

- Core formula at
  [prediction_markets/shared/kelly.py:10-38](prediction_markets/shared/kelly.py#L10-L38)
  (`kelly_fraction`) — arXiv 2412.14144 form, both guards against
  degenerate probs, 4-decimal rounded.
- Half-Kelly wrapper at
  [prediction_markets/shared/kelly.py:41-50](prediction_markets/shared/kelly.py#L41-L50)
  (`half_kelly`) — clamps negatives to zero so "no bet" is the
  default when edge is negative.

## When-to-touch

Route here when the user says:

- "kelly sizing" / "how big should this bet be"
- "half kelly" / "fractional kelly"
- "MARKOV_KELLY_HAIRCUT" / "MARKOV_KELLY_MAX_BET_PCT"
- "reduce bet size" / "shrink position"
- "why was the bet only $X when I said Y" (haircut + cap)
- "kelly formula" / "arxiv 2412.14144"
