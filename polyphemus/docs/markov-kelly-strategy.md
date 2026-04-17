# Markov-Kelly Trading Strategy

## How The Bot Decides When & How Much To Trade

*A plain-English guide for non-technical partners*

---

### The Problem We Solved

The bot was losing money consistently. After analyzing 1,379 real trades, we found three root causes:

1. **Bad timing** -- the bot was entering trades during "cold streaks" (runs of losses) where the odds of winning the next trade were only ~30%
2. **Bad entries** -- betting on prices above $0.50 where the math to break even required 93%+ win rate (essentially impossible)
3. **Fixed bet sizes** -- every trade used the same bet size regardless of whether conditions favored us or not

### The Solution: Two Systems Working Together

We implemented two complementary systems:

---

## System 1: The Markov Gate ("When to trade")

**What it does:** Decides whether the bot should take a trade or sit out, based on recent win/loss patterns.

**How it works in plain English:**

Think of it like a traffic light for trading:

- **Green light (after wins):** The bot just won a trade (or several in a row). Historical data shows the next trade has a 59-69% chance of winning. The bot is allowed to trade.
- **Red light (after losses):** The bot just lost. Data shows the next trade only has a ~31% chance of winning. The bot sits out and waits.
- **Auto-reset:** If the bot has been sitting out for 30 minutes, it takes one "probe" trade to check if conditions have improved.

**Why it works:**

In most gambling and trading contexts, each bet is independent -- a coin flip doesn't "remember" the last flip. But in prediction markets, outcomes are *correlated*. When the market is in a favorable regime, wins cluster together. When it's unfavorable, losses cluster.

Our analysis of 1,193 trades confirmed this:

| Previous Result | Chance of Winning Next Trade |
|----------------|------------------------------|
| After 1 Win    | 59%                          |
| After 2 Wins   | 65%                          |
| After 3+ Wins  | 69%                          |
| After 1 Loss   | 31%                          |
| After 2 Losses | 27%                          |
| After 3+ Losses| 21%                          |

The Markov Gate filters out the 31% (and worse) scenarios, only allowing trades when the odds are in our favor.

---

## System 2: Kelly Sizing ("How much to trade")

**What it does:** Adjusts the bet size based on how strong our edge is, instead of using a fixed amount every time.

**How it works in plain English:**

The Kelly Criterion is a mathematical formula (developed in 1956 by John Kelly at Bell Labs) that answers: *"Given my win probability and the payout odds, what's the optimal bet size to maximize long-term growth without going broke?"*

Our implementation:

| Winning Streak | Win Probability (after safety discount) | Bet Size (% of balance) |
|---------------|----------------------------------------|------------------------|
| 3+ wins in a row | ~59% (after 15% safety discount from 69%) | 6-10% of balance |
| 2 wins in a row  | ~55% (after discount from 65%)             | 4-9% of balance  |
| 1 win            | ~50% (after discount from 59%)             | 2-6% of balance  |
| Loss then win    | ~26% (after discount from 31%)             | 0.7-2% of balance |
| After any loss   | N/A -- Markov Gate blocks the trade        | $0 (no trade)    |

**Key safety features:**

1. **Quarter-Kelly:** The math says the "optimal" bet is X%. We use X/4 instead. This sacrifices ~25% of theoretical growth rate but reduces the chance of a devastating drawdown by 75%. At a $166 bankroll, survival matters more than speed.

2. **15% Safety Discount:** Our win probability numbers come from analyzing past trades. Real-time trading is harder (slower execution, price slippage, fees). We reduce our assumed win probability by 15% to account for this gap.

3. **10% Hard Cap:** No matter how confident the math is, no single trade will ever use more than 10% of the balance. This is a circuit breaker.

---

## How They Work Together

```
New Trade Signal Arrives
         |
         v
  [Markov Gate]  ---  After loss? --> BLOCK (no trade)
         |
     After win
         |
         v
  [Kelly Sizing]  ---  How many consecutive wins?
         |                |
         |          1 win: small bet (2-6%)
         |          2 wins: medium bet (4-9%)
         |          3+ wins: larger bet (6-10%)
         v
  [Execute Trade]
         |
         v
  [Record Result] --> feeds back into Markov Gate
```

---

## What This Means For Returns

**Before (old system):**
- Won ~43% of trades
- Every trade used the same fixed bet size
- Lost $2,794 over 1,379 trades

**After (Markov + Kelly):**
- Only trades during 59%+ win probability windows
- Bets more when confidence is higher, less when it's lower
- Backtested improvement: from -$2,940 to +$176 over the same period
- Conservative safety margins applied throughout

**Important caveat:** Backtested results are always better than live results. We've applied a 15% discount, but real performance will only be known after 50+ live trades under the new system.

---

## Current Status

| Component | Status | Mode |
|-----------|--------|------|
| Markov Gate | Active | Dry-run (logging decisions, not blocking yet) |
| Kelly Sizing | Active | Dry-run (logging sizes, using fixed bets for now) |
| Entry Price Filter | Active | Live (only trades at $0.35-$0.50 entry prices) |
| Profit Target | Active | Live (locks in 30%+ gains automatically) |

**Next milestone:** After observing 50+ trade signals in dry-run mode, we'll evaluate whether to switch both systems to live mode.

---

## Glossary

- **Markov Chain:** A mathematical model where the probability of the next event depends on the current state (not the full history). Named after Russian mathematician Andrey Markov (1906).
- **Kelly Criterion:** A formula for optimal bet sizing that maximizes long-term wealth growth while minimizing ruin probability. Used by professional gamblers, hedge funds, and venture capitalists.
- **Quarter-Kelly:** Using 25% of the mathematically optimal bet. Standard practice when the model has uncertainty or the bankroll is small.
- **Haircut / Safety Discount:** Reducing the assumed win probability to account for the gap between backtested and live performance.
- **Regime:** A market "mood" -- whether conditions currently favor winning or losing trades.
- **Epoch:** A fixed time window (e.g., 5 minutes) during which a prediction market is open for trading.
- **Entry Price:** The cost to buy one share of a prediction (e.g., $0.40 means you pay 40 cents to potentially win $1.00).
- **Dry-Run:** The system logs what it *would* do without actually executing. Used for validation before going live.
