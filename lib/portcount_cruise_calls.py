"""US cruise-port monthly ship calls via PortCount cruise hub.

Source: https://portcount.com/en/cruise

The hub embeds per-port ``sp.r`` arrays — trailing ~24 months of cruise
calls ending at ``cm`` (complete-to month). Trailing-12 sum matches the
published ``w12`` cruise total. We scrape, merge into local history
(newer scrape wins per YYYY-MM), and expose per-port + sum series.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "mosaicbot_themedata/0.1 (+lib.portcount_cruise_calls)"
)
CRUISE_HUB = "https://portcount.com/en/cruise"
HISTORY_NAME = "portcount_cruise_calls_history.json"

# Key US homeports for Recreation '24: Cruises (PortCount slugs).
PORTS: tuple[dict[str, str], ...] = (
    {
        "slug": "miami",
        "series_key": "miami_cruise_calls",
        "display_name": "PortMiami cruise calls",
    },
    {
        "slug": "port-canaveral",
        "series_key": "canaveral_cruise_calls",
        "display_name": "Port Canaveral cruise calls",
    },
    {
        "slug": "port-everglades",
        "series_key": "everglades_cruise_calls",
        "display_name": "Port Everglades cruise calls",
    },
    {
        "slug": "galveston",
        "series_key": "galveston_cruise_calls",
        "display_name": "Galveston cruise calls",
    },
    {
        "slug": "tampa-bay",
        "series_key": "tampa_cruise_calls",
        "display_name": "Port Tampa Bay cruise calls",
    },
    {
        "slug": "seattle",
        "series_key": "seattle_cruise_calls",
        "display_name": "Port of Seattle cruise calls",
    },
)
SUM_SERIES_KEY = "key_us_cruise_port_calls"

_PORT_HEAD = re.compile(
    r'\{"id":(\d+),"n":"([^"]+)","cc":"([^"]+)","sub":"([^"]*)","slug":"([^"]+)"'
)
_SP_R = re.compile(r'"sp":\{"r":\[([0-9,\s]+)\]\}')
_CM = re.compile(r'"cm":"(20\d{2}-\d{2})"')
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _get_text(url: str, *, timeout: float = 60) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "text/html,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _rsc_blob(html: str) -> str:
    parts: list[str] = []
    for m in _PUSH.finditer(html):
        raw = m.group(1)
        try:
            parts.append(bytes(raw, "utf-8").decode("unicode_escape"))
        except Exception:
            parts.append(raw)
    return "".join(parts)


def _extract_object(blob: str, start: int) -> str | None:
    depth = 0
    for j in range(start, min(start + 30000, len(blob))):
        ch = blob[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return blob[start : j + 1]
    return None


def parse_hub_ports(html: str) -> dict[str, dict[str, Any]]:
    """Return ``{slug: {name, cm, points: {YYYY-MM-01: calls}}}``."""
    blob = _rsc_blob(html)
    out: dict[str, dict[str, Any]] = {}
    for m in _PORT_HEAD.finditer(blob):
        slug = m.group(5)
        if slug in out:
            continue
        obj = _extract_object(blob, m.start())
        if not obj:
            continue
        sp = _SP_R.search(obj)
        cm = _CM.search(obj)
        if not sp or not cm:
            continue
        series = [int(x) for x in sp.group(1).split(",") if x.strip()]
        if len(series) < 6:
            continue
        cy, cmo = (int(x) for x in cm.group(1).split("-"))
        # ``sp.r[0]`` is oldest; last element aligns to ``cm``.
        start_y, start_m = _add_months(cy, cmo, -(len(series) - 1))
        points: dict[str, float] = {}
        for i, val in enumerate(series):
            y, mo = _add_months(start_y, start_m, i)
            points[f"{y:04d}-{mo:02d}-01"] = float(val)
        out[slug] = {
            "name": m.group(2),
            "cc": m.group(3),
            "cm": cm.group(1),
            "points": points,
            "n_months": len(series),
        }
    return out


def fetch_key_ports(
    slugs: tuple[str, ...] | list[str] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    want = list(slugs) if slugs is not None else [p["slug"] for p in PORTS]
    html = _get_text(CRUISE_HUB)
    parsed = parse_hub_ports(html)
    by_slug: dict[str, dict[str, float]] = {}
    notes: list[str] = []
    for slug in want:
        row = parsed.get(slug)
        if not row:
            notes.append(f"missing {slug}")
            continue
        by_slug[slug] = dict(row["points"])
        notes.append(
            f"ok {slug} cm={row['cm']} months={row['n_months']} "
            f"last={max(row['points'])}={int(row['points'][max(row['points'])])}"
        )
    if not by_slug:
        raise RuntimeError("PortCount cruise hub empty: " + "; ".join(notes[:6]))
    meta = {
        "source": CRUISE_HUB,
        "scrape_notes": notes,
        "n_ports": len(by_slug),
    }
    return by_slug, meta


def load_history(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    ports = data.get("ports") or {}
    out: dict[str, dict[str, float]] = {}
    for slug, pts in ports.items():
        out[str(slug)] = {str(k): float(v) for k, v in (pts or {}).items()}
    return out


def save_history(
    path: Path,
    ports: dict[str, dict[str, float]],
    *,
    meta: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "ports": {
            slug: {k: ports[slug][k] for k in sorted(ports[slug])}
            for slug in sorted(ports)
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _complete_month_dates(
    by_slug: dict[str, dict[str, float]],
) -> list[str]:
    """Months present in *every* port series (incl. legitimate zeros).

    PortCount ``cm`` lags differ by port — summing whatever is available
    creates a short partial tip (e.g. only Galveston+Seattle). Score and the
    sum series only use fully reported months.
    """
    if not by_slug:
        return []
    common: set[str] | None = None
    for pts in by_slug.values():
        keys = set(pts)
        common = keys if common is None else (common & keys)
    return sorted(common or [])


def _sum_points(
    by_slug: dict[str, dict[str, float]],
    *,
    dates: list[str] | None = None,
) -> dict[str, float]:
    use_dates = dates if dates is not None else _complete_month_dates(by_slug)
    totals: dict[str, float] = {}
    for d in use_dates:
        totals[d] = float(sum(pts[d] for pts in by_slug.values()))
    return totals


def refresh_monthly(
    *,
    history_path: Path,
    start: str = "2015-01-01",
) -> tuple[dict[str, list[dict[str, float]]], dict[str, Any]]:
    """Merge PortCount scrape into history; return series_key → points."""
    prior = load_history(history_path)
    scraped, scrape_meta = fetch_key_ports()
    merged: dict[str, dict[str, float]] = {slug: dict(pts) for slug, pts in prior.items()}
    for slug, pts in scraped.items():
        merged[slug] = {**merged.get(slug, {}), **pts}

    meta = {
        **scrape_meta,
        "n_months_total": sum(len(v) for v in merged.values()),
        "as_of": max((max(v) for v in merged.values() if v), default=None),
    }
    save_history(history_path, merged, meta=meta)

    slug_to_key = {p["slug"]: p["series_key"] for p in PORTS}
    key_ports = {slug: merged[slug] for slug in slug_to_key if slug in merged}
    complete_dates = _complete_month_dates(key_ports)
    complete_set = set(complete_dates)
    meta["complete_as_of"] = complete_dates[-1] if complete_dates else None
    meta["complete_months"] = len(complete_dates)

    out: dict[str, list[dict[str, float]]] = {}
    for slug, pts in key_ports.items():
        key = slug_to_key[slug]
        # Publish only fully reported months so the stacked chart tip is not a
        # 1–2 port stub while other hubs still show prior ``cm``.
        out[key] = [
            {"date": d, "value": pts[d]}
            for d in complete_dates
            if d >= start and d in pts
        ]

    sum_pts = _sum_points(key_ports, dates=complete_dates)
    out[SUM_SERIES_KEY] = [
        {"date": d, "value": v}
        for d, v in sorted(sum_pts.items())
        if d >= start and d in complete_set
    ]
    meta["series_keys"] = sorted(out)
    return out, meta
