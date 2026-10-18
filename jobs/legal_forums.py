#!/usr/bin/env python3
"""Legal forums sidecar — Arctic Shift weekly mention counts + thin digest.

Pack: legal_forums. Theme context for Legal & Professional Services Software.
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
PACK = "legal_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+legal_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "legal_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"

# Lexicon → series_key (posts can hit multiple).
LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    ("Harvey", re.compile(r"\bharvey\b", re.I), "legaltech_harvey_mentions"),
    ("Legora", re.compile(r"\blegora\b", re.I), "legaltech_legora_mentions"),
    (
        "AI/genAI",
        re.compile(r"\b(gen(?:erative)?\s*ai|chatgpt|llm|gpt-?\d|claude)\b", re.I),
        "legaltech_ai_mentions",
    ),
    ("CoCounsel", re.compile(r"cocounsel|co-counsel", re.I), "legaltech_cocounsel_mentions"),
    ("Lexis", re.compile(r"lexis\+?|\bprotege\b|protegé", re.I), "legaltech_lexis_mentions"),
    ("Westlaw", re.compile(r"\bwestlaw\b", re.I), "legaltech_westlaw_mentions"),
    ("Clio", re.compile(r"\bclio\b", re.I), "lawfirm_clio_mentions"),
    ("Relativity", re.compile(r"\brelativity\b", re.I), "software"),
    ("eDiscovery", re.compile(r"ediscovery|e-discovery", re.I), "software"),
    (
        "practice mgmt",
        re.compile(r"practice management|mycase|practicepanther|smokeball", re.I),
        "software",
    ),
    ("LegalZoom", re.compile(r"legalzoom|rocket lawyer", re.I), "software"),
    ("Intapp", re.compile(r"\bintapp\b", re.I), "software"),
    ("hallucination", re.compile(r"hallucin", re.I), "software"),
]

SOFTWARE_TAGS = {name for name, _, _ in LEXICON}

# Lightweight product-talk polarity (not a general NLP model).
# Applied at scrape time; we store weekly means only — no full text on R2.
_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|impressive|game[\s-]?changer|worth it|switched to|"
    r"recommend|recommended|must[\s-]?have|accurate|helpful|faster|saves? time|"
    r"works well|working well|solid|underrated|best in class|prefer|preferred|"
    r"finally|killer|fire|goated|indispensable"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|useless|overpriced|expensive|"
    r"hallucin|inaccurate|wrong|broken|buggy|spam|astroturf|shill|"
    r"house of cards|midlaw|don't buy|do not buy|avoid|disappoint|"
    r"scam|ban|banned|sanction|lawsuit|malpractice|risky|nightmare|"
    r"worse than|switched (away|off|from)|cancelled|canceled|churn"
    r")\b",
    re.I,
)
_BRANDS = ("Harvey", "Legora", "CoCounsel", "Lexis", "Westlaw", "Clio", "AI/genAI")


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
        "harvey": 0,
        "legora": 0,
        "cocounsel": 0,
        "lexis": 0,
        "westlaw": 0,
        "named_products": 0,  # unique posts hitting any of the 5 product brands
        "ai": 0,
        "clio": 0,
        # sentiment accumulators (mean taken at end)
        "_sent_software": [],
        "_sent_harvey": [],
        "_sent_legora": [],
        "_sent_cocounsel": [],
        "_sent_lexis": [],
        "_sent_westlaw": [],
        "_sent_named_products": [],  # unique AI-tool posts → ai_tools_sentiment
        "_sent_ai": [],
        "_sent_clio": [],
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
        if "Harvey" in tags:
            bucket["harvey"] += 1
            bucket["_sent_harvey"].append(sent)
        if "Legora" in tags:
            bucket["legora"] += 1
            bucket["_sent_legora"].append(sent)
        if "CoCounsel" in tags:
            bucket["cocounsel"] += 1
            bucket["_sent_cocounsel"].append(sent)
        if "Lexis" in tags:
            bucket["lexis"] += 1
            bucket["_sent_lexis"].append(sent)
        if "Westlaw" in tags:
            bucket["westlaw"] += 1
            bucket["_sent_westlaw"].append(sent)
        if {"Harvey", "Legora", "CoCounsel", "Lexis", "Westlaw"} & set(tags):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        if "AI/genAI" in tags:
            bucket["ai"] += 1
            bucket["_sent_ai"].append(sent)
        if "Clio" in tags:
            bucket["clio"] += 1
            bucket["_sent_clio"].append(sent)
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
            ("harvey_sentiment", "_sent_harvey"),
            ("legora_sentiment", "_sent_legora"),
            ("cocounsel_sentiment", "_sent_cocounsel"),
            ("lexis_sentiment", "_sent_lexis"),
            ("westlaw_sentiment", "_sent_westlaw"),
            ("ai_tools_sentiment", "_sent_named_products"),
            ("ai_sentiment", "_sent_ai"),
            ("clio_sentiment", "_sent_clio"),
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
    ("harvey", "harvey_sentiment"),
    ("legora", "legora_sentiment"),
    ("cocounsel", "cocounsel_sentiment"),
    ("lexis", "lexis_sentiment"),
    ("westlaw", "westlaw_sentiment"),
)


def _ensure_ai_tools_sentiment(row: dict[str, Any]) -> None:
    """Fill ai_tools_sentiment from unique-post mean, or brand-weighted fallback."""
    if row.get("ai_tools_sentiment") is not None:
        return
    num = den = 0.0
    for count_k, sent_k in _NAMED_SENT_WEIGHTS:
        c = float(row.get(count_k) or 0)
        s = row.get(sent_k)
        if c > 0 and s is not None:
            num += c * float(s)
            den += c
    if den > 0:
        row["ai_tools_sentiment"] = round(num / den, 4)


def _points_from_history(
    rows: list[dict[str, Any]], *, sub: str, field: str
) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for r in rows:
        if r.get("subreddit") != sub:
            continue
        we = r.get("week_ending")
        val = r.get(field)
        if not we or val is None:
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
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    history_path = out_dir / _HISTORY
    by = _load_history(history_path)
    errors: list[str] = []
    notes: list[str] = []

    for sub, n in (("legaltech", backfill_posts), ("lawfirm", max(400, backfill_posts // 2))):
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
                    and prev.get("ai_tools_sentiment") is not None
                ):
                    continue
                by[key] = row
            notes.append(f"{sub}: fetched {len(posts)} posts, {len(weeks)} week buckets")
        except Exception as e:
            errors.append(f"{sub}: {e}")

    for row in by.values():
        _ensure_ai_tools_sentiment(row)
    _write_history(history_path, by)
    rows = list(by.values())

    series_map = {
        "legaltech_software_mentions": ("legaltech", "software_mentions"),
        "legaltech_harvey_mentions": ("legaltech", "harvey"),
        "legaltech_legora_mentions": ("legaltech", "legora"),
        "legaltech_cocounsel_mentions": ("legaltech", "cocounsel"),
        "legaltech_lexis_mentions": ("legaltech", "lexis"),
        "legaltech_westlaw_mentions": ("legaltech", "westlaw"),
        "legaltech_named_product_mentions": ("legaltech", "named_products"),
        "legaltech_ai_mentions": ("legaltech", "ai"),
        "lawfirm_software_mentions": ("lawfirm", "software_mentions"),
        "lawfirm_clio_mentions": ("lawfirm", "clio"),
        "legaltech_ai_tools_sentiment": ("legaltech", "ai_tools_sentiment"),
        "legaltech_harvey_sentiment": ("legaltech", "harvey_sentiment"),
        "legaltech_legora_sentiment": ("legaltech", "legora_sentiment"),
        "legaltech_ai_sentiment": ("legaltech", "ai_sentiment"),
        "lawfirm_clio_sentiment": ("lawfirm", "clio_sentiment"),
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
                        "harvey": r.get("harvey"),
                        "legora": r.get("legora"),
                        "cocounsel": r.get("cocounsel"),
                        "lexis": r.get("lexis"),
                        "westlaw": r.get("westlaw"),
                        "ai": r.get("ai"),
                        "clio": r.get("clio"),
                        "harvey_sentiment": r.get("harvey_sentiment"),
                        "legora_sentiment": r.get("legora_sentiment"),
                        "ai_tools_sentiment": r.get("ai_tools_sentiment"),
                        "ai_sentiment": r.get("ai_sentiment"),
                        "clio_sentiment": r.get("clio_sentiment"),
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
        "source": "Arctic Shift (reddit archive) — r/legaltech + r/lawfirm",
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
    backfill_posts: int = 600,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        out_dir=out_dir,
        backfill_posts=backfill_posts,
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
        default=600,
        help="Max posts to pull per subreddit on this run (builds/refreshes weekly history)",
    )
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            backfill_posts=args.backfill_posts,
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
