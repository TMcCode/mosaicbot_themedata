"""USDA AMS Weekly National Chicken Report (ams_3646) — whole-wing prices.

Public PDF: https://www.ams.usda.gov/mnreports/ams_3646.pdf
Latest week only on that URL; append history locally (AISI pattern).

Parses **Wings - Whole** national weighted average (¢/lb, FOB plant).
Urner Barry wing index is paid; this is the free AMS substitute.

Bootstrap: meatread.com last-~10 weeks (AMS-derived). Going forward, each
pack run appends the latest PDF (and seeds prior week once if missing).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

PDF_URL = "https://www.ams.usda.gov/mnreports/ams_3646.pdf"
UA = "mosaicbot_themedata/0.1 (+usda_ams_chicken; USDA AMS public PDF)"
HISTORY_NAME = "ams_wing_history.jsonl"
MEATREAD_URL = "https://meatread.com/chicken-prices/chicken-wings"


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


def _parse_report_end(text: str) -> str:
    """Prefer 'Report For: MM/DD/YYYY to MM/DD/YYYY' end date; else header month day."""
    m = re.search(
        r"Report For:\s*(\d{1,2})/(\d{1,2})/(\d{4})\s+to\s+(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
        re.I,
    )
    if m:
        return f"{int(m.group(6)):04d}-{int(m.group(4)):02d}-{int(m.group(5)):02d}"
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
        text,
        re.I,
    )
    if not m:
        raise ValueError("could not find AMS report date")
    months = {
        n: i
        for i, n in enumerate(
            "January February March April May June July August "
            "September October November December".split(),
            start=1,
        )
    }
    return f"{int(m.group(3)):04d}-{months[m.group(1).title()]:02d}-{int(m.group(2)):02d}"


def parse_wings_whole(text: str) -> dict[str, Any]:
    """Extract Wings - Whole national wtd avg (¢/lb) from ams_3646 text.

    Layout: range, current wtd avg, change, volume, **previous** wtd avg, prev volume.
    """
    # PDF text often puts the label on its own line, then range / avg / change / lbs.
    pat = (
        r"Wings\s*[-–]\s*Whole\s*:?\s*"
        r"([\d.]+)\s*[-–]\s*([\d.]+)\s+"
        r"([\d.]+)\s+"
        r"([+-]?[\d.]+)\s+"
        r"([\d,]+)\s+"
        r"([\d.]+)\s+"
        r"([\d,]+)"
    )
    m = re.search(pat, text, re.I)
    if not m:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            if re.match(r"Wings\s*[-–]\s*Whole\s*:?\s*$", ln, re.I):
                chunk = " ".join(lines[i : i + 8])
                m = re.search(pat, chunk, re.I)
                break
    if not m:
        raise ValueError("could not parse Wings - Whole block from AMS PDF")

    week_ending = _parse_report_end(text)
    return {
        "week_ending": week_ending,
        "wings_whole_wtd_avg_cents_lb": float(m.group(3)),
        "wings_whole_range_low": float(m.group(1)),
        "wings_whole_range_high": float(m.group(2)),
        "wings_whole_change_cents": float(m.group(4)),
        "wings_whole_volume_klb": float(m.group(5).replace(",", "")),
        "prev_wings_whole_wtd_avg_cents_lb": float(m.group(6)),
        "prev_wings_whole_volume_klb": float(m.group(7).replace(",", "")),
        "source": PDF_URL,
        "report": "ams_3646",
    }


def load_history(path: Path) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return by
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        we = row.get("week_ending")
        if we:
            by[str(we)] = row
    return by


def write_history(path: Path, by: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(by.values(), key=lambda r: r.get("week_ending") or "")
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def scrape_and_upsert(
    out_dir: Path,
    *,
    fetched_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Fetch latest PDF, parse wings, upsert history. Returns (snap, inserted).

    Also seeds the prior week from the PDF's \"Previous Weeks Trading\" column
    when that week is not already in history (bootstrap only).
    """
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    raw = fetch_pdf_bytes()
    text = pdf_text(raw)
    snap = parse_wings_whole(text)
    snap["fetched_at"] = fetched_at
    hist_path = out_dir / HISTORY_NAME
    by = load_history(hist_path)
    we = snap["week_ending"]
    prev = by.get(we)
    inserted = prev is None or prev.get("wings_whole_wtd_avg_cents_lb") != snap.get(
        "wings_whole_wtd_avg_cents_lb"
    )
    by[we] = {k: v for k, v in snap.items() if not str(k).startswith("prev_")}

    prev_avg = snap.get("prev_wings_whole_wtd_avg_cents_lb")
    if prev_avg is not None:
        end = date.fromisoformat(we)
        prev_we = (end - timedelta(days=7)).isoformat()
        if prev_we not in by:
            by[prev_we] = {
                "week_ending": prev_we,
                "wings_whole_wtd_avg_cents_lb": float(prev_avg),
                "wings_whole_volume_klb": snap.get("prev_wings_whole_volume_klb"),
                "source": PDF_URL,
                "report": "ams_3646",
                "fetched_at": fetched_at,
                "notes": "Seeded from prior-week column of newer AMS PDF",
            }
            inserted = True

    write_history(hist_path, by)
    return snap, inserted


def _fetch_bytes(url: str, *, timeout: int = 60) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def meatread_weekly_rows() -> list[tuple[str, float]]:
    """Parse meatread's public last-N weeks table (AMS-derived ¢/lb)."""
    html = _fetch_bytes(MEATREAD_URL).decode("utf-8", "replace")
    months = {
        m: i
        for i, m in enumerate(
            "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1
        )
    }
    pat = re.compile(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),\s+(20\d{2})</td>"
        r".{0,300}?>([\d.]+)¢/lb</span></td>",
        re.S,
    )
    out: list[tuple[str, float]] = []
    for mon, day, year, avg in pat.findall(html):
        we = f"{int(year):04d}-{months[mon]:02d}-{int(day):02d}"
        out.append((we, float(avg)))
    return out


def bootstrap_meatread(out_dir: Path) -> int:
    """One-shot: seed ~10 recent weeks from meatread if missing. Returns upserts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    hist_path = out_dir / HISTORY_NAME
    by = load_history(hist_path)
    fetched_at = datetime.now(timezone.utc).isoformat()
    n = 0
    for we, avg in meatread_weekly_rows():
        if we in by:
            continue
        by[we] = {
            "week_ending": we,
            "wings_whole_wtd_avg_cents_lb": avg,
            "source": "meatread.com (USDA AMS-derived table)",
            "report": "ams_3646",
            "fetched_at": fetched_at,
            "notes": "Bootstrap from meatread weekly prints table",
        }
        n += 1
    write_history(hist_path, by)
    return n


def points_from_history(out_dir: Path) -> list[dict[str, float]]:
    by = load_history(out_dir / HISTORY_NAME)
    pts = [
        {
            "date": we,
            "value": float(row["wings_whole_wtd_avg_cents_lb"]),
        }
        for we, row in by.items()
        if row.get("wings_whole_wtd_avg_cents_lb") is not None
    ]
    pts.sort(key=lambda p: p["date"])
    return pts
