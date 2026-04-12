# Weather Market Strategy: Pre-Registration

**Date**: 2026-04-12
**Author**: Chudi Nnorukam
**Status**: PAPER TRADING (Phase 1)

## Thesis

Polymarket temperature markets are systematically mispriced because:
1. Free NOAA/GFS/ECMWF forecasts provide hours of edge over manual pricing
2. NegRisk multi-outcome markets spread liquidity thin across many buckets
3. Weather fee coefficient (5%) is lower than crypto (7.2%)
4. Edge is measured in hours, not milliseconds (no speed competition)

## Strategy

### Entry Criteria (ALL must be true)
1. `forecast_date > today` (market not yet resolving)
2. `abs(edge) >= 0.10` (10pp minimum divergence between forecast and market)
3. `ev_net >= 0.01` per share after fees
4. `kelly >= 0.05` (5% minimum Kelly fraction)
5. `market_price` between 0.02 and 0.98 (not settled)
6. Market has >= 10 shares available at the ask
7. Forecast cross-validated with >= 2 sources (std_dev of sources < 3 degrees)

### Position Sizing
- Half-Kelly: `size = 0.5 * kelly * bankroll`
- Max single bet: 10% of bankroll
- Max total exposure: 30% of bankroll across all open weather positions

### Exit Rules
- Hold to resolution (binary markets, no early exit unless market price > 0.95 for BUY positions)
- No averaging down

### Question Type Handling
- "X or higher" / "X or lower": Use cumulative probability (CDF)
- "between X-Y" / "exactly X": Use bucket probability (PDF)
- Forecast uncertainty: std_dev = 1.5 deg C (2.7 deg F) for 1-day, scale by sqrt(days) for longer horizon

## Hypotheses (Pre-registered)

### H1: Edge exists in bucket markets
Markets for the modal temperature bucket (nearest to forecast) are systematically underpriced.
- Measure: Average edge for modal bucket across all markets
- Pass criterion: Mean edge > 0.05 at n >= 30

### H2: Cumulative markets are less efficient than bucket markets
"X or higher" questions with strong directional forecast have larger edges.
- Measure: Compare mean |edge| for cumulative vs bucket questions
- Pass criterion: Cumulative mean |edge| > bucket mean |edge| at n >= 30

### H3: Forecast cross-validation improves accuracy
Opportunities where 2+ forecast sources agree have higher resolution rate.
- Measure: Resolution rate for single-source vs multi-source signals
- Pass criterion: Multi-source resolution rate > single-source at n >= 20 each

### H4: Strategy is profitable after fees
Net P&L is positive across the paper trading period.
- Measure: Total P&L after fees
- Pass criterion: P&L > 0 at n >= 50 resolved trades

## Phases

### Phase 1: Paper Trading (current)
- Duration: 2 weeks or 50 resolved trades, whichever comes first
- Track: entry price, forecast prob, edge, kelly, resolution, P&L
- Decision gate: If P&L > 0 AND resolution rate > 55%, proceed to Phase 2
- Abort: If P&L < -$20 paper OR resolution rate < 40% at n >= 20

### Phase 2: Micro-Live ($20 bankroll)
- Duration: 2 weeks or 30 resolved trades
- Max bet: $2 per position
- Decision gate: If live P&L > 0 AND matches paper within 20%, proceed to Phase 3

### Phase 3: Scale ($50-200 bankroll)
- Duration: Ongoing
- Half-Kelly sizing
- Weekly performance review

## Anti-Overfitting Safeguards
1. No parameter changes during a phase
2. Forecast uncertainty (std_dev) is fixed at 1.5 deg C, not optimized
3. Threshold (0.10) chosen from domain knowledge, not data-mined
4. All decisions logged to paper_trades.db before resolution
5. Resolution outcome recorded from market, not self-assessed

## Break-Even Economics

At 5% fee coefficient and typical market prices:
- At price 0.05: fee = 0.05 * 0.05 * 0.95 = $0.0024/share. Need ~5.5% WR to break even.
- At price 0.20: fee = 0.05 * 0.20 * 0.80 = $0.008/share. Need ~21% WR to break even.
- At price 0.50: fee = 0.05 * 0.50 * 0.50 = $0.0125/share. Need ~52% WR to break even.

Cheap-side weather (buying at 0.02-0.10) is structurally favorable: low break-even WR, high payoff ratio.
