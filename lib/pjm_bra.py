"""PJM Base Residual Auction (BRA) RTO clearing price — annual append history.

Seed from a public RTO time series, then append newer delivery years from
official PJM BRA results XLSX (Summary sheet, Resource Clearing Prices, RTO).

Delivery years run June 1–May 31; we date each print as ``YYYY-06-01`` of the
start year (e.g. 2025/26 → 2025-06-01). Unit: USD/MW-day.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

HISTORY_NAME = "pjm_bra_rto_history.jsonl"
LATEST_NAME = "pjm_bra_rto_latest.json"
R2_HISTORY_KEY = "themedata/ldes/pjm_bra_rto_history.jsonl"
SERIES_KEY = "pjm_bra_rto_clearing_price"

# Third-party RTO-only CSV (delivery_year, rto_clearing_price_usd_per_mw_day)
SEED_CSV_URL = "https://www.braiain.com/data/pjm-capacity-prices.csv"

# Official BRA results — path pattern works for recent DotCom years
_XLSX_TMPL = (
    "https://www.pjm.com/-/media/DotCom/markets-ops/rpm/rpm-auction-info/"
    "{dy}/{dy}-base-residual-auction-results.xlsx"
)

UA = "mosaicbot_themedata/0.1 (+https://mosaicbot.com; research)"

_DY_RE = re.compile(r"^(\d{4})\s*/\s*(\d{2,4})$")


def _parse_delivery_year(raw: str) -> tuple[str, str] | None:
    """'2025/26' or '2025/2026' → (as_of YYYY-06-01, label 2025/26)."""
    s = str(raw).strip().replace("-", "/")
    m = _DY_RE.match(s)
    if not m:
        return None
    y1 = int(m.group(1))
    y2_raw = m.group(2)
    y2 = int(y2_raw) if len(y2_raw) == 4 else 2000 + int(y2_raw)
    if y2 != y1 + 1:
        # still accept contiguous DY labels
        pass
    label = f"{y1}/{str(y2)[-2:]}"
    return f"{y1:04d}-06-01", label


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


def write_history(out_dir: Path, rows: list[dict[str, Any]]) -> None:
    rows = sorted(rows, key=lambda r: r.get("as_of") or "")
    path = history_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )
    if rows:
        latest_path(out_dir).write_text(
            json.dumps(rows[-1], indent=2) + "\n", encoding="utf-8"
        )


def upsert_many(out_dir: Path, new_rows: list[dict[str, Any]]) -> int:
    by: dict[str, dict[str, Any]] = {
        str(r.get("as_of")): r for r in load_history(out_dir) if r.get("as_of")
    }
    inserted = 0
    for row in new_rows:
        key = str(row.get("as_of") or "")
        if not key:
            continue
        if key not in by:
            inserted += 1
        by[key] = row
    write_history(out_dir, list(by.values()))
    return inserted


def points_from_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pts: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: x.get("as_of") or ""):
        d = r.get("as_of")
        v = r.get("price_usd_mw_day")
        if d is None or v is None:
            continue
        pts.append({"date": str(d), "value": float(v)})
    return pts


def _row(
    *,
    as_of: str,
    delivery_year: str,
    price: float,
    source: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "schema_version": 0,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "delivery_year": delivery_year,
        "price_usd_mw_day": float(price),
        "unit": "usd_per_mw_day",
        "notes": notes,
    }


def fetch_seed_csv(url: str = SEED_CSV_URL) -> list[dict[str, Any]]:
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
    with urlopen(req, timeout=45) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    out: list[dict[str, Any]] = []
    for rec in reader:
        keys = {k.lower().strip(): k for k in (rec.keys() if rec else [])}
        dy_k = keys.get("delivery_year") or keys.get("delivery year")
        px_k = (
            keys.get("rto_clearing_price_usd_per_mw_day")
            or keys.get("rto_clearing_price")
            or keys.get("price")
        )
        if not dy_k or not px_k:
            continue
        parsed = _parse_delivery_year(str(rec.get(dy_k) or ""))
        if not parsed:
            continue
        as_of, label = parsed
        try:
            price = float(str(rec.get(px_k)).replace(",", ""))
        except (TypeError, ValueError):
            continue
        out.append(
            _row(
                as_of=as_of,
                delivery_year=label,
                price=price,
                source=url,
                notes="PJM RTO BRA clearing $/MW-day (seed CSV).",
            )
        )
    return out


def _dy_slug(label: str) -> str | None:
    """'2025/26' → '2025-2026' for PJM URL path."""
    parsed = _parse_delivery_year(label)
    if not parsed:
        return None
    as_of, _ = parsed
    y1 = int(as_of[:4])
    return f"{y1}-{y1 + 1}"


def fetch_rto_from_xlsx(delivery_year_label: str) -> dict[str, Any] | None:
    """Parse official BRA XLSX Summary for RTO resource clearing price."""
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError("openpyxl required for PJM BRA XLSX parse") from e

    slug = _dy_slug(delivery_year_label)
    if not slug:
        return None
    url = _XLSX_TMPL.format(dy=slug)
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except Exception:
        return None
    if raw[:2] != b"PK":
        return None
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    price: float | None = None
    for row in ws.iter_rows(max_row=60, values_only=True):
        if not row or row[0] is None:
            continue
        if str(row[0]).strip().upper() != "RTO":
            continue
        if row[1] is None:
            continue
        try:
            price = float(row[1])
            break
        except (TypeError, ValueError):
            continue
    if price is None:
        return None
    parsed = _parse_delivery_year(delivery_year_label)
    if not parsed:
        return None
    as_of, label = parsed
    return _row(
        as_of=as_of,
        delivery_year=label,
        price=price,
        source=url,
        notes="PJM official BRA results XLSX — RTO resource clearing price.",
    )


def candidate_delivery_years(*, through_year: int | None = None) -> list[str]:
    """Recent + next DY labels to probe (official XLS may lag)."""
    y = through_year or datetime.now(timezone.utc).year
    # Probe a window around current calendar year
    out: list[str] = []
    for y1 in range(y - 2, y + 3):
        out.append(f"{y1}/{str(y1 + 1)[-2:]}")
    return out


def refresh_and_upsert(
    out_dir: Path,
    *,
    seed_if_empty: bool = True,
    probe_xlsx: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Ensure history exists; optionally seed CSV + append newer official XLS."""
    notes: list[str] = []
    rows = load_history(out_dir)
    if not rows and seed_if_empty:
        try:
            seeded = fetch_seed_csv()
            n = upsert_many(out_dir, seeded)
            notes.append(f"pjm_bra: seeded CSV n={len(seeded)} inserted={n}")
        except Exception as e:
            notes.append(f"pjm_bra: seed soft-fail: {e}")
        rows = load_history(out_dir)

    if probe_xlsx:
        for label in candidate_delivery_years():
            try:
                snap = fetch_rto_from_xlsx(label)
            except Exception as e:
                notes.append(f"pjm_bra: xlsx {label} soft-fail: {e}")
                continue
            if not snap:
                continue
            inserted = upsert_many(out_dir, [snap])
            notes.append(
                f"pjm_bra: xlsx {label} px={snap['price_usd_mw_day']} "
                f"inserted={inserted}"
            )

    return load_history(out_dir), notes


def rows_from_body(body: str | bytes) -> list[dict[str, Any]]:
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


def history_to_body(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
