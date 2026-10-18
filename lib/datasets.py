"""Load global dataset registry and match LLM watchlist rows (free, local)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT = _ROOT / "config" / "datasets.json"
_CANDIDATES_DEFAULT = _ROOT / "config" / "dataset_candidates.json"
_TE_SYMBOLS_DEFAULT = _ROOT / "config" / "te_symbols.json"

_TOKEN_RE = re.compile(r"[a-z0-9_./-]+", re.I)
_TE_PROVIDER_RE = re.compile(
    r"trading\s*economics|tradingeconomics|\bte\.com\b",
    re.I,
)
_te_symbology_cache: dict[str, Any] | None = None


def load_datasets(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT
    return json.loads(p.read_text(encoding="utf-8"))


def candidates_path(datasets_path: Path | None = None) -> Path:
    """Candidates file next to datasets.json (or default config path)."""
    if datasets_path is not None:
        return datasets_path.parent / "dataset_candidates.json"
    return _CANDIDATES_DEFAULT


def load_candidates(path: Path | None = None, *, datasets_path: Path | None = None) -> dict[str, Any]:
    p = path or candidates_path(datasets_path)
    if not p.is_file():
        return {"schema_version": 0, "candidates": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_candidates(doc: dict[str, Any], path: Path | None = None, *, datasets_path: Path | None = None) -> Path:
    p = path or candidates_path(datasets_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return p


def by_id(path: Path | None = None) -> dict[str, dict[str, Any]]:
    doc = load_datasets(path)
    out: dict[str, dict[str, Any]] = {}
    for row in doc.get("datasets") or []:
        did = row.get("id")
        if did:
            out[str(did)] = row
    return out


def _candidate_as_dataset_row(cand: dict[str, Any]) -> dict[str, Any]:
    """Shape a candidate like a datasets.json row for ``_score_dataset``."""
    return {
        "id": cand.get("id") or "",
        "status": "candidate",
        "access": cand.get("access_guess") or "",
        "cadence": cand.get("cadence") or "",
        "source": _norm(str(cand.get("provider") or "")).replace(" ", "_")[:40],
        "provider": cand.get("provider") or "",
        "aliases": cand.get("aliases") or "",
        "data_type": cand.get("data_type")
        or cand.get("metric_or_field")
        or "",
        "description": " ".join(
            str(x)
            for x in (
                cand.get("dataset_or_product"),
                cand.get("metric_or_field"),
                cand.get("notes"),
            )
            if x
        ),
        "series_keys": [],
        "as_of_semantics": cand.get("as_of_semantics") or "",
    }


def _candidate_fingerprint(item: dict[str, Any]) -> str:
    prov = _norm(str(item.get("provider_or_source") or item.get("provider") or ""))
    metric = _norm(
        str(
            item.get("metric_or_field")
            or item.get("dataset_or_product")
            or item.get("input_name")
            or ""
        )
    )[:120]
    return f"{prov}|{metric}"


def _slug_candidate_id(item: dict[str, Any]) -> str:
    prov = _norm(str(item.get("provider_or_source") or "unknown"))
    slug = re.sub(r"[^a-z0-9]+", "_", prov).strip("_")[:36] or "unknown"
    digest = hashlib.sha1(_candidate_fingerprint(item).encode("utf-8")).hexdigest()[:8]
    return f"cand_{slug}_{digest}"


def _upsert_candidate_into_doc(
    doc: dict[str, Any],
    item: dict[str, Any],
    *,
    theme: str | None = None,
) -> dict[str, Any] | None:
    """Merge one unmatched watchlist hit into an in-memory candidates doc (no disk I/O)."""
    prov = str(item.get("provider_or_source") or "").strip()
    if not prov:
        return None
    cands: list[dict[str, Any]] = list(doc.get("candidates") or [])
    doc["candidates"] = cands
    fp = _candidate_fingerprint(item)
    now = datetime.now(timezone.utc).isoformat()
    existing = None
    prov_n = _norm(prov)
    for row in cands:
        row_fp = row.get("fingerprint") or _candidate_fingerprint(
            {
                "provider_or_source": row.get("provider"),
                "metric_or_field": row.get("metric_or_field"),
                "dataset_or_product": row.get("dataset_or_product"),
            }
        )
        same = row_fp == fp or _norm(str(row.get("provider") or "")) == prov_n
        if not same:
            continue
        # Keep editorial rejections — do not re-open or duplicate
        if row.get("status") == "rejected":
            row["last_seen_at"] = now
            row["seen_count"] = int(row.get("seen_count") or 1) + 1
            return row
        existing = row
        break
    if existing is not None:
        existing["seen_count"] = int(existing.get("seen_count") or 1) + 1
        existing["last_seen_at"] = now
        if theme:
            seen = list(existing.get("themes_seen") or [])
            if theme not in seen:
                seen.append(theme)
            existing["themes_seen"] = seen
        if not existing.get("metric_or_field") and item.get("metric_or_field"):
            existing["metric_or_field"] = item.get("metric_or_field")
        return existing

    row = {
        "id": _slug_candidate_id(item),
        "status": "open",
        "fingerprint": fp,
        "provider": prov,
        "aliases": "",
        "dataset_or_product": str(item.get("dataset_or_product") or ""),
        "metric_or_field": str(item.get("metric_or_field") or ""),
        "data_type": str(item.get("metric_or_field") or item.get("dataset_or_product") or ""),
        "cadence": str(item.get("cadence") or ""),
        "access_guess": str(item.get("access") or ""),
        "aliased_to": None,
        "first_seen_theme": theme or "",
        "themes_seen": [theme] if theme else [],
        "seen_count": 1,
        "first_seen_at": now,
        "last_seen_at": now,
        "notes": "Auto-captured from watchlist registry_match=none",
    }
    cands.append(row)
    return row


def upsert_candidate_from_item(
    item: dict[str, Any],
    *,
    theme: str | None = None,
    datasets_path: Path | None = None,
    candidates_file: Path | None = None,
    force: bool = False,
    doc: dict[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any] | None:
    """Remember an unmatched watchlist hit in dataset_candidates.json.

    No-op unless ``force=True`` or ``THEMEDATA_UPSERT_CANDIDATES=1``
    (Contabo / CI safe by default).

    Pass an in-memory ``doc`` and ``persist=False`` for bulk harvest (caller saves).
    """
    if not force and (os.getenv("THEMEDATA_UPSERT_CANDIDATES") or "").strip() not in {
        "1",
        "true",
        "yes",
    }:
        return None
    path = candidates_file or candidates_path(datasets_path)
    if doc is None:
        doc = load_candidates(path)
    row = _upsert_candidate_into_doc(doc, item, theme=theme)
    if row is not None and persist:
        save_candidates(doc, path)
    return row


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


def load_te_symbology(path: Path | None = None) -> dict[str, Any]:
    """Cached ``config/te_symbols.json`` (no API)."""
    global _te_symbology_cache
    p = path or _TE_SYMBOLS_DEFAULT
    if _te_symbology_cache is not None and path is None:
        return _te_symbology_cache
    if not p.is_file():
        doc: dict[str, Any] = {"schema_version": 0, "symbols": {}}
    else:
        doc = json.loads(p.read_text(encoding="utf-8"))
    if path is None:
        _te_symbology_cache = doc
    return doc


def _is_te_provider_context(item: dict[str, Any]) -> bool:
    prov = str(item.get("provider_or_source") or item.get("provider") or "")
    if _TE_PROVIDER_RE.search(prov):
        return True
    # Explicit TE symbol on the watchlist row
    for k in ("te_symbol", "symbol", "ticker"):
        if str(item.get(k) or "").strip():
            blob = str(item.get(k) or "")
            if ":" in blob or blob.upper().endswith(":COM"):
                return True
    return False


def resolve_te_series_key(item: dict[str, Any], *, symbology: dict[str, Any] | None = None) -> str | None:
    """Map a watchlist item to a ``te_symbols.json`` series_key (local only).

    Exact symbol/ticker always wins. Commodity *name* (e.g. germanium) only
    when the provider context is Trading Economics — avoids stealing USGS hits.
    """
    doc = symbology if symbology is not None else load_te_symbology()
    symbols = doc.get("symbols") or {}
    if not isinstance(symbols, dict) or not symbols:
        return None
    hay = _haystack(item)
    if not hay:
        return None
    compact = re.sub(r"[\s_\-]", "", hay)
    te_ctx = _is_te_provider_context(item)

    for series_key, meta in symbols.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("status") or "pull") not in {"pull", ""}:
            continue
        symbol = _norm(str(meta.get("symbol") or ""))
        ticker = _norm(str(meta.get("ticker") or ""))
        if symbol:
            sym_c = re.sub(r"[\s_\-]", "", symbol)
            if sym_c and sym_c in compact:
                return str(series_key)
        if ticker and len(ticker) >= 3:
            if re.search(rf"(^|[^a-z0-9]){re.escape(ticker)}([^a-z0-9]|$)", hay):
                return str(series_key)

    if not te_ctx:
        return None
    for series_key, meta in symbols.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("status") or "pull") not in {"pull", ""}:
            continue
        name = _norm(str(meta.get("name") or ""))
        if name and len(name) >= 4:
            if re.search(rf"(^|[^a-z0-9]){re.escape(name)}([^a-z0-9]|$)", hay):
                return str(series_key)
    return None


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
        # Trading Economics
        item.get("te_symbol") or "",
        item.get("symbol") or "",
        item.get("ticker") or "",
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
    bls = _norm(str(row.get("bls_id") or ""))
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
    if bls and bls in hay.replace(" ", ""):
        return 18.0, f"bls:{bls}", 1.0
    te_sym = _norm(str(row.get("te_symbol") or ""))
    if te_sym:
        compact_hay = re.sub(r"[\s_\-]", "", hay)
        compact_te = re.sub(r"[\s_\-]", "", te_sym)
        if compact_te and compact_te in compact_hay:
            return 18.0, f"te:{te_sym}", 1.0
    if hs6 and hs6 in hay.replace(" ", ""):
        return 18.0, f"hs:{hs6}", 1.0
    if mid and mid.lower() in hay:
        return 18.0, f"manifold:{mid}", 1.0

    # Exact provider / alias equality — only rows marked provider_umbrella (e.g. fred).
    # Paid map_only rows share vendors; do not treat them as umbrellas.
    item_prov = _norm(str(item.get("provider_or_source") or item.get("provider") or ""))
    is_umbrella = bool(row.get("provider_umbrella"))
    if is_umbrella and item_prov:
        if provider and item_prov == provider:
            return 12.0, f"provider_exact:{provider}", 0.5
        if aliases:
            for part in re.split(r"[;,/|]", aliases):
                p = _norm(part)
                if len(p) >= 3 and item_prov == p:
                    return 12.0, f"alias_exact:{p}", 0.5

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
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """
    Match a watchlist / PM item to datasets.json, then dataset_candidates.json.

    Preference order among close scores: higher detailed ``data_type`` overlap,
    then score. No editorial rank.

    Candidates:
      - ``status=aliased`` + ``aliased_to`` → resolve to that datasets.json id
      - ``status=open`` → stamp ``registry_status=candidate`` so UI can show "seen / backlog"

    Returns enrichment fields:
      dataset_id, access, cadence, as_of_semantics, registry_match, registry_status, match_reason
    """
    rows = datasets if datasets is not None else list(load_datasets(path).get("datasets") or [])
    by_dataset = {str(r.get("id") or ""): r for r in rows if r.get("id")}

    # Trading Economics: resolve known symbols from te_symbols.json (no API)
    te_key = resolve_te_series_key(item)
    if te_key and te_key in by_dataset:
        target = by_dataset[te_key]
        return {
            "dataset_id": te_key,
            "access": str(target.get("access") or "api"),
            "cadence": str(target.get("cadence") or item.get("cadence") or "weekly"),
            "as_of_semantics": str(target.get("as_of_semantics") or "vendor_as_of"),
            "registry_match": "high",
            "registry_status": str(target.get("status") or "pull"),
            "match_reason": f"te_symbol:{te_key}",
        }

    best: dict[str, Any] | None = None
    best_score = 0.0
    best_type_q = -1.0
    best_reason = ""
    best_pull = False
    for row in rows:
        score, reason, type_q = _score_dataset(item, row)
        if score <= 0:
            continue
        is_pull = str(row.get("status") or "") == "pull"
        # Prefer higher score; then data_type quality; then status=pull over map_only
        better = score > best_score or (
            abs(score - best_score) < 1e-6
            and (
                type_q > best_type_q
                or (abs(type_q - best_type_q) < 1e-6 and is_pull and not best_pull)
            )
        )
        if better:
            best_score = score
            best_type_q = type_q
            best = row
            best_reason = reason
            best_pull = is_pull

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

    # --- candidates backlog (gaps + explicit aliases) ---
    cand_rows = (
        candidates
        if candidates is not None
        else list(load_candidates(datasets_path=path).get("candidates") or [])
    )
    best_cand: dict[str, Any] | None = None
    best_cand_score = 0.0
    best_cand_reason = ""
    for cand in cand_rows:
        status = str(cand.get("status") or "open")
        if status in {"rejected", "promoted"}:
            continue
        score, reason, _tq = _score_dataset(item, _candidate_as_dataset_row(cand))
        if score <= 0:
            continue
        if score > best_cand_score:
            best_cand_score = score
            best_cand = cand
            best_cand_reason = reason

    if best_cand and best_cand_score >= 10.0:
        aliased_to = str(best_cand.get("aliased_to") or "").strip()
        if aliased_to and aliased_to in by_dataset:
            target = by_dataset[aliased_to]
            conf = "high" if best_cand_score >= 14.0 else "medium"
            return {
                "dataset_id": aliased_to,
                "access": str(target.get("access") or ""),
                "cadence": str(
                    target.get("cadence") or item.get("cadence") or ""
                ),
                "as_of_semantics": str(
                    target.get("as_of_semantics") or "period_end"
                ),
                "registry_match": conf,
                "registry_status": str(target.get("status") or ""),
                "match_reason": f"candidate_alias:{best_cand.get('id')}:{best_cand_reason}",
            }
        # Open / remembered gap — stamp as candidate so we don't lose it
        heur = heuristic_access_cadence(item)
        return {
            "dataset_id": str(best_cand.get("id") or ""),
            "access": str(
                best_cand.get("access_guess") or heur.get("access") or ""
            ),
            "cadence": str(
                best_cand.get("cadence") or item.get("cadence") or heur.get("cadence") or ""
            ),
            "as_of_semantics": str(
                best_cand.get("as_of_semantics") or heur.get("as_of_semantics") or ""
            ),
            "registry_match": "medium",
            "registry_status": "candidate",
            "match_reason": f"candidate:{best_cand_reason}",
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
    theme: str | None = None,
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
    if enrich.get("registry_match") == "none":
        upsert_candidate_from_item(out, theme=theme, datasets_path=path)
    return out


def enrich_watchlist_items(
    items: list[dict[str, Any]],
    *,
    path: Path | None = None,
    theme: str | None = None,
) -> list[dict[str, Any]]:
    rows = list(load_datasets(path).get("datasets") or [])
    return [
        enrich_watchlist_item(it, datasets=rows, path=path, theme=theme)
        for it in items
    ]
