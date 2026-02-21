import json
import os
import time
import tempfile

from .config import setup_logger


class SelfTuner:
    """Cache-only multiplier tuner for Polyphemus trading bot.

    Minimal hot-path implementation: get_multiplier() is zero-disk, zero-DB.
    State refresh every 15 minutes only.
    """

    def __init__(self, state_path: str):
        """Initialize tuner with state file path.

        Args:
            state_path: Path to tuning_state.json
        """
        self._state_path = state_path
        self._cache: dict = {}
        self._killed: bool = False
        self._kill_time: float = 0
        self._last_refresh: float = 0
        self._refresh_interval = 900  # 15 minutes
        self._logger = setup_logger('polyphemus.tuner')
        self._load_state()

    def get_multiplier(self, price: float) -> float:
        """Get sizing multiplier for entry price. Cache-only, zero disk access.

        Args:
            price: Entry price (e.g., 0.68)

        Returns:
            Multiplier between 0.5 (floor) and 1.5 (ceiling)
        """
        # Determine bucket
        bucket = '0.65-0.70' if price < 0.70 else '0.70-0.80'

        # Refresh cache if stale
        if time.time() - self._last_refresh > self._refresh_interval:
            self._load_state()

        # Kill switch overrides everything: return 0.5x floor
        if self._killed:
            return 0.5

        # Return cached multiplier with bounds
        mult = self._cache.get(bucket, 1.0)
        return max(0.5, min(1.5, mult))

    def update_state(self, bucket: str, won: bool, balance: float) -> None:
        """Update state after trade (win/loss). Writes atomically to disk.

        Args:
            bucket: '0.65-0.70' or '0.70-0.80'
            won: True if trade won, False if lost
            balance: Current USDC balance
        """
        try:
            # Read current state
            state = self._read_state_file()

            multipliers = state.get('multipliers', {'0.65-0.70': 1.0, '0.70-0.80': 1.0})
            consecutive_losses = state.get('consecutive_losses', {'0.65-0.70': 0, '0.70-0.80': 0})

            # Handle backwards compat: if consecutive_losses is an int (old format), convert to dict
            if isinstance(consecutive_losses, int):
                consecutive_losses = {'0.65-0.70': consecutive_losses, '0.70-0.80': consecutive_losses}

            peak_balance = state.get('peak_balance', balance)
            kill_time = state.get('kill_time', 0)

            # Update multiplier and loss counter (per-bucket)
            if won:
                consecutive_losses[bucket] = 0
                multipliers[bucket] = min(1.5, multipliers[bucket] + 0.05)
                self._logger.info(f"✓ Win: {bucket} multiplier → {multipliers[bucket]:.2f}x")
            else:
                consecutive_losses[bucket] = consecutive_losses.get(bucket, 0) + 1
                multipliers[bucket] = max(0.5, multipliers[bucket] - 0.10)
                self._logger.info(f"✗ Loss #{consecutive_losses[bucket]}: {bucket} multiplier → {multipliers[bucket]:.2f}x")

            # Update peak and current balance
            peak_balance = max(peak_balance, balance)

            # Write atomically
            new_state = {
                'multipliers': multipliers,
                'consecutive_losses': consecutive_losses,
                'peak_balance': peak_balance,
                'current_balance': balance,
                'kill_time': kill_time,
            }
            self._write_state_atomic(new_state)

            # Invalidate cache to pick up new state on next refresh
            self._last_refresh = 0

        except Exception as e:
            self._logger.error(f"Failed to update state: {e}")

    def _load_state(self) -> None:
        """Load state from disk, apply circuit breaker and kill switch."""
        try:
            state = self._read_state_file()
            multipliers = state.get('multipliers', {'0.65-0.70': 1.0, '0.70-0.80': 1.0})
            consecutive_losses = state.get('consecutive_losses', {'0.65-0.70': 0, '0.70-0.80': 0})
            peak_balance = state.get('peak_balance', 0.0)
            current_balance = state.get('current_balance', 0.0)
            kill_time = state.get('kill_time', 0)

            # Handle backwards compat: if consecutive_losses is an int (old format), convert to dict
            if isinstance(consecutive_losses, int):
                consecutive_losses = {'0.65-0.70': consecutive_losses, '0.70-0.80': consecutive_losses}

            # Circuit breaker: 3+ consecutive losses per bucket → 0.5x floor for that bucket only
            for bucket in multipliers:
                if consecutive_losses.get(bucket, 0) >= 3:
                    multipliers[bucket] = 0.5
                    self._logger.warning(f"Circuit breaker: {bucket} has 3+ losses, multiplier → 0.5x")

            # Kill switch: >30% drawdown from peak → all 0.5x (disabled but with cooldown)
            self._killed = False
            if peak_balance > 0 and (peak_balance - current_balance) / peak_balance > 0.30:
                # Check if kill switch was previously activated
                if kill_time == 0:
                    # First time kill switch triggered: set kill_time and set all to 0.5x
                    kill_time = time.time()
                    multipliers = {k: 0.5 for k in multipliers}
                    self._killed = True
                    self._logger.error(f"Kill switch: >30% drawdown ({peak_balance:.2f} → {current_balance:.2f}), all multipliers → 0.5x floor, 15min cooldown active")
                else:
                    # Kill switch was previously set: check if 15min cooldown has expired
                    elapsed = time.time() - kill_time
                    if elapsed > 900:
                        # Cooldown expired: auto-reset
                        kill_time = 0
                        multipliers = {k: 0.5 for k in multipliers}
                        self._killed = False
                        self._logger.info(f"Kill switch auto-reset after 15min cooldown, multipliers at 0.5x floor")
                    else:
                        # Still in cooldown
                        multipliers = {k: 0.5 for k in multipliers}
                        self._killed = True
                        self._logger.warning(f"Kill switch active: {elapsed:.0f}s / 900s cooldown remaining")
            else:
                # Drawdown is no longer >30%: clear kill_time and re-enable
                kill_time = 0
                self._killed = False

            self._cache = multipliers
            self._kill_time = kill_time
            self._last_refresh = time.time()

            # Persist updated kill_time back to disk
            state['kill_time'] = kill_time
            state['consecutive_losses'] = consecutive_losses
            self._write_state_atomic(state)

        except FileNotFoundError:
            self._cache = {'0.65-0.70': 1.0, '0.70-0.80': 1.0}
            self._last_refresh = time.time()
        except Exception as e:
            self._logger.error(f"Failed to load state: {e}, using defaults")
            self._cache = {'0.65-0.70': 1.0, '0.70-0.80': 1.0}
            self._last_refresh = time.time()

    def _read_state_file(self) -> dict:
        """Read state from disk. Raises on error."""
        with open(self._state_path, 'r') as f:
            return json.load(f)

    def _write_state_atomic(self, state: dict) -> None:
        """Write state atomically: temp file → rename."""
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)

        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(self._state_path),
            prefix='.tuning_state_',
            suffix='.json'
        )
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(state, f)
            os.rename(temp_path, self._state_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
