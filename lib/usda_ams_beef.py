"""USDA AMS National Daily Boxed Beef Cutout (LM_XB403) — Choice 600-900.

Primary history: AMS MyMarketNews Datamart API (report 2453 / AMS_2453),
section "Current Cutout Values" — daily Choice/Select 600-900 back to 2004.

Fallback / confirm: public PDF https://www.ams.usda.gov/mnreports/ams_2453.pdf
(latest print + prior-5-day table) when the API is unavailable.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PDF_URL = "https://www.ams.usda.gov/mnreports/ams_2453.pdf"
DATAMART_SECTION_URL = (
    "https://mpr.datamart.ams.usda.gov/services/v1.1/reports/2453/"
    + quote("Current Cutout Values")
)
UA = "mosaicbot_themedata/0.1 (+usda_ams_beef; USDA AMS public)"
HISTORY_NAME = "ams_beef_cutout_history.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_pdf_bytes(url: str = PDF_URL) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=90) as resp:
        return resp.read()


def pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as e:
        raise RuntimeError("pypdf required — pip install pypdf") from e
    from io import BytesIO

    reader = PdfReader(BytesIO(raw))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _parse_header_date(text: str) -> str:
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
        text,
        re.I,
    )
    if not m:
        raise ValueError("could not find AMS beef report date")
    months = {
        n: i
        for i, n in enumerate(
            "January February March April May June July August "
            "September October November December".split(),
            start=1,
        )
    }
    return f"{int(m.group(3)):04d}-{months[m.group(1).title()]:02d}-{int(m.group(2)):02d}"


def parse_choice_cutout(text: str) -> dict[str, Any]:
    """Extract Choice 600-900 current cutout + prior-5-day table rows."""
    as_of = _parse_header_date(text)
    m = re.search(
        r"Current\s+Cutout\s+Values:\s*([\d.]+)\s+([\d.]+)",
        text,
        re.I | re.S,
    )
    if not m:
        m = re.search(
            r"Current\s+Cutout\s+Values:\s*\n?\s*([\d.]+)\s*\n\s*([\d.]+)",
            text,
            re.I,
        )
    if not m:
        raise ValueError("could not parse Current Cutout Values from LM_XB403")
    choice = float(m.group(1))
    select = float(m.group(2))

    prior: list[dict[str, Any]] = []
    for mm, dd, _c, _s, _t, _g, _tot, ch, sel in re.findall(
        r"(\d{1,2})/(\d{1,2})\s+"
        r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+"
        r"([\d.]+)\s+([\d.]+)",
        text,
    ):
        year = int(as_of[:4])
        month = int(mm)
        as_month = int(as_of[5:7])
        if month > as_month + 1:
            year -= 1
        prior.append(
            {
                "date": f"{year:04d}-{month:02d}-{int(dd):02d}",
                "choice_600_900": float(ch),
                "select_600_900": float(sel),
            }
        )

    return {
        "date": as_of,
        "choice_600_900": choice,
        "select_600_900": select,
        "spread": round(choice - select, 2),
        "prior_days": prior,
        "source": PDF_URL,
        "report": "LM_XB403",
    }


def fetch_datamart_cutouts() -> list[dict[str, Any]]:
    """Full daily Choice/Select 600-900 history from AMS Datamart (~2004→)."""
    req = Request(
        DATAMART_SECTION_URL,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    out: list[dict[str, Any]] = []
    for row in payload.get("results") or []:
        choice = row.get("choice_600_900_current")
        if choice is None or choice == "":
            continue
        try:
            d = datetime.strptime(str(row["report_date"]), "%m/%d/%Y").date()
            ch = float(choice)
        except (KeyError, TypeError, ValueError):
            continue
        sel_raw = row.get("select_600_900_current")
        try:
            sel = float(sel_raw) if sel_raw not in (None, "") else None
        except (TypeError, ValueError):
            sel = None
        item: dict[str, Any] = {
            "date": d.isoformat(),
            "choice_600_900": ch,
            "source": DATAMART_SECTION_URL,
            "report": "LM_XB403",
        }
        if sel is not None:
            item["select_600_900"] = sel
            item["spread"] = round(ch - sel, 2)
        out.append(item)
    out.sort(key=lambda r: r["date"])
    return out


def load_history(path: Path) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return by
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        d = row.get("date")
        if d:
            by[str(d)] = row
    return by


def write_history(path: Path, by: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(by.values(), key=lambda r: r.get("date") or "")
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def _upsert_row(
    by: dict[str, dict[str, Any]],
    *,
    date: str,
    choice: float,
    select: float | None,
    fetched_at: str,
    source: str,
    **extra: Any,
) -> bool:
    """Return True if inserted or value changed."""
    row: dict[str, Any] = {
        "date": date,
        "choice_600_900": float(choice),
        "source": source,
        "report": "LM_XB403",
        "fetched_at": fetched_at,
        **extra,
    }
    if select is not None:
        row["select_600_900"] = float(select)
        row["spread"] = round(float(choice) - float(select), 2)
    prev = by.get(date)
    changed = prev is None or prev.get("choice_600_900") != row["choice_600_900"]
    by[date] = row
    return changed


def scrape_and_upsert(
    out_dir: Path,
    *,
    fetched_at: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Backfill from Datamart; fall back to PDF prior-5-day if API fails.

    Returns (latest_snap, n_upserts).
    """
    fetched_at = fetched_at or _now_iso()
    hist_path = out_dir / HISTORY_NAME
    by = load_history(hist_path)
    n = 0
    source_note = "datamart"

    try:
        rows = fetch_datamart_cutouts()
        if not rows:
            raise ValueError("datamart returned no Choice cutout rows")
        for row in rows:
            if _upsert_row(
                by,
                date=row["date"],
                choice=row["choice_600_900"],
                select=row.get("select_600_900"),
                fetched_at=fetched_at,
                source=row.get("source") or DATAMART_SECTION_URL,
                spread=row.get("spread"),
            ):
                n += 1
        latest = rows[-1]
        snap = {
            "date": latest["date"],
            "choice_600_900": latest["choice_600_900"],
            "select_600_900": latest.get("select_600_900"),
            "spread": latest.get("spread"),
            "source": DATAMART_SECTION_URL,
            "report": "LM_XB403",
            "fetched_at": fetched_at,
            "history_days": len(rows),
        }
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        source_note = f"pdf_fallback ({e})"
        raw = fetch_pdf_bytes()
        text = pdf_text(raw)
        snap = parse_choice_cutout(text)
        snap["fetched_at"] = fetched_at
        if _upsert_row(
            by,
            date=snap["date"],
            choice=snap["choice_600_900"],
            select=snap.get("select_600_900"),
            fetched_at=fetched_at,
            source=PDF_URL,
            spread=snap.get("spread"),
        ):
            n += 1
        for p in snap.get("prior_days") or []:
            if _upsert_row(
                by,
                date=p["date"],
                choice=p["choice_600_900"],
                select=p.get("select_600_900"),
                fetched_at=fetched_at,
                source=PDF_URL,
                notes="prior-5-day table",
            ):
                n += 1
        snap["history_days"] = len(by)
        snap["fallback"] = source_note

    write_history(hist_path, by)
    snap["upserts"] = n
    snap["source_note"] = source_note
    return snap, n


def points_from_history(out_dir: Path) -> list[dict[str, float]]:
    by = load_history(out_dir / HISTORY_NAME)
    pts = [
        {"date": d, "value": float(row["choice_600_900"])}
        for d, row in by.items()
        if row.get("choice_600_900") is not None
    ]
    pts.sort(key=lambda p: p["date"])
    return pts
