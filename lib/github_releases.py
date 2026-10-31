"""GitHub Releases API — weekly published-release counts per repo.

Uses public REST ``/repos/{owner}/{repo}/releases`` (paginated). Optional
``GITHUB_TOKEN`` / ``GH_TOKEN`` raises rate limits. Drafts skipped; prereleases
counted (cadence signal). Point date = ISO week Monday (UTC).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.github_releases)"
API = "https://api.github.com"


def _token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _http_get_json(url: str) -> Any:
    headers = {
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_releases(repo: str, *, max_pages: int = 20) -> list[dict[str, Any]]:
    """Return release objects newest-first (up to max_pages * 100)."""
    repo = repo.strip().strip("/")
    if "/" not in repo:
        raise ValueError(f"repo must be owner/name, got {repo!r}")
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = f"{API}/repos/{repo}/releases?per_page=100&page={page}"
        try:
            batch = _http_get_json(url)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"GitHub releases {repo} HTTP {e.code}: {body}") from e
        if not isinstance(batch, list) or not batch:
            break
        out.extend(r for r in batch if isinstance(r, dict))
        if len(batch) < 100:
            break
    return out


def fetch_repo(repo: str) -> dict[str, Any]:
    """Public repo metadata (stars, etc.)."""
    repo = repo.strip().strip("/")
    if "/" not in repo:
        raise ValueError(f"repo must be owner/name, got {repo!r}")
    return _http_get_json(f"{API}/repos/{repo}")


def append_weekly_stars(
    history_path: str | Path,
    repo: str,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Append Monday-week stargazers_count snapshot; denser print wins same week."""
    path = Path(history_path)
    mon = _week_monday(as_of or datetime.now(timezone.utc).date())
    # Drop in-progress week — stamp prior Monday.
    today = datetime.now(timezone.utc).date()
    if mon >= _week_monday(today) and today.weekday() != 0:
        mon = mon - timedelta(days=7)
    meta = fetch_repo(repo)
    stars = float(meta.get("stargazers_count") or 0)
    prev: list[dict[str, Any]] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                prev = raw
        except Exception:
            prev = []
    by = {str(p.get("date")): float(p.get("value") or 0) for p in prev if p.get("date")}
    by[mon.isoformat()] = stars
    pts = [{"date": d, "value": by[d]} for d in sorted(by)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pts, indent=2) + "\n", encoding="utf-8")
    return pts


def weekly_release_counts(
    repo: str,
    *,
    start: str | None = None,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Bucket non-draft releases by week Monday → ``[{date, value}, ...]`` ascending."""
    releases = list_releases(repo, max_pages=max_pages)
    start_d = date.fromisoformat(start[:10]) if start else None
    counts: dict[str, int] = {}
    for rel in releases:
        if rel.get("draft"):
            continue
        pub = rel.get("published_at") or rel.get("created_at")
        if not pub:
            continue
        try:
            dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
        except ValueError:
            continue
        d = dt.astimezone(timezone.utc).date()
        if start_d and d < start_d:
            continue
        mon = _week_monday(d).isoformat()
        counts[mon] = counts.get(mon, 0) + 1

    if not counts:
        return []

    # Dense weekly from first to last (zeros for quiet weeks).
    keys = sorted(counts)
    first = date.fromisoformat(keys[0])
    last = date.fromisoformat(keys[-1])
    # Cap trailing incomplete week: drop current week if still in progress.
    today = datetime.now(timezone.utc).date()
    cur_mon = _week_monday(today)
    if last >= cur_mon:
        last = cur_mon - timedelta(days=7)
    if last < first:
        return []

    pts: list[dict[str, Any]] = []
    cur = first
    while cur <= last:
        ds = cur.isoformat()
        pts.append({"date": ds, "value": float(counts.get(ds, 0))})
        cur += timedelta(days=7)
    return pts
