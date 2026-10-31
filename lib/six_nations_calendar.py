"""Men's Six Nations monthly match counts from curated config.

No openfootball rugby repo — calendar is editorial JSON under
``config/six_nations_calendar.json`` (15 matches / tournament).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CFG = _ROOT / "config" / "six_nations_calendar.json"


def fetch_monthly_match_counts(
    *,
    start_year: int = 2018,
    ahead_months: int = 12,
    config_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = config_path or _DEFAULT_CFG
    cfg = json.loads(path.read_text(encoding="utf-8"))
    monthly = cfg.get("monthly") or {}
    if not isinstance(monthly, dict) or not monthly:
        raise RuntimeError(f"six_nations_calendar: empty monthly in {path}")

    today = datetime.now(timezone.utc).date()
    y, m = today.year, today.month
    for _ in range(max(0, ahead_months)):
        m += 1
        if m > 12:
            y, m = y + 1, 1
    horizon = f"{y:04d}-{m:02d}-01"
    start = f"{start_year:04d}-01-01"

    pts = [
        {"date": d, "value": float(v)}
        for d, v in sorted(monthly.items())
        if isinstance(d, str) and d >= start and d <= horizon
    ]
    if not pts:
        raise RuntimeError(
            f"six_nations_calendar: no points in [{start}, {horizon}] from {path}"
        )

    meta = {
        "source": cfg.get("source") or "curated_six_nations",
        "config": str(path),
        "notes": cfg.get("notes"),
        "horizon": horizon,
        "n_months": len(pts),
        "n_matches": int(sum(p["value"] for p in pts)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta
