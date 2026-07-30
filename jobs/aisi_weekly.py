#!/usr/bin/env python3
"""Fetch AISI weekly raw steel production; append to local history.

Public page only exposes the latest week — no historical archive.
Run weekly (e.g. Monday launchd on Mac Mini). Idempotent on week_ending.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://www.steel.org/industry-data/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SCHEMA_VERSION = 0

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = _ROOT / "data" / "aisi"

_MONTHS = {
    m: i
    for i, m in enumerate(
        "January February March April May June July August "
        "September October November December".split(),
        start=1,
    )
}


def _parse_date(s: str) -> str:
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", s.strip())
    if not m:
        raise ValueError(f"unparseable date: {s!r}")
    month = _MONTHS[m.group(1)]
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def _int_commas(s: str) -> int:
    return int(s.replace(",", ""))


def html_to_text(html: str) -> str:
    clean = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    clean = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", clean)
    clean = re.sub(r"(?is)<[^>]+>", " ", clean)
    clean = re.sub(r"&nbsp;|&#160;", " ", clean)
    clean = re.sub(r"&#\d+;|&[a-z]+;", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def parse_weekly(text: str, *, fetched_at: str | None = None) -> dict:
    m = re.search(
        r"week ending on ([A-Za-z]+ \d{1,2}, \d{4})\s*,?\s*"
        r"domestic raw steel production was ([\d,]+) net tons "
        r"while the capability utilization rate was ([\d.]+) percent",
        text,
        re.I,
    )
    if not m:
        raise ValueError("could not find current-week production block")

    out: dict = {
        "schema_version": SCHEMA_VERSION,
        "source": URL,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "week_ending": _parse_date(m.group(1)),
        "production_net_tons": _int_commas(m.group(2)),
        "capability_utilization_pct": float(m.group(3)),
    }

    yoy = re.search(
        r"Production was ([\d,]+) net tons in the week ending ([A-Za-z]+ \d{1,2}, \d{4}), "
        r"while the capability utilization then was ([\d.]+) percent\.\s*"
        r"The current week production represents a ([\d.]+) percent (increase|decrease)",
        text,
        re.I,
    )
    wow = re.search(
        r"(?:up|down) ([\d.]+) percent from the previous week ending "
        r"([A-Za-z]+ \d{1,2}, \d{4}) when production was ([\d,]+) net tons "
        r"and the rate of capability utilization was ([\d.]+) percent",
        text,
        re.I,
    )
    wow_dir = re.search(
        r"is (up|down) ([\d.]+) percent from the previous week ending",
        text,
        re.I,
    )
    ytd = re.search(
        r"year-to-date production through ([A-Za-z]+ \d{1,2}, \d{4}), was "
        r"([\d,]+) net tons, at a capability utilization rate of ([\d.]+) percent\.\s*"
        r"That is (up|down) ([\d.]+) percent from the ([\d,]+) net tons "
        r"during the same period last year, when the capability utilization rate was ([\d.]+) percent",
        text,
        re.I,
    )
    dist = re.search(
        r"North East:\s*([\d,]+);\s*Great Lakes:\s*([\d,]+);\s*Midwest:\s*([\d,]+);\s*"
        r"Southern:\s*([\d,]+);\s*and Western:\s*([\d,]+)",
        text,
        re.I,
    )

    if yoy:
        sign = 1 if yoy.group(5).lower() == "increase" else -1
        out["yoy"] = {
            "week_ending": _parse_date(yoy.group(2)),
            "production_net_tons": _int_commas(yoy.group(1)),
            "capability_utilization_pct": float(yoy.group(3)),
            "change_pct": sign * float(yoy.group(4)),
        }

    if wow and wow_dir:
        sign = 1 if wow_dir.group(1).lower() == "up" else -1
        out["wow"] = {
            "week_ending": _parse_date(wow.group(2)),
            "production_net_tons": _int_commas(wow.group(3)),
            "capability_utilization_pct": float(wow.group(4)),
            "change_pct": sign * float(wow.group(1)),
        }

    if ytd:
        sign = 1 if ytd.group(4).lower() == "up" else -1
        out["ytd"] = {
            "through": _parse_date(ytd.group(1)),
            "production_net_tons": _int_commas(ytd.group(2)),
            "capability_utilization_pct": float(ytd.group(3)),
            "yoy_change_pct": sign * float(ytd.group(5)),
            "prior_year_production_net_tons": _int_commas(ytd.group(6)),
            "prior_year_capability_utilization_pct": float(ytd.group(7)),
        }

    if dist:
        out["districts_000_net_tons"] = {
            "north_east": _int_commas(dist.group(1)),
            "great_lakes": _int_commas(dist.group(2)),
            "midwest": _int_commas(dist.group(3)),
            "southern": _int_commas(dist.group(4)),
            "western": _int_commas(dist.group(5)),
        }

    return out


def fetch(url: str = URL) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urlopen(req, timeout=30) as resp:
        body = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    text = body.decode("utf-8", errors="replace")
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    if "Attention Required" in text and "cf-error" in text:
        raise RuntimeError("Cloudflare challenge page (bot block)")
    return text


def _load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _upsert_history(path: Path, row: dict) -> bool:
    """Append or replace by week_ending. Returns True if newly inserted."""
    rows = _load_history(path)
    key = row["week_ending"]
    replaced = False
    out: list[dict] = []
    for existing in rows:
        if existing.get("week_ending") == key:
            out.append(row)
            replaced = True
        else:
            out.append(existing)
    inserted = False
    if not replaced:
        out.append(row)
        inserted = True
    out.sort(key=lambda r: r.get("week_ending") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in out),
        encoding="utf-8",
    )
    return inserted


def run(*, out_dir: Path, dry_run: bool = False) -> dict:
    html = fetch()
    payload = parse_weekly(html_to_text(html))
    if dry_run:
        return payload

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "industry_data.html").write_text(html, encoding="utf-8")
    latest = out_dir / "weekly_latest.json"
    latest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    inserted = _upsert_history(out_dir / "weekly_history.jsonl", payload)
    payload["_history_inserted"] = inserted
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"output directory (default: {_DEFAULT_OUT})",
    )
    p.add_argument("--dry-run", action="store_true", help="fetch+parse only; no writes")
    args = p.parse_args(argv)

    try:
        payload = run(out_dir=args.out_dir, dry_run=args.dry_run)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    print(json.dumps({k: v for k, v in payload.items() if not k.startswith("_")}, indent=2))
    if not args.dry_run:
        flag = "new week" if payload.get("_history_inserted") else "updated existing week"
        print(f"\nwrote {args.out_dir}/weekly_latest.json ({flag})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
