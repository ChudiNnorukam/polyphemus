"""Simple JSON-based state persistence. Prevents duplicate actions on restarts."""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Set


class StateStore:
    """Persists and loads state from JSON files with TTL-based pruning.

    F1 fix: Atomic writes via tempfile → rename (crash-safe).
    F2 fix: load() unwraps {"timestamp":..,"value":..} → returns raw values.
    F6 fix: Sets saved with timestamps for proper TTL pruning.
    """

    def __init__(self, data_dir: str = "data", default_ttl_secs: int = 3600):
        self._data_dir = Path(__file__).parent.parent / data_dir
        self._default_ttl = default_ttl_secs
        self._logger = logging.getLogger("polyphemus.state_store")
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def load(self, key: str, ttl_secs: int = None) -> Any:
        """Load state from JSON file, pruning stale entries.

        Returns raw values (unwrapped from timestamp envelope).
        Sets are returned as set(), dicts as dict with raw values.
        """
        file_path = self._data_dir / f"{key}.json"
        try:
            if not file_path.exists():
                return set() if key.endswith("_slugs") else {}
            data = json.loads(file_path.read_text())
            if not isinstance(data, dict):
                return set() if key.endswith("_slugs") else {}
            now = time.time()
            ttl = ttl_secs if ttl_secs is not None else self._default_ttl
            pruned = {}
            for k, v in data.items():
                # Extract timestamp: wrapped entries have {"timestamp":..,"value":..}
                if isinstance(v, dict) and "timestamp" in v:
                    ts = v["timestamp"]
                    raw_value = v.get("value", v)
                else:
                    # Legacy format (set-encoded True or raw value) — use now as ts
                    ts = now
                    raw_value = v
                if now - ts < ttl:
                    pruned[k] = raw_value
            # Convert to set if this is a slugs key
            if key.endswith("_slugs"):
                return set(pruned.keys()) if pruned else set()
            return pruned if pruned else {}
        except Exception as e:
            self._logger.warning(f"Could not load state '{key}': {e}")
            return set() if key.endswith("_slugs") else {}

    def save(self, key: str, data: Any) -> None:
        """Save state to JSON file atomically (tempfile → rename).

        All entries wrapped with timestamp for TTL pruning on load.
        """
        file_path = self._data_dir / f"{key}.json"
        try:
            now = time.time()
            if isinstance(data, set):
                # Sets: store each entry with a timestamp (F6 fix)
                serialized = {item: {"timestamp": now, "value": True} for item in data}
            elif isinstance(data, dict):
                serialized = {}
                for k, v in data.items():
                    serialized[k] = {"timestamp": now, "value": v}
            else:
                serialized = data
            # F1 fix: atomic write — tempfile → rename
            self._write_atomic(file_path, serialized)
        except Exception as e:
            self._logger.warning(f"Could not save state '{key}': {e}")

    def _write_atomic(self, file_path: Path, data: Any) -> None:
        """Write JSON atomically: temp file → rename (crash-safe on POSIX)."""
        fd, temp_path = tempfile.mkstemp(
            dir=str(self._data_dir),
            prefix='.state_',
            suffix='.json',
        )
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f)
            os.rename(temp_path, str(file_path))
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
