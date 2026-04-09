"""BTC 5m evidence verdicts built from local signal history.

This module is intentionally read-only in the first implementation pass.
It scores an incoming BTC 5m signal against comparable local cohorts and
returns a verdict object that can be logged or surfaced in reports.
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from .config import setup_logger
from .models import parse_window_from_slug


ROLLED_LEVELS = (
    ("source", "price_bucket", "time_bucket", "regime", "fear_greed", "oi_trend"),
    ("source", "price_bucket", "time_bucket", "regime", "fear_greed"),
    ("source", "price_bucket", "time_bucket", "regime"),
    ("source", "price_bucket", "time_bucket"),
    ("source", "price_bucket"),
)


@dataclass
class EvidenceVerdict:
    """Comparable-cohort verdict for an incoming BTC 5m signal."""

    cohort_key: str
    sample_size: int
    r8_label: str
    expected_pnl: float
    verdict: str
    reason: str
    match_level: str = ""
    filtered_count: int = 0
    no_signal_epochs: int = 0
    total_epochs: int = 0

    def as_signal_updates(self) -> Dict[str, Any]:
        """Return signal_logger update fields."""
        return {
            "evidence_cohort": self.cohort_key,
            "evidence_sample_size": self.sample_size,
            "evidence_r8_label": self.r8_label,
            "evidence_expected_pnl": self.expected_pnl,
            "evidence_verdict": self.verdict,
            "evidence_reason": self.reason,
            "evidence_match_level": self.match_level,
        }


def r8_label(sample_size: int) -> str:
    """Apply the repo's sample-size confidence buckets."""
    if sample_size < 30:
        return f"ANECDOTAL n={sample_size}"
    if sample_size < 107:
        return f"LOW n={sample_size}"
    if sample_size < 385:
        return f"MODERATE n={sample_size}"
    return f"SIGNIFICANT n={sample_size}"


def price_bucket(price: Optional[float]) -> str:
    """Return a compact entry-price bucket label."""
    if price is None:
        return "unknown"
    if price < 0.40:
        return "0.00-0.39"
    if price < 0.60:
        return "0.40-0.59"
    if price < 0.80:
        return "0.60-0.79"
    return "0.80-1.00"


def time_bucket(secs: Optional[int]) -> str:
    """Return a compact time-remaining bucket label."""
    if secs is None:
        return "unknown"
    if secs < 60:
        return "<60s"
    if secs < 120:
        return "60-119s"
    if secs < 180:
        return "120-179s"
    return "180s+"


def fear_greed_bucket(value: Optional[float]) -> str:
    """Convert Fear & Greed into a readable regime bucket."""
    if value is None:
        return "unknown"
    if value < 20:
        return "extreme_fear"
    if value < 40:
        return "fear"
    if value < 60:
        return "neutral"
    if value < 80:
        return "greed"
    return "extreme_greed"


def normalize_text(value: Any) -> str:
    """Normalize optional text dimensions for cohort keys."""
    if value is None:
        return "unknown"
    text = str(value).strip().lower()
    return text or "unknown"


def safe_float(value: Any) -> Optional[float]:
    """Return a float when coercible, else None."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> Optional[int]:
    """Return an int when coercible, else None."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class BTC5MEvidenceEngine:
    """Read-only cohort engine for BTC 5m evidence verdicts."""

    def __init__(self, db_path: str, min_samples: int = 30, cache_ttl_secs: int = 60):
        self._db_path = Path(db_path)
        self._min_samples = min_samples
        self._cache_ttl_secs = cache_ttl_secs
        self._logger = setup_logger("polyphemus.evidence_verdict")
        self._cache_loaded_at = 0.0
        self._cache_mtime_ns: Optional[int] = None
        self._cohort_stats: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
        self._coverage_summary: Dict[str, int] = {
            "total_epochs": 0,
            "signaled_epochs": 0,
            "no_signal_epochs": 0,
        }

    def evaluate_signal(self, signal: Dict[str, Any]) -> Optional[EvidenceVerdict]:
        """Return a verdict for BTC 5m signals, else None."""
        if normalize_text(signal.get("asset")).upper() != "BTC":
            return None

        market_window_secs = safe_int(signal.get("market_window_secs"))
        if not market_window_secs:
            market_window_secs = parse_window_from_slug(signal.get("slug", ""))
        if market_window_secs != 300:
            return None

        self._refresh_cache_if_needed()
        dimensions = self._signal_dimensions(signal)
        selected = self._select_stats(dimensions)
        if selected is None:
            return EvidenceVerdict(
                cohort_key=self._format_cohort_key("source+price", (dimensions["source"], dimensions["price_bucket"])),
                sample_size=0,
                r8_label=r8_label(0),
                expected_pnl=0.0,
                verdict="shadow",
                reason="no_comparable_btc5m_history",
                match_level="none",
                no_signal_epochs=self._coverage_summary["no_signal_epochs"],
                total_epochs=self._coverage_summary["total_epochs"],
            )

        level_name, key_values, stats = selected
        sample_size = int(stats["executed_count"])
        expected_pnl = float(stats["total_pnl"]) / sample_size if sample_size else 0.0
        dominant_exit, dominant_exit_count = self._dominant_exit_reason(stats["exit_reasons"])
        filtered_count = int(stats["filtered_count"])

        if sample_size < self._min_samples:
            verdict = "shadow"
            reason = (
                f"insufficient_sample {sample_size}<{self._min_samples}"
                f" | level={level_name}"
            )
        elif expected_pnl <= 0:
            verdict = "block"
            reason = (
                f"negative_expectancy avg_pnl={expected_pnl:.4f}"
                f" | level={level_name}"
            )
        else:
            verdict = "allow"
            reason = (
                f"positive_expectancy avg_pnl={expected_pnl:.4f}"
                f" | level={level_name}"
            )

        if dominant_exit and dominant_exit_count:
            reason += f" | dominant_exit={dominant_exit}({dominant_exit_count}/{sample_size})"
        if filtered_count:
            reason += f" | filtered={filtered_count}"
        if self._coverage_summary["total_epochs"]:
            reason += (
                f" | no_signal_epochs={self._coverage_summary['no_signal_epochs']}"
                f"/{self._coverage_summary['total_epochs']}"
            )

        return EvidenceVerdict(
            cohort_key=self._format_cohort_key(level_name, key_values),
            sample_size=sample_size,
            r8_label=r8_label(sample_size),
            expected_pnl=expected_pnl,
            verdict=verdict,
            reason=reason,
            match_level=level_name,
            filtered_count=filtered_count,
            no_signal_epochs=self._coverage_summary["no_signal_epochs"],
            total_epochs=self._coverage_summary["total_epochs"],
        )

    def _refresh_cache_if_needed(self) -> None:
        """Refresh local cohort cache when the DB changes or TTL expires."""
        if not self._db_path.exists():
            self._cohort_stats = {}
            self._coverage_summary = {"total_epochs": 0, "signaled_epochs": 0, "no_signal_epochs": 0}
            return

        try:
            mtime_ns = self._db_path.stat().st_mtime_ns
        except OSError:
            return

        now = time.time()
        if (
            self._cache_mtime_ns == mtime_ns
            and (now - self._cache_loaded_at) < self._cache_ttl_secs
        ):
            return

        self._load_cache()
        self._cache_mtime_ns = mtime_ns
        self._cache_loaded_at = now

    def _load_cache(self) -> None:
        """Load comparable cohort statistics from signals.db."""
        cohort_stats: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
        coverage_summary = {"total_epochs": 0, "signaled_epochs": 0, "no_signal_epochs": 0}

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            signal_columns = self._get_table_columns(conn, "signals")
            if not signal_columns:
                self._cohort_stats = {}
                self._coverage_summary = coverage_summary
                return

            rows = conn.execute(
                f"""
                SELECT
                    {self._optional_expr(signal_columns, "source", "source", "''")},
                    {self._optional_expr(signal_columns, "entry_price", "entry_price")},
                    {self._optional_expr(signal_columns, "midpoint", "midpoint")},
                    {self._optional_expr(signal_columns, "time_remaining_secs", "time_remaining_secs")},
                    {self._optional_expr(signal_columns, "regime", "regime", "''")},
                    {self._optional_expr(signal_columns, "fear_greed", "fear_greed")},
                    {self._optional_expr(signal_columns, "oi_trend", "oi_trend", "''")},
                    {self._optional_expr(signal_columns, "pnl", "pnl")},
                    {self._optional_expr(signal_columns, "is_win", "is_win")},
                    {self._optional_expr(signal_columns, "exit_reason", "exit_reason", "''")},
                    {self._optional_expr(signal_columns, "outcome", "outcome", "''")}
                FROM signals
                WHERE asset = 'BTC' AND market_window_secs = 300
                """
            ).fetchall()

            for row in rows:
                dimensions = self._row_dimensions(dict(row))
                outcome = normalize_text(row["outcome"])
                pnl = safe_float(row["pnl"])
                is_filtered = "filtered" in outcome or outcome == "shadow"
                for level_name, key_values in self._iter_rollup_keys(dimensions):
                    stats = cohort_stats.setdefault(
                        (level_name, key_values),
                        {
                            "executed_count": 0,
                            "total_pnl": 0.0,
                            "wins": 0,
                            "exit_reasons": Counter(),
                            "filtered_count": 0,
                        },
                    )
                    if pnl is not None:
                        stats["executed_count"] += 1
                        stats["total_pnl"] += pnl
                        stats["wins"] += 1 if safe_int(row["is_win"]) == 1 else 0
                        exit_reason = normalize_text(row["exit_reason"])
                        stats["exit_reasons"][exit_reason] += 1
                    elif is_filtered:
                        stats["filtered_count"] += 1

            coverage_columns = self._get_table_columns(conn, "epoch_coverage")
            if coverage_columns:
                coverage_row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_epochs,
                        SUM(CASE WHEN bot_saw_signal = 1 THEN 1 ELSE 0 END) AS signaled_epochs
                    FROM epoch_coverage
                    WHERE asset = 'BTC' AND window_secs = 300
                    """
                ).fetchone()
                if coverage_row:
                    coverage_summary["total_epochs"] = int(coverage_row["total_epochs"] or 0)
                    coverage_summary["signaled_epochs"] = int(coverage_row["signaled_epochs"] or 0)
                    coverage_summary["no_signal_epochs"] = (
                        coverage_summary["total_epochs"] - coverage_summary["signaled_epochs"]
                    )
        finally:
            conn.close()

        self._cohort_stats = cohort_stats
        self._coverage_summary = coverage_summary

    def _select_stats(
        self,
        dimensions: Dict[str, str],
    ) -> Optional[Tuple[str, Tuple[str, ...], Dict[str, Any]]]:
        """Return the best matching roll-up cohort."""
        best_fallback = None
        best_n = -1

        for level_name, key_values in self._iter_rollup_keys(dimensions):
            stats = self._cohort_stats.get((level_name, key_values))
            if not stats:
                continue
            executed_count = int(stats["executed_count"])
            if executed_count >= self._min_samples:
                return level_name, key_values, stats
            if executed_count > best_n:
                best_fallback = (level_name, key_values, stats)
                best_n = executed_count

        return best_fallback

    def _signal_dimensions(self, signal: Dict[str, Any]) -> Dict[str, str]:
        """Normalize incoming signal into cohort dimensions."""
        entry_price = safe_float(signal.get("price"))
        if entry_price is None:
            entry_price = safe_float(signal.get("entry_price"))
        return {
            "source": normalize_text(signal.get("source")),
            "price_bucket": price_bucket(entry_price),
            "time_bucket": time_bucket(self._resolve_time_remaining(signal)),
            "regime": normalize_text(signal.get("regime") or signal.get("market_regime")),
            "fear_greed": fear_greed_bucket(safe_float(signal.get("fear_greed"))),
            "oi_trend": normalize_text(signal.get("oi_trend")),
        }

    def _row_dimensions(self, row: Dict[str, Any]) -> Dict[str, str]:
        """Normalize stored signal row into cohort dimensions."""
        entry_price = safe_float(row.get("entry_price"))
        if entry_price is None:
            entry_price = safe_float(row.get("midpoint"))
        return {
            "source": normalize_text(row.get("source")),
            "price_bucket": price_bucket(entry_price),
            "time_bucket": time_bucket(safe_int(row.get("time_remaining_secs"))),
            "regime": normalize_text(row.get("regime")),
            "fear_greed": fear_greed_bucket(safe_float(row.get("fear_greed"))),
            "oi_trend": normalize_text(row.get("oi_trend")),
        }

    def _resolve_time_remaining(self, signal: Dict[str, Any]) -> Optional[int]:
        """Get time remaining from signal payload or slug-derived epoch."""
        secs = safe_int(signal.get("time_remaining_secs"))
        if secs is not None and secs > 0:
            return secs

        slug = signal.get("slug", "")
        parts = slug.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            market_epoch = int(parts[1])
            window_secs = safe_int(signal.get("market_window_secs")) or parse_window_from_slug(slug)
            return max(0, int(market_epoch + window_secs - time.time()))
        return secs

    def _iter_rollup_keys(self, dimensions: Dict[str, str]) -> Iterable[Tuple[str, Tuple[str, ...]]]:
        """Yield roll-up keys from strictest to broadest."""
        for fields in ROLLED_LEVELS:
            yield "+".join(fields), tuple(dimensions[field] for field in fields)

    def _dominant_exit_reason(self, reasons: Counter) -> Tuple[str, int]:
        """Return the dominant exit reason if present."""
        if not reasons:
            return "", 0
        return reasons.most_common(1)[0]

    def _format_cohort_key(self, level_name: str, key_values: Tuple[str, ...]) -> str:
        """Format a readable cohort key for logging/reporting."""
        labels = level_name.split("+") if level_name else []
        parts = [f"{label}={value}" for label, value in zip(labels, key_values)]
        return " | ".join(parts)

    def _get_table_columns(self, conn: sqlite3.Connection, table: str) -> set:
        """Return table columns or an empty set when missing."""
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchall()
        if not rows:
            return set()
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] for row in info}

    def _optional_expr(
        self,
        columns: set,
        name: str,
        alias: str,
        default_sql: str = "NULL",
    ) -> str:
        """Build an optional SQLite select expression."""
        if name in columns:
            return f"{name} AS {alias}"
        return f"{default_sql} AS {alias}"
