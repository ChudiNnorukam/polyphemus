-- Phase 4 — Trade Observability Overhaul
--
-- vw_trade_attribution: one row per CLOSED trade, enriched with the
-- attribution columns added in Phase 1. All filters are in-view so
-- downstream queries (vw_strategy_perf, MTC gate --segment-by) can
-- stay trivial.
--
-- Entry-band buckets match the analysis we run manually today:
--   00-55, 55-70, 70-85, 85-93, 93-97, 97+
-- The friend's "priced-in" hypothesis lives in 93-97 and 97+.
--
-- Idempotent: DROP + CREATE is safe to re-run, no data loss (views
-- are queries, not tables).
DROP VIEW IF EXISTS vw_trade_attribution;
CREATE VIEW vw_trade_attribution AS
SELECT
    trade_id,
    entry_time,
    slug,
    signal_source,
    signal_id,
    strategy,
    fill_model,
    fill_model_reason,
    entry_mode,
    is_dry_run,
    CASE
        WHEN entry_price < 0.55 THEN '00-55'
        WHEN entry_price < 0.70 THEN '55-70'
        WHEN entry_price < 0.85 THEN '70-85'
        WHEN entry_price < 0.93 THEN '85-93'
        WHEN entry_price < 0.97 THEN '93-97'
        ELSE '97+'
    END AS entry_band,
    entry_price,
    exit_price,
    outcome,
    exit_reason,
    pnl,
    profit_loss_pct,
    hold_seconds,
    book_spread_at_entry,
    book_depth_bid,
    book_depth_ask,
    fill_latency_ms,
    adverse_fill,
    adverse_fill_bps,
    CASE WHEN pnl > 0 THEN 1 ELSE 0 END AS is_win
FROM trades
WHERE exit_time IS NOT NULL;
