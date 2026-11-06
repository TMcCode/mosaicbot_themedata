"""ChinaData.live GACC monthly HS totals.

Free, no key. The ``/api/v2/trade/hs`` route 404s; the product page embeds the
same monthly JSON in ``#hs-runtime-bootstrap``. Browser-like UA required
(bot UA → 403).
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PAGE = "https://chinadata.live/china-trade/hs"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_BOOTSTRAP_RE = re.compile(
    r'<script[^>]*\bid="hs-runtime-bootstrap"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _month_end(ym: str) -> str:
    """``YYYY-MM`` → period-end ``YYYY-MM-DD``."""
    y, m = ym.split("-", 1)
    yi, mi = int(y), int(m)
    last = monthrange(yi, mi)[1]
    return f"{yi:04d}-{mi:02d}-{last:02d}"


def _bootstrap_payload(html: str) -> dict[str, Any]:
    match = _BOOTSTRAP_RE.search(html)
    if not match:
        raise RuntimeError("hs-runtime-bootstrap missing")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise RuntimeError("hs-runtime-bootstrap is not an object")
    return data


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
    url = f"{PAGE}/{hs}/?{qs}"
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8")
            final_url = resp.geturl()
    except HTTPError as e:
        raise RuntimeError(f"chinadata HS{hs} {flow}: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"chinadata HS{hs} {flow}: {e}") from e

    try:
        data = _bootstrap_payload(html)
    except (RuntimeError, json.JSONDecodeError) as e:
        raise RuntimeError(f"chinadata HS{hs} {flow}: {e}") from e

    if not data.get("success"):
        raise RuntimeError(f"chinadata HS{hs} {flow}: unexpected payload")
    got_hs = str(data.get("hs_code") or "").strip()
    got_flow = str(data.get("flow") or "").strip().lower()
    if got_hs != hs or got_flow != flow:
        raise RuntimeError(
            f"chinadata HS{hs} {flow}: page returned HS{got_hs or '?'} {got_flow or '?'} ({final_url})"
        )

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
    if not pts:
        raise RuntimeError(f"chinadata HS{hs} {flow}: no monthly points")
    return pts
