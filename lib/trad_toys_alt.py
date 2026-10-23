"""Alt scrapes for Trad Toys pack — Wikipedia, Amazon, Tonies, BGG, CPSC.

Best-effort public endpoints / headless Chrome. Soft-fail callers should
keep history when a scrape is blocked.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+trad_toys_alt; research read-only)"
WIKI_UA = "mosaicbot_themedata/0.1 (https://stockcontext.info; trad-toys pageviews)"

WIKI_ARTICLES: list[tuple[str, str]] = [
    ("barbie", "Barbie"),
    ("hot_wheels", "Hot_Wheels"),
    ("labubu", "Labubu"),
    ("tonies", "Tonies"),
]

AMAZON_BRANDS: list[tuple[str, re.Pattern[str]]] = [
    ("barbie", re.compile(r"\bbarbie\b", re.I)),
    ("hot_wheels", re.compile(r"hot\s*wheels", re.I)),
    ("labubu", re.compile(r"\blabubu\b|pop\s*mart", re.I)),
    ("tonies", re.compile(r"\btonies?\b|toniebox", re.I)),
    ("mattel", re.compile(r"\bmattel\b", re.I)),
    ("hasbro", re.compile(r"\bhasbro\b|\bnerf\b|\bmonopoly\b|\btransformers\b", re.I)),
    ("fisher_price", re.compile(r"fisher[\s-]*price", re.I)),
    ("play_doh", re.compile(r"play[\s-]*doh", re.I)),
    ("leapfrog", re.compile(r"leap\s*frog|leapfrog", re.I)),
]

TONIE_TYPES = frozenset(
    {
        "Tonie",
        "Clever Tonie",
        "My First Tonie",
        "Audiobook",
        "tonies Set",
        "Tonieplay Games",
    }
)

# Family / mass-market board games — BGG object ids (own-count basket).
BGG_FAMILY_GAMES: list[tuple[str, int]] = [
    ("monopoly", 1406),
    ("uno", 2223),
    ("scrabble", 320),
    ("jenga", 2452),
    ("connect_four", 2719),
    ("candy_land", 5048),
    ("ticket_to_ride", 9209),
]

BGG_FAMILY_HOT_RX = re.compile(
    r"\b("
    r"monopoly|uno|scrabble|jenga|connect\s*four|candy\s*land|"
    r"sorry!?|clue|cluedo|operation|twister|boggle|trouble|"
    r"the\s*game\s*of\s*life|life\b|guess\s*who|mouse\s*trap|"
    r"chutes\s*and\s*ladders|hungry\s*hungry\s*hippos"
    r")\b",
    re.I,
)


def _week_ending_sunday(d: date | None = None) -> str:
    d = d or datetime.now(timezone.utc).date()
    return (d + timedelta(days=(6 - d.weekday()))).isoformat()


def _month_start(ts: str) -> str:
    # Wikimedia monthly timestamp YYYYMMDDHH → YYYY-MM-01
    s = str(ts)[:8]
    return f"{s[:4]}-{s[4:6]}-01"


def _http_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 60) -> Any:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_bytes(url: str, *, headers: dict[str, str] | None = None, timeout: int = 60) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _upsert_jsonl_by_date(path: Path, row: dict[str, Any], *, date_key: str = "date") -> None:
    """Replace same-date row or append."""
    rows = _load_jsonl(path)
    d = str(row.get(date_key) or "")
    out = [r for r in rows if str(r.get(date_key) or "") != d]
    out.append(row)
    out.sort(key=lambda r: str(r.get(date_key) or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in out),
        encoding="utf-8",
    )


# --- Wikipedia pageviews -----------------------------------------------------


def fetch_wiki_monthly(
    article: str,
    *,
    start: date,
    end: date,
) -> list[dict[str, float]]:
    """Monthly user pageviews for one enwiki article."""
    art = urllib.parse.quote(article, safe="")
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/user/{art}/monthly/"
        f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    )
    data = _http_json(url, headers={"User-Agent": WIKI_UA})
    pts: list[dict[str, float]] = []
    for item in data.get("items") or []:
        ts = item.get("timestamp")
        views = item.get("views")
        if not ts or views is None:
            continue
        pts.append({"date": _month_start(str(ts)), "value": float(views)})
    pts.sort(key=lambda p: p["date"])
    return pts


def wikipedia_brand_and_basket(
    *,
    start_year: int = 2018,
) -> tuple[dict[str, list[dict[str, float]]], list[dict[str, float]], list[str]]:
    """Return per-brand points, basket sum points, notes."""
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    start = date(start_year, 1, 1)
    by_brand: dict[str, list[dict[str, float]]] = {}
    notes: list[str] = []
    by_month: dict[str, float] = defaultdict(float)
    for key, article in WIKI_ARTICLES:
        try:
            pts = fetch_wiki_monthly(article, start=start, end=end)
            by_brand[key] = pts
            for p in pts:
                by_month[str(p["date"])] += float(p["value"])
            notes.append(f"wiki_{key}: {article} n={len(pts)}")
        except Exception as e:
            notes.append(f"wiki_{key}: ERR {e}")
        time.sleep(0.25)
    basket = [{"date": d, "value": v} for d, v in sorted(by_month.items())]
    return by_brand, basket, notes


# --- Amazon bestsellers (Chrome) ---------------------------------------------


def _chrome_bin() -> str | None:
    for cand in (
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if cand and Path(cand).exists():
            return cand
    return None


def _amazon_dump_dom(url: str, *, timeout: int = 90) -> str:
    chrome = _chrome_bin()
    if not chrome:
        raise RuntimeError("Chrome/Chromium not found for Amazon scrape")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--dump-dom",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"Chrome dump-dom failed rc={proc.returncode}")
    return proc.stdout or ""


def scrape_amazon_toys_titles(*, pages: int = 2) -> list[str]:
    titles: list[str] = []
    for pg in range(1, max(1, pages) + 1):
        url = (
            "https://www.amazon.com/Best-Sellers-Toys-Games/zgbs/toys-and-games/"
            f"ref=zg_bs_pg_{pg}_toys-and-games?_encoding=UTF8&pg={pg}"
        )
        html = _amazon_dump_dom(url)
        found = re.findall(
            r'class="[^"]*p13n-sc-css-line-clamp[^"]*"[^>]*>([^<]{6,200})<',
            html,
        )
        for t in found:
            t = re.sub(r"\s+", " ", t).strip()
            if t and t not in titles:
                titles.append(t)
        time.sleep(1.0)
    return titles


def amazon_brand_counts(titles: list[str]) -> dict[str, int]:
    counts = {k: 0 for k, _ in AMAZON_BRANDS}
    counts["any_theme_brand"] = 0
    for title in titles:
        hit_any = False
        for key, rx in AMAZON_BRANDS:
            if rx.search(title):
                counts[key] += 1
                hit_any = True
        if hit_any:
            counts["any_theme_brand"] += 1
    counts["titles_scanned"] = len(titles)
    return counts


def scrape_amazon_bestsellers(
    history_path: Path,
    *,
    pages: int = 2,
) -> tuple[dict[str, Any], list[str]]:
    """Append weekly brand-presence snapshot. Returns (meta, notes)."""
    notes: list[str] = []
    we = _week_ending_sunday()
    try:
        titles = scrape_amazon_toys_titles(pages=pages)
        counts = amazon_brand_counts(titles)
        row = {
            "date": we,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            **counts,
            "sample_titles": titles[:12],
        }
        _upsert_jsonl_by_date(history_path, row)
        notes.append(
            f"amazon: week={we} titles={counts['titles_scanned']} "
            f"theme_brands={counts['any_theme_brand']}"
        )
        return row, notes
    except Exception as e:
        notes.append(f"amazon: ERR {e}")
        return {"date": we, "error": str(e)}, notes


def amazon_points_from_history(
    history_path: Path, *, field: str
) -> list[dict[str, float]]:
    pts = [
        {"date": str(r["date"]), "value": float(r.get(field) or 0)}
        for r in _load_jsonl(history_path)
        if r.get("date") and field in r
    ]
    pts.sort(key=lambda p: p["date"])
    return pts


# --- Tonies Shopify catalog --------------------------------------------------


def fetch_tonies_products(*, max_pages: int = 20, pause: float = 4.0) -> list[dict[str, Any]]:
    """Paginate Shopify products.json. Shopify rate-limits aggressively — slow."""
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = f"https://us.tonies.com/products.json?limit=250&page={page}"
        try:
            data = _http_json(url, headers={"User-Agent": UA})
        except urllib.error.HTTPError as e:
            if e.code == 429 and out:
                break
            if e.code == 429:
                time.sleep(pause * 2)
                data = _http_json(url, headers={"User-Agent": UA})
            else:
                raise
        batch = data.get("products") or []
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 250:
            break
        time.sleep(pause)
    return out


def _is_tonie_content(prod: dict[str, Any]) -> bool:
    ptype = str(prod.get("product_type") or "")
    if ptype in TONIE_TYPES:
        return True
    tags = " ".join(str(t) for t in (prod.get("tags") or [])).lower()
    title = str(prod.get("title") or "").lower()
    if "toniebox" in title and "bag" in title:
        return False
    return "tonie" in ptype.lower() or "tonie" in tags


def scrape_tonies_catalog(history_path: Path) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    we = _week_ending_sunday()
    try:
        products = fetch_tonies_products()
        content = [p for p in products if _is_tonie_content(p)]
        # New adds in last 30d by created_at
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        new_30 = 0
        for p in content:
            raw = p.get("created_at") or p.get("published_at") or ""
            try:
                # 2026-06-19T09:17:22-07:00
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                new_30 += 1
        row = {
            "date": we,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "catalog_size": len(content),
            "catalog_all_products": len(products),
            "new_skus_30d": new_30,
        }
        _upsert_jsonl_by_date(history_path, row)
        notes.append(
            f"tonies: catalog={len(content)} (all={len(products)}) new_30d={new_30}"
        )
        return row, notes
    except Exception as e:
        notes.append(f"tonies: ERR {e}")
        return {"date": we, "error": str(e)}, notes


def tonies_points_from_history(
    history_path: Path, *, field: str
) -> list[dict[str, float]]:
    pts = [
        {"date": str(r["date"]), "value": float(r.get(field) or 0)}
        for r in _load_jsonl(history_path)
        if r.get("date") and r.get(field) is not None
    ]
    pts.sort(key=lambda p: p["date"])
    return pts


# --- CPSC recalls ------------------------------------------------------------


def fetch_cpsc_toy_recalls() -> list[dict[str, Any]]:
    url = (
        "https://www.saferproducts.gov/RestWebServices/Recall"
        "?format=json&ProductType=Toys"
    )
    data = _http_json(url, timeout=90)
    if not isinstance(data, list):
        raise RuntimeError(f"CPSC unexpected type {type(data)}")
    return data


def cpsc_monthly_counts(
    *,
    start_year: int = 2015,
) -> list[dict[str, float]]:
    rows = fetch_cpsc_toy_recalls()
    by_month: dict[str, float] = defaultdict(float)
    for r in rows:
        d = str(r.get("RecallDate") or "")[:10]
        if len(d) < 7:
            continue
        month = d[:7] + "-01"
        if month < f"{start_year:04d}-01-01":
            continue
        by_month[month] += 1.0
    return [{"date": d, "value": v} for d, v in sorted(by_month.items())]


# --- BoardGameGeek -----------------------------------------------------------


def bgg_own_count(object_id: int) -> int:
    url = (
        "https://api.geekdo.com/api/collections"
        f"?objectid={int(object_id)}&objecttype=thing&subtype=boardgame"
        "&stattype=own&pagelength=1"
    )
    data = _http_json(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://boardgamegeek.com/",
        },
    )
    return int((data.get("config") or {}).get("numitems") or 0)


def bgg_hotness_items() -> list[dict[str, Any]]:
    url = "https://api.geekdo.com/api/hotness?objecttype=thing&showall=1"
    data = _http_json(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://boardgamegeek.com/",
        },
    )
    return list(data.get("items") or [])


def scrape_bgg(history_path: Path) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    we = _week_ending_sunday()
    try:
        owns: dict[str, int] = {}
        total_own = 0
        for key, oid in BGG_FAMILY_GAMES:
            n = bgg_own_count(oid)
            owns[key] = n
            total_own += n
            time.sleep(0.35)
        hot = bgg_hotness_items()
        family_hot = 0
        hot_names: list[str] = []
        for it in hot:
            name = str(it.get("name") or "")
            hot_names.append(name)
            if BGG_FAMILY_HOT_RX.search(name):
                family_hot += 1
        row = {
            "date": we,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "family_own_sum": total_own,
            "family_hot_count": family_hot,
            "hot_list_size": len(hot),
            "owns": owns,
            "hot_family_names": [n for n in hot_names if BGG_FAMILY_HOT_RX.search(n)][:20],
        }
        _upsert_jsonl_by_date(history_path, row)
        notes.append(
            f"bgg: family_own_sum={total_own} family_hot={family_hot}/{len(hot)}"
        )
        return row, notes
    except Exception as e:
        notes.append(f"bgg: ERR {e}")
        return {"date": we, "error": str(e)}, notes


def bgg_points_from_history(
    history_path: Path, *, field: str
) -> list[dict[str, float]]:
    pts = [
        {"date": str(r["date"]), "value": float(r.get(field) or 0)}
        for r in _load_jsonl(history_path)
        if r.get("date") and r.get(field) is not None
    ]
    pts.sort(key=lambda p: p["date"])
    return pts
