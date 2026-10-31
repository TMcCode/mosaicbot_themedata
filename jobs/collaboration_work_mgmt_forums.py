#!/usr/bin/env python3
"""Collaboration / work-management Reddit forums sidecar.

Pack: collaboration_work_mgmt_forums.
Theme: Enterprise Software '24: Collaboration, Productivity & Work Management.
Category talk (work management / AI agents / vendor consolidation) + Atlassian /
monday / Asana / Box names + r/projectmanagement + r/asana volume. Chart-only.
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
PACK = "collaboration_work_mgmt_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+collaboration_work_mgmt_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "collaboration_work_mgmt_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
_SERIES_FROM = "2024-01-01"

_WATCHED_SUBS = ("projectmanagement", "asana")

LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    (
        "Atlassian",
        re.compile(
            r"\batlassian\b|\bjira\b|\bconfluence\b|\brovo\b|"
            r"\bteamwork\s*graph\b|\btrello\b",
            re.I,
        ),
        "cwm_atlassian_mentions",
    ),
    (
        "monday",
        re.compile(r"\bmonday\.com\b|\bmonday\s*ai\b|\bmndy\b", re.I),
        "cwm_monday_mentions",
    ),
    (
        "Asana",
        re.compile(r"\basana\b", re.I),
        "cwm_asana_mentions",
    ),
    (
        "Box",
        re.compile(
            r"\bbox\s*ai\b|\bbox\.com\b|\bgetbox\b|\bdropbox\b|\bdbx\b",
            re.I,
        ),
        "cwm_box_mentions",
    ),
    (
        "CWM talk",
        re.compile(
            r"\bwork\s*management\b|\bproject\s*management\b|"
            r"\bcollaborative\s*work\b|\bagentic\b|\bAI\s*agent\b|"
            r"\bcopilot\b|\bvendor\s*consolidation\b|"
            r"\bseats?\s*(\+|plus|and)\s*credits?\b|"
            r"\bproductivity\s*software\b|\bdigital\s*workplace\b",
            re.I,
        ),
        "cwm_talk_mentions",
    ),
]

_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|impressive|game[\s-]?changer|worth it|"
    r"recommend|recommended|must[\s-]?have|solid|underrated|fire|goated|"
    r"happy|excited|works\s*well|seamless|easy|bullish"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|useless|overpriced|expensive|"
    r"broken|avoid|disappoint|scam|nightmare|worst|ripoff|rip-off|"
    r"bearish|bankrupt|sunk"
    r")\b",
    re.I,
)
_NAMED = ("Atlassian", "monday", "Asana", "Box")
_NAMED_COUNT_KEYS = ("atlassian", "monday", "asana", "box")
_CATEGORY_COUNT_KEYS = ("cwm_talk",)
_NAME_TO_FIELD = {
    "Atlassian": "atlassian",
    "monday": "monday",
    "Asana": "asana",
    "Box": "box",
    "CWM talk": "cwm_talk",
}


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


def _empty_bucket(we: str, sub: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "week_ending": we,
        "subreddit": sub,
        "total_posts": 0,
        "lexicon_hits": 0,
        "named_products": 0,
        "_sent_lexicon": [],
        "_sent_named_products": [],
        "_sent_category": [],
        "digest_candidates": [],
    }
    for k in _NAMED_COUNT_KEYS + _CATEGORY_COUNT_KEYS:
        row[k] = 0
    return row


def aggregate_weeks(
    posts: list[dict[str, Any]],
    *,
    sub: str,
) -> dict[str, dict[str, Any]]:
    weeks: dict[str, dict[str, Any]] = {}
    for p in posts:
        dt = _post_ts(p)
        if not dt:
            continue
        we = _week_ending_sunday(dt.date())
        bucket = weeks.setdefault(we, _empty_bucket(we, sub))
        bucket["total_posts"] += 1
        text = _blob(p)
        tags = _hits(text)
        if not tags:
            continue
        sent = _sentiment(text)
        bucket["lexicon_hits"] += 1
        bucket["_sent_lexicon"].append(sent)
        for tag in tags:
            field = _NAME_TO_FIELD.get(tag)
            if field:
                bucket[field] = int(bucket.get(field) or 0) + 1
        if set(_NAMED) & set(tags):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        if "CWM talk" in tags:
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
        for field, acc in (
            ("lexicon_sentiment", "_sent_lexicon"),
            ("cwm_named_sentiment", "_sent_named_products"),
            ("cwm_talk_sentiment", "_sent_category"),
        ):
            vals = bucket.pop(acc, [])
            bucket[field] = (
                round(sum(vals) / len(vals), 4) if vals else None
            )
        cands = bucket.pop("digest_candidates")
        cands.sort(
            key=lambda x: (
                1 if x.get("brands") else 0,
                1 if "CWM talk" in (x.get("hits") or []) else 0,
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


def _volume_common_start(rows: list[dict[str, Any]]) -> str | None:
    """Latest first-week among watched subs — avoids Arctic dense-scrape cliffs.

    Busy subs (devops) only fill ~N recent weeks from a fixed post budget;
    sparse subs (sre) stretch further back. Stacking raw total_posts then looks
    like a fake late surge. Truncate community volume to the shared window.
    """
    first: dict[str, str] = {}
    for r in rows:
        sub = str(r.get("subreddit") or "")
        if sub not in _WATCHED_SUBS:
            continue
        we = str(r.get("week_ending") or "")
        if not we or we < _SERIES_FROM:
            continue
        if r.get("total_posts") is None:
            continue
        prev = first.get(sub)
        if prev is None or we < prev:
            first[sub] = we
    if len(first) < len(_WATCHED_SUBS):
        return None
    return max(first.values()) if first else None


def _points_from_history(
    rows: list[dict[str, Any]],
    *,
    sub: str,
    field: str,
    start_date: str | None = None,
) -> list[dict[str, float]]:
    by_week: dict[str, list[float]] = {}
    floor = start_date if start_date and start_date > _SERIES_FROM else _SERIES_FROM
    for r in rows:
        if sub != "*" and r.get("subreddit") != sub:
            continue
        we = r.get("week_ending")
        val = r.get(field)
        if not we or val is None or str(we) < floor:
            continue
        by_week.setdefault(str(we), []).append(float(val))
    # Sparse brand/category mention counts: emit only weeks with a hit.
    # Keep full weekly spine for community volume (total_posts) + sentiment.
    sparse_counts = field in _NAMED_COUNT_KEYS + _CATEGORY_COUNT_KEYS
    # Week-ending Sunday can sit a few days in the future for the current
    # incomplete week — drop those so charts never show fake "Scheduled".
    today = datetime.now(timezone.utc).date().isoformat()
    pts: list[dict[str, float]] = []
    for we, vals in by_week.items():
        if str(we) > today:
            continue
        if "sentiment" in field:
            pts.append({"date": we, "value": round(sum(vals) / len(vals), 4)})
        else:
            total = float(sum(vals))
            if sparse_counts and total <= 0:
                continue
            pts.append({"date": we, "value": total})
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
        for sub in _WATCHED_SUBS:
            n = max(400, int(backfill_posts))
            try:
                posts = fetch_subreddit(sub, max_posts=n)
                weeks = aggregate_weeks(posts, sub=sub)
                for we, row in weeks.items():
                    key = f"{sub}|{we}"
                    prev = by.get(key)
                    prev_complete = (
                        prev
                        and all(k in prev for k in _NAMED_COUNT_KEYS)
                        and all(k in prev for k in _CATEGORY_COUNT_KEYS)
                    )
                    if (
                        prev_complete
                        and we != _week_ending_sunday(datetime.now(timezone.utc).date())
                        and int(prev.get("total_posts") or 0)
                        > int(row.get("total_posts") or 0)
                    ):
                        continue
                    by[key] = row
                notes.append(
                    f"{sub}: fetched {len(posts)} posts, {len(weeks)} week buckets"
                )
            except Exception as e:
                errors.append(f"{sub}: {e}")

    _write_history(history_path, by)
    rows = list(by.values())

    volume_keys = {
        "cwm_pm_sub_posts",
        "cwm_asana_sub_posts",
    }
    volume_start = _volume_common_start(rows)
    if volume_start:
        notes.append(
            f"community volume truncated to shared dense window start={volume_start} "
            f"(avoids Arctic post-budget cliffs across {_WATCHED_SUBS})"
        )

    series_map = {
        "cwm_atlassian_mentions": ("*", "atlassian"),
        "cwm_monday_mentions": ("*", "monday"),
        "cwm_asana_mentions": ("*", "asana"),
        "cwm_box_mentions": ("*", "box"),
        "cwm_talk_mentions": ("*", "cwm_talk"),
        "cwm_pm_sub_posts": ("projectmanagement", "total_posts"),
        "cwm_asana_sub_posts": ("asana", "total_posts"),
        "cwm_talk_sentiment": ("*", "cwm_talk_sentiment"),
    }
    series_out: dict[str, Any] = {}
    latest_date = ""
    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key or key not in series_map:
            continue
        sub, field = series_map[key]
        start = volume_start if key in volume_keys else None
        pts = _points_from_history(rows, sub=sub, field=field, start_date=start)
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
        digest_weeks.append(
            {
                "week_ending": we,
                "top_posts": entries[:10],
                "counts": {
                    r.get("subreddit"): {
                        "lexicon_hits": r.get("lexicon_hits"),
                        "total_posts": r.get("total_posts"),
                        "named_products": r.get("named_products"),
                        "cwm_talk": r.get("cwm_talk"),
                    }
                    for r in rows
                    if r.get("week_ending") == we
                },
            }
        )

    if not series_out and errors:
        notes.append(
            "soft-fail: Arctic Shift unavailable — publishing empty shell; "
            "retry later (history appends when scrape recovers)"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "Arctic Shift — r/projectmanagement + r/asana; "
            "work-management/agentic category + TEAM/MNDY/ASAN/BOX"
        ),
        "fetched_at": fetched_at,
        "as_of": latest_date or fetched_at[:10],
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "digest": digest_weeks,
        "errors": errors,
        "notes": notes,
        "provenance_notes": (
            "Best-effort scrape via Arctic Shift; not official Reddit API. "
            "Weekly mention counts + thin titles/links only. Not in theme scores."
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
            key: {"raw_latest": ser["raw"]["latest"]}
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
    backfill_posts: int = 2500,
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
    p.add_argument("--backfill-posts", type=int, default=2500)
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
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "series": {
            k: (payload["series"][k]["raw"]["latest"])
            for k in (payload.get("series") or {})
        },
        "digest_weeks": len(payload.get("digest") or []),
        "errors": payload.get("errors") or [],
        "notes": payload.get("notes") or [],
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    if payload.get("errors"):
        print(f"\nWARN: {len(payload['errors'])} issues", file=sys.stderr)
        for err in payload["errors"]:
            print(f"  - {err}", file=sys.stderr)
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
