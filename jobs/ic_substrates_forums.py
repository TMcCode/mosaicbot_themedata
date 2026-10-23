#!/usr/bin/env python3
"""IC Substrates Reddit forums sidecar; publish to R2.

Theme: Chips '26: IC Substrates.
Named tech stack (ABF / FC-BGA / glass-core) + substrate category % +
r/semiconductors community volume + combined tech sentiment + thin digest.
Not in backdrop scores.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402

SCHEMA_VERSION = 0
PACK = "ic_substrates_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+ic_substrates_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "ic_substrates_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
_SERIES_FROM = "2024-01-01"
_SUB = "semiconductors"

# Named tech → series_key (posts can hit multiple).
LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    (
        "ABF",
        re.compile(
            r"\babf\b|ajinomoto\s*build[- ]?up|build[- ]?up\s*film",
            re.I,
        ),
        "semis_abf_mentions",
    ),
    (
        "FC-BGA",
        re.compile(
            r"\bfc[- ]?bga\b|flip[- ]?chip\s*bga|flip[- ]?chip\s*ball\s*grid",
            re.I,
        ),
        "semis_fcbga_mentions",
    ),
    (
        "Glass core",
        re.compile(
            r"\bglass[- ]?core\b|\bgcs\b|\btgv\b|through[- ]?glass\s*via|"
            r"glass\s*substrate",
            re.I,
        ),
        "semis_glass_core_mentions",
    ),
    (
        "substrate_category",
        re.compile(
            r"\bic\s*substrate|\bpackage\s*substrate|\bpcb\s*substrate|"
            r"\bsubstrate\s*(?:shortage|capacity|maker|supplier)|"
            r"\bbuild[- ]?up\s*substrate|\babf\s*substrate|"
            r"\bfc[- ]?bga\b|\bglass[- ]?core\b",
            re.I,
        ),
        "software",
    ),
]

_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|recommend|recommended|"
    r"worth it|powerful|useful|game[-\s]?changer|impressive|"
    r"best|solid|underrated|productive|breakthrough|ramping|ramp"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|scam|refund|"
    r"bug|buggy|broken|unreliable|shortage|bottleneck|constrain|"
    r"overpriced|expensive|avoid|disappoint|never\s*again|worst|"
    r"nightmare|rip\-?off|delay|delays|sold\s*out"
    r")\b",
    re.I,
)
_NAMED = ("ABF", "FC-BGA", "Glass core")
_NAMED_COUNT_KEYS = ("abf", "fcbga", "glass_core")
_TOPIC_COUNT_KEYS = ("substrate_category",)


def _sentiment(text: str) -> float:
    if not text:
        return 0.0
    pos = len(_POS.findall(text))
    neg = len(_NEG.findall(text))
    if pos == 0 and neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def _sentiment_label(score: float) -> str:
    if score >= 0.2:
        return "positive"
    if score <= -0.2:
        return "negative"
    return "mixed"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_attachments() -> dict:
    attachments = (
        _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    )
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
            attachments = remote
    except Exception:
        pass
    return attachments


def _week_ending_sunday(d: date) -> str:
    ahead = 6 - d.weekday()
    return (d + timedelta(days=ahead)).isoformat()


def _hits(text: str) -> list[str]:
    return [name for name, rx, _ in LEXICON if rx.search(text)]


def _blob(post: dict[str, Any]) -> str:
    return " ".join(
        filter(None, [post.get("title") or "", post.get("selftext") or ""])
    )


def _arctic_get(params: dict[str, Any], *, retries: int = 4) -> dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    url = f"{ARCTIC}?{qs}"
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(1.2 * (i + 1))
    assert last is not None
    raise last


def fetch_subreddit(sub: str, *, max_posts: int = 600) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    before: float | None = None
    while len(out) < max_posts:
        params: dict[str, Any] = {
            "subreddit": sub,
            "limit": 100,
            "sort": "desc",
        }
        if before is not None:
            params["before"] = str(int(before))
        try:
            data = _arctic_get(params)
        except urllib.error.HTTPError as e:
            if out and e.code in (422, 429, 502, 503):
                break
            raise
        batch = data.get("data") or []
        if not batch:
            break
        out.extend(batch)
        before = min(
            float(p["created_utc"])
            for p in batch
            if p.get("created_utc") is not None
        )
        time.sleep(0.55)
        if len(batch) < 100:
            break
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for p in out:
        pid = str(p.get("id") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        uniq.append(p)
    return uniq


def _post_ts(post: dict[str, Any]) -> datetime | None:
    t = post.get("created_utc")
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=timezone.utc)


def _empty_bucket(we: str) -> dict[str, Any]:
    return {
        "week_ending": we,
        "subreddit": _SUB,
        "total_posts": 0,
        "abf": 0,
        "fcbga": 0,
        "glass_core": 0,
        "named_tech": 0,
        "substrate_category": 0,
        "_sent_named": [],
        "_sent_category": [],
        "digest_candidates": [],
    }


def aggregate_weeks(posts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    weeks: dict[str, dict[str, Any]] = {}
    for p in posts:
        dt = _post_ts(p)
        if not dt:
            continue
        we = _week_ending_sunday(dt.date())
        bucket = weeks.setdefault(we, _empty_bucket(we))
        bucket["total_posts"] += 1
        text = _blob(p)
        tags = _hits(text)
        if not tags:
            continue
        sent = _sentiment(text)
        if "ABF" in tags:
            bucket["abf"] += 1
        if "FC-BGA" in tags:
            bucket["fcbga"] += 1
        if "Glass core" in tags:
            bucket["glass_core"] += 1
        named_hit = bool(set(_NAMED) & set(tags))
        if named_hit:
            bucket["named_tech"] += 1
            bucket["_sent_named"].append(sent)
        if "substrate_category" in tags:
            bucket["substrate_category"] += 1
            bucket["_sent_category"].append(sent)
        title = (p.get("title") or "").strip()
        if not title:
            continue
        permalink = p.get("permalink") or ""
        url = (
            f"https://www.reddit.com{permalink}"
            if permalink.startswith("/")
            else (permalink or None)
        )
        brands = [t for t in tags if t in _NAMED]
        bucket["digest_candidates"].append(
            {
                "date": dt.date().isoformat(),
                "title": title[:160],
                "score": int(p.get("score") or 0),
                "comments": int(p.get("num_comments") or 0),
                "hits": tags,
                "brands": brands,
                "sentiment": round(sent, 3),
                "sentiment_label": _sentiment_label(sent),
                "url": url,
                "flair": p.get("link_flair_text"),
            }
        )
    for bucket in weeks.values():
        named_vals = bucket.pop("_sent_named", [])
        cat_vals = bucket.pop("_sent_category", [])
        bucket["tech_sentiment"] = (
            round(sum(named_vals) / len(named_vals), 4) if named_vals else None
        )
        bucket["category_sentiment"] = (
            round(sum(cat_vals) / len(cat_vals), 4) if cat_vals else None
        )
        cands = bucket.pop("digest_candidates")
        cands.sort(
            key=lambda x: (
                1 if x.get("brands") else 0,
                abs(float(x.get("sentiment") or 0)),
                x.get("score") or 0,
                x.get("comments") or 0,
            ),
            reverse=True,
        )
        bucket["digest"] = cands[:8]
    return weeks


def _load_history(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    by: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("subreddit") != _SUB:
            continue
        key = f"{row.get('subreddit')}|{row.get('week_ending')}"
        by[key] = row
    return by


def _write_history(path: Path, by: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        by.values(),
        key=lambda r: (r.get("subreddit") or "", r.get("week_ending") or ""),
    )
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def _points_count(rows: list[dict[str, Any]], field: str) -> list[dict[str, float]]:
    pts = [
        {"date": str(r["week_ending"]), "value": float(r.get(field) or 0)}
        for r in rows
        if r.get("week_ending") and str(r["week_ending"]) >= _SERIES_FROM
    ]
    pts.sort(key=lambda p: p["date"])
    return pts


def _points_category_pct(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for r in rows:
        we = r.get("week_ending")
        total = float(r.get("total_posts") or 0)
        if not we or total <= 0 or str(we) < _SERIES_FROM:
            continue
        hit = float(r.get("substrate_category") or 0)
        pts.append({"date": str(we), "value": round(100.0 * hit / total, 2)})
    pts.sort(key=lambda p: p["date"])
    return pts


def _points_sentiment(
    rows: list[dict[str, Any]], field: str
) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for r in rows:
        we = r.get("week_ending")
        val = r.get(field)
        if not we or val is None or str(we) < _SERIES_FROM:
            continue
        pts.append({"date": str(we), "value": float(val)})
    pts.sort(key=lambda p: p["date"])
    return pts


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    out_dir: Path,
    backfill_posts: int,
    history_only: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    history_path = out_dir / _HISTORY
    by = _load_history(history_path)
    errors: list[str] = []
    notes: list[str] = []

    if history_only:
        notes.append("history_only: skipped Arctic Shift fetch")
    else:
        try:
            posts = fetch_subreddit(_SUB, max_posts=max(4000, backfill_posts))
            weeks = aggregate_weeks(posts)
            cur = _week_ending_sunday(datetime.now(timezone.utc).date())
            for we, row in weeks.items():
                key = f"{_SUB}|{we}"
                prev = by.get(key)
                prev_complete = (
                    prev
                    and all(k in prev for k in _NAMED_COUNT_KEYS)
                    and all(k in prev for k in _TOPIC_COUNT_KEYS)
                )
                if (
                    prev_complete
                    and we != cur
                    and int(prev.get("total_posts") or 0)
                    > int(row.get("total_posts") or 0)
                    and prev.get("tech_sentiment") is not None
                ):
                    continue
                by[key] = row
            notes.append(
                f"{_SUB}: fetched {len(posts)} posts, {len(weeks)} week buckets"
            )
        except Exception as e:
            errors.append(f"{_SUB}: {e}")

    drop = [k for k, r in by.items() if r.get("subreddit") != _SUB]
    for k in drop:
        del by[k]
    if drop:
        notes.append(f"dropped {len(drop)} non-{_SUB} history rows")

    _write_history(history_path, by)
    rows = list(by.values())

    series_builders = {
        "semis_abf_mentions": lambda r: _points_count(r, "abf"),
        "semis_fcbga_mentions": lambda r: _points_count(r, "fcbga"),
        "semis_glass_core_mentions": lambda r: _points_count(r, "glass_core"),
        "ic_substrates_named_mentions": lambda r: _points_count(r, "named_tech"),
        "substrate_category_mentions": _points_category_pct,
        "ic_substrates_semiconductors_community_posts": lambda r: _points_count(
            r, "total_posts"
        ),
        "ic_substrates_tech_sentiment": lambda r: _points_sentiment(
            r, "tech_sentiment"
        ),
        "substrate_category_sentiment": lambda r: _points_sentiment(
            r, "category_sentiment"
        ),
    }

    series_out: dict[str, Any] = {}
    latest_date = ""
    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key or key not in series_builders:
            continue
        pts = series_builders[key](rows)
        if not pts:
            errors.append(f"{key}: no points")
            continue
        series_out[key] = ps.series_shell(meta, points=pts, fetched_at=fetched_at)
        latest = series_out[key]["raw"]["latest"]
        if latest and latest.get("date"):
            latest_date = max(latest_date, str(latest["date"]))

    digest_weeks: list[dict[str, Any]] = []
    week_ends = sorted(
        {r.get("week_ending") for r in rows if r.get("week_ending")}, reverse=True
    )[:4]
    for we in week_ends:
        entries = []
        for r in rows:
            if r.get("week_ending") != we:
                continue
            for item in r.get("digest") or []:
                entries.append({**item, "subreddit": r.get("subreddit")})
        entries.sort(
            key=lambda x: (x.get("score") or 0, x.get("comments") or 0), reverse=True
        )
        digest_weeks.append({"week_ending": we, "top_posts": entries[:10]})

    if not series_out and not history_only:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "Arctic Shift — r/semiconductors "
            "(ABF / FC-BGA / glass-core + substrate category % + volume)"
        ),
        "fetched_at": fetched_at,
        "as_of": latest_date,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "digest": digest_weeks,
        "errors": errors,
        "notes": notes,
        "provenance_notes": (
            "Best-effort scrape via Arctic Shift; not official Reddit API. "
            "Stores weekly counts + thin titles/links only. Not in backdrop scores."
        ),
    }


def _write_local(out_dir: Path, payload: dict) -> None:
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
    raw_dir = out_dir / "raw"
    der_dir = out_dir / "derived"
    raw_dir.mkdir(exist_ok=True)
    der_dir.mkdir(exist_ok=True)
    for key, ser in (payload.get("series") or {}).items():
        (raw_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "key": key,
                    "label": ser.get("label"),
                    "display_name": ser.get("display_name"),
                    "frequency": ser.get("frequency"),
                    "unit": ser.get("unit"),
                    "points": ser["raw"]["points"],
                    "latest": ser["raw"]["latest"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (der_dir / f"{key}.json").write_text(
            json.dumps({"key": key, "derived": ser.get("derived")}, indent=2) + "\n",
            encoding="utf-8",
        )
    (out_dir / "digest.json").write_text(
        json.dumps({"digest": payload.get("digest") or []}, indent=2) + "\n",
        encoding="utf-8",
    )


def _publish_r2(payload: dict) -> list[str]:
    uris = [
        r2lib.put_json(f"{R2_PREFIX}/latest.json", payload),
        r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]),
        r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]),
        r2lib.put_json(
            f"{R2_PREFIX}/digest.json", {"digest": payload.get("digest") or []}
        ),
    ]
    for key, ser in (payload.get("series") or {}).items():
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
    slim = {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "fetched_at": payload.get("fetched_at"),
        "as_of": payload.get("as_of"),
        "metrics": {
            key: {
                "raw_latest": ser["raw"]["latest"],
                "derived_latest": {
                    name: meta.get("latest")
                    for name, meta in (ser.get("derived") or {}).items()
                },
            }
            for key, ser in (payload.get("series") or {}).items()
        },
    }
    uris.append(r2lib.put_json(f"{R2_PREFIX}/metrics_latest.json", slim))
    return uris


def run(
    *,
    out_dir: Path,
    dry_run: bool = False,
    skip_r2: bool = False,
    backfill_posts: int = 6000,
    history_only: bool = False,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        out_dir=out_dir,
        backfill_posts=backfill_posts,
        history_only=history_only,
    )
    if dry_run:
        return payload
    _write_local(out_dir, payload)
    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.extend(_publish_r2(payload))
        try:
            r2_uris.append(
                r2lib.put_json(
                    "themedata/series_attachments.json", payload["attachments"]
                )
            )
        except Exception:
            pass
    payload["_r2_uris"] = r2_uris
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument("--history-only", action="store_true")
    p.add_argument("--backfill-posts", type=int, default=6000)
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            backfill_posts=args.backfill_posts,
            history_only=args.history_only,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "series": {
            k: (v.get("raw") or {}).get("latest")
            for k, v in (payload.get("series") or {}).items()
        },
        "digest_weeks": len(payload.get("digest") or []),
        "errors": payload.get("errors") or [],
        "notes": payload.get("notes") or [],
        "r2": payload.get("_r2_uris") or [],
    }
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
