# Hypothesis: [name]

**Mode**: DISCOVERY / VALIDATION
**Date**: YYYY-MM-DD
**Status**: ACTIVE / KILLED / VALIDATED / DEPLOYED
**Author**: Chudi

---

## 1. Hypothesis

[One sentence with mechanism. State WHY the edge exists, not just WHAT it is.]

Example: "BTC Polymarket contracts bought at $0.45-0.50 during extreme fear (F&G <= 20) have positive EV because retail sentiment is too negative relative to resolution probability."

## 2. Success Criteria (defined BEFORE testing)

- **WR threshold**: [e.g., > 55%]
- **Minimum n**: [e.g., 100 OOS trades]
- **CI requirement**: [e.g., Wilson 95% CI lower bound > breakeven WR]
- **DSR requirement**: [e.g., > 0.95]
- **WFE requirement**: [e.g., > 0.3]

## 3. Data

- **Source**: [performance.db / Binance OHLCV / signals.db]
- **Date range**: [YYYY-MM-DD to YYYY-MM-DD]
- **Filters applied**: [is_error=0, price bucket, asset, hours, etc.]
- **Total n**: [number of observations]
- **Known biases**: [survivorship, config changes during period, etc.]

## 4. In-Sample Results

[Full distribution of outcomes, not just mean]

| Metric | Value | CI | R8 Label |
|--------|-------|----|----------|
| Win rate | | | |
| Fee-adjusted EV | | | |
| Sharpe | | | |
| Max drawdown | | | |

## 5. Out-of-Sample Results (run ONCE, never revisit)

| Metric | Value | CI | R8 Label |
|--------|-------|----|----------|
| Win rate | | | |
| Fee-adjusted EV | | | |
| Sharpe | | | |
| WFE (OOS/IS) | | | |

## 6. Walk-Forward Results (per-window)

| Window | Train n | Test n | Train WR | Test WR | CI |
|--------|---------|--------|----------|---------|-----|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

**Splits positive**: X/5
**Mean test WR**: X%
**Consistent**: YES/NO

## 7. Fee-Adjusted Economics

- **Entry price range**: [e.g., $0.45-$0.50]
- **Fee per trade**: [from fees.py: taker_fee_per_share(price)]
- **Break-even WR**: [from fees.py: breakeven_wr(price)]
- **Fee-adjusted EV per trade**: $X.XX
- **Kelly fraction**: X% (half-Kelly recommended: X%)

## 8. Decision

**DEPLOY / KILL / NEEDS MORE DATA**

[If DEPLOY: what config changes, what monitoring, what kill criteria]
[If KILL: why, what was learned, save the anti-pattern]
[If NEEDS MORE DATA: what data, how much, checkpoint query, expected date]

## 9. Tests Run Counter

**Total parameter combinations tested this session**: N
**DSR applied**: yes/no (if yes, DSR = X.XX)
**FDR applied**: yes/no (if yes, N/M survive correction)

---

## Appendix: Commands Used

```bash
# Data extraction
python3 -m polyphemus.research.trade_analyzer --db ... --min-price ... --max-price ...

# Walk-forward
python3 -m polyphemus.research.grid_search --db ... --asset ...

# Key queries
SELECT ... FROM trades WHERE ...
```
