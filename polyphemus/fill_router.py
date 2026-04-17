"""Unified dry-run fill routing for the Trade Observability Overhaul.

Before Phase 2, the Phase 1.4 probabilistic maker fill model (V2) was wired
only into ``accumulator.py``. The primary signal path (``signal_bot`` +
``position_executor``) still reported instant taker-optimistic fills in dry
run, which meant:

  - V2 model improvements never reached the signal_bot entries we actually
    evaluate for go/no-go decisions.
  - Every dry-run entry point needed its own fill-decision code to populate
    the v4 observability columns (``fill_model``, ``fill_model_reason``,
    ``book_spread_at_entry``, ...).

This router centralizes both choices. Callers pass the inputs they'd need
for a legacy instant-fill; the router returns a ``FillRecord`` with the fill
outcome AND every observability field populated, so ``record_entry`` can be
invoked without branch-aware glue in every caller.

Feature-flagged via ``POLYPHEMUS_DRY_RUN_V2``. When off (default), emits
``fill_model="v1_taker"`` with instant fill so the rollout is reversible.
Live fills do NOT route through here: the live CLOB path sets
``fill_model="live"`` directly at the fill-confirmation callsite.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dry_run_fill_model import MakerFillModel, dry_run_v2_enabled


@dataclass(frozen=True)
class FillRecord:
    """Unified result of a dry-run fill routing decision.

    Every field is populated regardless of which fill model ran, so callers
    can pass this straight to ``record_entry`` without branching on
    ``fill_model``. ``filled=False`` means the order did not fill this
    evaluation cycle; the caller should keep it resting (V2 flag on) or
    treat as an error (V1, which never returns ``filled=False``).
    """

    filled: bool
    fill_price: float
    fill_qty: float
    # v4 observability fields, always populated.
    fill_model: str
    fill_model_reason: str
    book_spread_at_decision: float
    book_depth_bid: float
    book_depth_ask: float
    elapsed_secs_at_fill: float


_shared_model: MakerFillModel | None = None


def _get_model() -> MakerFillModel:
    """Lazy singleton for the default (unseeded) MakerFillModel.

    Tests that need determinism construct their own ``MakerFillModel(rng=...)``
    and pass it via ``model=`` to :func:`route_dry_run_fill`.
    """
    global _shared_model
    if _shared_model is None:
        _shared_model = MakerFillModel()
    return _shared_model


def route_dry_run_fill(
    *,
    our_price: float,
    best_bid: float,
    best_ask: float,
    qty: float,
    elapsed_secs: float = 0.0,
    book_depth_bid: float = 0.0,
    book_depth_ask: float = 0.0,
    model: MakerFillModel | None = None,
) -> FillRecord:
    """Route a dry-run fill through V1 instant or V2 probabilistic.

    V1 (default, flag off): instant fill at ``our_price`` with
    ``fill_model="v1_taker"``. Preserves legacy behavior so the rollout is
    fully reversible; every v4 column still populated so DB shape is uniform.

    V2 (``POLYPHEMUS_DRY_RUN_V2=true``): delegates to
    :class:`MakerFillModel.evaluate` and wraps the result with
    ``fill_model="v2_probabilistic"`` plus the reason tag
    (``prob_hit``/``prob_miss``/``crossed_book``/``buried``) for attribution.

    ``book_depth_bid``/``book_depth_ask`` are optional because not every
    caller has depth data. When unknown, pass 0.0 and the attribution view
    will treat them as "unknown" rather than "zero depth."
    """
    spread = best_ask - best_bid if (best_ask > 0 and best_bid > 0) else 0.0

    if not dry_run_v2_enabled() or best_bid <= 0 or best_ask <= 0:
        return FillRecord(
            filled=True,
            fill_price=our_price,
            fill_qty=qty,
            fill_model="v1_taker",
            fill_model_reason="v1_instant",
            book_spread_at_decision=spread,
            book_depth_bid=book_depth_bid,
            book_depth_ask=book_depth_ask,
            elapsed_secs_at_fill=elapsed_secs,
        )

    decision = (model or _get_model()).evaluate(
        our_price=our_price,
        best_bid=best_bid,
        best_ask=best_ask,
        qty=qty,
        elapsed_secs=elapsed_secs,
    )
    return FillRecord(
        filled=decision.filled,
        fill_price=decision.fill_price,
        fill_qty=decision.fill_qty,
        fill_model="v2_probabilistic",
        fill_model_reason=decision.reason,
        book_spread_at_decision=spread,
        book_depth_bid=book_depth_bid,
        book_depth_ask=book_depth_ask,
        elapsed_secs_at_fill=elapsed_secs,
    )
