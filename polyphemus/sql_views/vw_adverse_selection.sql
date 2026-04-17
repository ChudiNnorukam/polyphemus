-- Phase 4 — Trade Observability Overhaul
--
-- vw_adverse_selection: per (signal_source, entry_mode, fill_model)
-- rollup of the adverse-fill diagnostics we already collect in
-- trades.{adverse_fill, adverse_fill_bps, fill_latency_ms}. Lets the
-- webapp / MTC gate answer:
--   "Does maker entry suffer worse adverse selection than taker under
--    the v2 fill model? How fast are live fills vs. dry-run?"
--
-- Only rows where adverse_fill has been populated (update_adverse_selection
-- fired with a Binance snapshot) contribute — rows with NULL adverse_fill
-- are explicitly excluded so the avg_adv_bps isn't diluted by missing data.
--
-- A per-fill-model row count is preserved so the reader can see when a
-- signal source has tiny samples and downgrade trust accordingly.
DROP VIEW IF EXISTS vw_adverse_selection;
CREATE VIEW vw_adverse_selection AS
SELECT
    COALESCE(signal_source, 'unknown') AS signal_source,
    COALESCE(entry_mode, 'unknown') AS entry_mode,
    COALESCE(fill_model, 'unknown') AS fill_model,
    COUNT(*) AS n,
    ROUND(AVG(adverse_fill_bps), 1) AS avg_adv_bps,
    ROUND(AVG(fill_latency_ms), 0) AS avg_latency_ms,
    ROUND(
        CAST(SUM(CASE WHEN adverse_fill = 1 THEN 1 ELSE 0 END) AS REAL)
        / COUNT(*),
        3
    ) AS adv_rate
FROM trades
WHERE adverse_fill IS NOT NULL
GROUP BY signal_source, entry_mode, fill_model;
