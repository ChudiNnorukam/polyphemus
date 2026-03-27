"""Data Health Check - Session startup and cron monitor for Polyphemus data pipeline.

Run at session startup to catch data gaps before they block research.
Run as cron for continuous monitoring.

Usage:
    # Session startup (on VPS)
    python3 tools/data_health_check.py --data-dir /opt/lagbot/instances/emmanuel/data

    # With label spot-check (slower, but catches silent corruption)
    python3 tools/data_health_check.py --data-dir /opt/lagbot/instances/emmanuel/data --spot-check

    # Cron mode (exit code 1 if unhealthy, for alerting)
    python3 tools/data_health_check.py --data-dir /opt/lagbot/instances/emmanuel/data --cron
"""

import argparse
import sys
import os

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_utils import health_check, spot_check_labels


def main():
    parser = argparse.ArgumentParser(description="Polyphemus data health check")
    parser.add_argument("--data-dir", required=True, help="Path to instance data dir")
    parser.add_argument("--spot-check", action="store_true", help="Spot-check label accuracy")
    parser.add_argument("--spot-n", type=int, default=20, help="Number of labels to spot-check")
    parser.add_argument("--cron", action="store_true", help="Cron mode: exit 1 if unhealthy")
    args = parser.parse_args()

    print("=" * 60)
    print("POLYPHEMUS DATA HEALTH CHECK")
    print("=" * 60)

    result = health_check(args.data_dir)
    any_issues = False

    for name, report in result["reports"].items():
        status = "OK" if not report.issues else "WARN"
        fresh = "FRESH" if report.is_fresh else "STALE"
        if report.issues:
            any_issues = True
            status_icon = "!!"
        else:
            status_icon = "OK"

        print(f"\n[{status_icon}] {name}")
        print(f"    Exists: {report.exists} | Integrity: {report.integrity_ok} | Rows: {report.row_count:,}")
        print(f"    Latest: {report.latest_timestamp} | Freshness: {report.freshness_secs:.0f}s [{fresh}]")
        if report.issues:
            for issue in report.issues:
                print(f"    >> {issue}")

    if args.spot_check:
        signals_db = os.path.join(args.data_dir, "signals.db")
        if os.path.exists(signals_db):
            print(f"\n{'='*60}")
            print(f"LABEL SPOT-CHECK (n={args.spot_n})")
            print(f"{'='*60}")
            sc = spot_check_labels(signals_db, args.spot_n)
            print(f"  Checked: {sc['checked']}")
            print(f"  Correct: {sc['correct']}")
            print(f"  Incorrect: {sc['incorrect']}")
            print(f"  Accuracy: {sc['accuracy']:.1%}")
            if sc["mismatches"]:
                any_issues = True
                print(f"\n  MISMATCHES:")
                for mm in sc["mismatches"][:5]:
                    print(f"    id={mm['id']} {mm['slug']}: "
                          f"dir={mm['direction']} oracle={mm['oracle']} "
                          f"is_win={mm['is_win']} expected={mm['expected']}")
            if sc["accuracy"] < 0.95:
                any_issues = True
                print(f"\n  >> CRITICAL: Label accuracy {sc['accuracy']:.1%} < 95% threshold")
                print(f"  >> Run outcome backfill to fix labels before any ML training")

    print(f"\n{'='*60}")
    if any_issues:
        print("STATUS: ISSUES DETECTED - review before research")
    else:
        print("STATUS: ALL HEALTHY")
    print(f"Checked at: {result['checked_at']}")
    print(f"{'='*60}")

    if args.cron and any_issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
