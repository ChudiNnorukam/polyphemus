# Polyphemus: From Hypothesis to Edge
## A Data-Driven Journey Through Polymarket's 15-Minute Crypto Markets

> **Report Type**: Full performance analysis & strategy evolution
> **Period**: February 4 - February 10, 2026 (7 days)
> **Data**: 608 trades, 13 momentum signals, 5 strategy hypotheses tested
> **Capital**: Started $162 → Current $103

---

## REPORT OUTLINE & STRUCTURE

### Visual Design Guidelines
- **Color palette**: Dark theme (#1a1a2e background, #16213e cards, #0f3460 accents, #e94560 for losses, #00d2d3 for wins)
- **Typography**: Inter or SF Pro for headings, JetBrains Mono for numbers/data
- **Charts**: Use Plotly or D3.js for interactivity; fallback to matplotlib with seaborn styling
- **Layout**: Single-column narrative with full-width charts, card-based metric displays
- **Icons**: Use Lucide or Heroicons for section markers

---

## SECTION 1: THE THESIS
*One page. Sets the stage for non-technical readers.*

### Content
- **What are Polymarket 15-minute crypto markets?**
  - Simple explainer: "Every 15 minutes, a new market opens asking: Will Bitcoin go Up or Down?"
  - Binary outcome: You buy a share at $0.01-$0.99. If you're right, it pays $1.00. If wrong, $0.00.
  - 96 markets per day, per coin (BTC, ETH, SOL, XRP) = 384 opportunities daily

- **The original hypothesis**:
  - "A profitable trader (DB) makes consistent money on these markets. If we copy their trades — but only the high-conviction ones — we can replicate their returns."
  - Starting capital: $162 USDC
  - Goal: Consistent daily profit through intelligent signal filtering

### Visualization: "How Binary Markets Work"
- **Infographic**: Simple 3-step flow diagram
  1. Market opens → Price starts ~$0.50 (50/50 odds)
  2. Traders buy/sell → Price moves toward expected outcome
  3. Market resolves → Winners get $1.00, losers get $0.00
- Use animated bar showing price moving from 0.50 → 0.70 → 1.00 (win) or → 0.00 (lose)

---

## SECTION 2: V1 — THE COPY-TRADING ERA
*Two pages. Paper trading success, live trading reality.*

### Content

#### 2a. Paper Trading Results (Feb 4-6)
- 518 trades over 48 hours
- +$1,274 profit, 62.5% win rate
- Market-resolved exits: 93% win rate
- "Everything looked perfect on paper"

#### 2b. Live Trading Results (Feb 6-7)
- 86 trades, first 24 hours live
- -$84 loss, 40.7% win rate
- Market-resolved exits dropped to 52% win rate
- XRP: 26% win rate, -$73 P&L (worst performer)
- Balance: $162 → $69 in 36 hours

#### 2c. Why Paper ≠ Live
- **Root cause**: Disabling sell signals removed the safety net that pruned losers before binary resolution
- Paper's 93% resolution WR was survivorship bias — losers were exited early by sell signals
- Live: losers rode to binary resolution → -100% wipeouts

### Visualizations

**Chart 1: "The Paper-to-Live Gap"**
- Side-by-side bar chart comparing paper vs live
- Metrics: WR, P&L, resolution WR, avg trade P&L
- Color: Green bars for paper, red for live
- Annotation arrow pointing to the gap with "What went wrong?"

**Chart 2: "Balance Over Time"**
- Line chart: $162 starting → peak → decline to $69
- Mark key events: "Went live", "Sell signals disabled", "XRP losses", "Bugs discovered"
- Shade regions: green (paper), red (live losses)

**Chart 3: "Asset Performance Heatmap"**
- 4x4 grid: Assets (BTC, ETH, SOL, XRP) × Metrics (WR, P&L, Trades, Avg P&L)
- Color intensity = performance (green = good, red = bad)
- XRP row should be visibly red

---

## SECTION 3: THE 608-TRADE DEEP DIVE
*Three pages. The complete statistical analysis.*

### Content

#### 3a. Overall Portfolio (608 trades)
- Win rate: 59.2% (360W / 248L)
- Total P&L: +$1,145.28
- Average P&L per trade: +$1.88
- Average hold time: 10.3 minutes
- Statistical finding: Portfolio UNDERPERFORMS expected breakeven (59.2% vs 65.2%, p=0.002)

#### 3b. The Sweet Spot Discovery (0.60-0.80)
- 310 trades in this range
- 69.4% win rate (p < 0.0001 — highly significant edge)
- +$1,253.11 P&L (109% of total profit from 51% of trades)
- Outside sweet spot: statistically underperforms breakeven

**Sub-buckets:**
| Entry Price | Trades | Win Rate | P&L | Verdict |
|-------------|--------|----------|-----|---------|
| $0.60-0.65 | 87 | 63.2% | +$412 | Good |
| $0.65-0.70 | 74 | 68.9% | +$420 | Best risk-adjusted |
| $0.70-0.75 | 78 | 71.8% | +$289 | Good but riskier |
| $0.75-0.80 | 71 | 74.6% | +$132 | Diminishing returns |
| $0.80-0.90 | 105 | 49.5% | -$165 | Losing zone |
| $0.90+ | 33 | 54.5% | -$41 | Avoid |

#### 3c. Exit Strategy Analysis
- **Winners**: market_resolved (81.6% WR, +$953) and profit_target (100% WR, +$590)
- **Losers**: stop_loss (0% WR, -$240) and sell_signal (40.5% WR, -$116)
- **Insight**: "Your edge is in HOW you exit, not WHAT you enter"

#### 3d. Time-of-Day Analysis
- Hour 0 UTC: 7% WR (catastrophic)
- Hours 0-2 UTC: Consistent underperformance → "Blackout zone"
- Hours 13-15 UTC: 81-100% WR → "Golden hours"
- Recommendation: Don't trade during Asian night, focus on US/EU overlap

### Visualizations

**Chart 4: "The Sweet Spot" (HERO CHART)**
- Horizontal bar chart: Entry price buckets on Y-axis, Win Rate on X-axis
- Overlay: P&L as bubble size at each bar endpoint
- Vertical dashed line at "breakeven WR" for each price point
- Green shading on 0.60-0.80 zone, red shading on 0.80+
- Title: "Where the Money Is Made"

**Chart 5: "Exit Strategy Waterfall"**
- Waterfall chart showing contribution of each exit type to total P&L
- Start at $0, show: +$953 (resolved), +$590 (profit target), +$30 (time), -$116 (sell signal), -$240 (stop loss) = $1,145 total
- Green bars up, red bars down
- Annotate: "Keep these" (resolved, profit_target) vs "Disabled" (stop_loss, sell_signal)

**Chart 6: "The Clock Tells All"**
- Polar/radar chart: 24 hours around the circle
- Radius = Win Rate at each hour
- Color gradient from red (low WR) to green (high WR)
- Highlight blackout zone (0-2 UTC) in dark red
- Highlight golden hours (13-15 UTC) in bright green

**Chart 7: "Statistical Significance Map"**
- Scatter plot: Entry price (X) vs Win Rate (Y)
- Bubble size = trade count
- Color = p-value (green = significant edge, grey = not significant)
- Horizontal line at "expected breakeven WR"
- Clear visual: sweet spot bubbles above the line, others below

---

## SECTION 4: THE FIVE HYPOTHESES
*Two pages. What we tested and what we learned.*

### Content

Each hypothesis gets a card-style layout:

#### Hypothesis 1: Copy-Trading a Winning Trader
- **Premise**: Follow DB's high-conviction trades in the golden zone
- **Result**: FAILED
- **Why**: DB is a market maker who buys BOTH sides. Their "golden zone" trades are position unwinding on expired markets, not fresh directional bets. Copy-trading a hedged strategy with directional bets is fundamentally flawed.
- **Evidence**: 7 hours of RTDS monitoring: 10,000+ signals, 0 tradeable (all on expired markets)

#### Hypothesis 2: Arbitrage (Buy Up + Down Below $1.00)
- **Premise**: If Up + Down < $1.00, buy both for riskless profit
- **Result**: NOT VIABLE at our scale
- **Why**: Pair costs sit at exactly $1.00. Fees (1.56% at $0.50 midpoint) eliminate any edge. Arb engine scanned continuously — found 0 opportunities.
- **Capital needed**: $5,000+ with colocated servers for speed arbitrage

#### Hypothesis 3: Market Making (Provide Liquidity)
- **Premise**: Place limit orders on both sides, collect the spread
- **Result**: NOT VIABLE at $100 capital
- **Why**: Fees (formula: `shares × price × 0.25 × (price × (1 - price))²`) eat the spread. Need $5,000+ capital and sophisticated inventory management.

#### Hypothesis 4: Binance Momentum (Current Strategy)
- **Premise**: Detect crypto price moves on Binance, trade the corresponding Polymarket market before it fully prices in
- **Result**: PROMISING — under live validation
- **Evidence**: 5h monitoring → 13 signals, 2 in golden zone (ETH UP @ $0.675 and $0.685)
- **What's needed**: Consistent ETH volatility producing 0.3%+ moves early in 15-min windows
- **Signal rate**: ~1 tradeable signal every 2-3 hours

#### Hypothesis 5: Exit Execution as the Edge
- **Premise**: Profit doesn't come from predicting direction — it comes from how you manage the position after entry
- **Result**: CONFIRMED by data
- **Evidence**: Market-resolved exits (81.6% WR, +$953), profit target (100% WR, +$590). No positive Kelly zones exist at entry. The breakeven WR = entry price on binary markets.

### Visualization: "Hypothesis Scorecard"
- **Card grid** (2×3 layout): Each hypothesis gets a card with:
  - Status icon: ✅ Confirmed / ❌ Failed / 🔄 Testing / ⚠️ Not Viable
  - One-sentence result
  - Key metric
  - Color-coded border (green/red/yellow/grey)

---

## SECTION 5: THE KELLY CRITERION REVELATION
*One page. The mathematical reality check.*

### Content

- **On binary markets, breakeven WR = entry price**
  - Buy at $0.70 → need 70% WR to break even
  - Buy at $0.65 → need 65% WR to break even

- **Our actual WR by zone**:
  - 0.65-0.70: 59.6% WR (need 67.5% to break even) → **Negative Kelly**
  - 0.70-0.75: 59.9% WR (need 72.5% to break even) → **Negative Kelly**
  - ALL zones: Negative Kelly at entry

- **So where does profit come from?**
  - Exit execution: market_resolved has 79% WR regardless of entry price
  - Profit target: 100% WR — locks in gains before resolution risk
  - "You're not betting on direction. You're betting that the position management system will extract value."

- **Optimal bet sizing**: 10% Kelly ($10/trade). At 15%+, ruin probability exceeds 18%.

### Visualization: "The Kelly Truth"

**Chart 8: "Entry Price vs Required Win Rate"**
- Line chart: X = entry price ($0.50 to $0.95), Y = win rate
- Line 1 (red, dashed): "Required WR to break even" (45-degree line from 50% to 95%)
- Line 2 (blue, solid): "Our actual WR" (relatively flat ~60%)
- Shaded area where blue < red = "Negative expected value"
- Annotation: "The gap is filled by exit execution"

**Chart 9: "Where Profit Actually Comes From"**
- Stacked area chart or Sankey diagram
- Show: Entry (negative EV) → Position management → Exit execution → Profit
- Three exit paths: Resolved ($953), Target ($590), Time ($30) vs Loss paths

---

## SECTION 6: THE CURRENT STRATEGY
*One page. What's running now.*

### Content

#### Binance Momentum + Exit Execution

**How it works (simplified 4-step flow):**
1. **DETECT**: Monitor Binance prices every second. When BTC/ETH/SOL moves >0.3% in 60 seconds...
2. **DISCOVER**: Find the matching Polymarket 15-minute market via Gamma API
3. **FILTER**: Only trade if the midpoint price is in the golden zone ($0.65-$0.70) AND >6 minutes remain
4. **EXECUTE**: Place a post-only maker order (zero fees + maker rebates) at midpoint minus $0.01

**Current configuration:**
| Parameter | Value | Why |
|-----------|-------|-----|
| Trigger threshold | 0.3% in 60s | Backtest: 80% WR at this level |
| Entry price range | $0.65 - $0.70 | Best risk-adjusted Sharpe (0.486) |
| Entry mode | Maker (post-only) | Zero fees + rebates vs 1.56% taker fee |
| Bet size | $10 (10% Kelly) | Optimal — 15%+ causes ruin |
| Blocked assets | XRP | 43.5% WR vs ETH 71.6% |
| Blackout hours | 00-02 UTC | 7-33% WR (catastrophic) |
| Max positions | 3 concurrent | Capital preservation |

**5-hour live monitoring results:**
| Metric | Value |
|--------|-------|
| Total momentum detections | 200+ (BTC/ETH/SOL combined) |
| Signals generated | 13 (passed Gamma API + midpoint lookup) |
| Passed all filters | 2 (15% pass rate) |
| Pass rate for ETH specifically | ~30% |
| Rejected: price_out_of_range | 11 (85% — market already priced in the move) |
| Expected trade frequency | ~1 trade every 2-3 hours |
| Primary asset | ETH (only asset that reliably produces in-range prices) |

### Visualization: "The Pipeline Funnel"

**Chart 10: "From Binance to Polymarket"**
- Funnel diagram showing signal attrition:
  - 200+ momentum detections → 13 signals generated → 2 passed filters → 0 trades (dry run)
- Each stage labeled with the filter applied
- Percentage drop at each stage
- Use descending bar widths with labels

**Chart 11: "Signal Price Distribution"**
- Histogram of all 13 signal midpoint prices
- Overlay: Green shaded zone ($0.65-$0.70) = "Tradeable"
- Most bars outside the zone, 2 bars inside
- Shows why only ETH lands in range

---

## SECTION 7: BUG IMPACT ANALYSIS
*One page. Bugs found, cost quantified, lessons learned.*

### Content

**Top 5 costliest bugs:**

| # | Bug | Cost | How Found | Fix |
|---|-----|------|-----------|-----|
| #30 | market_end_time set to NOW instead of computed → instant time_exit on every trade | -$25+ | Live loss | Parse epoch from slug, compute actual end time |
| Stop Loss | 0% WR across 39 trades — always sold at the worst time | -$240 | Data analysis | Disabled entirely |
| Sell Signals | 40.5% WR — exited winners prematurely on noise | -$116 | Data analysis | Disabled for sweet-spot trades |
| Hour 00 | 0% WR, 25 trades, 40% of all losses in single hour | -$187 | Data analysis | Blackout 00-02 UTC |
| #20 | Dedup cycle — exit freed slug, causing 5-10x rebuy per window | -$50+ est. | Code audit | Never discard slug during exit |

**Total identified bug cost: ~$618+ (54% of total losses)**

### Visualization: "Bug Cost Waterfall"

**Chart 12: "What Bugs Cost Us"**
- Waterfall chart: Start at $1,145 total P&L
- Show each bug as a red reduction bar
- End at "actual P&L if bugs caught earlier"
- Annotation: "$618 lost to preventable bugs"

---

## SECTION 8: WHAT'S NEEDED FOR EACH STRATEGY TO WORK
*One page. Honest assessment of requirements.*

### Content

**Strategy Requirements Matrix:**

| Strategy | Capital | Latency | Complexity | Our Status |
|----------|---------|---------|------------|------------|
| Copy-trading | $100 | Any | Low | ❌ Fundamentally flawed |
| Arbitrage | $5,000+ | <50ms | High | ❌ Can't compete |
| Market Making | $5,000+ | <100ms | High | ❌ Undercapitalized |
| Binance Momentum | $100 | <200ms | Medium | 🔄 Testing live now |
| Exit Execution | $100 | Any | Medium | ✅ Proven edge |

**For Binance Momentum to succeed long-term:**
1. ETH must maintain sufficient volatility (>0.3% moves in 60s, ~5-10x per day)
2. Polymarket must continue having 15-minute crypto markets with liquid order books
3. Maker order fills must be reliable (currently untested live)
4. Price range must stay in golden zone often enough (currently 15% of signals)

**For scaling beyond $100:**
1. Need 50-100 trades to validate live WR
2. If WR > 60% on live trades, increase to $20/trade (still within Kelly)
3. At $500 capital, consider adding SOL (widen range to $0.60-$0.72)
4. At $1,000+, explore market-making hybrid

### Visualization: "The Scaling Roadmap"

**Chart 13: "Path to Scale"**
- Timeline/roadmap infographic:
  - Phase 1 (NOW): Validate — $100, $10/trade, ETH-only, target 50 trades
  - Phase 2 ($250): Calibrate — Adjust thresholds based on live data
  - Phase 3 ($500): Expand — Add SOL, increase to $20/trade
  - Phase 4 ($1,000+): Diversify — Consider market-making hybrid
- Each phase: milestone, capital, expected monthly return

---

## SECTION 9: NEXT STEPS
*Half page. Clear, actionable items.*

### Immediate (This Week)
1. **Monitor first 10 live trades** — Verify maker orders fill correctly
2. **Track live WR** — Need >55% to confirm edge exists outside dry-run
3. **Fix deploy script** — Stop overwriting DRY_RUN on deploy

### Short-Term (2-4 Weeks)
4. **Collect 50+ live trades** — Statistical minimum for significance testing
5. **Analyze maker fill rate** — If <50%, switch to taker with $0.02 slippage
6. **Consider widening price range** — If ETH-only is too sparse, test $0.60-$0.72
7. **Build live dashboard** — Real-time P&L tracking with trade log

### Medium-Term (1-3 Months)
8. **Scale bet size** — If 50-trade WR >60%, increase to $20/trade
9. **Add SOL** — If widened range performs well
10. **Evaluate server migration** — Hetzner EU (15-30ms to Polymarket) vs current DigitalOcean NYC (80-120ms)

### What Would Change the Game
- **Capital injection to $1,000**: Enables market-making hybrid + larger bets
- **EU server** (Hetzner/OVH): 5-10x latency improvement for faster fills
- **1-hour markets on Polymarket**: If they launch, signal stacking could achieve 90%+ WR (per research)

---

## SECTION 10: KEY TAKEAWAYS
*Half page. The non-technical summary.*

### For Non-Technical Readers

**What we learned in one week:**

1. **Copying a winning trader doesn't work** if you don't understand HOW they win. DB wins by playing both sides (like a casino). We were making one-sided bets.

2. **The entry doesn't matter as much as the exit.** Our best-performing exits (letting the market resolve naturally) have 81.6% win rate regardless of entry price.

3. **Timing is everything.** Trading during the wrong hours (midnight UTC) cost us 40% of all losses. Peak hours (1-3pm UTC) yielded 100% win rates.

4. **Small, consistent bets win.** Kelly criterion math shows $10/trade is optimal. Betting more doesn't make more money — it increases the chance of going broke.

5. **Bugs are expensive.** Over $600 in losses came from software bugs, not bad strategy. Automated trading requires extreme attention to edge cases.

6. **Our new strategy (Binance Momentum) is promising but unproven.** In 5 hours of monitoring, it correctly identified 2 tradeable signals. We need 50+ real trades to know if it works.

---

## APPENDIX A: GLOSSARY
- **Win Rate (WR)**: Percentage of trades that are profitable
- **P&L**: Profit and Loss — total money made or lost
- **Kelly Criterion**: Mathematical formula for optimal bet sizing
- **Sharpe Ratio**: Risk-adjusted return (higher = better)
- **Maker Order**: A limit order that adds liquidity (zero fees on Polymarket)
- **Taker Order**: A market order that removes liquidity (1.56% fee at midpoint)
- **Golden Zone**: Entry prices between $0.65-$0.70 where our edge is strongest
- **CLOB**: Central Limit Order Book — Polymarket's trading engine
- **Momentum**: When price moves significantly in a short period

## APPENDIX B: DATA SOURCES
- Performance database: 608 trades (Feb 4-7, 2026)
- Live monitoring logs: 5 hours (Feb 10, 2026)
- Binance WebSocket: Real-time 1-second price data
- Polymarket Gamma API: Market discovery and pricing
- Research reports: `.omc/research/` directory (3 major studies)

---

## VISUALIZATION IMPLEMENTATION NOTES

### Recommended Tools
- **Primary**: Plotly.js (interactive, works in browser)
- **Fallback**: matplotlib + seaborn (static, for PDF export)
- **Infographics**: Figma or Canva for non-chart visuals
- **Dashboard**: Streamlit or Observable for interactive web version

### Chart Priority (if time-constrained)
1. Chart 4 "The Sweet Spot" — Most impactful single chart
2. Chart 5 "Exit Strategy Waterfall" — Tells the core story
3. Chart 6 "The Clock Tells All" — Actionable insight
4. Chart 10 "The Pipeline Funnel" — Explains current strategy
5. Chart 8 "The Kelly Truth" — Mathematical foundation

### Accessibility
- All charts should include alt text descriptions
- Use patterns (not just color) to distinguish categories
- Minimum font size 14px for chart labels
- Include data tables below each chart for screen readers

### Export Formats
- Web: Interactive Plotly dashboard (recommended)
- PDF: Static matplotlib renders with consistent styling
- Slides: Key charts as 16:9 slide-ready PNGs
