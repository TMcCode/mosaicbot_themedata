"""Remap themedata configs + published scores after a stockthemes theme rename.

Touches:
  config/themes.json
  config/series_attachments.json
  config/theme_scores.json
  config/default_boards.json
  data/scores/themes/<slug>.json (+ latest.json)
  R2: themedata/series_attachments.json, boards.json, scores/recipe.json,
      scores/latest.json, scores/themes/<slug>.json
  local data/<pack>/attachments.json (name/slug fields only)

Slug changes move the published score detail object; old key is deleted when possible.
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from lib.theme_score import (
    build_scores_index_from_theme_files,
    slugify_theme,
)

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "config"
_SCORES_DIR = _ROOT / "data" / "scores"

R2_ATTACHMENTS = "themedata/series_attachments.json"
R2_BOARDS = "themedata/boards.json"
R2_RECIPE = "themedata/scores/recipe.json"
R2_SCORES_LATEST = "themedata/scores/latest.json"
R2_SCORES_THEMES = "themedata/scores/themes"


def _group_base(theme_name: str) -> str:
    raw = str(theme_name or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    return re.sub(r"\s+'?\d{2}\s*$", "", raw).strip()


def _group_slug(theme_name: str) -> str:
    return slugify_theme(_group_base(theme_name))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed reading %s: %s", path, exc)
        return None


def _row_matches(row: dict[str, Any], *, old_name: str, old_slug: str) -> bool:
    theme = str(row.get("theme") or "").strip()
    slug = str(row.get("theme_slug") or "").strip()
    if old_slug and slug == old_slug:
        return True
    if old_name and theme == old_name:
        return True
    return False


def _apply_theme_fields(
    row: dict[str, Any],
    *,
    new_name: str,
    new_slug: str,
    new_group: str | None = None,
    new_group_slug: str | None = None,
    include_group: bool = False,
) -> bool:
    changed = False
    if str(row.get("theme") or "") != new_name:
        row["theme"] = new_name
        changed = True
    if new_slug and str(row.get("theme_slug") or "") != new_slug:
        row["theme_slug"] = new_slug
        changed = True
    if include_group:
        if new_group is not None and str(row.get("group") or "") != new_group:
            row["group"] = new_group
            changed = True
        if new_group_slug is not None and str(row.get("group_slug") or "") != new_group_slug:
            row["group_slug"] = new_group_slug
            changed = True
    return changed


def _remap_list(
    rows: list[Any],
    *,
    old_name: str,
    old_slug: str,
    new_name: str,
    new_slug: str,
    include_group: bool = False,
    new_group: str | None = None,
    new_group_slug: str | None = None,
) -> int:
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not _row_matches(row, old_name=old_name, old_slug=old_slug):
            continue
        if _apply_theme_fields(
            row,
            new_name=new_name,
            new_slug=new_slug,
            new_group=new_group,
            new_group_slug=new_group_slug,
            include_group=include_group,
        ):
            n += 1
    return n


def remap_local_configs(
    old_name: str,
    new_name: str,
    *,
    old_slug: str | None = None,
    new_slug: str | None = None,
) -> dict[str, Any]:
    """Update themedata config seeds. Returns a summary of counts."""
    old_s = str(old_name or "").strip()
    new_s = str(new_name or "").strip()
    if not old_s or not new_s or old_s == new_s:
        raise ValueError("old_name and new_name are required and must differ")

    old_slug_s = str(old_slug or slugify_theme(old_s)).strip()
    new_slug_s = str(new_slug or slugify_theme(new_s)).strip()
    new_group = _group_base(new_s)
    new_group_slug = _group_slug(new_s)

    summary: dict[str, Any] = {
        "old_name": old_s,
        "new_name": new_s,
        "old_slug": old_slug_s,
        "new_slug": new_slug_s,
        "new_group": new_group,
        "new_group_slug": new_group_slug,
        "themes_json": 0,
        "attachments": 0,
        "theme_scores": 0,
        "boards": 0,
        "pack_attachments_local": 0,
    }

    themes_path = _CONFIG / "themes.json"
    themes_doc = _load_json(themes_path)
    if isinstance(themes_doc, dict) and isinstance(themes_doc.get("themes"), list):
        summary["themes_json"] = _remap_list(
            themes_doc["themes"],
            old_name=old_s,
            old_slug=old_slug_s,
            new_name=new_s,
            new_slug=new_slug_s,
            include_group=True,
            new_group=new_group,
            new_group_slug=new_group_slug,
        )
        if summary["themes_json"]:
            _write_json(themes_path, themes_doc)

    att_path = _CONFIG / "series_attachments.json"
    att_doc = _load_json(att_path)
    if isinstance(att_doc, dict) and isinstance(att_doc.get("attachments"), list):
        summary["attachments"] = _remap_list(
            att_doc["attachments"],
            old_name=old_s,
            old_slug=old_slug_s,
            new_name=new_s,
            new_slug=new_slug_s,
        )
        if summary["attachments"]:
            _write_json(att_path, att_doc)

    scores_path = _CONFIG / "theme_scores.json"
    scores_doc = _load_json(scores_path)
    if isinstance(scores_doc, dict):
        n = 0
        for key in ("themes", "scores"):
            if isinstance(scores_doc.get(key), list):
                n += _remap_list(
                    scores_doc[key],
                    old_name=old_s,
                    old_slug=old_slug_s,
                    new_name=new_s,
                    new_slug=new_slug_s,
                )
        summary["theme_scores"] = n
        if n:
            _write_json(scores_path, scores_doc)

    boards_path = _CONFIG / "default_boards.json"
    boards_doc = _load_json(boards_path)
    if isinstance(boards_doc, dict) and isinstance(boards_doc.get("boards"), list):
        summary["boards"] = _remap_list(
            boards_doc["boards"],
            old_name=old_s,
            old_slug=old_slug_s,
            new_name=new_s,
            new_slug=new_slug_s,
        )
        if summary["boards"]:
            _write_json(boards_path, boards_doc)

    # Pack-local attachment mirrors (subset docs).
    data_root = _ROOT / "data"
    if data_root.is_dir():
        pack_hits = 0
        for path in sorted(data_root.glob("*/attachments.json")):
            doc = _load_json(path)
            if not isinstance(doc, dict):
                continue
            rows = doc.get("attachments")
            if not isinstance(rows, list):
                continue
            n = _remap_list(
                rows,
                old_name=old_s,
                old_slug=old_slug_s,
                new_name=new_s,
                new_slug=new_slug_s,
            )
            if n:
                _write_json(path, doc)
                pack_hits += n
        summary["pack_attachments_local"] = pack_hits

    return summary


def remap_published_scores(
    old_name: str,
    new_name: str,
    *,
    old_slug: str | None = None,
    new_slug: str | None = None,
    skip_r2: bool = False,
) -> dict[str, Any]:
    """Rename local + R2 score detail/index for one theme."""
    old_s = str(old_name or "").strip()
    new_s = str(new_name or "").strip()
    old_slug_s = str(old_slug or slugify_theme(old_s)).strip()
    new_slug_s = str(new_slug or slugify_theme(new_s)).strip()

    themes_dir = _SCORES_DIR / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    old_path = themes_dir / f"{old_slug_s}.json"
    new_path = themes_dir / f"{new_slug_s}.json"

    detail: dict[str, Any] | None = None
    source = None
    if old_path.is_file():
        raw = _load_json(old_path)
        if isinstance(raw, dict):
            detail = raw
            source = "local_old"
    if detail is None and new_path.is_file() and new_slug_s != old_slug_s:
        raw = _load_json(new_path)
        if isinstance(raw, dict):
            detail = raw
            source = "local_new"
    if detail is None and not skip_r2:
        from lib import r2 as r2lib

        remote = r2lib.get_json(f"{R2_SCORES_THEMES}/{old_slug_s}.json")
        if isinstance(remote, dict):
            detail = remote
            source = "r2_old"
        elif new_slug_s != old_slug_s:
            remote = r2lib.get_json(f"{R2_SCORES_THEMES}/{new_slug_s}.json")
            if isinstance(remote, dict):
                detail = remote
                source = "r2_new"

    summary: dict[str, Any] = {
        "old_slug": old_slug_s,
        "new_slug": new_slug_s,
        "detail_source": source,
        "local_written": False,
        "local_deleted_old": False,
        "r2_written": False,
        "r2_deleted_old": False,
        "index_rebuilt": False,
        "skipped": False,
    }

    if detail is None:
        summary["skipped"] = True
        summary["reason"] = "no score detail found"
        return summary

    detail = deepcopy(detail)
    detail["theme"] = new_s
    detail["theme_slug"] = new_slug_s
    _write_json(new_path, detail)
    summary["local_written"] = True
    if old_slug_s != new_slug_s and old_path.is_file() and old_path.resolve() != new_path.resolve():
        old_path.unlink()
        summary["local_deleted_old"] = True

    index = build_scores_index_from_theme_files(themes_dir)
    _write_json(_SCORES_DIR / "latest.json", index)
    summary["index_rebuilt"] = True

    if skip_r2:
        return summary

    from lib import r2 as r2lib

    r2lib.put_json(f"{R2_SCORES_THEMES}/{new_slug_s}.json", detail)
    r2lib.put_json(R2_SCORES_LATEST, index)
    summary["r2_written"] = True

    if old_slug_s != new_slug_s:
        try:
            client, bucket = r2lib._client()
            client.delete_object(Bucket=bucket, Key=f"{R2_SCORES_THEMES}/{old_slug_s}.json")
            summary["r2_deleted_old"] = True
        except Exception as exc:
            logger.warning("Could not delete old R2 score %s: %s", old_slug_s, exc)
            summary["r2_delete_error"] = str(exc)

    return summary


def publish_editorial_mirrors(*, skip_r2: bool = False) -> dict[str, Any]:
    """Push config attachments/boards/recipe to R2 after local remap."""
    out: dict[str, Any] = {"attachments": False, "boards": False, "recipe": False}
    if skip_r2:
        return out

    from lib import r2 as r2lib

    att = _load_json(_CONFIG / "series_attachments.json")
    if isinstance(att, dict):
        r2lib.put_json(R2_ATTACHMENTS, att)
        out["attachments"] = True

    boards = _load_json(_CONFIG / "default_boards.json")
    if isinstance(boards, dict):
        # R2 canonical key is boards.json; seed file is default_boards.json
        r2lib.put_json(R2_BOARDS, boards)
        out["boards"] = True

    recipe = _load_json(_CONFIG / "theme_scores.json")
    if isinstance(recipe, dict):
        r2lib.put_json(R2_RECIPE, recipe)
        out["recipe"] = True

    return out


def remap_theme_rename(
    old_name: str,
    new_name: str,
    *,
    old_slug: str | None = None,
    new_slug: str | None = None,
    skip_r2: bool = False,
) -> dict[str, Any]:
    """Full themedata rename: configs + scores + R2 editorial mirrors."""
    cfg = remap_local_configs(
        old_name,
        new_name,
        old_slug=old_slug,
        new_slug=new_slug,
    )
    touched = (
        cfg["themes_json"]
        + cfg["attachments"]
        + cfg["theme_scores"]
        + cfg["boards"]
        + cfg["pack_attachments_local"]
    )
    scores = remap_published_scores(
        old_name,
        new_name,
        old_slug=cfg["old_slug"],
        new_slug=cfg["new_slug"],
        skip_r2=skip_r2,
    )
    mirrors: dict[str, Any] = {}
    if touched and not skip_r2:
        mirrors = publish_editorial_mirrors(skip_r2=skip_r2)
    elif not touched:
        mirrors = {"skipped": True, "reason": "no local config rows matched"}

    return {
        "config": cfg,
        "scores": scores,
        "r2_mirrors": mirrors,
        "touched": bool(touched) or not scores.get("skipped"),
    }
