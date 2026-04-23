# Markov Gate Audit & Tuning Recommendation — 2026-04-17

## Live state (as of 2026-04-17 17:00 PST)

```
# /opt/lagbot/instances/emmanuel/.env
MARKOV_GATE_ENABLED=true
MARKOV_GATE_DRY_RUN=false        # actually blocks (not shadow)
MARKOV_GATE_MAX_LOSSES=3         # blocks after 3 consecutive losses
MARKOV_GATE_MIN_WINS=1
MARKOV_KELLY_ENABLED=true
MARKOV_KELLY_HAIRCUT=0.15
MARKOV_KELLY_MAX_BET_PCT=0.10
```

Last 48 hours (emmanuel DRY_RUN=true): **14 trades, 0 wins, -$100.39 PnL.**

journalctl shows `Markov gate BLOCKED at startup | last 10 trades were losses`
repeated at every restart since 2026-04-17 09:12 PST. The gate IS firing, IS
blocking signals, but the auto-unblock every 30 min probes a regime that is
still cold → another loss → re-block. The gate slows bleed; it does not stop
it.

## Task #2 premise vs reality

The original Task #2 was "activate Markov-Kelly gate in shadow mode." That
premise is stale — the gate has been live (not shadow) since at least Apr 16.
The question shifts to: **is the current `MAX_LOSSES=3` tuning correct?**

## Sensitivity simulation on 508 trades (Mar 4 - Mar 25)

All 508 trades predate the gate's activation, so this is a clean
counterfactual: what would the gate have done to this sample?

| max_L | taken_n | blocked_n | taken_WR | taken_Sh | taken_PnL | blocked_PnL |
|:---:|---:|---:|---:|---:|---:|---:|
| 1 | 174 | 334 | 57.5% | +0.04 | **+$29.86** | **-$91.19** |
| 2 | 256 | 252 | 57.8% | +0.01 | +$9.69 | -$71.02 |
| **3 (current)** | 345 | 163 | 57.4% | -0.02 | **-$54.33** | -$6.99 |
| 4 | 414 | 94 | 56.3% | -0.02 | -$59.83 | -$1.49 |
| 5 | 456 | 52 | 57.7% | +0.01 | +$15.64 | -$76.96 |

Baseline (no gate): 508 trades, -$61.33.

**Current `MAX_LOSSES=3` barely helps**: saves only $7 of blocked PnL. Because
it requires 3 full losses to trigger, small-streak bleeds slip through.

**`MAX_LOSSES=1` would have saved $91** on this sample. The gate blocks the
entire 334-trade -$91 loss cluster.

## Biggest loss streaks in the 508-trade sample

| streak | duration | start → end (UTC) |
|---:|:---:|:---|
| 8 losses | 2.6 h | 03-24 15:05 → 17:41 |
| 8 losses | 0.5 h | 03-23 07:56 → 08:26 |
| 7 losses | 18.4 h | 03-20 20:25 → 03-21 14:51 |
| 7 losses | 1.2 h | 03-20 18:30 → 19:40 |
| 6 losses | 0.2 h | 03-23 23:45 → 23:55 |

These match the "0-for-N" collapses MEMORY.md flags. `MAX_LOSSES=1`
catches all of them after loss #1; `MAX_LOSSES=3` only catches after #3.

## Recommendation

**Tighten `MARKOV_GATE_MAX_LOSSES` from 3 → 1.** Keep `MIN_WINS=1` and
`TIMEOUT=1800`.

Trade-off:
- Blocks 66% of signals (vs 32% at current). Volume drops from ~24 trades/day
  to ~8/day during normal regimes, close to zero during cold regimes.
- In good regimes the gate still leaves money on the table (it will block
  after any single loss even when WR is 56%). Acceptable because: (a) the
  downside (-$91 over 21 days on max_L=3) is much worse than the upside of
  squeezing more trades in.
- Keeps emmanuel in DRY_RUN while we observe the tuning. No live-money risk.

## Alternative: move toward TRUE shadow mode

If Chudi wants to preserve the original "shadow mode" framing for data
collection, flip `MARKOV_GATE_DRY_RUN=true`. That way the gate logs what it
WOULD block but takes every trade. Cleaner for A/B measurement but costs us
the bleed-mitigation the gate is currently providing.

**Not recommended** given emmanuel is actively in a cold regime.

## .env patch draft (NOT yet deployed — requires Chudi OK)

```bash
# On VPS, as root:
cp /opt/lagbot/instances/emmanuel/.env \
   /opt/lagbot/instances/emmanuel/.env.bak.$(date +%s)
sed -i 's/^MARKOV_GATE_MAX_LOSSES=.*/MARKOV_GATE_MAX_LOSSES=1/' \
   /opt/lagbot/instances/emmanuel/.env

# Verify
grep ^MARKOV_ /opt/lagbot/instances/emmanuel/.env

# Check open positions (LIFECYCLE required)
sqlite3 /opt/lagbot/instances/emmanuel/data/performance.db \
  'SELECT COUNT(*) FROM trades WHERE exit_time IS NULL'
# Expect: 0  (emmanuel is currently 0-for-14 with no open positions)

# Restart
systemctl restart lagbot@emmanuel

# Post-deploy verify (60s minimum)
journalctl -u lagbot@emmanuel --since '60 seconds ago' | \
  grep -iE 'error|traceback|markov gate'
```

Rollback: `cp /opt/lagbot/instances/emmanuel/.env.bak.<ts> .env && systemctl restart lagbot@emmanuel`

## Open question for Task #3

The 14/14 loss streak at >99% statistical implausibility for a 56% WR strategy
suggests something more fundamental than streak variance. The next
investigation (Task #3) is the `binance_price_at_fill` dropout — 91% NULL for
recent trades means adverse-selection instrumentation is broken and we cannot
see whether our entry price is systematically off from the Binance reference.
That may be the root cause of the recent collapse.
