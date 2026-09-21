"""Trading Economics germanium spot — public HTML latest only; append history locally.

Same pattern as AISI weekly: page exposes the current level; we scrape on a
schedule and accumulate `germanium_te_history.jsonl`. CFD proxy (GECNYBGQ),
not a physical assessment (Argus/SMM) or USGS annual MCS.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

URL = "https://tradingeconomics.com/commodity/germanium"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HISTORY_NAME = "germanium_te_history.jsonl"
LATEST_NAME = "germanium_te_latest.json"
R2_HISTORY_KEY = "themedata/dew_defense/germanium_te_history.jsonl"

_MONTHS = {
    m: i
    for i, m in enumerate(
        "January February March April May June July August "
        "September October November December".split(),
        start=1,
    )
}


def _parse_mdy(s: str) -> str:
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", s.strip())
    if not m:
        raise ValueError(f"unparseable date: {s!r}")
    month = _MONTHS.get(m.group(1))
    if not month:
        raise ValueError(f"unknown month in {s!r}")
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def _parse_te_last_update(raw: str) -> str | None:
    """TELastUpdate like '202609211500' → YYYY-MM-DD."""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", raw.strip())
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def fetch_html(url: str = URL) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urlopen(req, timeout=45) as resp:
        body = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    text = body.decode("utf-8", errors="replace")
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    if "Attention Required" in text and "cf-error" in text:
        raise RuntimeError("Cloudflare challenge page (bot block)")
    return text


def parse_spot(html: str, *, fetched_at: str | None = None) -> dict[str, Any]:
    """Extract latest germanium CNY/kg and as-of date from TE commodity page."""
    price: float | None = None
    as_of: str | None = None
    symbol = "GECNYBGQ"
    full_name = "Germanium Ingot 50Ohm"

    m = re.search(
        r"Germanium traded(?: flat)? at ([\d,]+(?:\.\d+)?) CNY/Kg on "
        r"([A-Za-z]+ \d{1,2}, \d{4})",
        html,
        re.I,
    )
    if m:
        price = float(m.group(1).replace(",", ""))
        as_of = _parse_mdy(m.group(2))

    meta = re.search(r"TEChartsMeta\s*=\s*(\[.*?\]);", html, re.S)
    if meta:
        # Prefer structured last when prose parse missed price
        last_m = re.search(r'"last"\s*:\s*([\d.]+)', meta.group(1))
        if last_m and price is None:
            price = float(last_m.group(1))
        sym_m = re.search(r'"ticker"\s*:\s*"([^"]+)"', meta.group(1))
        if sym_m:
            symbol = sym_m.group(1).split(":")[0]
        name_m = re.search(r'"full_name"\s*:\s*"([^"]+)"', meta.group(1))
        if name_m:
            full_name = name_m.group(1).strip()

    # Prefer TELastUpdate date when prose date missing; prefer prose when both exist
    # (prose is the human-facing "traded … on DATE").
    upd = re.search(r"TELastUpdate\s*=\s*'(\d+)'", html)
    if upd and as_of is None:
        as_of = _parse_te_last_update(upd.group(1))

    sym_line = re.search(r"TESymbol\s*=\s*'([^']+)'", html)
    if sym_line:
        symbol = sym_line.group(1)

    if price is None or as_of is None:
        raise ValueError("could not parse germanium spot from Trading Economics HTML")

    return {
        "schema_version": 0,
        "source": URL,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "price_cny_kg": price,
        "unit": "cny_per_kg",
        "symbol": symbol,
        "full_name": full_name,
        "notes": "CFD proxy (Trading Economics); not USGS MCS or Argus/SMM physical.",
    }


def history_path(out_dir: Path) -> Path:
    return out_dir / HISTORY_NAME


def latest_path(out_dir: Path) -> Path:
    return out_dir / LATEST_NAME


def load_history(out_dir: Path) -> list[dict[str, Any]]:
    path = history_path(out_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def upsert_history(out_dir: Path, row: dict[str, Any]) -> bool:
    """Insert or replace by as_of. Returns True if a new as_of was inserted."""
    path = history_path(out_dir)
    rows = load_history(out_dir)
    key = row["as_of"]
    replaced = False
    out: list[dict[str, Any]] = []
    for existing in rows:
        if existing.get("as_of") == key:
            out.append(row)
            replaced = True
        else:
            out.append(existing)
    inserted = False
    if not replaced:
        out.append(row)
        inserted = True
    out.sort(key=lambda r: r.get("as_of") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in out),
        encoding="utf-8",
    )
    latest_path(out_dir).write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
    return inserted


def points_from_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pts: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: x.get("as_of") or ""):
        d = r.get("as_of")
        v = r.get("price_cny_kg")
        if d is None or v is None:
            continue
        pts.append({"date": str(d), "value": float(v)})
    return pts


def scrape_and_upsert(out_dir: Path) -> tuple[dict[str, Any], bool]:
    html = fetch_html()
    snap = parse_spot(html)
    inserted = upsert_history(out_dir, snap)
    return snap, inserted


def history_to_r2_body(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def rows_from_r2_body(body: str | bytes) -> list[dict[str, Any]]:
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = body
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows
