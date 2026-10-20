"""ChinaData.live public trade API (GACC monthly HS totals).

Free, no key. Browser-like UA required (bot UA → 403).
Docs: https://chinadata.live/api-docs/
"""

from __future__ import annotations

import json
from calendar import monthrange
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://chinadata.live/api/v2/trade/hs"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _month_end(ym: str) -> str:
    """``YYYY-MM`` → period-end ``YYYY-MM-DD``."""
    y, m = ym.split("-", 1)
    yi, mi = int(y), int(m)
    last = monthrange(yi, mi)[1]
    return f"{yi:04d}-{mi:02d}-{last:02d}"


def fetch_hs_monthly(
    hs: str,
    *,
    flow: str = "import",
    timeout: float = 90.0,
) -> list[dict[str, Any]]:
    """Return ``[{date, value}]`` monthly USD totals for an HS code.

    ``flow`` is ``import`` or ``export``. Values are GACC customs USD.
    """
    hs = str(hs).strip()
    flow = str(flow).strip().lower()
    if flow not in ("import", "export"):
        raise ValueError(f"flow must be import|export, got {flow!r}")
    qs = urlencode({"flow": flow})
    url = f"{API}/{hs}?{qs}"
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"chinadata HS{hs} {flow}: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"chinadata HS{hs} {flow}: {e}") from e

    if not isinstance(data, dict) or not data.get("success"):
        raise RuntimeError(f"chinadata HS{hs} {flow}: unexpected payload")

    monthly = data.get("monthly") or []
    pts: list[dict[str, Any]] = []
    for row in monthly:
        if not isinstance(row, dict):
            continue
        ym = str(row.get("month") or "").strip()
        val = row.get("value_usd")
        if not ym or val is None:
            continue
        try:
            pts.append({"date": _month_end(ym), "value": float(val)})
        except (TypeError, ValueError):
            continue
    pts.sort(key=lambda p: p["date"])
    return pts
