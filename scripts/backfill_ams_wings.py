#!/usr/bin/env python3
"""Optional one-shot bootstrap of AMS whole-wing weeks from meatread.

Normal tracking is append-only via the weekly pack scrape of ams_3646.pdf.
Use this only if history is empty / short.

Usage:
  python scripts/backfill_ams_wings.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import usda_ams_chicken as ams  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "restaurants_chicken")
    args = ap.parse_args()
    n = ams.bootstrap_meatread(args.out)
    snap, inserted = ams.scrape_and_upsert(args.out)
    pts = ams.points_from_history(args.out)
    print(
        {
            "meatread_new": n,
            "live": snap.get("week_ending"),
            "live_inserted": inserted,
            "weeks": len(pts),
            "from": pts[0]["date"] if pts else None,
            "to": pts[-1]["date"] if pts else None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
