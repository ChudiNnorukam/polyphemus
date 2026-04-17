-- Phase 4 — Trade Observability Overhaul
--
-- vw_strategy_perf: grouped win-rate / pnl / n-samples per
-- (signal_source, fill_model, is_dry_run, entry_band). This is the
-- dashboard view that answers:
--   "Is pair_arb profitable under the v2 fill model in the 93-97
--    band? How many samples is that verdict based on?"
--
-- Reads from vw_trade_attribution (closed trades only) so no dilution
-- from open positions. Counts include both live and dry-run rows; the
-- is_dry_run column in the GROUP BY preserves separation.
--
-- Stat columns mirror what MTC gate consumes downstream:
--   n       sample size
--   wins    count(pnl > 0)
--   wr      win rate (0..1)
--   pnl     sum of P&L in USD
--   avg_pnl mean P&L per trade in USD
DROP VIEW IF EXISTS vw_strategy_perf;
CREATE VIEW vw_strategy_perf AS
SELECT
    COALESCE(signal_source, 'unknown') AS signal_source,
    COALESCE(fill_model, 'unknown') AS fill_model,
    is_dry_run,
    entry_band,
    COUNT(*) AS n,
    SUM(is_win) AS wins,
    ROUND(CAST(SUM(is_win) AS REAL) / COUNT(*), 4) AS wr,
    ROUND(SUM(pnl), 2) AS pnl,
    ROUND(AVG(pnl), 4) AS avg_pnl
FROM vw_trade_attribution
GROUP BY signal_source, fill_model, is_dry_run, entry_band;
