# Binance Momentum Strategy - Implementation Complete

**Date:** 2026-02-09  
**Status:** ✓ COMPLETE & VERIFIED  
**Location:** `/Users/chudinnorukam/Projects/business/sigil/`

## Summary

Successfully implemented the Binance Momentum strategy for Sigil Polymarket trading bot. This replaces copy-trading signals with real-time Binance spot price momentum detection + post-only maker orders (zero fees).

## What Was Built

### 1. New File: `binance_momentum.py` (330 LOC)

**BinanceMomentumFeed** class that:
- Connects to Binance WebSocket (1s klines for BTC, ETH, SOL)
- Detects momentum via rolling window % change
- Queries Gamma API for Polymarket market discovery
- Generates signals with slug-based deduplication
- Maintains exponential backoff reconnection logic

### 2. Enhanced Configuration (`config.py`)

Added 6 new Settings fields:
```python
signal_mode: str = "copy_trade"              # Primary mode selector
momentum_trigger_pct: float = 0.005          # 0.5% momentum threshold
momentum_window_secs: int = 60               # Rolling window duration
min_secs_remaining: int = 480                # Min time left in market
entry_mode: str = "taker"                    # Order mode: taker vs maker
maker_offset: float = 0.01                   # Maker offset from midpoint
```

### 3. Post-Only Order Support (`clob_wrapper.py`)

Enhanced `place_order()` method:
- Added `post_only: bool = False` parameter
- When `post_only=True`: Two-step order (sign + post with flag)
- When `post_only=False`: Existing behavior (unchanged)
- Zero fees on post-only fills

### 4. Maker Order Mode (`position_executor.py`)

Added conditional logic in `execute_buy()`:
- **Maker mode** (`entry_mode="maker"`):
  - Place order at `midpoint - maker_offset`
  - Use `post_only=True` flag
  - Zero trading fees
- **Taker mode** (default):
  - Keep existing smart slippage logic
  - `+$0.02` on live midpoint, cap `+$0.05` from signal
  - Guaranteed fill

### 5. Signal Feed Integration (`signal_bot.py`)

Wired momentum feed as primary signal source:
- When `signal_mode == "binance_momentum"`: Create `BinanceMomentumFeed` (PRIMARY)
- When `signal_mode == "copy_trade"`: Create existing feed (UNCHANGED)
- Conditional task management for momentum feed
- Skip Binance confirmation for momentum signals

### 6. Smart Signal Filtering (`signal_guard.py`)

Adapted filters for momentum signals:
- Skip direction check (momentum signals always BUY by design)
- Skip conviction check (momentum signals have synthetic high conviction)
- Keep market expiry, blocklist, and blackout checks
- Per-slug deduplication (max 1 signal per 15-min market)

## Key Features

✓ **Real-Time Momentum Detection**
- Binance WebSocket streaming (1s candles)
- Configurable rolling window & trigger threshold
- Three crypto assets: BTC, ETH, SOL

✓ **Post-Only Maker Orders**
- Zero trading fees on fills
- Tight spreads (default: $0.01 below midpoint)
- Graceful fallback on missing midpoint

✓ **Dual-Mode Operation**
- Default: `signal_mode=copy_trade` (existing behavior)
- Optional: `signal_mode=binance_momentum` (new strategy)
- Zero breaking changes

✓ **Backward Compatible**
- All existing APIs unchanged
- Copy-trade mode fully functional
- Easy rollback via .env change

## Verification

All 6 files compiled successfully:
```
✓ binance_momentum.py - New momentum signal generator
✓ config.py - Configuration fields
✓ clob_wrapper.py - Post-only order support
✓ position_executor.py - Maker order mode
✓ signal_bot.py - Momentum feed integration
✓ signal_guard.py - Filter adaptation
```

Integration tests passed:
```
✓ Config fields instantiable
✓ BinanceMomentumFeed instantiable (3 symbols ready)
✓ Signal guard skips conviction check for momentum
✓ ClobWrapper post_only parameter functional
```

## Deployment Checklist

### Phase 1: Local Testing (dry_run=True)

- [ ] Review all 6 modified files
- [ ] Update `.env` with momentum config:
  ```bash
  SIGNAL_MODE=binance_momentum
  MOMENTUM_TRIGGER_PCT=0.005
  MOMENTUM_WINDOW_SECS=60
  MIN_SECS_REMAINING=480
  ENTRY_MODE=maker
  MAKER_OFFSET=0.01
  DRY_RUN=true
  ```
- [ ] Start bot: `python3 -m sigil.main`
- [ ] Monitor logs for:
  - `[INFO] Binance momentum WS connected`
  - `[INFO] Momentum detected: BTC ...`
  - `[INFO] Signal generated: ...`
- [ ] Verify no errors for 1+ hour
- [ ] Check Gamma API success rate (target: >99%)

### Phase 2: VPS Deployment

- [ ] Copy files to VPS:
  ```bash
  scp -r sigil/ user@142.93.143.178:/opt/sigil/sigil/
  ```
- [ ] SSH to VPS and restart:
  ```bash
  systemctl restart sigil
  journalctl -u sigil -f
  ```
- [ ] Monitor for 24+ hours in dry_run mode
- [ ] Verify post-only fill rates (target: >70%)
- [ ] Compare performance vs copy-trade baseline

### Phase 3: Live Trading (if confident)

- [ ] Set `DRY_RUN=false` in .env
- [ ] Restart: `systemctl restart sigil`
- [ ] Monitor for 5+ trades
- [ ] Verify P&L positive
- [ ] Scale position size gradually

## Configuration Examples

### Conservative (Lower frequency, higher confidence)
```bash
MOMENTUM_TRIGGER_PCT=0.010       # 1% move
MOMENTUM_WINDOW_SECS=120         # 2-minute window
MAKER_OFFSET=0.005               # Tighter spread
```

### Aggressive (Higher frequency, faster entry)
```bash
MOMENTUM_TRIGGER_PCT=0.002       # 0.2% move
MOMENTUM_WINDOW_SECS=30          # 30-second window
MAKER_OFFSET=0.020               # Wider spread
```

### Recommended (Balanced - default)
```bash
MOMENTUM_TRIGGER_PCT=0.005       # 0.5% move (default)
MOMENTUM_WINDOW_SECS=60          # 1-minute window (default)
MAKER_OFFSET=0.010               # 1¢ offset (default)
MIN_SECS_REMAINING=480           # 8 minutes left (default)
```

## Monitoring & Troubleshooting

### Expected Log Messages

```
[INFO] Starting Binance momentum feed | trigger=0.5% | window=60s | entry_mode=maker
[INFO] Binance momentum WS connected
[INFO] Momentum detected: BTC UP +0.523% in 60s (0.4831 -> 0.4856)
[INFO] Signal generated: btc-updown-15m-1770620400 Up @ 0.4844 (480s remaining)
[DEBUG] Received signal: btc-updown-15m-1770620400
[INFO] Buy executed: btc-updown-15m-1770620400 Order: 0x123abc @ 0.4744 x 50.0
```

### If Momentum Signals Not Detected

- Check Binance WebSocket connectivity
- Verify `MOMENTUM_TRIGGER_PCT` is reasonable (0.5% default)
- Check `MOMENTUM_WINDOW_SECS` (60s default)
- Look for WebSocket disconnect/reconnect logs

### If Gamma API Fails

- Verify network connectivity to `gamma-api.polymarket.com`
- Check JSON parsing (clobTokenIds & outcomes are JSON STRINGS)
- Monitor API latency (target: <500ms)

### If Post-Only Orders Not Filling

- Check `MAKER_OFFSET` value (too tight = fewer fills)
- Monitor market spreads on target markets
- Consider larger offset (0.02-0.03) for better fill rate
- Verify order book depth with `get_order_book()`

## Fallback Plan

To revert to copy-trade without code changes:

```bash
# Update .env
SIGNAL_MODE=copy_trade
ENABLE_BINANCE_CONFIRMATION=true
ENTRY_MODE=taker

# Restart
systemctl restart sigil
journalctl -u sigil -f
```

All code paths are conditional — no recompilation needed.

## File Locations

All files at: `/Users/chudinnorukam/Projects/business/sigil/`

```
sigil/
├── binance_momentum.py          [NEW - 330 LOC]
├── config.py                    [MODIFIED - +6 fields]
├── clob_wrapper.py              [MODIFIED - +post_only]
├── position_executor.py         [MODIFIED - +maker mode]
├── signal_bot.py                [MODIFIED - +momentum wiring]
└── signal_guard.py              [MODIFIED - +momentum filtering]
```

## Critical Implementation Details

1. **Gamma API Quirk**: `clobTokenIds` and `outcomes` are JSON STRINGS
   - Must `json.loads()` before use

2. **Binance Symbols**: Lowercase with 'usdt' suffix
   - `btcusdt`, `ethusdt`, `solusdt` (NOT `BTC`, `ETH`, `SOL`)

3. **Market Slug Pattern**: `{asset}-updown-15m-{epoch}`
   - Epoch rounded to nearest 900-second boundary

4. **Signal Marker**: `source='binance_momentum'`
   - Allows guard & signal_bot to skip irrelevant checks

5. **Post-Only Two-Step**: `create_order()` then `post_order(post_only=True)`
   - Required for `OrderType.GTC` with post_only flag

## Success Criteria

- [ ] Binance momentum signals detected every 5-30 minutes
- [ ] Post-only order fill rate >70% (at 1¢ offset)
- [ ] Gamma API success rate >99%
- [ ] No WebSocket disconnects for 24+ hours
- [ ] P&L positive vs copy-trade baseline
- [ ] Zero duplicate entries per market

## Next Actions

1. **Review**: Check all 6 modified files in the codebase
2. **Test**: Deploy locally with dry_run=True for 24+ hours
3. **Monitor**: Track signal frequency, fill rates, Gamma API health
4. **Tune**: Adjust momentum_trigger_pct based on signal patterns
5. **Deploy**: Copy to VPS and restart systemd service
6. **Verify**: Monitor live trading for confidence
7. **Scale**: Increase position sizing once profitable

## Support

For questions or issues:
- Check logs for error messages
- Review critical implementation details above
- Test with more conservative parameters first
- Monitor Gamma API latency & success rate

---

**Implementation Date:** 2026-02-09  
**Status:** Ready for deployment ✓
