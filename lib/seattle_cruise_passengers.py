"""Port of Seattle monthly cruise passengers via Tableau Public vizql.

Dashboard: Cruise Operation & Marketing KPI (Port of Seattle BI)
  https://www.portseattle.org/cruisedashboard
  https://public.tableau.com/app/profile/portofseattlebi/viz/CruiseOperationMarketingKPI-2026/Passengers

Author disabled CSV / view-data export (``allow_export_data: false``), but the
Passengers sheet still returns mark data in ``bootstrapSession``. We:

1. ``startSession/viewing`` → session id + ``Global-Session-Header``
2. ``bootstrapSession`` for the Passengers sheet
3. Parse month labels + Actual Passengers from ``dataColumns``
4. Merge year workbooks (2025, 2026, …) into append-only local history

Seasonal homeport (roughly Apr–Oct); winter months are absent, not zero.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "mosaicbot_themedata/0.1 (+lib.seattle_cruise_passengers)"
)

# Newest first — add a new year workbook when Port of Seattle publishes it.
WORKBOOKS = (
    "CruiseOperationMarketingKPI-2026",
    "CruiseOperationMarketingKPI-2025",
)
SHEET_PATH = "Passengers"
SHEET_ID = "Passengers"
HISTORY_NAME = "seattle_cruise_passengers_history.json"

_MONTH = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_NAME_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)
_FMT_NUM_RE = re.compile(r"^(\d{1,3}(?:,\d{3})+)$")


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def _call(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120,
) -> tuple[bytes, Any]:
    h = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://public.tableau.com/",
        "Origin": "https://public.tableau.com",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read(), resp.headers


def _find_datacolumns(text: str) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    for m in re.finditer(r'"dataColumns"\s*:\s*\[', text):
        start = m.end() - 1
        depth = 0
        for j in range(start, min(start + 500_000, len(text))):
            ch = text[j]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        out.append(json.loads(text[start : j + 1]))
                    except json.JSONDecodeError:
                        pass
                    break
    return out


def parse_monthly_actuals(bootstrap_text: str, *, workbook_year: int) -> dict[str, float]:
    """Extract ``{YYYY-MM-01: passengers}`` Actual Passengers from a Passengers boot."""
    points: dict[str, float] = {}
    for block in _find_datacolumns(bootstrap_text):
        cstrings: list[str] = []
        for col in block:
            if col.get("dataType") == "cstring":
                cstrings.extend(str(x) for x in (col.get("dataValues") or []))
        # Longest run of full month names (dashboard often lists months desc).
        best: list[str] = []
        run: list[str] = []
        for s in cstrings:
            key = s.strip().lower()
            if key in _MONTH:
                run.append(s.strip())
            else:
                if len(run) > len(best):
                    best = run
                run = []
        if len(run) > len(best):
            best = run
        if len(best) < 3:
            continue

        # Prefer formatted integers immediately after the month run in cstring.
        try:
            start = cstrings.index(best[0])
        except ValueError:
            continue
        # Walk forward from after the month block for formatted numbers.
        nums: list[float] = []
        for s in cstrings[start + len(best) :]:
            m = _FMT_NUM_RE.match(s.strip())
            if m:
                nums.append(float(m.group(1).replace(",", "")))
                if len(nums) >= len(best):
                    break
            elif nums:
                # stop at first non-number once we've started collecting
                break
        if len(nums) < len(best):
            # Fallback: reals that look like passenger counts, same length.
            reals: list[float] = []
            for col in block:
                if col.get("dataType") != "real":
                    continue
                for v in col.get("dataValues") or []:
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if 1_000 <= fv <= 2_000_000:
                        reals.append(fv)
            if len(reals) >= len(best):
                nums = reals[: len(best)]
        if len(nums) < len(best):
            continue

        # Year: prefer an explicit year token in cstrings near the block, else workbook.
        year = workbook_year
        for s in cstrings[start : start + len(best) + len(nums) + 8]:
            if re.fullmatch(r"20\d{2}", s.strip()):
                year = int(s.strip())
                break

        for name, val in zip(best, nums):
            mo = _MONTH[name.lower()]
            points[f"{year:04d}-{mo:02d}-01"] = float(val)
    return points


def fetch_workbook_monthly(workbook: str) -> tuple[dict[str, float], dict[str, Any]]:
    opener = _opener()
    view = (
        f"https://public.tableau.com/views/{workbook}/{SHEET_PATH}"
        "?:display_static_image=y&:bootstrapWhenNotified=true&:embed=true"
        "&:language=en-US&:showVizHome=n"
    )
    _call(opener, view)
    ss = (
        f"https://public.tableau.com/vizql/w/{workbook}/v/{SHEET_PATH}/startSession/viewing?"
        + urllib.parse.urlencode(
            {
                ":display_static_image": "y",
                ":bootstrapWhenNotified": "true",
                ":embed": "true",
                ":language": "en-US",
                ":showVizHome": "n",
                ":redirect": "auth",
            }
        )
    )
    raw, hdrs = _call(
        opener, ss, data=b"", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    cfg = json.loads(raw.decode("utf-8"))
    sid = cfg["sessionid"]
    gsh = hdrs.get("Global-Session-Header") or hdrs.get("global-session-header") or ""
    form = {
        "worksheetPortSize": json.dumps({"w": 1366, "h": 768}),
        "dashboardPortSize": json.dumps({"w": 1366, "h": 768}),
        "clientDimension": json.dumps({"w": 1400, "h": 820}),
        "renderMapsClientSide": "true",
        "isBrowserRendering": "true",
        "browserRenderingThreshold": "100",
        "formatDataValueLocally": "false",
        "clientSupportsAnimation": "true",
        "sheet_id": SHEET_ID,
        "showParams": json.dumps(
            {"checkpoint": False, "refresh": False, "unknownParams": ""}
        ),
        "stickySessionKey": cfg["stickySessionKey"],
        "filterTileSize": "200",
        "locale": "en_US",
        "language": "en",
        "verboseMode": "false",
        ":session_feature_flags": "{}",
        "keychain_version": "1",
    }
    boot_url = (
        f"https://public.tableau.com/vizql/w/{workbook}/v/{SHEET_PATH}"
        f"/bootstrapSession/sessions/{sid}"
    )
    body, _ = _call(
        opener,
        boot_url,
        data=urllib.parse.urlencode(form).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Global-Session-Header": gsh,
        },
    )
    text = body.decode("utf-8", "replace")
    m = re.search(r"(20\d{2})", workbook)
    workbook_year = int(m.group(1)) if m else datetime.now(timezone.utc).year
    pts = parse_monthly_actuals(text, workbook_year=workbook_year)
    meta = {
        "workbook": workbook,
        "session": sid,
        "n_months": len(pts),
        "as_of": max(pts) if pts else None,
        "allow_export_data": cfg.get("allow_export_data"),
    }
    if not pts:
        raise RuntimeError(f"no Seattle passenger months parsed from {workbook}")
    return pts, meta


def fetch_all(
    workbooks: tuple[str, ...] | list[str] = WORKBOOKS,
) -> tuple[dict[str, float], list[str]]:
    merged: dict[str, float] = {}
    notes: list[str] = []
    for wb in workbooks:
        try:
            pts, meta = fetch_workbook_monthly(wb)
            # Newer workbook listed first → apply reversed so newest wins.
            notes.append(
                f"ok {wb} months={meta['n_months']} as_of={meta.get('as_of')}"
            )
            # stash for ordered merge
            merged = {**pts, **merged} if not merged else {**merged, **pts}
            # Prefer later workbook values on overlap: since we iterate newest-first,
            # update with pts after so newest overwrites.
            merged.update(pts)
        except Exception as e:
            notes.append(f"skip {wb}: {e}")
    if not merged:
        raise RuntimeError("Seattle Tableau passenger scrape empty: " + "; ".join(notes))
    return merged, notes


def load_history(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in (data.get("points") or {}).items()}


def save_history(path: Path, points: dict[str, float], *, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "points": {k: points[k] for k in sorted(points)},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh_monthly(
    *,
    history_path: Path,
    start: str = "2015-01-01",
    workbooks: tuple[str, ...] | list[str] = WORKBOOKS,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    prior = load_history(history_path)
    scraped, notes = fetch_all(workbooks)
    merged = {**prior, **scraped}
    meta = {
        "n_months": len(merged),
        "scrape_notes": notes,
        "source": "Port of Seattle Tableau Public — Cruise Operation & Marketing KPI / Passengers",
        "as_of": max(merged) if merged else None,
    }
    save_history(history_path, merged, meta=meta)
    pts = [
        {"date": d, "value": v}
        for d, v in sorted(merged.items())
        if d >= start
    ]
    return pts, meta
