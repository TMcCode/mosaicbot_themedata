"""Theme backdrop scores from pack series + config/theme_scores.json.

Recompute whenever contributing pack series update (cheap: no source refetch).
Shared entry point: ``recompute_and_publish``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RECIPE = _ROOT / "config" / "theme_scores.json"
_DEFAULT_SCORES_OUT = _ROOT / "data" / "scores"
_DEFAULT_DATA = _ROOT / "data"
R2_SCORES_PREFIX = "themedata/scores"


def load_pack_latest(pack: str) -> dict[str, Any] | None:
    """Load a pack's latest.json from local data/, else R2."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "" for ch in pack)
    if not safe:
        return None
    local = _DEFAULT_DATA / safe / "latest.json"
    if local.exists():
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        from lib import r2 as r2lib

        remote = r2lib.get_json(f"themedata/{safe}/latest.json")
        if isinstance(remote, dict):
            return remote
    except Exception:
        pass
    return None


def theme_uses_pack(theme_cfg: dict[str, Any], pack: str) -> bool:
    """True if this theme's primary pack or any component source_pack matches."""
    if not pack:
        return True
    if theme_cfg.get("pack") == pack:
        return True
    for comp in theme_cfg.get("components") or []:
        if comp.get("source_pack") == pack:
            return True
    return False


def component_source_pack(
    theme_cfg: dict[str, Any], comp: dict[str, Any]
) -> str | None:
    return comp.get("source_pack") or theme_cfg.get("pack")


def enrich_payload_series_for_theme(
    theme_cfg: dict[str, Any],
    pack_payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge cross-pack series listed on the recipe into a scoring payload.

    Components may set ``source_pack`` when the series lives in another pack
    (e.g. Space score pulls ``us_defense_spending`` / ``gpr_index`` from
    dew_defense). No source refetch — local/R2 latest only.
    """
    series = dict(pack_payload.get("series") or {})
    needed: dict[str, list[str]] = {}
    for comp in theme_cfg.get("components") or []:
        if not comp.get("enabled", True):
            continue
        key = comp.get("series_key")
        if not key or key in series:
            continue
        src_pack = component_source_pack(theme_cfg, comp)
        if not src_pack:
            continue
        needed.setdefault(str(src_pack), []).append(str(key))

    for src_pack, keys in needed.items():
        other = load_pack_latest(src_pack)
        if not other:
            continue
        other_series = other.get("series") or {}
        for key in keys:
            if key in other_series:
                series[key] = other_series[key]

    if series is pack_payload.get("series"):
        return pack_payload
    out = dict(pack_payload)
    out["series"] = series
    return out


def slugify_theme(name: str) -> str:
    """Match mosaicbot_themedata_web `slugify` (apostrophes stripped; & → separator)."""
    s = name.lower().replace("'", "").replace("'", "").replace("'", "")
    out: list[str] = []
    prev_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _slugify(name: str) -> str:
    return slugify_theme(name)


def yoy_to_signal(yoy_pct: float, *, clip_pct: float = 10.0) -> float:
    """Map YoY % into [-1, +1] by clipping at ±clip_pct."""
    if clip_pct <= 0:
        return 0.0
    return max(-1.0, min(1.0, yoy_pct / clip_pct))


def monthly_score_as_of_cap(now: datetime | None = None) -> str:
    """Last complete calendar month (period_end label YYYY-MM-01).

    Drops the current (incomplete) month and any future USASpending prints so
    scores are not yanked to the rails by partial MTD obligations.
    """
    from lib.as_of import monthly_as_of_cap_iso

    return monthly_as_of_cap_iso(now)


def _merge_signal_cfg(
    global_cfg: dict[str, Any] | None,
    theme_cfg: dict[str, Any],
    comp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """global ← theme.signal ← component yoy_clip_pct."""
    out = dict(global_cfg or {})
    theme_sig = theme_cfg.get("signal")
    if isinstance(theme_sig, dict):
        out.update(theme_sig)
    if comp is not None and comp.get("yoy_clip_pct") is not None:
        out["yoy_clip_pct"] = comp["yoy_clip_pct"]
    return out


def _metric_points(series: dict[str, Any], metric: str) -> list[dict[str, Any]]:
    if metric in ("level", "raw", "value"):
        return list((series.get("raw") or {}).get("points") or [])
    derived = series.get("derived") or {}
    meta = derived.get(metric) or {}
    return list(meta.get("points") or [])


def _metric_latest(
    series: dict[str, Any],
    metric: str,
    *,
    max_as_of: str | None = None,
) -> dict[str, Any] | None:
    points = _metric_points(series, metric)
    if max_as_of:
        points = [p for p in points if str(p.get("date") or "") <= max_as_of]
    if not points:
        return None
    points = sorted(points, key=lambda p: str(p.get("date") or ""))
    return points[-1]


def _history_point_count(
    series: dict[str, Any],
    comp: dict[str, Any],
) -> int:
    """Points available for ``min_history_points`` gate (default: raw prints)."""
    basis = str(comp.get("min_history_basis") or "raw").lower()
    if basis == "metric":
        metric = str(comp.get("metric") or "m3_avg_yoy_pct")
        return len(_metric_points(series, metric))
    return len((series.get("raw") or {}).get("points") or [])


def _waiting_history(
    comp: dict[str, Any],
    series: dict[str, Any],
) -> tuple[bool, int, int | None]:
    """True when component has ``min_history_points`` and series is too short.

    Returns ``(waiting, have, need)``. Used so weekly DX spots can be wired
    into the recipe now and auto-join once ~1y of prints exist.
    """
    need_raw = comp.get("min_history_points")
    if need_raw is None:
        return False, _history_point_count(series, comp), None
    need = int(need_raw)
    have = _history_point_count(series, comp)
    return have < need, have, need


def _component_max_as_of(
    comp: dict[str, Any],
    theme_max_as_of: str | None,
    series: dict[str, Any] | None = None,
) -> str | None:
    """Per-component as_of cap. Daily/high-freq series can opt into latest print.

    Theme ``exclude_incomplete_month`` still applies to monthlies (BLS/FRED).
    Set component ``exclude_incomplete_month: false`` (or ``as_of: \"latest\"``)
    so the score chip matches the live chart for daily AMS / weekly scrapes.
    """
    if comp.get("as_of") == "latest" or comp.get("exclude_incomplete_month") is False:
        return None
    freq = str((series or {}).get("frequency") or "").lower()
    # Default: daily prints are complete enough to use latest (not month-end stub).
    if freq == "daily" and comp.get("exclude_incomplete_month") is not True:
        return None
    return theme_max_as_of


def _asof_lookup(points: list[dict[str, Any]]) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for p in points:
        d, v = p.get("date"), p.get("value")
        if d is None or v is None:
            continue
        rows.append((str(d), float(v)))
    rows.sort(key=lambda x: x[0])
    return rows


def _value_asof(rows: list[tuple[str, float]], date: str) -> float | None:
    """Last observation on or before date (forward-carry for mixed frequencies)."""
    hit: float | None = None
    for d, v in rows:
        if d > date:
            break
        hit = v
    return hit


def score_history(
    theme_cfg: dict[str, Any],
    *,
    pack_payload: dict[str, Any],
    signal_cfg: dict[str, Any] | None = None,
    min_weight_frac: float = 0.5,
    max_as_of: str | None = None,
) -> list[dict[str, float]]:
    """Monthly backdrop score time series using the same weights as the latest score.

    At each date t, each enabled component contributes its last known YoY on/before t
    (so quarterly series carry forward). A point is emitted only when available
    components cover at least `min_weight_frac` of total enabled weight.
    """
    signal_cfg = _merge_signal_cfg(signal_cfg, theme_cfg)
    pack_series = pack_payload.get("series") or {}
    if max_as_of is None and signal_cfg.get("exclude_incomplete_month", True):
        max_as_of = monthly_score_as_of_cap()

    components: list[dict[str, Any]] = []
    total_weight = 0.0
    all_dates: set[str] = set()

    for comp in theme_cfg.get("components") or []:
        if not comp.get("enabled", True):
            continue
        key = comp["series_key"]
        metric = comp.get("metric") or "m3_avg_yoy_pct"
        weight = float(comp.get("weight") or 0.0)
        polarity = float(comp.get("polarity") or 0.0)
        if weight <= 0 or polarity == 0:
            continue
        ser = pack_series.get(key)
        if not ser:
            continue
        waiting, _, _ = _waiting_history(comp, ser)
        if waiting:
            continue
        rows = _asof_lookup(_metric_points(ser, metric))
        comp_max = _component_max_as_of(comp, max_as_of, ser)
        if comp_max:
            rows = [(d, v) for d, v in rows if d <= comp_max]
        if not rows:
            continue
        clip = float(
            _merge_signal_cfg(signal_cfg, theme_cfg, comp).get("yoy_clip_pct") or 10.0
        )
        components.append(
            {
                "key": key,
                "weight": weight,
                "polarity": polarity,
                "rows": rows,
                "clip": clip,
            }
        )
        total_weight += weight
        for d, _ in rows:
            all_dates.add(d)

    if total_weight <= 0 or not all_dates:
        return []

    min_w = total_weight * min_weight_frac
    history: list[dict[str, float]] = []
    for date in sorted(all_dates):
        w_sum = 0.0
        weighted = 0.0
        for c in components:
            yoy = _value_asof(c["rows"], date)
            if yoy is None:
                continue
            sig = yoy_to_signal(yoy, clip_pct=float(c["clip"]))
            w_sum += c["weight"]
            weighted += c["weight"] * c["polarity"] * sig
        if w_sum < min_w:
            continue
        normalized = weighted / w_sum
        history.append(
            {
                "date": date,
                "value": round(50.0 + 50.0 * normalized, 2),
            }
        )
    return history


def smooth_score_history(
    history: list[dict[str, float]], *, window: int
) -> list[dict[str, float]]:
    """Trailing mean of monthly blended scores (presentation smooth, same recipe)."""
    if window <= 1 or len(history) < 2:
        return history
    out: list[dict[str, float]] = []
    for i in range(len(history)):
        chunk = history[max(0, i - window + 1) : i + 1]
        avg = sum(float(p["value"]) for p in chunk) / len(chunk)
        out.append({"date": history[i]["date"], "value": round(avg, 2)})
    return out


def score_theme(
    theme_cfg: dict[str, Any],
    *,
    pack_payload: dict[str, Any],
    signal_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute one theme backdrop score from a pack payload's series block."""
    signal_cfg = _merge_signal_cfg(signal_cfg, theme_cfg)
    theme_clip = float(signal_cfg.get("yoy_clip_pct") or 10.0)
    max_as_of = None
    if signal_cfg.get("exclude_incomplete_month", True):
        max_as_of = monthly_score_as_of_cap()
    pack_series = pack_payload.get("series") or {}
    components_out: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_sum = 0.0
    as_of = ""
    missing: list[str] = []

    for comp in theme_cfg.get("components") or []:
        if not comp.get("enabled", True):
            continue
        key = comp["series_key"]
        metric = comp.get("metric") or "m3_avg_yoy_pct"
        weight = float(comp.get("weight") or 0.0)
        polarity = float(comp.get("polarity") or 0.0)
        if weight <= 0 or polarity == 0:
            continue

        ser = pack_series.get(key)
        if not ser:
            missing.append(f"{key}:{metric}")
            components_out.append(
                {
                    "series_key": key,
                    "metric": metric,
                    "weight": weight,
                    "polarity": polarity,
                    "status": "missing_series",
                }
            )
            continue

        waiting, have_n, need_n = _waiting_history(comp, ser)
        if waiting:
            missing.append(f"{key}:{metric}")
            components_out.append(
                {
                    "series_key": key,
                    "metric": metric,
                    "weight": weight,
                    "polarity": polarity,
                    "status": "waiting_history",
                    "history_points": have_n,
                    "min_history_points": need_n,
                    "display_name": ser.get("display_name")
                    or ser.get("short_name")
                    or ser.get("label"),
                }
            )
            continue

        comp_max = _component_max_as_of(comp, max_as_of, ser)
        latest = _metric_latest(ser, metric, max_as_of=comp_max)
        if not latest or latest.get("value") is None:
            missing.append(f"{key}:{metric}")
            components_out.append(
                {
                    "series_key": key,
                    "metric": metric,
                    "weight": weight,
                    "polarity": polarity,
                    "status": "missing_metric",
                    "display_name": ser.get("display_name") or ser.get("label"),
                }
            )
            continue

        yoy = float(latest["value"])
        clip = float(
            _merge_signal_cfg(signal_cfg, theme_cfg, comp).get("yoy_clip_pct")
            or theme_clip
        )
        signal = yoy_to_signal(yoy, clip_pct=clip)
        contribution = weight * polarity * signal
        weighted_sum += contribution
        weight_sum += weight
        date = str(latest.get("date") or "")
        if date > as_of:
            as_of = date

        components_out.append(
            {
                "series_key": key,
                "display_name": ser.get("display_name")
                or ser.get("short_name")
                or ser.get("label"),
                "metric": metric,
                "weight": weight,
                "polarity": polarity,
                "yoy_pct": round(yoy, 4),
                "signal": round(signal, 4),
                "contribution": round(contribution, 4),
                "as_of": date,
                "yoy_clip_pct": clip,
                "status": "ok",
            }
        )

    if weight_sum <= 0:
        score = None
        normalized = None
    else:
        normalized = weighted_sum / weight_sum
        score = round(50.0 + 50.0 * normalized, 1)

    history_raw = score_history(
        theme_cfg,
        pack_payload=pack_payload,
        signal_cfg=signal_cfg,
        max_as_of=max_as_of,
    )
    smooth_n = int(signal_cfg.get("history_smooth_months") or 0)
    history = (
        smooth_score_history(history_raw, window=smooth_n)
        if smooth_n > 1
        else history_raw
    )
    # Chip tracks the chart endpoint when history is smoothed (regime, not spike).
    if smooth_n > 1 and history:
        score = round(float(history[-1]["value"]), 1)
        normalized = (score - 50.0) / 50.0
        as_of = str(history[-1].get("date") or as_of)

    return {
        "schema_version": 0,
        "score_id": theme_cfg.get("score_id"),
        "label": theme_cfg.get("label"),
        "theme": theme_cfg.get("theme"),
        "theme_slug": str(
            theme_cfg.get("theme_slug")
            or _slugify(str(theme_cfg.get("theme") or ""))
        ),
        "pack": theme_cfg.get("pack") or pack_payload.get("pack"),
        "score": score,
        "normalized": None if normalized is None else round(normalized, 4),
        "as_of": as_of or pack_payload.get("as_of"),
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "signal": {
            "method": signal_cfg.get("method") or "clip_linear",
            "yoy_clip_pct": theme_clip,
            "exclude_incomplete_month": bool(
                signal_cfg.get("exclude_incomplete_month", True)
            ),
            "max_as_of": max_as_of,
            "history_smooth_months": smooth_n if smooth_n > 1 else None,
        },
        "components": components_out,
        "missing": missing,
        "history": history,
        "notes": theme_cfg.get("notes"),
    }


def score_themes_for_pack(
    scores_cfg: dict[str, Any],
    *,
    pack_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Score every enabled theme recipe that this pack contributes to.

    Includes themes whose primary ``pack`` matches **or** any component
    ``source_pack`` matches (multi-pack recipes). Cross-pack series are merged
    from local/R2 latest before scoring.
    """
    pack = pack_payload.get("pack")
    signal_cfg = scores_cfg.get("signal") or {}
    out: list[dict[str, Any]] = []
    for theme_cfg in scores_cfg.get("themes") or []:
        if theme_cfg.get("status") == "disabled":
            continue
        if not theme_uses_pack(theme_cfg, str(pack or "")):
            continue
        enriched = enrich_payload_series_for_theme(theme_cfg, pack_payload)
        out.append(
            score_theme(theme_cfg, pack_payload=enriched, signal_cfg=signal_cfg)
        )
    return out


def index_row_from_theme_score(s: dict[str, Any]) -> dict[str, Any]:
    """Slim index row derived from a full theme score payload."""
    slug = s.get("theme_slug")
    return {
        "score_id": s.get("score_id"),
        "theme": s.get("theme"),
        "theme_slug": slug,
        "label": s.get("label"),
        "pack": s.get("pack"),
        "score": s.get("score"),
        "as_of": s.get("as_of"),
        "history_points": len(s.get("history") or []),
        "path": f"themes/{slug}.json",
    }


def scores_index(theme_scores: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 0,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "themes": [index_row_from_theme_score(s) for s in theme_scores],
    }


def merge_scores_index(
    existing: dict[str, Any] | None,
    theme_scores: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace/insert scored themes by theme_slug; keep other themes intact.

    Only rows present in ``theme_scores`` are updated. Callers must upload the
    matching theme detail files in the same publish — never put a new score in
    the index without writing/uploading that theme payload.
    """
    by_slug: dict[str, dict[str, Any]] = {}
    if existing and isinstance(existing.get("themes"), list):
        for row in existing["themes"]:
            if isinstance(row, dict) and row.get("theme_slug"):
                by_slug[str(row["theme_slug"])] = dict(row)
    fresh = scores_index(theme_scores)
    for row in fresh["themes"]:
        by_slug[str(row["theme_slug"])] = row
    return {
        "schema_version": 0,
        "computed_at": fresh["computed_at"],
        "themes": sorted(by_slug.values(), key=lambda r: str(r.get("theme") or "")),
    }


def build_scores_index_from_theme_files(
    themes_dir: Path,
    *,
    computed_at: str | None = None,
) -> dict[str, Any]:
    """Rebuild ``latest.json`` exclusively from on-disk theme payloads.

    Theme detail files are the source of truth; the index is a projection.
    """
    themes: list[dict[str, Any]] = []
    if themes_dir.is_dir():
        for path in sorted(themes_dir.glob("*.json")):
            try:
                detail = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(detail, dict):
                continue
            if not detail.get("theme_slug"):
                detail = {**detail, "theme_slug": path.stem}
            themes.append(index_row_from_theme_score(detail))
    return {
        "schema_version": 0,
        "computed_at": computed_at
        or datetime.now(timezone.utc).isoformat(),
        "themes": sorted(themes, key=lambda r: str(r.get("theme") or "")),
    }


def assert_index_matches_theme_scores(
    index: dict[str, Any],
    theme_scores: list[dict[str, Any]],
) -> None:
    """Raise if any just-scored theme's index row disagrees with its payload."""
    by_slug = {
        str(r.get("theme_slug")): r
        for r in (index.get("themes") or [])
        if isinstance(r, dict) and r.get("theme_slug")
    }
    for s in theme_scores:
        slug = str(s.get("theme_slug") or "")
        row = by_slug.get(slug)
        if row is None:
            raise AssertionError(f"scores index missing theme_slug={slug!r}")
        if row.get("score") != s.get("score") or row.get("as_of") != s.get("as_of"):
            raise AssertionError(
                f"scores index drifted for {slug!r}: "
                f"index score={row.get('score')} as_of={row.get('as_of')} "
                f"vs detail score={s.get('score')} as_of={s.get('as_of')}"
            )


def load_recipe(*, prefer_remote: bool = True) -> dict[str, Any] | None:
    """Load score recipe: R2 ``themedata/scores/recipe.json`` or local seed."""
    if prefer_remote:
        try:
            from lib import r2 as r2lib

            remote = r2lib.get_json(f"{R2_SCORES_PREFIX}/recipe.json")
            if isinstance(remote, dict) and isinstance(remote.get("themes"), list):
                return remote
        except Exception:
            pass
    if _DEFAULT_RECIPE.exists():
        return json.loads(_DEFAULT_RECIPE.read_text(encoding="utf-8"))
    return None


def write_theme_score_files(
    theme_scores: list[dict[str, Any]],
    *,
    scores_dir: Path | None = None,
) -> Path:
    """Write full theme score payloads under ``scores/themes/<slug>.json``."""
    scores_dir = scores_dir or _DEFAULT_SCORES_OUT
    themes_dir = scores_dir / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    for s in theme_scores:
        slug = s.get("theme_slug") or "unknown"
        (themes_dir / f"{slug}.json").write_text(
            json.dumps(s, indent=2) + "\n", encoding="utf-8"
        )
    return themes_dir


def write_scores_local(
    theme_scores: list[dict[str, Any]],
    index: dict[str, Any] | None = None,
    *,
    scores_dir: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Write theme payloads, then rebuild local ``latest.json`` from those files.

    ``index`` is ignored for the on-disk index (kept for call-compat). Local
    latest is always a projection of ``themes/*.json`` so list UIs cannot drift
    from theme-page chips.
    """
    scores_dir = scores_dir or _DEFAULT_SCORES_OUT
    scores_dir.mkdir(parents=True, exist_ok=True)
    write_theme_score_files(theme_scores, scores_dir=scores_dir)
    local_index = build_scores_index_from_theme_files(scores_dir / "themes")
    (scores_dir / "latest.json").write_text(
        json.dumps(local_index, indent=2) + "\n", encoding="utf-8"
    )
    return scores_dir, local_index


def publish_scores_r2(
    theme_scores: list[dict[str, Any]],
    index: dict[str, Any],
    *,
    recipe: dict[str, Any] | None = None,
) -> list[str]:
    """Publish index + the theme payloads that justify the updated index rows.

    Invariant: every theme_score in this call is uploaded with the index. Rows
    for other themes are left as they were in ``index`` (typically remote merge)
    and must already match previously uploaded detail files.
    """
    from lib import r2 as r2lib

    assert_index_matches_theme_scores(index, theme_scores)
    uris = [r2lib.put_json(f"{R2_SCORES_PREFIX}/latest.json", index)]
    for s in theme_scores:
        slug = s.get("theme_slug") or "unknown"
        uris.append(r2lib.put_json(f"{R2_SCORES_PREFIX}/themes/{slug}.json", s))
    if recipe is not None:
        uris.append(r2lib.put_json(f"{R2_SCORES_PREFIX}/recipe.json", recipe))
    return uris


def recompute_and_publish(
    pack_payload: dict[str, Any],
    *,
    scores_cfg: dict[str, Any] | None = None,
    scores_dir: Path | None = None,
    skip_r2: bool = False,
    sync_recipe: bool = False,
    prefer_remote_recipe: bool = True,
) -> dict[str, Any]:
    """Recompute backdrop scores for themes tied to this pack; write local + R2.

    Call at the end of any job that updates pack series (full pack or partial
    refresh). No source refetch — uses ``pack_payload["series"]`` plus any
    cross-pack series loaded via component ``source_pack``.

    Consistency:
    - Theme detail JSON is the source of truth.
    - Local ``latest.json`` is rebuilt from all on-disk theme files.
    - R2 ``latest.json`` only updates rows for themes scored in this run, and
      those detail files are uploaded in the same publish (no stale-local
      poison of remote rows for other themes).

    Returns ``{theme_scores, index, local_index, r2_uris, recipe}`` (theme_scores
    empty if no recipe matches this pack). ``index`` is the R2 publish index.
    """
    scores_dir = scores_dir or _DEFAULT_SCORES_OUT
    recipe = scores_cfg
    if recipe is None:
        recipe = load_recipe(prefer_remote=prefer_remote_recipe)
    if recipe is None:
        return {
            "theme_scores": [],
            "index": None,
            "local_index": None,
            "r2_uris": [],
            "recipe": None,
        }

    theme_scores = score_themes_for_pack(recipe, pack_payload=pack_payload)
    if not theme_scores:
        return {
            "theme_scores": [],
            "index": None,
            "local_index": None,
            "r2_uris": [],
            "recipe": recipe,
        }

    remote_existing = None
    try:
        from lib import r2 as r2lib

        remote_existing = r2lib.get_json(f"{R2_SCORES_PREFIX}/latest.json")
    except Exception:
        remote_existing = None

    # Local disk: write this pack's payloads, rebuild index from all theme files.
    _, local_index = write_scores_local(
        theme_scores, scores_dir=scores_dir
    )

    # R2 publish index: merge remote (preferred) with only this run's scores so
    # a machine with stale local files for other themes cannot overwrite them.
    if isinstance(remote_existing, dict):
        publish_index = merge_scores_index(remote_existing, theme_scores)
    else:
        publish_index = local_index
    assert_index_matches_theme_scores(publish_index, theme_scores)

    # Attach slim summary on pack payload for callers
    pack_payload["theme_scores"] = [
        {
            "score_id": s.get("score_id"),
            "theme": s.get("theme"),
            "score": s.get("score"),
            "as_of": s.get("as_of"),
        }
        for s in theme_scores
    ]

    r2_uris: list[str] = []
    if not skip_r2:
        recipe_to_put = None
        if sync_recipe:
            # Prefer the seed file when syncing so editorial edits ship with scores
            if _DEFAULT_RECIPE.exists():
                recipe_to_put = json.loads(_DEFAULT_RECIPE.read_text(encoding="utf-8"))
            else:
                recipe_to_put = recipe
        r2_uris = publish_scores_r2(
            theme_scores, publish_index, recipe=recipe_to_put
        )

    return {
        "theme_scores": theme_scores,
        "index": publish_index,
        "local_index": local_index,
        "r2_uris": r2_uris,
        "recipe": recipe,
    }
