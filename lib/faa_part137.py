"""FAA Part 137 agricultural operator counts via v-1x roster mirror.

FAA does not publish a clean time-series API for Part 137 certificates.
v-1x.com exposes a paginated JSON roster of FAR Part 137 operators compiled
from FAA air-operator data. We snapshot unique operator counts on each run
and append to local history (AISI-style).

Series:
  - total operators (farPart=137)
  - drone-named subset (name/slug lexicon — proxy for UAS spray adopters;
    undercounts operators that use drones without "drone" in the legal name)
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.faa_part137; v-1x read-only)"
API = "https://v-1x.com/api/operators"
HISTORY_NAME = "faa_part137_history.json"

_DRONE_NAME = re.compile(
    r"\bdrone|\buas\b|\buav\b|hylio|\bxag\b|agras|guardian\s*ag|"
    r"eavision|sentera|agri\s*spray|rantizo|\bpyka\b|spray\s*drone|"
    r"ag[\s-]?drone|drone\s*service",
    re.I,
)


def _get_json(url: str, *, timeout: float = 60) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise RuntimeError(f"v-1x unexpected payload type {type(data)}")
    return data


def fetch_operators(*, part: str = "137", page_size: int = 100) -> list[dict[str, Any]]:
    """Paginate the full Part 137 roster (unique by id)."""
    by_id: dict[str, dict[str, Any]] = {}
    page = 1
    total_pages = 1
    while page <= total_pages and page <= 200:
        q = urllib.parse.urlencode(
            {"part": part, "page": page, "limit": page_size}
        )
        try:
            data = _get_json(f"{API}?{q}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"v-1x HTTP {e.code} page={page}") from e
        ops = data.get("operators") or []
        if not isinstance(ops, list):
            raise RuntimeError("v-1x operators not a list")
        for o in ops:
            if isinstance(o, dict) and o.get("id"):
                by_id[str(o["id"])] = o
        total_pages = int(data.get("totalPages") or page)
        if not ops:
            break
        page += 1
        time.sleep(0.05)
    return list(by_id.values())


def count_snapshot(operators: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(operators)
    drone = [
        o
        for o in operators
        if _DRONE_NAME.search(str(o.get("name") or ""))
        or _DRONE_NAME.search(str(o.get("slug") or ""))
    ]
    return {
        "total": total,
        "drone_named": len(drone),
        "drone_sample": [
            {"name": o.get("name"), "state": o.get("state")} for o in drone[:12]
        ],
    }


def load_history(path: Path) -> dict[str, dict[str, float]]:
    """``{YYYY-MM-DD: {total, drone_named}}`` keyed by scrape as_of date."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for d, row in (data.get("snapshots") or {}).items():
        if isinstance(row, dict):
            out[str(d)] = {
                "total": float(row.get("total") or 0),
                "drone_named": float(row.get("drone_named") or 0),
            }
    return out


def save_history(
    path: Path,
    snapshots: dict[str, dict[str, float]],
    *,
    meta: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "snapshots": {k: snapshots[k] for k in sorted(snapshots)},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh_bundle(
    *,
    history_path: Path,
    start: str = "2020-01-01",
    as_of: str | None = None,
) -> tuple[dict[str, list[dict[str, float]]], dict[str, Any]]:
    """Fetch roster once, append today's snapshot, return total + drone_named."""
    prior = load_history(history_path)
    ops = fetch_operators()
    counts = count_snapshot(ops)
    as_of_d = as_of or date.today().isoformat()
    prior[as_of_d] = {
        "total": float(counts["total"]),
        "drone_named": float(counts["drone_named"]),
    }
    meta = {
        "n_snapshots": len(prior),
        "source": "v-1x FAR Part 137 operator roster (FAA air-operator mirror)",
        "as_of": as_of_d,
        "latest": counts,
        "note": (
            "drone_named is a name/slug lexicon proxy; undercounts UAS operators "
            "without 'drone' in the certificate name."
        ),
    }
    save_history(history_path, prior, meta=meta)
    bundle = {
        "total": [
            {"date": d, "value": float(row["total"])}
            for d, row in sorted(prior.items())
            if d >= start
        ],
        "drone_named": [
            {"date": d, "value": float(row["drone_named"])}
            for d, row in sorted(prior.items())
            if d >= start
        ],
        "drone_share_pct": [
            {
                "date": d,
                "value": round(
                    100.0 * float(row["drone_named"]) / float(row["total"]), 2
                ),
            }
            for d, row in sorted(prior.items())
            if d >= start and float(row.get("total") or 0) > 0
        ],
    }
    if not bundle["total"]:
        raise RuntimeError("Part 137 snapshot empty")
    return bundle, meta


def refresh_snapshot(
    *,
    history_path: Path,
    metric_key: str = "total",
    start: str = "2020-01-01",
    as_of: str | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Fetch roster, append today's snapshot, return points for metric_key.

    ``metric_key`` is ``total``, ``drone_named``, or ``drone_share_pct``.
    Points use calendar date of scrape (``scrape_as_of``). Same-day re-runs
    overwrite the prior snapshot for that date.
    """
    if metric_key not in ("total", "drone_named", "drone_share_pct"):
        raise RuntimeError(f"unknown part137 metric_key={metric_key!r}")
    bundle, meta = refresh_bundle(
        history_path=history_path, start=start, as_of=as_of
    )
    return bundle[metric_key], {**meta, "metric_key": metric_key}
