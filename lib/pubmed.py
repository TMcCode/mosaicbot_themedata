"""PubMed monthly publication counts via NCBI E-utilities.

Uses esearch ``rettype=count`` with ``datetype=pdat`` per calendar month.
History is append/refresh (like AISI): denser past months win; trailing months
re-fetched each run. Optional ``NCBI_API_KEY`` raises the rate limit.
"""

from __future__ import annotations

import calendar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.pubmed; NCBI E-utilities research use)"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
HISTORY_NAME = "pubmed_history.json"


def _api_key() -> str:
    return (
        os.environ.get("NCBI_API_KEY")
        or os.environ.get("PUBMED_API_KEY")
        or ""
    ).strip()


def _sleep() -> None:
    # NCBI: 3 req/s without key, 10/s with key — stay conservative.
    time.sleep(0.12 if _api_key() else 0.34)


def _month_iter(start: str, end: str) -> list[str]:
    """Inclusive YYYY-MM month keys from start/end (YYYY-MM or YYYY-MM-DD)."""
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def _month_bounds(ym: str) -> tuple[str, str, str]:
    """Return (point_date YYYY-MM-01, mindate, maxdate) for NCBI."""
    y, m = int(ym[:4]), int(ym[5:7])
    last = calendar.monthrange(y, m)[1]
    return (
        f"{y:04d}-{m:02d}-01",
        f"{y:04d}/{m:02d}/01",
        f"{y:04d}/{m:02d}/{last:02d}",
    )


def esearch_count(term: str, *, mindate: str, maxdate: str) -> int:
    params: dict[str, str] = {
        "db": "pubmed",
        "term": term,
        "rettype": "count",
        "retmode": "json",
        "mindate": mindate,
        "maxdate": maxdate,
        "datetype": "pdat",
    }
    key = _api_key()
    if key:
        params["api_key"] = key
    url = f"{EUTILS}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"NCBI esearch HTTP {e.code}") from e
    try:
        return int(data["esearchresult"]["count"])
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"NCBI esearch unexpected payload: {data!r}") from e


def fetch_monthly_counts(
    term: str,
    *,
    start: str,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch monthly PubMed hit counts for ``term`` (publication date)."""
    end = end or date.today().strftime("%Y-%m")
    pts: list[dict[str, Any]] = []
    for ym in _month_iter(start, end):
        point_date, mind, maxd = _month_bounds(ym)
        n = esearch_count(term, mindate=mind, maxdate=maxd)
        pts.append({"date": point_date, "value": float(n)})
        _sleep()
    return pts


def _load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 0, "queries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 0, "queries": {}}
    if not isinstance(data, dict):
        return {"schema_version": 0, "queries": {}}
    data.setdefault("schema_version", 0)
    data.setdefault("queries", {})
    return data


def _save_history(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def refresh_query(
    *,
    history_path: Path,
    query_id: str,
    term: str,
    start: str,
    refresh_months: int = 4,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Merge history with newly fetched months; re-fetch trailing N months.

    Returns (points, meta). Points are ``{date, value}`` sorted.
    """
    hist = _load_history(history_path)
    queries = hist.setdefault("queries", {})
    q = queries.get(query_id) or {"term": term, "months": {}}
    months: dict[str, float] = {
        str(k): float(v) for k, v in (q.get("months") or {}).items()
    }

    today = date.today()
    end_ym = today.strftime("%Y-%m")
    all_months = _month_iter(start, end_ym)
    # Always refresh trailing window + any missing months.
    trailing = set(all_months[-max(1, int(refresh_months)) :])
    missing = [ym for ym in all_months if ym not in months]
    to_fetch = sorted(set(missing) | trailing)

    fetched = 0
    notes: list[str] = []
    for ym in to_fetch:
        point_date, mind, maxd = _month_bounds(ym)
        try:
            n = esearch_count(term, mindate=mind, maxdate=maxd)
        except Exception as e:
            notes.append(f"{query_id} {ym}: {e}")
            _sleep()
            continue
        months[ym] = float(n)
        fetched += 1
        _sleep()

    q["term"] = term
    q["months"] = {k: months[k] for k in sorted(months)}
    q["updated_at"] = datetime.now(timezone.utc).isoformat()
    queries[query_id] = q
    hist["queries"] = queries
    hist["updated_at"] = q["updated_at"]
    _save_history(history_path, hist)

    pts = [
        {"date": f"{ym}-01", "value": float(months[ym])}
        for ym in sorted(months)
        if ym >= start[:7]
    ]
    meta = {
        "query_id": query_id,
        "term": term,
        "n_points": len(pts),
        "fetched_months": fetched,
        "notes": notes,
        "source": "ncbi_eutils_pubmed",
    }
    return pts, meta
