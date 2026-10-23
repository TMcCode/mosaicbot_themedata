"""StockX public product-page scrape via headless Chrome.

Cloudflare blocks plain urllib/curl. Headless Chrome ``--dump-dom`` works on Mac
(and Contabo if Chromium is installed). We parse rendered PDP text for last sale,
buy-now (lowest ask), highest offer, and retail — then append weekly history.

Not official StockX API. Soft-fails when Chrome/CF unavailable so packs still ship.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Stable hero SKUs for streetwear/sneaker culture basket (url_key after stockx.com/).
DEFAULT_HERO_SKUS: list[dict[str, Any]] = [
    {
        "id": "dunk_panda",
        "url_key": "nike-dunk-low-retro-white-black-2021",
        "label": "Nike Dunk Low Panda",
        "brand": "Nike",
    },
    {
        "id": "aj1_chicago_laf",
        "url_key": "air-jordan-1-retro-high-og-chicago-reimagined-lost-and-found",
        "label": "Jordan 1 Chicago Lost & Found",
        "brand": "Jordan",
    },
    {
        "id": "yeezy_350_bone",
        "url_key": "adidas-yeezy-boost-350-v2-bone",
        "label": "Yeezy Boost 350 V2 Bone",
        "brand": "Adidas",
    },
    {
        "id": "samba_og_black",
        "url_key": "adidas-samba-og-black-white-gum",
        "label": "Adidas Samba OG Black White Gum",
        "brand": "Adidas",
    },
    {
        "id": "nb_550",
        "url_key": "new-balance-550-white-green",
        "label": "New Balance 550 White Green",
        "brand": "New Balance",
    },
    {
        "id": "on_cloudmonster",
        "url_key": "on-cloudmonster-2-undyed-white-frost",
        "label": "On Cloudmonster 2 Undyed White Frost",
        "brand": "On",
    },
    {
        "id": "asics_kayano_14",
        "url_key": "asics-gel-kayano-14-white-midnight",
        "label": "ASICS Gel-Kayano 14 White Midnight",
        "brand": "ASICS",
    },
    {
        "id": "crocs_clog_bone",
        "url_key": "crocs-classic-clog-bone",
        "label": "Crocs Classic Clog Bone",
        "brand": "Crocs",
    },
]


def _week_ending_sunday(d: date | None = None) -> str:
    d = d or datetime.now(timezone.utc).date()
    ahead = 6 - d.weekday()
    return (d + timedelta(days=ahead)).isoformat()


def find_chrome() -> str | None:
    env = (os.environ.get("STOCKX_CHROME_PATH") or os.environ.get("CHROME_PATH") or "").strip()
    if env and Path(env).exists():
        return env
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def _money_after(label: str, html: str, *, window: int = 500) -> float | None:
    i = html.find(label)
    if i < 0:
        return None
    m = re.search(r"\$([0-9,.]+)", html[i : i + window])
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def parse_product_html(html: str, *, url_key: str) -> dict[str, Any]:
    """Extract market fields from a rendered StockX PDP dump-dom HTML."""
    title_m = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", html, re.I)
    title = (title_m.group(1).strip() if title_m else "") or url_key
    last_sale = _money_after("Last Sale", html)
    lowest_ask = _money_after("Buy Now for", html)
    highest_bid = _money_after("Highest Offer", html)
    retail: float | None = None
    i = html.find("Product Details")
    if i >= 0:
        moneys = [
            float(x.replace(",", ""))
            for x in re.findall(r"\$([0-9,.]+)", html[i : i + 3000])
        ]
        # Retail sits in details; skip tiny fee-looking numbers.
        for v in moneys:
            if v >= 40:
                retail = v
                break
    if retail is None:
        # Fallback: "Below/Above Retail" gap + ask implies retail.
        below = _money_after("Below Retail", html)
        above = _money_after("Above Retail", html)
        if lowest_ask is not None and below is not None:
            retail = lowest_ask + below
        elif lowest_ask is not None and above is not None:
            retail = max(lowest_ask - above, 1.0)

    premium_pct: float | None = None
    if last_sale is not None and retail and retail > 0:
        premium_pct = round(100.0 * (last_sale - retail) / retail, 2)
    elif lowest_ask is not None and retail and retail > 0:
        premium_pct = round(100.0 * (lowest_ask - retail) / retail, 2)

    return {
        "url_key": url_key,
        "title": title,
        "last_sale": last_sale,
        "lowest_ask": lowest_ask,
        "highest_bid": highest_bid,
        "retail": retail,
        "premium_pct": premium_pct,
        "ok": last_sale is not None or lowest_ask is not None,
    }


def fetch_product_html(
    url_key: str,
    *,
    chrome: str | None = None,
    user_data_dir: Path | None = None,
    virtual_time_ms: int = 25000,
) -> str:
    chrome = chrome or find_chrome()
    if not chrome:
        raise RuntimeError(
            "Chrome/Chromium not found — set STOCKX_CHROME_PATH or install Chrome"
        )
    url = f"https://stockx.com/{url_key.lstrip('/')}"
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--virtual-time-budget={int(virtual_time_ms)}",
        f"--user-agent={UA}",
        "--dump-dom",
        url,
    ]
    if user_data_dir is not None:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        cmd.insert(-2, f"--user-data-dir={user_data_dir}")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=max(60, virtual_time_ms // 1000 + 30),
    )
    html = proc.stdout or ""
    if len(html) < 5000:
        err = (proc.stderr or "")[:300]
        raise RuntimeError(f"StockX dump-dom too short for {url_key}: {err}")
    return html


def scrape_product(
    url_key: str,
    *,
    chrome: str | None = None,
    user_data_dir: Path | None = None,
) -> dict[str, Any]:
    html = fetch_product_html(
        url_key, chrome=chrome, user_data_dir=user_data_dir
    )
    return parse_product_html(html, url_key=url_key)


def scrape_hero_basket(
    skus: list[dict[str, Any]] | None = None,
    *,
    history_path: Path,
    sleep_s: float = 2.5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape hero SKUs, append week bucket, return (snapshots, meta)."""
    skus = skus or DEFAULT_HERO_SKUS
    chrome = find_chrome()
    meta: dict[str, Any] = {
        "chrome": chrome,
        "week_ending": _week_ending_sunday(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if not chrome:
        meta["error"] = "chrome_missing"
        return [], meta

    ud = history_path.parent / ".stockx_chrome_profile"
    snaps: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, spec in enumerate(skus):
        key = str(spec["url_key"])
        try:
            row = scrape_product(key, chrome=chrome, user_data_dir=ud)
            row.update(
                {
                    "id": spec.get("id") or key,
                    "label": spec.get("label") or row.get("title"),
                    "brand": spec.get("brand"),
                    "week_ending": meta["week_ending"],
                    "fetched_at": meta["fetched_at"],
                }
            )
            snaps.append(row)
            if not row.get("ok"):
                errors.append(f"{key}: parsed but no sale/ask")
        except Exception as e:
            errors.append(f"{key}: {e}")
        if i + 1 < len(skus):
            time.sleep(sleep_s)

    meta["errors"] = errors
    meta["n_ok"] = sum(1 for s in snaps if s.get("ok"))
    _append_history(history_path, snaps, week_ending=meta["week_ending"])
    return snaps, meta


def _append_history(
    path: Path, snaps: list[dict[str, Any]], *, week_ending: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            k = f"{row.get('id')}|{row.get('week_ending')}"
            existing[k] = row
    for s in snaps:
        if not s.get("ok"):
            continue
        k = f"{s.get('id')}|{week_ending}"
        existing[k] = {
            "id": s.get("id"),
            "url_key": s.get("url_key"),
            "label": s.get("label"),
            "brand": s.get("brand"),
            "week_ending": week_ending,
            "last_sale": s.get("last_sale"),
            "lowest_ask": s.get("lowest_ask"),
            "highest_bid": s.get("highest_bid"),
            "retail": s.get("retail"),
            "premium_pct": s.get("premium_pct"),
            "fetched_at": s.get("fetched_at"),
        }
    rows = sorted(
        existing.values(),
        key=lambda r: (r.get("id") or "", r.get("week_ending") or ""),
    )
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def basket_premium_points(history: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Weekly mean resale premium % (last_sale vs retail) across hero SKUs."""
    by_week: dict[str, list[float]] = {}
    for r in history:
        we = r.get("week_ending")
        prem = r.get("premium_pct")
        if not we or prem is None:
            continue
        by_week.setdefault(str(we), []).append(float(prem))
    pts = [
        {"date": we, "value": round(sum(vs) / len(vs), 2)}
        for we, vs in by_week.items()
        if vs
    ]
    pts.sort(key=lambda p: p["date"])
    return pts


def basket_last_sale_index_points(
    history: list[dict[str, Any]],
) -> list[dict[str, float]]:
    """Weekly mean last-sale USD across hero SKUs (simple equal-weight index)."""
    by_week: dict[str, list[float]] = {}
    for r in history:
        we = r.get("week_ending")
        last = r.get("last_sale")
        if not we or last is None:
            continue
        by_week.setdefault(str(we), []).append(float(last))
    pts = [
        {"date": we, "value": round(sum(vs) / len(vs), 2)}
        for we, vs in by_week.items()
        if vs
    ]
    pts.sort(key=lambda p: p["date"])
    return pts
