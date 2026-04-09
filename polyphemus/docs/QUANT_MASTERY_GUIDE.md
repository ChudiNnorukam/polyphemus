# Crypto Quant Mastery Guide

> Domain entry audit for Polymarket crypto quant and crypto futures market quant.
> Generated 2026-04-06. Treat as living document, update as you learn.

---

## Part 0: Honest Assessment of Where You Stand

Before anything else, answer three questions:

1. **Do you have a proven edge?** Your data says: n=40 post-March-27 clean 45-50c trades, 45.0% WR, -$4.59. The 95% CI on that win rate spans roughly 30-60%. You cannot distinguish "slight edge" from "no edge" from "negative edge" at this sample size. **Verdict: unproven.**

2. **Do you understand why you're losing?** The 7.2% crypto taker fee at mid-market prices costs ~1.8% per trade (fee formula: `C * feeRate * p * (1-p)`). Round-trip at $0.475 entry costs ~3.6% of notional in fees alone. Your break-even WR after fees is ~52%, not 50%. **This fee structure is the single largest drag on your current strategy.**

3. **What's the minimum viable improvement?** Either (a) become a maker instead of taker (makers pay 0% and receive rebates), (b) move to lower-fee market categories (geopolitics = 0% taker), or (c) prove your win rate exceeds 55% with n >= 100.

---

## Part 1: Polymarket Market Microstructure

### How the CLOB Works

- Off-chain order matching (centralized operator), on-chain settlement (Polygon USDC)
- EIP-712 signed orders submitted to `https://clob.polymarket.com`
- Limit orders: free to place and cancel (gasless relayer)
- Each market has YES/NO tokens satisfying `YES + NO = $1.00`
- Not an AMM. No bonding curve. Pure order book supply/demand pricing.

### Fee Structure (This Is Why You're Losing)

```
fee = C * feeRate * p * (1 - p)
```

Where C = shares traded, p = share price. Fees peak at p=0.50 and approach zero at extremes.

| Category | Taker Fee | Maker Rebate |
|----------|-----------|--------------|
| Crypto | 7.2% | 20% of fees collected |
| Sports | 3.0% | 25% |
| Finance/Politics/Tech | 4.0% | 25% |
| Economics/Culture/Weather | 5.0% | 25% |
| Geopolitics/World events | **0%** | None needed |

At your entry range (p=0.475):
- Effective fee per trade: `7.2% * 0.475 * 0.525 = 1.79%`
- Round-trip cost: ~3.6% of notional
- This means your strategy needs 53-55% WR just to break even

**The geopolitics category charges 0% taker fee.** This is the most immediate arbitrage opportunity.

### Liquidity Rewards (Market Making Incentive)

Reward scoring formula: `S(v,s) = ((v-s)/v)^2 * b`

This is quadratic: posting at half the max spread earns 4x the score. April 2026 reward pool: $5M+. Professional market makers earn meaningful passive income independent of directional accuracy.

Two-sided quoting required when midpoint is $0.10-$0.90. Outside that range, single-sided is allowed.

---

## Part 2: Known Quantitative Edges (Ranked by Accessibility)

### Tier 1: Immediately Actionable

**1. Market Making on Non-Crypto Categories**

Your bot is already automated. Redirecting to provide liquidity in geopolitics (0% taker) or sports (3% taker, 25% rebate) eliminates the fee drag destroying your BTC taker edge.

- At $100K daily volume, 2-cent spread, capturing 10% of flow: ~$200/day per market
- Plus daily USDC rebates from reward pool
- Primary risk: holding inventory into binary resolution = 100% loss on losing side
- Mitigation: close positions before resolution, or ensure balanced fill rates

**2. Cross-Platform Arbitrage (Polymarket + Kalshi)**

Buy YES on cheaper platform, NO on more expensive platform. Guaranteed profit regardless of outcome.

- Profitable gaps (5+ points after fees): 15-25 per week across 7,900+ markets
- Average gap duration: 18 hours (no HFT needed)
- Best on geopolitics (0% Polymarket fee + ~1.2% Kalshi fee)
- Open source scanners exist: github.com/ImMike/polymarket-arbitrage

**3. Behavioral Bias Exploitation (Fade Extreme Contracts)**

Academic research (Reichenbach & Walther, Dec 2025, 124M trades) found:
- "Yes bias": traders systematically overtrade the affirmative option
- Overconfidence at extremes: $0.90 YES contracts resolve YES less than 90% of the time
- Actionable: buy NO when YES > $0.85 if your model says it should be $0.80

### Tier 2: Requires Infrastructure Investment

**4. Oracle Lag Exploitation (Your Current Market, Improved)**

Chainlink updates every 10-30 seconds or on 0.5% deviation. In the final 10-30 seconds before 5-minute resolution, the real BTC price may have diverged from the oracle snapshot.

- Your RTDS agreement filter is in the right direction
- Theoretical: 55-60% WR on backtests vs 50% random
- The fee at mid-market (~1.8%) consumes most of this thin edge
- Moving entries to 0.35-0.45 reduces fees (formula yields less at extremes)
- Requires: Binance WebSocket feed, pre-signed orders, sub-200ms execution

**5. Domain Expert Edge (Highest Alpha, Hardest to Build)**

Weather markets: trader neobrother used ECMWF/GFS models, 2,373 predictions, $20,000+.
Hans323 made $1.11M from a single weather bet using asymmetric tail strategy.

This requires building a probability model outside Polymarket. The edge is durable because it requires domain expertise most traders lack.

### Tier 3: Institutional-Grade

**6. Intra-Market Arbitrage (Sum-to-1 Violations)**

When YES + NO sum to less than $1.00, buy all outcomes for guaranteed profit. Average window duration fell from 12.3s (2024) to 2.7s (2025). 73% captured by sub-100ms bots. Not viable without co-location.

---

## Part 3: Crypto Futures Strategies (Beyond Polymarket)

### Funding Rate Arbitrage (Delta-Neutral)

Long spot, short perpetual when funding rates are positive. Collect funding every 8 hours.

- Average annualized returns: 14.39% (2024), 19.26% (2025)
- BTC on major exchanges: Sharpe 0.5-1.5 (compressed post-ETF)
- Alt-perps (XRP, SOL on DEXes like Hyperliquid): Sharpe 1.0-2.0
- Risk: 10x leverage = margin calls in >50% of months (BIS research)
- Minimum capital: ~$50K to net meaningful returns after fees/gas

**Implementation**: Z-score normalization with 2.0 threshold for entry/exit. Market-make one leg, immediately take the other.

### Mean Reversion (Daily Timeframe)

- BTC-neutral residual mean reversion: Sharpe ~2.3 in backtests, expect 1.0-1.5 live
- Daily data with 20-60 day lookback is the documented sweet spot
- Outperformed pure momentum post-2021 in choppy markets
- Critical: one study showed Sharpe drop from 3.19 IS to 0.46 OOS (85% degradation)
- Rule: backtest Sharpe > 3.0 = almost certainly overfitting

### Statistical Arbitrage (BTC-ETH Pairs)

- BTC-ETH cointegration: 14.2% annualized, Sharpe 3.1 in 2022 bear market OOS
- Use dynamic cointegration (rolling Engle-Granger with 60-120 day windows)
- Caution: BTC-ETH correlation may be weakening since mid-2024

### Volatility Risk Premium (Deribit Options)

- IV consistently exceeds RV on BTC/ETH options
- Sell options, delta-hedge
- Requires Deribit account and understanding of Greeks
- BTC IV compressed to high-30s/low-40s in 2025 (roughly half of 2024)

### Basis Trading (CME Futures)

- Only worth it at 15%+ annualized basis (bull market peaks)
- Currently ~5%, compressed by institutional arbitrage post-ETF
- Edge revives during euphoric rallies when retail demand overwhelms arb capital

---

## Part 4: Signal Sources That Actually Work

### Tier 1: Strong Evidence

| Signal | What It Predicts | Lead Time | Provider |
|--------|-----------------|-----------|----------|
| VPIN > 0.55 | Volatility/price jump imminent | Minutes | Buildix.trade (free) |
| Exchange inflows (large) | Selling pressure | 15-60 min | CryptoQuant, Glassnode |
| Exchange outflows (large) | Accumulation | 15-60 min | CryptoQuant, Glassnode |
| Funding rate > 0.1%/8h | Overleveraged longs, correction risk | Hours | CoinGlass (free) |
| Funding rate deeply negative | Short squeeze setup | Hours | CoinGlass (free) |
| Rising OI + falling price | Short buildup, cascade risk | Hours | CoinGlass (free) |

### Tier 2: Useful with Context

| Signal | Notes |
|--------|-------|
| Fear & Greed <= 30 | Your own data: 65% WR at F&G <= 30 + 0.45-0.50 (n=20, ANECDOTAL) |
| Order book imbalance | Strong at sub-5min timeframes, decays rapidly |
| CVD divergence | Leading reversal signal |
| Options put-call ratio | Rising PCR = defensive positioning |

### Tier 3: Noisy / Supplemental

| Signal | Notes |
|--------|-------|
| Social media sentiment | High false positive rate, works only at extremes (contrarian) |
| On-chain single metrics | Unreliable in isolation, better for position sizing than timing |

### Data Providers Worth Paying For

| Provider | Best For | Cost | Verdict |
|----------|----------|------|---------|
| Tardis.dev | Historical tick/orderbook replay for backtesting | Paid | **Highest ROI purchase for systematic traders** |
| Nansen | Wallet entity labeling, smart money tracking | $150+/mo | Worth it if trading on-chain signals |
| CoinGlass | Liquidations, OI, funding rates | Free | Sufficient for derivatives signals |
| Coinalyze | Real-time liquidation data | Free + Paid | Good supplement to CoinGlass |

---

## Part 5: Backtesting and Walk-Forward Architecture

### The Two-Phase Approach

```
VECTORIZED PHASE          EVENT-DRIVEN PHASE        LIVE PHASE
-----------------         -----------------         ----------
VectorBT screen           NautilusTrader replay     Live bot
thousands of params  -->  with realistic fills  --> with same
in minutes                on top N candidates       execution code
```

**Phase 1 (Research)**: VectorBT. Runs on your laptop, processes years of 5m OHLCV in seconds, handles parameter sweeps.

**Phase 2 (Validation)**: NautilusTrader. Has a native Polymarket integration. Supports L2 order book replay, nanosecond timestamps. Code runs unmodified in live mode.

### Walk-Forward Validation (Non-Negotiable Gate)

Rolling walk-forward (recommended default):
```
Window 1:  [TRAIN 6mo][TEST 1mo]
Window 2:       [TRAIN 6mo][TEST 1mo]
Window 3:            [TRAIN 6mo][TEST 1mo]
```

**Walk-Forward Efficiency (WFE)**:
```
WFE = OOS_performance / IS_performance
```
- WFE > 0.5: healthy, robust
- WFE 0.3-0.5: acceptable, monitor
- WFE < 0.3: overfitted, do not trade live

### Statistical Gates Before Live Deployment

- [ ] Minimum 100 OOS trades (not IS trades)
- [ ] Deflated Sharpe Ratio > 0.95 (corrected for number of parameter combos tested)
- [ ] 95% CI on win rate does not include breakeven WR
- [ ] WFE > 0.3 (ideally > 0.5)
- [ ] Bootstrap max drawdown P95 within capital tolerance
- [ ] Transaction costs included in all calculations
- [ ] Can state the mechanism (why the edge exists) in one sentence without referencing data

### Combinatorial Purged Cross-Validation (CPCV)

Standard cross-validation violates temporal ordering. CPCV adds:
- **Purging**: Remove training observations whose label horizon overlaps with test period
- **Embargoing**: Remove training observations immediately after test set

```python
from skfolio.model_selection import CombinatorialPurgedCV

cv = CombinatorialPurgedCV(
    n_folds=10,
    n_test_folds=2,
    purged_size=5,    # bars to purge before test period
    embargo_size=2,
)
```

With 10 folds and 2 test folds: C(10,2) = 45 distinct backtest paths instead of one.

### Probability of Backtest Overfitting (PBO)

- PBO > 0.5: strategy selection is no better than chance
- Rule: if you tested > 20 parameter combinations, your observed Sharpe needs 2-3x higher than breakeven to survive correction

### Deflated Sharpe Ratio (DSR)

Corrects for number of strategies tested, non-normality, and sample length:

```python
from scipy.stats import norm
import numpy as np

def deflated_sharpe_ratio(sharpe_observed, n_trials, n_obs, returns):
    skew = float(pd.Series(returns).skew())
    kurt = float(pd.Series(returns).kurtosis())
    e_max_sr = (
        (1 - np.euler_gamma) * norm.ppf(1 - 1/n_trials)
        + np.euler_gamma * norm.ppf(1 - 1/(n_trials * np.e))
    )
    sr_variance = (
        (1 + (0.5 * sharpe_observed**2) - (skew * sharpe_observed)
         + ((kurt - 3)/4) * sharpe_observed**2)
        / (n_obs - 1)
    )
    return norm.cdf((sharpe_observed - e_max_sr) / np.sqrt(sr_variance))
# DSR > 0.95 = likely positive true Sharpe
# DSR < 0.5 = probably noise
```

### Database Architecture

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Tick storage | QuestDB | High-speed ingestion, nanosecond precision |
| Research queries | DuckDB on Parquet files | Zero setup, runs in-process, OLAP-optimized |
| Bot state | SQLite | Trade log, positions (you already do this) |
| Data collection | cryptofeed + CCXT | WebSocket tick ingestion, exchange abstraction |

---

## Part 6: The Bias Catalogue (Memorize These)

### 1. Look-Ahead Bias
Using data at time T that was only available at T+N. Common with on-chain data that gets backfilled.
**Defense**: Timestamp every data point with the time it was *available*, not the time it *refers to*.

### 2. Survivorship Bias
Only using coins/markets that still exist. Only studying markets with enough volume.
**Defense**: Include resolved-NO markets. Filter error trades separately, don't ignore them.

### 3. Overfitting / Data Snooping
Testing many parameters, reporting the best. With 20 tested, expect 1 "significant" by chance at p<0.05.
**Defense**: Single OOS test, walk-forward, Deflated Sharpe Ratio. Sharpe > 3.0 = red flag.

### 4. Selection Bias in Reporting
Published strategies are survivors. The failures are invisible.
**Defense**: Read critically. QuantPedia notes average return degradation after publication.

### 5. Confusing Luck with Skill
n=40 trades, 45% WR: cannot distinguish 45% true WR from 55% true WR. CI is too wide.
**Defense**: Never change parameters based on n < 50. Never claim edge with n < 100.

### 6. Over-Optimization (Curve Fitting)
Your CHEAP_SIDE_MIN_PRICE=0.45 and ACTIVE_HOURS=22-6 UTC were chosen from historical data.
**Defense**: Test these on data not used to select them. Hour-of-day restriction from n=15 (CI: 59.8-100%) is premature.

### 7. Ignoring Transaction Costs
At $0.47 entry with 7.2% formula: effective cost ~$0.01/share, shifting break-even WR from ~47% to ~52%.
**Defense**: Include fees in EVERY WR calculation.

### 8. Regime Change Blindness
Strategy that worked in 2023 may fail in 2026. Market participants change.
**Defense**: Monitor autocorrelation of outcomes and fill price drift, not just WR.

---

## Part 7: The Alpha Research Process

### The Professional Workflow

1. **Hypothesis first, data second.** State a falsifiable hypothesis BEFORE looking at data.
   Example: "BTC Polymarket contracts bought at $0.45-0.50 during extreme fear (F&G <= 20) have positive EV because retail sentiment is too negative relative to resolution probability."

2. **Define success criteria before testing.**
   Example: "Supported if live WR exceeds 55% with n >= 100 and Wilson 95% CI lower bound above 48%."

3. **Single out-of-sample test.** Split data: train on first 60%, reserve last 40%. Test ONCE.

4. **Walk-forward validation.** If OOS passes, run walk-forward with 6+ non-overlapping windows. Check the distribution of Sharpe across windows, not just the average.

5. **Paper trade.** System must prove itself with real-time data and real order logic before capital.

### Research Notebook Template

```
## Hypothesis
[One sentence, includes mechanism]

## Data
Source, date range, known biases, n, filters applied

## Feature Construction
[With timestamps verified, no look-ahead]

## In-Sample Results
[Full distribution of outcomes, not just mean]

## Out-of-Sample Results
[Run once, never revisited]

## Walk-Forward Results
[Distribution across windows]

## Transaction Cost Model
[Fees + slippage + price impact]

## Decision
Deploy / Kill / Needs More Data
[If "needs more data": what data, how much, by when]
```

---

## Part 8: Learning Roadmap (12-Month Progression)

### Phase 0: Diagnosis Before Learning (Weeks 1-2)

Before reading anything, apply the scientific method to your existing system.

Run a structured autopsy:
1. What is your break-even WR at current entry prices after fees? (~52%)
2. Is your live WR above that with n > 50? (Not yet proven)
3. What is the 95% CI on your WR? (For n=40, WR=45%: CI spans ~30-61%)

**Key realization**: You are not losing because of a lack of quant knowledge. You are losing because you have not confirmed positive EV with sufficient data. More sophisticated techniques are not the fix. Rigorous evidence gathering is.

### Phase 1: Statistical Foundations (Months 1-2)

**1a. Hypothesis testing and confidence intervals (highest priority)**
- Binomial confidence intervals (Wilson score, not normal approximation)
- t-test and what t-stat > 2.0 means
- p-values, Type I/II errors
- Multiple comparisons problem
- Resource: "Statistics" by Freedman, Pisani, Purves + Khan Academy

**1b. Time series basics**
- Stationarity and why it matters
- Autocorrelation (ACF/PACF): you already found ACF=0.401 on loss sequences
- GARCH: crypto volatility clusters, tomorrow's vol is predictable from today's
- Resource: DataCamp "GARCH Models in Python" (4 hours)

**Skip at Phase 1**: Stochastic calculus, linear algebra for ML, information theory.

### Phase 2: Market Microstructure (Months 2-3)

Learn in this order:

1. **Bid-ask spread decomposition**: Spread compensates for inventory risk and adverse selection
2. **Order flow imbalance**: Ratio of buy-initiated to total volume predicts short-term direction (VPIN, Kyle lambda)
3. **Price impact**: Large orders move price against you. Polymarket has thin books.
4. **Information asymmetry**: Glosten-Milgrom model. Are you actually informed?

Resource: QuantStart microstructure series + Cornell/Easley crypto microstructure paper

### Phase 3: Alpha Research Process (Months 3-5)

This is the highest-leverage phase. See Part 7 above. Goal: produce 1 formally documented hypothesis per month. A killed hypothesis is a success.

### Phase 4: Intermediate Concepts (Months 5-9)

Only pursue once you have at least one strategy with verified positive EV in live trading.

**Regime detection**: HMM via `hmmlearn` library. High vol = trending, low vol = mean-reverting.
**Information theory**: Mutual information for feature selection (`sklearn.feature_selection.mutual_info_classif`).
**Factor models**: Market beta, size, momentum for crypto.
**Optimal execution**: TWAP, VWAP, price impact on Polymarket's thin books.

### Phase 5: Advanced (Months 9-18, only if profitable)

Stochastic calculus (only if moving to options), linear algebra for ML, deep learning (massive overfitting risk, defer until validation framework is mastered).

### Milestone Table

| Month | Focus | Milestone |
|-------|-------|-----------|
| 1 | Hypothesis testing, CI on existing data | Know with a number whether you have an edge |
| 2 | Time series, GARCH, autocorrelation | Understand loss clustering mechanically |
| 3 | Market microstructure, order flow | Identify one structural inefficiency |
| 4 | Alpha research process, walk-forward setup | First hypothesis documented |
| 5 | First walk-forward backtest with costs | Deploy / kill / needs more data decision |
| 6 | Paper trading the validated strategy | System behavior matches backtest |
| 7-8 | Regime detection, conditional logic | Second hypothesis in pipeline |
| 9 | Progress review | Generating positive EV live? |
| 10-12 | Factor models, second strategy | Portfolio of 2-3 uncorrelated edges |

---

## Part 9: Essential Reading List

### Read First (Within 60 Days)

1. **"Quantitative Trading"** by Ernest Chan - practical, retail-focused, directly applicable
2. **"Finding Alphas"** edited by Igor Tulchinsky - WorldQuant research process

### Read Second (Months 2-5)

3. **"Advances in Financial Machine Learning"** by Marcos Lopez de Prado - Chapters 1-4, 7, 11 first
   - Free lectures: quantresearch.org/Lectures.htm
4. **"Machine Learning for Algorithmic Trading"** by Stefan Jansen - paired with zipline-reloaded codebase

### Reference (Keep Available)

5. **"Analysis of Financial Time Series"** by Ruey S. Tsay
6. **"Evidence-Based Technical Analysis"** by David Aronson - definitive guide to avoiding data snooping

### Daily Reading Habit

- Quantocracy (quantocracy.com) - aggregator of quant blog posts
- QuantPedia (quantpedia.com) - 700+ analyzed trading systems with papers
- Robot Wealth (robotwealth.com) - algo trading, ML, regime detection
- Ernie Chan's blog (epchan.blogspot.com)
- PyQuant News newsletter (pyquantnews.com)

---

## Part 10: Python Library Stack

```python
# Data layer
import polars as pl              # 10-30x faster than pandas
import pyarrow as pa             # Parquet I/O
import duckdb                    # SQL on Parquet, zero setup

# Market data
import ccxt                      # 100+ exchange connectors
# cryptofeed                     # WebSocket tick/orderbook feeds

# Backtesting
import vectorbt as vbt           # Vectorized research phase
# NautilusTrader                 # Event-driven validation phase

# Statistics
import numpy as np
import scipy.stats as stats
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests  # FDR correction
from skfolio.model_selection import CombinatorialPurgedCV

# ML (when ready)
from sklearn.ensemble import RandomForestClassifier
import lightgbm as lgb

# Visualization
import plotly.graph_objects as go  # Interactive
import matplotlib.pyplot as plt    # Static
```

---

## Part 11: Infrastructure Recommendations

### Latency Reality

| Tier | Latency | How |
|------|---------|-----|
| Unoptimized | 80-150ms | Home/office VPS |
| Geographic optimization | 10-20ms | Same AWS region as exchange |
| Production competitive | P50: 78ms, P95: 142ms | Optimized cloud |

Crypto is not traditional HFT. Exchange processing is the bottleneck (5-10ms). Sub-millisecond optimization is documented waste in crypto.

Where exchanges live: Binance = AWS Tokyo, Bybit = AWS Singapore, OKX = AWS Hong Kong.

### Monthly Cost Estimate (Multi-Strategy)

- Multi-region servers (3 regions): ~$790/mo
- Network/bandwidth: ~$150/mo
- Monitoring: ~$200/mo
- Total without developer cost: ~$1,140/mo

---

## Part 12: Realistic Expectations

### Live Sharpe Ratios by Strategy

| Strategy | Live Sharpe (Realistic) |
|----------|------------------------|
| Funding rate arb (BTC, Binance) | 0.5-1.5 |
| Funding rate arb (alt-perps, DEX) | 1.0-2.0 |
| Mean reversion (BTC daily) | 1.0-1.5 |
| BTC-ETH stat arb | 0.8-1.5 |
| Multi-strategy platform | 2.0-4.0 |

### The Overfitting Haircut

Expect 50-85% reduction from backtested Sharpe to live Sharpe. A backtest Sharpe of 3.0 corresponds to live Sharpe of 0.5-1.5.

### Minimum Capital by Strategy

| Strategy | Minimum Capital |
|----------|----------------|
| Daily mean reversion/momentum | $10K-$50K |
| Funding rate arbitrage | $50K |
| Multi-exchange stat arb | $100K |
| HFT infrastructure | $100K + $1,140/mo infra |

### Edge Decay

- Cross-exchange arb spreads persist 200-800ms
- Funding rate arb on BTC: decaying for 2+ years (still works on alt-perps)
- CME basis: compressed from 27% (Mar 2024) to ~5% (late 2025) in 20 months
- Momentum strategies: 3-6 months typical lifecycle
- HFT: days to weeks

---

## Part 13: Immediate Action Items

Ranked by expected impact, lowest effort first:

### This Week

1. **Calculate your exact break-even WR with fees.** Use the fee formula at your actual average entry price. Know the number.

2. **Set up a research notebook template.** Use the format in Part 7. Document your current strategy as Hypothesis #1 with the formal structure.

3. **Order "Quantitative Trading" by Ernest Chan.** Read before doing anything else.

### This Month

4. **Evaluate market making on geopolitics markets (0% fee).** This is the single highest-leverage change: same automation, different market category, zero fee drag.

5. **Build a cross-platform arbitrage scanner.** Polymarket geopolitics + Kalshi. 15-25 opportunities per week, 18-hour average windows. No HFT needed.

6. **Start collecting your trades in a formal research DB.** Every trade gets: entry price, exit price, fees paid, signal values at entry, market state variables. This is the raw material for everything else.

### This Quarter

7. **Set up VectorBT and run your first walk-forward backtest** on the 0.45-0.50 cheap side strategy with proper transaction cost modeling.

8. **Study Wilson confidence intervals and apply to your live data.** Know exactly what you can and cannot conclude from your current sample sizes.

9. **Evaluate Tardis.dev for historical orderbook data.** This is the foundation for realistic backtesting with fill probability modeling.

---

## References

### Polymarket Specific
- [Polymarket Fees Docs](https://docs.polymarket.com/trading/fees)
- [Polymarket Liquidity Rewards](https://docs.polymarket.com/market-makers/liquidity-rewards)
- [Polymarket CLOB Overview](https://docs.polymarket.com/trading/overview)
- [py-clob-client](https://github.com/Polymarket/py-clob-client)
- [NautilusTrader Polymarket Integration](https://nautilustrader.io/docs/latest/integrations/polymarket/)
- [PolymarketData API](https://www.polymarketdata.co/)
- [Polymarket Analytics Leaderboard](https://polymarketanalytics.com/traders)

### Academic Research
- [SSRN: Accuracy, Skill, Bias on Polymarket (Reichenbach & Walther, Dec 2025)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5910522)
- [arXiv: Combinatorial Arbitrage in Prediction Markets](https://arxiv.org/abs/2508.03474)
- [arXiv: Kelly Criterion in Prediction Markets](https://arxiv.org/html/2412.14144v1)
- [SSRN: Deflated Sharpe Ratio (Bailey & Lopez de Prado)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- [SSRN: Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
- [ScienceDirect: VPIN and Bitcoin Price Jumps](https://www.sciencedirect.com/science/article/pii/S0275531925004192)
- [ScienceDirect: Funding Rate Arbitrage Risk/Return](https://www.sciencedirect.com/science/article/pii/S2096720925000818)
- [CEPR: Crypto Carry and Market Segmentation](https://cepr.org/voxeu/columns/crypto-carry-market-segmentation-and-price-distortions-digital-asset-markets)
- [Cornell: Crypto Market Microstructure (Easley)](https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf)

### Tools and Frameworks
- [VectorBT](https://vectorbt.pro/)
- [NautilusTrader](https://nautilustrader.io/docs/latest/concepts/backtesting/)
- [skfolio CPCV](https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)
- [QuestDB for Tick Data](https://questdb.com/blog/ingesting-financial-tick-data-using-time-series-database/)
- [Tardis.dev Historical Data](https://docs.tardis.dev/)
- [CCXT Exchange Library](https://github.com/ccxt/ccxt)
- [Buildix VPIN Tools](https://www.buildix.trade/blog/vpin-indicator-how-crypto-whales-signal-moves-guide-2026)
- [CoinGlass Derivatives Data](https://www.coinglass.com/BitcoinOpenInterest)

### Learning Resources
- [Lopez de Prado Free Lectures](https://www.quantresearch.org/Lectures.htm)
- [QuantStart Self-Study Plan](https://www.quantstart.com/articles/Self-Study-Plan-for-Becoming-a-Quantitative-Trader-Part-I/)
- [QuantPedia: 700+ Trading Systems](https://quantpedia.com/)
- [Quantocracy Blog Aggregator](https://quantocracy.com/)
- [Robot Wealth](https://robotwealth.com/)
- [Awesome Quant (GitHub)](https://github.com/wilsonfreitas/awesome-quant)
- [Awesome Systematic Trading (GitHub)](https://github.com/wangzhe3224/awesome-systematic-trading)

### Crypto Futures Specific
- [CME Crypto Basis Trading](https://www.cmegroup.com/openmarkets/equity-index/2025/Spot-ETFs-Give-Rise-to-Crypto-Basis-Trading.html)
- [Deribit Insights](https://insights.deribit.com/)
- [LiveVolatile: Crypto Options 2026](https://www.livevolatile.com/blog/crypto-options-implied-volatility-2026)
- [arXiv: Dynamic Cointegration Pairs Trading](https://arxiv.org/pdf/2109.10662)

### Tax
- [GreenTraderTax: Digital Asset Rules](https://greentradertax.com/digital-asset-trading-explained-tax-rules-for-crypto-etfs-futures-options-and-tokens/)
- [CoinLedger: Wash Sale Rule](https://coinledger.io/blog/crypto-wash-sale-rule)
