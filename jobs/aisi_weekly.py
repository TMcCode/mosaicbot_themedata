#!/usr/bin/env python3
"""Fetch AISI weekly raw steel; append history; publish pack to R2.

Public page = latest week only. History is whatever we append locally.
Mac Mini host (Cloudflare). Pack series rebuilt from weekly_history.jsonl.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402

URL = "https://www.steel.org/industry-data/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SCHEMA_VERSION = 0
PACK = "aisi_weekly"
R2_PREFIX = f"themedata/{PACK}"

_LEGACY_OUT = _ROOT / "data" / "aisi"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "aisi_weekly_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"

_MONTHS = {
    m: i
    for i, m in enumerate(
        "January February March April May June July August "
        "September October November December".split(),
        start=1,
    )
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _history_paths() -> list[Path]:
    return [
        _DEFAULT_OUT / "weekly_history.jsonl",
        _LEGACY_OUT / "weekly_history.jsonl",
    ]


def _load_history() -> list[dict]:
    for path in _history_paths():
        if not path.exists():
            continue
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        if rows:
            return rows
    return []


def _upsert_history(path: Path, row: dict) -> bool:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
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


def history_to_points(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    prod: list[dict] = []
    util: list[dict] = []
    for r in sorted(rows, key=lambda x: x.get("week_ending") or ""):
        d = r.get("week_ending")
        if not d:
            continue
        if r.get("production_net_tons") is not None:
            prod.append({"date": d, "value": float(r["production_net_tons"])})
        if r.get("capability_utilization_pct") is not None:
            util.append({"date": d, "value": float(r["capability_utilization_pct"])})
    return prod, util


def build_pack_from_history(
    *,
    catalog: dict,
    attachments: dict,
    history: list[dict],
    snapshot: dict | None = None,
) -> dict[str, Any]:
    prod_pts, util_pts = history_to_points(history)
    by_key = {
        "aisi_production": prod_pts,
        "aisi_utilization": util_pts,
    }
    series_out: dict[str, Any] = {}
    for meta in catalog.get("series") or []:
        key = meta["key"]
        pts = by_key.get(key) or []
        if not pts:
            continue
        series_out[key] = ps.series_shell(meta, points=pts)

    as_of = ""
    if history:
        as_of = max(str(r.get("week_ending") or "") for r in history)
    fetched_at = (snapshot or {}).get("fetched_at") or datetime.now(timezone.utc).isoformat()

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": URL,
        "fetched_at": fetched_at,
        "as_of": as_of,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "latest_snapshot": snapshot,
        "history_weeks": len(history),
    }


def _load_attachments() -> dict:
    attachments = _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
            attachments = remote
    except Exception:
        pass
    return attachments


def _write_local(out_dir: Path, payload: dict, *, html: str | None, snapshot: dict | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "catalog.json").write_text(
        json.dumps(payload["catalog"], indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "attachments.json").write_text(
        json.dumps(payload["attachments"], indent=2) + "\n", encoding="utf-8"
    )
    if snapshot:
        (out_dir / "weekly_latest.json").write_text(
            json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
        )
        # Legacy freshness path
        _LEGACY_OUT.mkdir(parents=True, exist_ok=True)
        (_LEGACY_OUT / "weekly_latest.json").write_text(
            json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
        )
    if html:
        (out_dir / "industry_data.html").write_text(html, encoding="utf-8")


def _publish_r2(payload: dict) -> list[str]:
    uris = [
        r2lib.put_json(f"{R2_PREFIX}/latest.json", payload),
        r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]),
        r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]),
    ]
    slim = {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "fetched_at": payload["fetched_at"],
        "as_of": payload["as_of"],
        "metrics": {},
    }
    for key, ser in payload["series"].items():
        slim["metrics"][key] = {
            "raw_latest": ser["raw"]["latest"],
            "derived_latest": {
                name: meta.get("latest") for name, meta in (ser.get("derived") or {}).items()
            },
        }
        uris.append(
            r2lib.put_json(
                f"{R2_PREFIX}/raw/{key}.json",
                {
                    "key": key,
                    "label": ser.get("label"),
                    "display_name": ser.get("display_name"),
                    "frequency": ser.get("frequency"),
                    "unit": ser.get("unit"),
                    "points": ser["raw"]["points"],
                    "latest": ser["raw"]["latest"],
                },
            )
        )
        uris.append(
            r2lib.put_json(
                f"{R2_PREFIX}/derived/{key}.json",
                {"key": key, "derived": ser.get("derived")},
            )
        )
    uris.append(r2lib.put_json(f"{R2_PREFIX}/metrics_latest.json", slim))
    return uris


def run(
    *,
    out_dir: Path,
    dry_run: bool = False,
    skip_r2: bool = False,
    skip_fetch: bool = False,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()

    html = None
    snapshot = None
    inserted = False
    if not skip_fetch:
        html = fetch()
        snapshot = parse_weekly(html_to_text(html))
        if not dry_run:
            hist_path = out_dir / "weekly_history.jsonl"
            inserted = _upsert_history(hist_path, snapshot)
            # Mirror history to legacy path
            _upsert_history(_LEGACY_OUT / "weekly_history.jsonl", snapshot)

    history = _load_history()
    if snapshot and not any(r.get("week_ending") == snapshot.get("week_ending") for r in history):
        history = list(history) + [snapshot]

    if not history:
        raise RuntimeError("no AISI history — fetch a week first (or place weekly_history.jsonl)")

    payload = build_pack_from_history(
        catalog=catalog,
        attachments=attachments,
        history=history,
        snapshot=snapshot,
    )
    payload["_history_inserted"] = inserted

    if dry_run:
        return payload

    _write_local(out_dir, payload, html=html, snapshot=snapshot)
    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.extend(_publish_r2(payload))
    payload["_r2_uris"] = r2_uris
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Rebuild pack from existing weekly_history.jsonl only",
    )
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_fetch=args.skip_fetch,
        )
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "history_weeks": payload.get("history_weeks"),
        "series": list(payload.get("series") or {}),
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    if not args.dry_run:
        flag = "new week" if payload.get("_history_inserted") else "history rebuild"
        print(f"\nwrote {args.out_dir}/latest.json ({flag})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
