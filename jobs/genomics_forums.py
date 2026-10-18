#!/usr/bin/env python3
"""Genomics sequencing forums sidecar — Arctic Shift weekly mentions + digest.

Pack: genomics_forums. Theme: Genomics '25: Sequencing Platforms & Instruments.
Subs: r/bioinformatics (products/category/sentiment) + r/genomics (community).
Not in backdrop scores. Soft-fails if Arctic Shift is down.
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
from collections import defaultdict
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
PACK = "genomics_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+genomics_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "genomics_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
# Chart window: both subs need overlapping weeks. Quiet r/genomics Arctic Shift
# tails go back years and make the community stack look empty-then-cliff.
# ~52w is enough for Level; YoY tabs wait until history fills in.
_SERIES_FROM = "2025-01-01"

# Lexicon → series_key (posts can hit multiple).
LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    (
        "Illumina",
        re.compile(r"\billumina\b|novaseq|nextseq|\bmiseq\b|\bdragen\b", re.I),
        "bioinformatics_illumina_mentions",
    ),
    (
        "PacBio",
        re.compile(
            r"\bpacbio\b|pacific\s*biosciences|\brevio\b|hifi\s*sequenc",
            re.I,
        ),
        "bioinformatics_pacbio_mentions",
    ),
    (
        "Nanopore",
        re.compile(
            r"nanopore|oxford\s*nanopore|\bont\b|minion|gridion|promethion",
            re.I,
        ),
        "bioinformatics_nanopore_mentions",
    ),
    (
        "MGI",
        re.compile(r"\bmgi\b|dnbseq|complete\s*genomics", re.I),
        "bioinformatics_mgi_mentions",
    ),
    (
        "NGS",
        re.compile(
            r"\bngs\b|next[\s\-]?gen(?:eration)?\s*sequenc|"
            r"short[\s\-]?read|long[\s\-]?read|whole[\s\-]?genome\s*sequenc|"
            r"\bwgs\b|\brna[\s\-]?seq\b",
            re.I,
        ),
        "bioinformatics_ngs_mentions",
    ),
    ("Ion Torrent", re.compile(r"ion\s*torrent|\bgenexus\b", re.I), "software"),
    ("AXELIOS", re.compile(r"\baxelios\b", re.I), "software"),
    ("sequencer", re.compile(r"\bsequencer(s)?\b|flow\s*cell", re.I), "software"),
]

SOFTWARE_TAGS = {name for name, _, _ in LEXICON}

# Lightweight product-talk polarity (not a general NLP model).
_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|impressive|game[\s-]?changer|worth it|switched to|"
    r"recommend|recommended|must[\s-]?have|accurate|helpful|faster|saves? time|"
    r"works well|working well|solid|underrated|best in class|prefer|preferred|"
    r"reliable|robust|clean\s*reads|high[\s-]?quality|finally|killer|fire|goated"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|useless|overpriced|expensive|"
    r"inaccurate|error[\s-]?prone|noisy|broken|buggy|unreliable|unstable|"
    r"don't buy|do not buy|avoid|disappoint|scam|nightmare|worse than|"
    r"switched (away|off|from)|cancelled|canceled|churn|layoffs?"
    r")\b",
    re.I,
)
_BRANDS = ("Illumina", "PacBio", "Nanopore", "MGI", "NGS")
_NAMED = ("Illumina", "PacBio", "Nanopore", "MGI")


def _sentiment(text: str) -> float:
    """Return polarity in [-1, 1] from title+body lexicon hits."""
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
    """ISO-ish week bucket: Sunday end date (YYYY-MM-DD)."""
    # weekday: Mon=0 … Sun=6 → days until Sunday
    ahead = 6 - d.weekday()
    end = d + timedelta(days=ahead)
    return end.isoformat()


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
        data = _arctic_get(params)
        batch = data.get("data") or []
        if not batch:
            break
        out.extend(batch)
        before = min(
            float(p["created_utc"])
            for p in batch
            if p.get("created_utc") is not None
        )
        time.sleep(0.45)
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
    return {
        "week_ending": we,
        "subreddit": sub,
        "total_posts": 0,
        "software_mentions": 0,
        "illumina": 0,
        "pacbio": 0,
        "nanopore": 0,
        "mgi": 0,
        "named_products": 0,
        "ngs": 0,
        "_sent_software": [],
        "_sent_illumina": [],
        "_sent_pacbio": [],
        "_sent_nanopore": [],
        "_sent_mgi": [],
        "_sent_named_products": [],
        "_sent_ngs": [],
        "digest_candidates": [],
    }


def aggregate_weeks(
    posts: list[dict[str, Any]],
    *,
    sub: str,
) -> dict[str, dict[str, Any]]:
    """week_ending -> counts + brand sentiment means + digest candidates."""
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
        bucket["software_mentions"] += 1
        bucket["_sent_software"].append(sent)
        if "Illumina" in tags:
            bucket["illumina"] += 1
            bucket["_sent_illumina"].append(sent)
        if "PacBio" in tags:
            bucket["pacbio"] += 1
            bucket["_sent_pacbio"].append(sent)
        if "Nanopore" in tags:
            bucket["nanopore"] += 1
            bucket["_sent_nanopore"].append(sent)
        if "MGI" in tags:
            bucket["mgi"] += 1
            bucket["_sent_mgi"].append(sent)
        if set(_NAMED) & set(tags):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        if "NGS" in tags:
            bucket["ngs"] += 1
            bucket["_sent_ngs"].append(sent)
        title = (p.get("title") or "").strip()
        if not title:
            continue
        permalink = p.get("permalink") or ""
        url = (
            f"https://www.reddit.com{permalink}"
            if permalink.startswith("/")
            else (permalink or None)
        )
        brands = [t for t in tags if t in _BRANDS]
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
            ("software_sentiment", "_sent_software"),
            ("illumina_sentiment", "_sent_illumina"),
            ("pacbio_sentiment", "_sent_pacbio"),
            ("nanopore_sentiment", "_sent_nanopore"),
            ("mgi_sentiment", "_sent_mgi"),
            ("platform_sentiment", "_sent_named_products"),
            ("ngs_sentiment", "_sent_ngs"),
        ):
            vals = bucket.pop(acc, [])
            bucket[field] = (
                round(sum(vals) / len(vals), 4) if vals else None
            )
        cands = bucket.pop("digest_candidates")
        # Prefer brand-tagged + extreme sentiment for digest substance
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


_NAMED_SENT_WEIGHTS = (
    ("illumina", "illumina_sentiment"),
    ("pacbio", "pacbio_sentiment"),
    ("nanopore", "nanopore_sentiment"),
    ("mgi", "mgi_sentiment"),
)


def _ensure_platform_sentiment(row: dict[str, Any]) -> None:
    """Fill platform_sentiment from unique-post mean, or brand-weighted fallback."""
    if row.get("platform_sentiment") is not None:
        return
    num = den = 0.0
    for count_k, sent_k in _NAMED_SENT_WEIGHTS:
        c = float(row.get(count_k) or 0)
        s = row.get(sent_k)
        if c > 0 and s is not None:
            num += c * float(s)
            den += c
    if den > 0:
        row["platform_sentiment"] = round(num / den, 4)


def _points_from_history(
    rows: list[dict[str, Any]], *, sub: str, field: str
) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for r in rows:
        if r.get("subreddit") != sub:
            continue
        we = r.get("week_ending")
        val = r.get(field)
        if not we or val is None or str(we) < _SERIES_FROM:
            continue
        pts.append({"date": str(we), "value": float(val)})
    pts.sort(key=lambda p: p["date"])
    return pts


def _prune_pre_series_history(by: dict[str, dict[str, Any]]) -> int:
    """Drop pre-window archive weeks so community stacks stay honest."""
    drop = [k for k, r in by.items() if str(r.get("week_ending") or "") < _SERIES_FROM]
    for k in drop:
        del by[k]
    return len(drop)


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

    # Enough posts to cover _SERIES_FROM→now on busy r/bioinformatics (~80/wk).
    if history_only:
        notes.append("history_only: skipped Arctic Shift fetch")
    else:
        for sub, n in (
            ("bioinformatics", backfill_posts),
            ("genomics", max(800, min(2000, backfill_posts // 3))),
        ):
            try:
                posts = fetch_subreddit(sub, max_posts=n)
                weeks = aggregate_weeks(posts, sub=sub)
                for we, row in weeks.items():
                    key = f"{sub}|{we}"
                    prev = by.get(key)
                    # Always refresh current week; else keep denser prior scrape
                    if (
                        prev
                        and we != _week_ending_sunday(datetime.now(timezone.utc).date())
                        and int(prev.get("total_posts") or 0)
                        > int(row.get("total_posts") or 0)
                        and prev.get("platform_sentiment") is not None
                    ):
                        continue
                    by[key] = row
                notes.append(
                    f"{sub}: fetched {len(posts)} posts, {len(weeks)} week buckets"
                )
            except Exception as e:
                errors.append(f"{sub}: {e}")

    pruned = _prune_pre_series_history(by)
    if pruned:
        notes.append(
            f"pruned {pruned} week rows before {_SERIES_FROM} "
            "(quiet-sub Arctic Shift tail — not continuous comparable history)"
        )
    for row in by.values():
        _ensure_platform_sentiment(row)
    _write_history(history_path, by)
    rows = list(by.values())

    series_map = {
        "bioinformatics_sequencing_mentions": ("bioinformatics", "software_mentions"),
        "bioinformatics_illumina_mentions": ("bioinformatics", "illumina"),
        "bioinformatics_pacbio_mentions": ("bioinformatics", "pacbio"),
        "bioinformatics_nanopore_mentions": ("bioinformatics", "nanopore"),
        "bioinformatics_mgi_mentions": ("bioinformatics", "mgi"),
        "bioinformatics_named_product_mentions": ("bioinformatics", "named_products"),
        "bioinformatics_ngs_mentions": ("bioinformatics", "ngs"),
        "genomics_sequencing_mentions": ("genomics", "software_mentions"),
        "bioinformatics_platform_sentiment": ("bioinformatics", "platform_sentiment"),
        "bioinformatics_illumina_sentiment": ("bioinformatics", "illumina_sentiment"),
        "bioinformatics_pacbio_sentiment": ("bioinformatics", "pacbio_sentiment"),
        "bioinformatics_ngs_sentiment": ("bioinformatics", "ngs_sentiment"),
    }

    series_out: dict[str, Any] = {}
    latest_date = ""
    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key or key not in series_map:
            continue
        sub, field = series_map[key]
        pts = _points_from_history(rows, sub=sub, field=field)
        if not pts:
            errors.append(f"{key}: no points")
            continue
        series_out[key] = ps.series_shell(meta, points=pts, fetched_at=fetched_at)
        latest = series_out[key]["raw"]["latest"]
        if latest and latest.get("date"):
            latest_date = max(latest_date, str(latest["date"]))

    # Thin digest: last 4 complete-ish weeks across both subs
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
                        "software_mentions": r.get("software_mentions"),
                        "total_posts": r.get("total_posts"),
                        "illumina": r.get("illumina"),
                        "pacbio": r.get("pacbio"),
                        "nanopore": r.get("nanopore"),
                        "mgi": r.get("mgi"),
                        "ngs": r.get("ngs"),
                        "platform_sentiment": r.get("platform_sentiment"),
                        "ngs_sentiment": r.get("ngs_sentiment"),
                        "illumina_sentiment": r.get("illumina_sentiment"),
                        "pacbio_sentiment": r.get("pacbio_sentiment"),
                        "software_sentiment": r.get("software_sentiment"),
                    }
                    for r in rows
                    if r.get("week_ending") == we
                },
            }
        )

    if not series_out and errors:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": "Arctic Shift (reddit archive) — r/bioinformatics + r/genomics",
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
            "Stores weekly mention counts + thin titles/links only (no full post bodies). "
            "Not used in theme backdrop scores."
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
        r2lib.put_json(f"{R2_PREFIX}/digest.json", {"digest": payload.get("digest") or []}),
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
    p.add_argument(
        "--backfill-posts",
        type=int,
        default=6000,
        help=(
            "Max posts for r/bioinformatics this run (r/genomics uses a smaller cap). "
            f"Target window from {_SERIES_FROM}; ~6k covers ~1.5y on the busy sub."
        ),
    )
    p.add_argument(
        "--history-only",
        action="store_true",
        help="Rebuild series/digest from weekly_history.jsonl without scraping",
    )
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
