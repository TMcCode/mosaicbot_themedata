"""Load global dataset registry and match LLM watchlist rows (free, local)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT = _ROOT / "config" / "datasets.json"

_TOKEN_RE = re.compile(r"[a-z0-9_./-]+", re.I)


def load_datasets(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT
    return json.loads(p.read_text(encoding="utf-8"))


def by_id(path: Path | None = None) -> dict[str, dict[str, Any]]:
    doc = load_datasets(path)
    out: dict[str, dict[str, Any]] = {}
    for row in doc.get("datasets") or []:
        did = row.get("id")
        if did:
            out[str(did)] = row
    return out


def series_to_dataset_id(series_key: str, path: Path | None = None) -> str | None:
    for row in load_datasets(path).get("datasets") or []:
        keys = row.get("series_keys") or []
        if series_key in keys:
            return str(row.get("id") or "") or None
        if row.get("id") == series_key and row.get("status") == "pull":
            return series_key
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(s or "") if len(t) > 1}


def _haystack(item: dict[str, Any]) -> str:
    parts = [
        item.get("provider_or_source") or "",
        item.get("dataset_or_product") or "",
        item.get("metric_or_field") or "",
        item.get("suggested_series_key") or "",
        item.get("venue") or "",
        item.get("question") or "",
        item.get("market_url_or_slug") or "",
        # KeyInputsAndSourcing rows
        item.get("input_name") or "",
        item.get("input_type") or "",
        item.get("commodity_code") or "",
        item.get("commodity_code_system") or "",
        item.get("source_or_comment") or "",
        item.get("sourcing_geography") or "",
    ]
    return _norm(" ".join(str(p) for p in parts))


def heuristic_access_cadence(item: dict[str, Any]) -> dict[str, str]:
    """Best-effort access/cadence when no registry row matches (still free)."""
    text = _haystack(item)
    access = ""
    cadence = _norm(item.get("cadence") or "")
    as_of = ""

    if any(k in text for k in ("fred", "bls", "qcew", "census", "bea", "eia", "usda", "usaspending", "manifold", "polymarket", "kalshi", "gpr")):
        access = "api"
        as_of = "period_end"
        if "manifold" in text or "polymarket" in text or "kalshi" in text:
            as_of = "vendor_as_of"
            if not cadence:
                cadence = "daily"
    elif any(k in text for k in ("aisi", "steel.org", "tradingeconomics", "trading economics", "scrape")):
        access = "scrape"
        as_of = "vendor_as_of"
        if not cadence:
            cadence = "weekly"
    elif any(
        k in text
        for k in (
            "placer",
            "revelio",
            "lightcast",
            "thinknum",
            "workforce.ai",
            "jane",
            "mordor",
            "marketsandmarkets",
            "argus",
            "smm",
        )
    ):
        access = "file"
        if not cadence:
            cadence = "monthly"

    if cadence in ("event_driven", "event"):
        cadence = "event"
    return {
        "access": access,
        "cadence": cadence,
        "as_of_semantics": as_of,
        "registry_match": "none",
    }


def _type_overlap_score(hay: str, label: str) -> tuple[float, str, float]:
    """Score how well a registry type label fits the haystack.

    Returns (points, reason_fragment, quality 0..1 for tie-breaks).
    """
    lab = _norm(label)
    if not lab or len(lab) < 4:
        return 0.0, "", 0.0
    if lab in hay:
        return 6.0, f"type:{lab}", 1.0
    lab_tokens = {t for t in _tokens(lab) if len(t) >= 4}
    if not lab_tokens:
        return 0.0, "", 0.0
    hit = lab_tokens & _tokens(hay)
    if not hit:
        return 0.0, "", 0.0
    quality = len(hit) / max(len(lab_tokens), 1)
    if len(hit) >= 2 or quality >= 0.5:
        pts = 3.0 + min(3.0, 3.0 * quality)
        return pts, f"type_tokens:{','.join(sorted(hit)[:3])}", quality
    return 0.0, "", quality


def _score_dataset(item: dict[str, Any], row: dict[str, Any]) -> tuple[float, str, float]:
    """Return (score, reason, type_quality). Higher score wins; type_quality breaks ties.

    Paid-map rows are distinguished mainly by detailed ``data_type`` (not only
    provider / canonical bucket). No editorial rank field.
    """
    hay = _haystack(item)
    if not hay:
        return 0.0, "", 0.0

    rid = _norm(str(row.get("id") or ""))
    keys = [_norm(str(k)) for k in (row.get("series_keys") or [])]
    source = _norm(str(row.get("source") or ""))
    fred = _norm(str(row.get("fred_id") or ""))
    hs6 = _norm(str(row.get("hs6") or row.get("hs_code") or ""))
    mid = _norm(str(row.get("manifold_id") or ""))
    provider = _norm(str(row.get("provider") or ""))
    aliases = _norm(str(row.get("aliases") or ""))
    desc = _norm(str(row.get("description") or ""))
    canonical = _norm(str(row.get("canonical_data_type") or ""))
    data_type = _norm(str(row.get("data_type") or ""))
    sector = _norm(str(row.get("sector") or ""))
    metric = _norm(
        item.get("metric_or_field") or item.get("commodity_code") or ""
    )
    product = _norm(
        item.get("dataset_or_product") or item.get("input_name") or ""
    )
    suggested = _norm(item.get("suggested_series_key") or item.get("commodity_code") or "")

    for cand in [rid, *keys]:
        if not cand:
            continue
        if cand == metric or cand == product or cand == suggested:
            return 20.0, f"exact:{cand}", 1.0
        if re.search(rf"(^|[^a-z0-9]){re.escape(cand)}([^a-z0-9]|$)", hay):
            return 16.0, f"id_in_text:{cand}", 1.0

    if fred and fred in hay.replace(" ", ""):
        return 18.0, f"fred:{fred}", 1.0
    if hs6 and hs6 in hay.replace(" ", ""):
        return 18.0, f"hs:{hs6}", 1.0
    if mid and mid.lower() in hay:
        return 18.0, f"manifold:{mid}", 1.0

    score = 0.0
    reasons: list[str] = []
    provider_hit = False

    if source and source in hay:
        score += 4.0
        reasons.append(f"source:{source}")
    if provider and provider in hay:
        score += 5.0
        provider_hit = True
        reasons.append(f"provider:{provider}")
    if aliases:
        for part in re.split(r"[;,/|]", aliases):
            p = _norm(part)
            if len(p) >= 4 and p in hay:
                score += 4.0
                provider_hit = True
                reasons.append(f"alias:{p}")
                break

    # Detailed data_type is the main paid-map differentiator
    dt_pts, dt_reason, type_quality = _type_overlap_score(hay, data_type)
    if dt_pts:
        score += dt_pts
        reasons.append(dt_reason)

    # Canonical = soft bucket boost (only with provider or data_type signal)
    can_pts, can_reason, can_q = _type_overlap_score(hay, canonical)
    if can_pts and (provider_hit or dt_pts):
        score += min(2.5, can_pts * 0.4)
        reasons.append(f"canonical:{can_reason.split(':', 1)[-1]}")
        type_quality = max(type_quality, can_q * 0.5)

    # Sector = soft context (only with provider or data_type)
    if sector and len(sector) >= 4 and sector in hay and (provider_hit or dt_pts):
        score += 1.5
        reasons.append(f"sector:{sector}")

    # Light description/id token overlap (capped; don't swamp data_type)
    blob = " ".join([rid, " ".join(keys), desc])
    overlap = {t for t in (_tokens(hay) & _tokens(blob)) if len(t) >= 4}
    if overlap:
        score += min(4.0, 1.0 * len(overlap))
        reasons.append(f"tokens:{','.join(sorted(overlap)[:3])}")

    # Provider alone without type signal stays below high-confidence unless strong ids
    if provider_hit and not dt_pts and score < 10.0:
        # keep score; may still medium-miss
        pass

    if score >= 6.0:
        return score, reasons[0] if reasons else "overlap", type_quality
    return 0.0, "", 0.0


def match_dataset(
    item: dict[str, Any],
    *,
    datasets: list[dict[str, Any]] | None = None,
    path: Path | None = None,
) -> dict[str, str]:
    """
    Match a watchlist / PM item to datasets.json.

    Preference order among close scores: higher detailed ``data_type`` overlap,
    then score. No editorial rank.

    Returns enrichment fields:
      dataset_id, access, cadence, as_of_semantics, registry_match, registry_status, match_reason
    """
    rows = datasets if datasets is not None else list(load_datasets(path).get("datasets") or [])
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_type_q = -1.0
    best_reason = ""
    for row in rows:
        score, reason, type_q = _score_dataset(item, row)
        if score <= 0:
            continue
        # Prefer higher score; tie-break on data_type quality
        better = score > best_score or (
            abs(score - best_score) < 1e-6 and type_q > best_type_q
        )
        if better:
            best_score = score
            best_type_q = type_q
            best = row
            best_reason = reason

    if best and best_score >= 10.0:
        conf = "high" if best_score >= 14.0 else "medium"
        return {
            "dataset_id": str(best.get("id") or ""),
            "access": str(best.get("access") or ""),
            "cadence": str(best.get("cadence") or item.get("cadence") or ""),
            "as_of_semantics": str(best.get("as_of_semantics") or "period_end"),
            "registry_match": conf,
            "registry_status": str(best.get("status") or ""),
            "match_reason": best_reason,
        }

    heur = heuristic_access_cadence(item)
    return {
        "dataset_id": "",
        "access": heur.get("access") or "",
        "cadence": heur.get("cadence") or str(item.get("cadence") or ""),
        "as_of_semantics": heur.get("as_of_semantics") or "",
        "registry_match": "none",
        "registry_status": "",
        "match_reason": "",
    }


def enrich_watchlist_item(
    item: dict[str, Any],
    *,
    datasets: list[dict[str, Any]] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    out = dict(item)
    enrich = match_dataset(out, datasets=datasets, path=path)
    # Registry cadence/access win on match; else keep LLM cadence if present
    for k, v in enrich.items():
        if not v:
            continue
        if k == "cadence" and out.get("cadence") and enrich.get("registry_match") == "none":
            continue
        out[k] = v
    return out


def enrich_watchlist_items(
    items: list[dict[str, Any]],
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    rows = list(load_datasets(path).get("datasets") or [])
    return [enrich_watchlist_item(it, datasets=rows, path=path) for it in items]
