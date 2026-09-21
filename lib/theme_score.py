"""Theme backdrop scores from pack series + config/theme_scores.json."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


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


def _metric_points(series: dict[str, Any], metric: str) -> list[dict[str, Any]]:
    if metric in ("level", "raw", "value"):
        return list((series.get("raw") or {}).get("points") or [])
    derived = series.get("derived") or {}
    meta = derived.get(metric) or {}
    return list(meta.get("points") or [])


def _metric_latest(series: dict[str, Any], metric: str) -> dict[str, Any] | None:
    if metric in ("level", "raw", "value"):
        return (series.get("raw") or {}).get("latest")
    derived = series.get("derived") or {}
    meta = derived.get(metric)
    if not meta:
        return None
    return meta.get("latest")


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
) -> list[dict[str, float]]:
    """Monthly backdrop score time series using the same weights as the latest score.

    At each date t, each enabled component contributes its last known YoY on/before t
    (so quarterly series carry forward). A point is emitted only when available
    components cover at least `min_weight_frac` of total enabled weight.
    """
    signal_cfg = signal_cfg or {}
    clip = float(signal_cfg.get("yoy_clip_pct") or 10.0)
    pack_series = pack_payload.get("series") or {}

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
        rows = _asof_lookup(_metric_points(ser, metric))
        if not rows:
            continue
        components.append(
            {"key": key, "weight": weight, "polarity": polarity, "rows": rows}
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
            sig = yoy_to_signal(yoy, clip_pct=clip)
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


def score_theme(
    theme_cfg: dict[str, Any],
    *,
    pack_payload: dict[str, Any],
    signal_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute one theme backdrop score from a pack payload's series block."""
    signal_cfg = signal_cfg or {}
    clip = float(signal_cfg.get("yoy_clip_pct") or 10.0)
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

        latest = _metric_latest(ser, metric)
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
                "status": "ok",
            }
        )

    if weight_sum <= 0:
        score = None
        normalized = None
    else:
        normalized = weighted_sum / weight_sum
        score = round(50.0 + 50.0 * normalized, 1)

    history = score_history(
        theme_cfg, pack_payload=pack_payload, signal_cfg=signal_cfg
    )

    return {
        "schema_version": 0,
        "score_id": theme_cfg.get("score_id"),
        "label": theme_cfg.get("label"),
        "theme": theme_cfg.get("theme"),
        "theme_slug": _slugify(str(theme_cfg.get("theme") or "")),
        "pack": theme_cfg.get("pack") or pack_payload.get("pack"),
        "score": score,
        "normalized": None if normalized is None else round(normalized, 4),
        "as_of": as_of or pack_payload.get("as_of"),
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "signal": {
            "method": signal_cfg.get("method") or "clip_linear",
            "yoy_clip_pct": clip,
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
    """Score every enabled theme recipe that points at this pack."""
    pack = pack_payload.get("pack")
    signal_cfg = scores_cfg.get("signal") or {}
    out: list[dict[str, Any]] = []
    for theme_cfg in scores_cfg.get("themes") or []:
        if theme_cfg.get("status") == "disabled":
            continue
        if theme_cfg.get("pack") and theme_cfg.get("pack") != pack:
            continue
        out.append(
            score_theme(theme_cfg, pack_payload=pack_payload, signal_cfg=signal_cfg)
        )
    return out


def scores_index(theme_scores: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 0,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "themes": [
            {
                "score_id": s.get("score_id"),
                "theme": s.get("theme"),
                "theme_slug": s.get("theme_slug"),
                "label": s.get("label"),
                "pack": s.get("pack"),
                "score": s.get("score"),
                "as_of": s.get("as_of"),
                "history_points": len(s.get("history") or []),
                "path": f"themes/{s.get('theme_slug')}.json",
            }
            for s in theme_scores
        ],
    }


def merge_scores_index(
    existing: dict[str, Any] | None,
    theme_scores: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace/insert scored themes by theme_slug; keep other themes intact."""
    by_slug: dict[str, dict[str, Any]] = {}
    if existing and isinstance(existing.get("themes"), list):
        for row in existing["themes"]:
            if isinstance(row, dict) and row.get("theme_slug"):
                by_slug[str(row["theme_slug"])] = row
    fresh = scores_index(theme_scores)
    for row in fresh["themes"]:
        by_slug[str(row["theme_slug"])] = row
    return {
        "schema_version": 0,
        "computed_at": fresh["computed_at"],
        "themes": sorted(by_slug.values(), key=lambda r: str(r.get("theme") or "")),
    }
