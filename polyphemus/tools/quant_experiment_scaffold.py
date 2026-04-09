#!/usr/bin/env python3
"""Create a repo-native scaffold for a single quant experiment.

The scaffold enforces one-hypothesis-per-era and gives every experiment the
same minimum artifacts before strategy work starts.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_DIR = ROOT / ".omc" / "experiments"
TEMPLATES_DIR = ROOT / ".omc" / "templates"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "experiment"


def render_template(name: str, replacements: dict[str, str]) -> str:
    content = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"<{key}>", value)
    return content


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scaffold a new quant experiment")
    parser.add_argument("--slug", required=True, help="Experiment slug or title")
    parser.add_argument("--asset", required=True, help="Primary asset, e.g. BTC")
    parser.add_argument("--windows", required=True, help="Window scope, e.g. 5m or 5m,15m")
    parser.add_argument("--entry-family", required=True, help="Entry family, e.g. cheap_side")
    parser.add_argument("--exit-family", default="hold_to_resolution", help="Exit family")
    parser.add_argument("--mode", default="shadow", choices=["shadow", "dry-run", "live-candidate"])
    parser.add_argument("--force", action="store_true", help="Overwrite if the scaffold exists")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    slug = slugify(args.slug)
    experiment_dir = EXPERIMENTS_DIR / slug

    if experiment_dir.exists() and not args.force:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "error": "already_exists",
                    "experiment_dir": str(experiment_dir),
                }
            )
        )

    experiment_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    replacements = {
        "slug": slug,
        "timestamp": now,
        "asset": args.asset,
        "windows": args.windows,
        "entry_family": args.entry_family,
        "exit_family": args.exit_family,
        "shadow|dry-run|live-candidate": args.mode,
    }

    files = {
        "hypothesis.md": render_template("quant_hypothesis.md", replacements),
        "evidence_log.md": render_template("quant_evidence_log.md", replacements),
        "promotion_review.md": render_template("quant_promotion_review.md", replacements),
    }

    for rel_path, content in files.items():
        (experiment_dir / rel_path).write_text(content, encoding="utf-8")

    metadata = {
        "slug": slug,
        "created_at": now,
        "asset": args.asset,
        "windows": args.windows,
        "entry_family": args.entry_family,
        "exit_family": args.exit_family,
        "mode": args.mode,
    }
    (experiment_dir / "meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "experiment_dir": str(experiment_dir),
                "files": sorted(files.keys()) + ["meta.json"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
