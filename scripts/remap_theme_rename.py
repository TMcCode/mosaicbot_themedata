#!/usr/bin/env python3
"""Remap themedata after a stockthemes theme rename.

Examples:
  python scripts/remap_theme_rename.py \\
    --old "Cybersecurity '26: Vulnerability, Exposure & Attack Surface Management" \\
    --new "Cybersecurity '26: Exposure Management"

  # Batch: any Theme_Metadata previous_name still present in config/themes.json
  python scripts/remap_theme_rename.py --from-metadata

  python scripts/remap_theme_rename.py --from-metadata --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.theme_rename import remap_theme_rename  # noqa: E402
from lib.theme_score import slugify_theme  # noqa: E402


def _load_dotenv() -> None:
    from lib.env import load_env

    load_env()
    main_env = _ROOT.parent / "MosaicBotMain_Local_Dev" / ".env"
    if main_env.is_file():
        load_env(env_path=main_env)


def _stale_pairs_from_metadata() -> list[tuple[str, str, str, str]]:
    """Return (old_name, new_name, old_slug, new_slug) for themes.json still on previous_name."""
    themes_doc = json.loads((_ROOT / "config" / "themes.json").read_text(encoding="utf-8"))
    td_names = {str(t.get("theme") or "").strip() for t in themes_doc.get("themes") or []}
    td_by_name = {
        str(t.get("theme") or "").strip(): t for t in themes_doc.get("themes") or []
    }

    pairs: list[tuple[str, str, str, str]] = []

    # Prefer MosaicBot Theme_Metadata via storage if available.
    meta_rows = None
    try:
        sys.path.insert(0, str(_ROOT.parent / "MosaicBotMain_Local_Dev"))
        from storage_compat import storage  # type: ignore
        from utils.object_storage import mosaic_themes_bucket  # type: ignore
        import io
        import pandas as pd

        blob = (
            storage.Client()
            .bucket(mosaic_themes_bucket())
            .blob("text_tables/Theme_Metadata.parquet")
        )
        if blob.exists():
            meta_rows = pd.read_parquet(io.BytesIO(blob.download_as_bytes()))
    except Exception as exc:
        print(f"Theme_Metadata via R2 unavailable ({exc}); falling back to none", file=sys.stderr)

    if meta_rows is None:
        return pairs

    prev = meta_rows.dropna(subset=["previous_name"]).copy()
    prev["previous_name"] = prev["previous_name"].astype(str).str.strip()
    prev["theme"] = prev["theme"].astype(str).str.strip()
    prev = prev[(prev["previous_name"] != "") & (prev["previous_name"] != prev["theme"])]
    stale = prev[prev["previous_name"].isin(td_names)]

    for _, row in stale.iterrows():
        old_name = str(row["previous_name"]).strip()
        new_name = str(row["theme"]).strip()
        td = td_by_name.get(old_name) or {}
        old_slug = str(td.get("theme_slug") or slugify_theme(old_name)).strip()
        # Prefer former_slugs[0] if present and matches registry
        former_raw = row.get("former_slugs")
        if isinstance(former_raw, str) and former_raw.strip().startswith("["):
            try:
                former = json.loads(former_raw)
                if isinstance(former, list) and former:
                    cand = str(former[0] or "").strip()
                    if cand:
                        old_slug = cand
            except Exception:
                pass
        new_slug = slugify_theme(new_name)
        pairs.append((old_name, new_name, old_slug, new_slug))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", help="Previous theme display name")
    parser.add_argument("--new", help="Current theme display name")
    parser.add_argument("--old-slug", default=None)
    parser.add_argument("--new-slug", default=None)
    parser.add_argument(
        "--from-metadata",
        action="store_true",
        help="Remap every Theme_Metadata previous_name still present in themes.json",
    )
    parser.add_argument("--skip-r2", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    _load_dotenv()

    jobs: list[tuple[str, str, str | None, str | None]] = []
    if args.from_metadata:
        for old, new, oslug, nslug in _stale_pairs_from_metadata():
            jobs.append((old, new, oslug, nslug))
        if not jobs:
            print("No stale themedata themes found vs Theme_Metadata.")
            return 0
    else:
        if not args.old or not args.new:
            parser.error("--old/--new required unless --from-metadata")
        jobs.append((args.old, args.new, args.old_slug, args.new_slug))

    exit_code = 0
    for old, new, oslug, nslug in jobs:
        print(f"=== {old!r} → {new!r}")
        print(f"    slug {oslug or slugify_theme(old)} → {nslug or slugify_theme(new)}")
        if args.dry_run:
            continue
        try:
            summary = remap_theme_rename(
                old,
                new,
                old_slug=oslug,
                new_slug=nslug,
                skip_r2=args.skip_r2,
            )
            print(json.dumps(summary, indent=2, default=str))
            if not summary.get("touched"):
                print("  (no themedata rows matched)")
        except Exception as exc:
            exit_code = 1
            print(f"FAILED: {exc}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
