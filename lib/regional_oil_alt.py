"""Alt scrapes for Regional Oil Pre-Salt & North Sea pack.

NOD FactPages field oil, ANP PreSal well zips + bulletin Santos basin,
Gassco NCS exit nominations (daily append), UK DESNZ ET 3.1 indigenous oil,
Abicom fuel parity (soft-fail), Wikipedia pageviews.
"""

from __future__ import annotations

import calendar
import csv
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+regional_oil_alt; research read-only)"
WIKI_UA = "mosaicbot_themedata/0.1 (https://stockcontext.info; regional oil pageviews)"

NOD_FIELD_CSV = (
    "https://factpages.sodir.no/public?/Factpages/external/tableview/"
    "field_production_monthly&rs:Command=Render&rc:Toolbar=false"
    "&rc:Parameters=f&IpAddress=not_used&CultureCode=en&rs:Format=CSV&Top100=false"
)

# mill Sm³ oil → barrels (standard NOD factor)
_SM3_TO_BBL = 6.28981

ANP_POCO_PAGE = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "producao-de-petroleo-e-gas-natural-por-poco"
)
ANP_BOLETIM_PAGE = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/publicacoes/boletins-anp/"
    "boletim-mensal-da-producao-de-petroleo-e-gas-natural"
)
# Offshore well-level open data (m³/month) — annual CSVs; newer than bulletin xlsm.
ANP_OPENDATA_MAR_BASE = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/"
    "arquivos-fase-de-desenvolvimento-e-producao/"
    "arquivos-producao-de-petroleo-e-gas-natural-nacional/pm"
)
ENERGY_TRENDS_OIL = (
    "https://www.gov.uk/government/statistics/"
    "oil-and-oil-products-section-3-energy-trends"
)

# Campo aliases → series field key (uppercase substrings / exact)
ANP_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "buzios": ("BÚZIOS", "BUZIOS"),
    "tupi": ("TUPI", "LULA"),
    "mero": ("MERO",),
    "atapu": ("ATAPU",),
    "sepia": ("SÉPIA", "SEPIA"),
}

NOD_FIELDS: dict[str, str] = {
    "johan_sverdrup": "JOHAN SVERDRUP",
    "johan_castberg": "JOHAN CASTBERG",
    "alvheim": "ALVHEIM",
    "balder": "BALDER",
}

WIKI_ARTICLES: list[tuple[str, str]] = [
    ("petrobras", "Petrobras"),
    ("equinor", "Equinor"),
    ("johan_sverdrup", "Johan_Sverdrup_oil_field"),
    ("buzios", "Búzios_oil_field"),
    ("presalt", "Pre-salt_layer"),
]


def _http_bytes(url: str, *, timeout: int = 120, headers: dict[str, str] | None = None) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_text(url: str, *, timeout: int = 60, encoding: str = "utf-8") -> str:
    return _http_bytes(url, timeout=timeout).decode(encoding, errors="replace")


def _month_end(year: int, month: int) -> str:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last:02d}"


def _quarter_end(year: int, q: int) -> str:
    m = q * 3
    return _month_end(year, m)


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _mill_sm3_month_to_kbpd(mill_sm3: float, year: int, month: int) -> float:
    """NOD net oil mill Sm³/month → thousand barrels per day."""
    days = _days_in_month(year, month)
    if days <= 0:
        return 0.0
    return (float(mill_sm3) * _SM3_TO_BBL * 1_000_000.0 / days) / 1000.0


def _parse_br_float(raw: str | None) -> float:
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(".", "").replace(",", ".") if False else str(raw).strip()
    # ANP uses comma decimal; some files mix
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# NOD FactPages
# ---------------------------------------------------------------------------


def fetch_nod_field_oil_kbpd(
    *, start_year: int = 2015
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Return {field_key: [{date,value}...]} for NOD focus fields (kbpd)."""
    notes: list[str] = []
    raw = _http_bytes(NOD_FIELD_CSV, timeout=180)
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    buckets: dict[str, dict[str, float]] = {k: {} for k in NOD_FIELDS}
    for row in reader:
        name = (row.get("prfInformationCarrier") or "").strip().upper()
        try:
            y = int(row["prfYear"])
            m = int(row["prfMonth"])
            oil = float(row.get("prfPrdOilNetMillSm3") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if y < start_year:
            continue
        for key, needle in NOD_FIELDS.items():
            if name == needle:
                d = _month_end(y, m)
                buckets[key][d] = _mill_sm3_month_to_kbpd(oil, y, m)
                break
    out: dict[str, list[dict[str, Any]]] = {}
    for key, by_date in buckets.items():
        pts = [
            {"date": d, "value": round(v, 3)}
            for d, v in sorted(by_date.items())
            if v is not None
        ]
        out[key] = pts
        notes.append(f"NOD {key}: {len(pts)} months")
    return out, notes


# ---------------------------------------------------------------------------
# ANP PreSal well zips + Santos bulletin
# ---------------------------------------------------------------------------


def _anp_zip_urls_from_page() -> list[str]:
    html = _http_text(ANP_POCO_PAGE, timeout=60)
    urls = []
    seen: set[str] = set()
    for href in re.findall(r'href="([^"]+\.zip)"', html, re.I):
        if href.startswith("/"):
            href = "https://www.gov.br" + href
        if "producao" not in href.lower() and "produção" not in href.lower():
            continue
        if href not in seen:
            seen.add(href)
            urls.append(href)
    return urls


def _campo_matches(campo: str, needles: tuple[str, ...]) -> bool:
    c = (campo or "").upper()
    for n in needles:
        if c == n or c.startswith(n + "_") or c.startswith(n + " ") or n + "_" in c:
            return True
        # avoid TUPI matching SUL DE TUPI as separate — we WANT sul de tupi in tupi group
        if n in c.split() or c.startswith(n):
            return True
    return False


def _aggregate_presal_csv(text: str) -> dict[str, float]:
    """Campo group → oil bbl/d sum for one PreSal file."""
    # detect delimiter
    sample = text[:2000]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    totals: dict[str, float] = defaultdict(float)
    oil_key = None
    campo_key = None
    for row in reader:
        if oil_key is None:
            for k in row.keys():
                kl = (k or "").lower()
                if "petr" in kl and "bbl" in kl:
                    oil_key = k
                if (k or "").lower() == "campo" or (k or "").endswith("Campo"):
                    campo_key = k
            if not oil_key or not campo_key:
                # fallback common names
                oil_key = oil_key or "Petróleo (bbl/dia)"
                campo_key = campo_key or "Campo"
        campo = (row.get(campo_key) or "").strip()
        oil = _parse_br_float(row.get(oil_key))
        if oil <= 0:
            continue
        for gkey, needles in ANP_FIELD_GROUPS.items():
            if _campo_matches(campo, needles):
                totals[gkey] += oil
                break
    # also total all pre-salt wells
    return dict(totals)


def _period_from_presal_name_or_rows(name: str, text: str) -> str | None:
    # filename like 2023_12_producao_PreSal.csv
    m = re.search(r"(20\d{2})[_-](\d{2})", name)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return _month_end(y, mo)
    # Período column 2023/12
    delim = ";" if text[:500].count(";") >= text[:500].count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    for row in reader:
        for k, v in row.items():
            if k and "per" in k.lower() and v:
                mm = re.match(r"(20\d{2})[/-](\d{1,2})", str(v).strip())
                if mm:
                    return _month_end(int(mm.group(1)), int(mm.group(2)))
        break
    return None


def fetch_anp_presalt_fields(
    cache_dir: Path,
    *,
    start_year: int = 2018,
    max_zips: int | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Download ANP well zips (through ~2023 static files); aggregate focus fields (kbpd)."""
    notes: list[str] = []
    cache_dir.mkdir(parents=True, exist_ok=True)
    urls = _anp_zip_urls_from_page()
    notes.append(f"ANP zip index: {len(urls)} files")
    if max_zips is not None:
        urls = urls[: max_zips]

    # date → field → bbl/d
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    downloaded = 0
    for url in urls:
        fname = url.rstrip("/").split("/")[-1]
        # skip very old yearly archives before start_year when obvious
        ym = re.search(r"(20\d{2})", fname)
        if ym and int(ym.group(1)) < start_year and "producao-por-poco-20" in url:
            # yearly historical — still allow if >= start_year-ish; skip pre-start
            if int(ym.group(1)) < start_year:
                continue
        local = cache_dir / fname
        try:
            if not local.exists() or local.stat().st_size < 1000:
                local.write_bytes(_http_bytes(url, timeout=180))
                downloaded += 1
                time.sleep(0.35)
            data = local.read_bytes()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for name in zf.namelist():
                    if "presal" not in name.lower() and "pré-sal" not in name.lower():
                        if "PreSal" not in name and "presal" not in name.lower():
                            continue
                    text = zf.read(name).decode("latin-1", errors="replace")
                    d = _period_from_presal_name_or_rows(name, text)
                    if not d or int(d[:4]) < start_year:
                        continue
                    totals = _aggregate_presal_csv(text)
                    for gkey, bbl_d in totals.items():
                        by_date[d][gkey] = bbl_d
        except Exception as e:
            notes.append(f"ANP zip skip {fname}: {e}")
            continue

    notes.append(f"ANP downloaded/refreshed {downloaded} zips; months={len(by_date)}")
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ANP_FIELD_GROUPS}
    out["presalt_focus"] = []
    for d in sorted(by_date.keys()):
        row = by_date[d]
        focus_sum = 0.0
        for gkey in ANP_FIELD_GROUPS:
            v = float(row.get(gkey) or 0.0) / 1000.0  # → kbpd
            out[gkey].append({"date": d, "value": round(v, 3)})
            focus_sum += v
        out["presalt_focus"].append({"date": d, "value": round(focus_sum, 3)})
    for k, pts in out.items():
        notes.append(f"ANP zip {k}: {len(pts)} months")
    return out, notes


def _norm_campo(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().replace("_", " ").strip())


# Bulletin Tabela 10 campo names (normalized) → focus series key.
# Sum satellite concessions into the parent field (matches well-zip grouping).
_BULLETIN_FIELD_NAMES: dict[str, set[str]] = {
    "buzios": {"buzios", "buzios eco", "tambuata"},
    "tupi": {"tupi", "sul de tupi", "anc tupi"},
    "mero": {"mero", "anc mero"},
    "atapu": {"atapu", "atapu eco", "oeste de atapu", "anc norte atapu"},
    "sepia": {"sepia", "sepia eco", "sepia leste"},
}


def _bulletin_ym_from_name(url_or_name: str) -> tuple[int | None, int | None]:
    year = None
    month = None
    m2 = re.search(r"boletim_(20\d{2})_(\d{2})_", url_or_name)
    if m2:
        return int(m2.group(1)), int(m2.group(2))
    ym = re.search(r"(?:^|/)(20\d{2})(?:/|_)", url_or_name)
    if ym:
        year = int(ym.group(1))
    low = url_or_name.lower()
    for name, num in _PT_MONTH.items():
        if name in low:
            month = num
            break
    if year is None:
        m3 = re.search(r"(20\d{2})", url_or_name)
        if m3:
            year = int(m3.group(1))
    return year, month


def _parse_bulletin_presalt_table(path: Path) -> dict[str, float] | None:
    """Tabela 10 — Pré-sal field oil bbl/d by focus group."""
    try:
        import openpyxl
    except ImportError:
        return None
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = None
    for sn in wb.sheetnames:
        if "dados" in sn.lower() or sn.strip().startswith("2"):
            sheet = wb[sn]
            break
    if sheet is None:
        return None

    in_table = False
    totals: dict[str, float] = defaultdict(float)
    for row in sheet.iter_rows(max_row=260, max_col=10, values_only=True):
        cells = [c for c in (row or ()) if c is not None]
        if not cells:
            continue
        joined = " ".join(str(c) for c in cells).lower()
        if "tabela 10" in joined and "pr" in joined and "sal" in joined:
            in_table = True
            continue
        if not in_table:
            continue
        # header row
        if str(cells[0]).strip().lower() in ("campo",):
            continue
        if str(cells[0]).strip().lower() == "total":
            break
        campo = _norm_campo(str(cells[0]))
        # oil is first numeric after campo name
        oil = None
        for c in cells[1:]:
            try:
                oil = float(c)
                break
            except (TypeError, ValueError):
                continue
        if oil is None or oil <= 0:
            continue
        for gkey, names in _BULLETIN_FIELD_NAMES.items():
            if campo in names:
                totals[gkey] += oil
                break
    return dict(totals) if totals else None


def fetch_anp_presalt_fields_from_bulletins(
    cache_dir: Path,
    *,
    start_year: int = 2022,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Pré-sal field oil (kbpd) from bulletin Tabela 10 — covers 2024+ after CDP migration."""
    notes: list[str] = []
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return {}, ["ANP bulletin fields: openpyxl missing"]

    # Ensure cache is populated (reuse Santos bulletin downloader side-effect)
    _santos, snotes = fetch_anp_santos_oil_from_bulletins(
        cache_dir, start_year=start_year
    )
    notes.extend([n for n in snotes if n.startswith("ANP bulletin")])

    by_date: dict[str, dict[str, float]] = {}
    for local in sorted(cache_dir.glob("boletim_*.xlsm")):
        year, month = _bulletin_ym_from_name(local.name)
        if year is None or month is None or year < start_year:
            continue
        try:
            totals = _parse_bulletin_presalt_table(local)
        except Exception as e:
            notes.append(f"bulletin field skip {local.name}: {e}")
            continue
        if not totals:
            continue
        by_date[_month_end(year, month)] = totals

    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ANP_FIELD_GROUPS}
    out["presalt_focus"] = []
    for d in sorted(by_date.keys()):
        row = by_date[d]
        focus_sum = 0.0
        for gkey in ANP_FIELD_GROUPS:
            v = float(row.get(gkey) or 0.0) / 1000.0
            out[gkey].append({"date": d, "value": round(v, 3)})
            focus_sum += v
        out["presalt_focus"].append({"date": d, "value": round(focus_sum, 3)})
    for k, pts in out.items():
        notes.append(f"ANP bulletin field {k}: {len(pts)} months")
    return out, notes


def merge_anp_field_histories(
    *layers: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Merge field histories left→right; later layers win on overlapping months."""
    keys = set(ANP_FIELD_GROUPS) | {"presalt_focus"}
    out: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        by: dict[str, float] = {}
        for layer in layers:
            for p in (layer or {}).get(key) or []:
                by[str(p["date"])[:10]] = float(p["value"])
        out[key] = [
            {"date": d, "value": round(v, 3)} for d, v in sorted(by.items())
        ]
    return out


def _parse_br_number(s: str | None) -> float:
    t = (s or "").strip().strip('"')
    if not t:
        return 0.0
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _opendata_mar_urls(start_year: int) -> list[tuple[int, str]]:
    """Candidate annual offshore CSV URLs (2024 naming differs)."""
    now_y = datetime.now(timezone.utc).year
    out: list[tuple[int, str]] = []
    for year in range(max(start_year, 2024), now_y + 1):
        if year == 2024:
            name = "producao_por_poco_2024.csv"
        else:
            name = f"producao-mar-{year}.csv"
        out.append((year, f"{ANP_OPENDATA_MAR_BASE}/{name}"))
    return out


def fetch_anp_presalt_fields_from_opendata(
    *,
    start_year: int = 2024,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Pré-sal field oil (kbpd) from ANP open-data offshore well CSVs (post-bulletin).

    Bulletin xlsm stops ~Jun-2025; pm/producao-mar-YYYY.csv continues (e.g. through 2026-08).
    Aggregates the same campo groups as Tabela 10 (incl. ECO / ANC / satellites).
    """
    notes: list[str] = []
    by_date: dict[str, dict[str, float]] = {}
    for year, url in _opendata_mar_urls(start_year):
        try:
            raw = _http_bytes(url, timeout=180)
        except Exception as e:
            notes.append(f"ANP opendata mar {year}: skip ({e})")
            continue
        try:
            text = raw.decode("utf-8-sig")
            lines = text.splitlines()
            if not lines:
                continue
            header = lines[0].replace("[", "").replace("]", "")
            body = "\n".join([header] + lines[1:])
            rows = list(csv.DictReader(io.StringIO(body)))
        except Exception as e:
            notes.append(f"ANP opendata mar {year}: parse fail ({e})")
            continue
        month_m3: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in rows:
            mes = (row.get("Mês/Ano") or row.get("Mes/Ano") or "").strip()
            campo = _norm_campo(row.get("Campo") or "")
            oil_m3 = _parse_br_number(row.get("Produção de Óleo (m³)"))
            if not mes or oil_m3 <= 0 or not campo:
                continue
            gkey = None
            for gk, names in _BULLETIN_FIELD_NAMES.items():
                if campo in names:
                    gkey = gk
                    break
            if gkey is None:
                continue
            parts = mes.split("/")
            if len(parts) != 2:
                continue
            try:
                mo, y = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if y < start_year:
                continue
            month_m3[_month_end(y, mo)][gkey] += oil_m3
        for d, vals in month_m3.items():
            y = int(d[:4])
            mo = int(d[5:7])
            days = _days_in_month(y, mo)
            by_date[d] = {
                gk: (m3 * _SM3_TO_BBL / days)
                for gk, m3 in vals.items()
            }
        notes.append(
            f"ANP opendata mar {year}: {len(month_m3)} months from {url.split('/')[-1]}"
        )

    out: dict[str, list[dict[str, Any]]] = {k: [] for k in ANP_FIELD_GROUPS}
    out["presalt_focus"] = []
    for d in sorted(by_date.keys()):
        row = by_date[d]
        focus_sum = 0.0
        for gkey in ANP_FIELD_GROUPS:
            v = float(row.get(gkey) or 0.0) / 1000.0
            out[gkey].append({"date": d, "value": round(v, 3)})
            focus_sum += v
        out["presalt_focus"].append({"date": d, "value": round(focus_sum, 3)})
    for k, pts in out.items():
        notes.append(f"ANP opendata field {k}: {len(pts)} months")
    return out, notes


_PT_MONTH = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


def fetch_anp_santos_oil_from_bulletins(
    cache_dir: Path,
    *,
    start_year: int = 2022,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Santos basin oil (kbpd) from ANP monthly bulletin xlsm Table 6 — covers 2024+."""
    notes: list[str] = []
    try:
        import openpyxl
    except ImportError:
        return [], ["ANP bulletin: openpyxl missing — pip install openpyxl"]

    html = _http_text(ANP_BOLETIM_PAGE, timeout=60)
    urls = []
    for href in re.findall(r'href="([^"]+\.xlsm)"', html, re.I):
        if href.startswith("/"):
            href = "https://www.gov.br" + href
        if "bmppgn" not in href.lower() and "boletim" not in href.lower():
            continue
        urls.append(href)
    # newest first; keep unique
    seen: set[str] = set()
    uniq = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    notes.append(f"ANP bulletin xlsm: {len(uniq)}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    by_date: dict[str, float] = {}

    def _ym_from_name(url_or_name: str) -> tuple[int | None, int | None]:
        year = None
        month = None
        ym = re.search(r"(?:^|/)(20\d{2})(?:/|_)", url_or_name)
        if ym:
            year = int(ym.group(1))
        # also boletim_YYYY_MM_ prefix
        m2 = re.search(r"boletim_(20\d{2})_(\d{2})_", url_or_name)
        if m2:
            year = int(m2.group(1))
            month = int(m2.group(2))
        low = url_or_name.lower()
        if month is None:
            for name, num in _PT_MONTH.items():
                if name in low:
                    month = num
                    break
        if year is None:
            m3 = re.search(r"(20\d{2})", url_or_name)
            if m3:
                year = int(m3.group(1))
        return year, month

    def _parse_santos(path: Path, year: int, month: int) -> float | None:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        sheet = None
        for sn in wb.sheetnames:
            if "dados" in sn.lower() or sn.strip().startswith("2"):
                sheet = wb[sn]
                break
        if sheet is None:
            return None
        # Bulletin sheets often have a blank first column (merged title row);
        # look for "Santos" anywhere in the row, then the next numeric cell.
        for row in sheet.iter_rows(max_row=120, max_col=8, values_only=True):
            if not row:
                continue
            cells = list(row)
            for i, cell in enumerate(cells):
                if cell is None:
                    continue
                if str(cell).strip().lower() != "santos":
                    continue
                for j in range(i + 1, len(cells)):
                    try:
                        return float(cells[j])
                    except (TypeError, ValueError):
                        continue
                return None
        return None

    for url in uniq:
        fname = url.rstrip("/").split("/")[-1]
        year, month = _ym_from_name(url)
        if year is None or month is None:
            year2, month2 = _ym_from_name(fname)
            year = year or year2
            month = month or month2
        if year is None or month is None or year < start_year:
            continue
        local = cache_dir / f"boletim_{year:04d}_{month:02d}_{fname}"
        try:
            if not local.exists() or local.stat().st_size < 1000:
                local.write_bytes(_http_bytes(url, timeout=120))
                time.sleep(0.25)
            bbl_d = _parse_santos(local, year, month)
            if bbl_d is None:
                continue
            by_date[_month_end(year, month)] = bbl_d / 1000.0  # kbpd
        except Exception as e:
            notes.append(f"bulletin skip {fname}: {e}")
            continue

    # Also harvest any already-cached files (in case page links change)
    for local in cache_dir.glob("boletim_*.xlsm"):
        year, month = _ym_from_name(local.name)
        if year is None or month is None or year < start_year:
            continue
        d = _month_end(year, month)
        if d in by_date:
            continue
        try:
            bbl_d = _parse_santos(local, year, month)
            if bbl_d is not None:
                by_date[d] = bbl_d / 1000.0
        except Exception as e:
            notes.append(f"bulletin cache skip {local.name}: {e}")

    pts = [{"date": d, "value": round(v, 3)} for d, v in sorted(by_date.items())]
    notes.append(f"ANP Santos basin: {len(pts)} months")
    return pts, notes


# ---------------------------------------------------------------------------
# Gassco NCS exit nominations (daily append)
# ---------------------------------------------------------------------------


def scrape_gassco_ncs_exit(history_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Append today's Sum Exit Nominations NCS (MSm³/d) after accepting UMM disclaimer."""
    notes: list[str] = []
    try:
        import requests

        sess = requests.Session()
        sess.headers["User-Agent"] = UA
        sess.get("https://umm.gassco.no/", timeout=40)
        r = sess.get(
            "https://umm.gassco.no/disclaimer/acceptDisclaimer",
            timeout=40,
            allow_redirects=True,
        )
        html = r.text
    except Exception:
        # urllib fallback
        import http.cookiejar

        class _RH(urllib.request.HTTPRedirectHandler):
            def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
                return self.redirect_request(req, fp, code, msg, headers, headers.get("Location"))

        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj), urllib.request.HTTPRedirectHandler()
        )
        try:
            opener.open(
                urllib.request.Request(
                    "https://umm.gassco.no/", headers={"User-Agent": UA}
                ),
                timeout=40,
            )
            resp = opener.open(
                urllib.request.Request(
                    "https://umm.gassco.no/disclaimer/acceptDisclaimer",
                    headers={"User-Agent": UA},
                ),
                timeout=40,
            )
            html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return None, [f"Gassco scrape failed: {e}"]

    m_val = re.search(
        r"Sum Exit Nominations NCS.*?<div class=\"value\">\s*([\d.]+)",
        html,
        re.S | re.I,
    )
    m_day = re.search(r"gasday\s*<span[^>]*>\s*([^<]+)", html, re.I)
    if not m_val:
        return None, ["Gassco: Sum Exit Nominations NCS not found"]
    value = float(m_val.group(1))
    gasday = (m_day.group(1).strip() if m_day else datetime.now(timezone.utc).date().isoformat())[
        :10
    ]
    row = {"date": gasday, "value": value, "fetched_at": datetime.now(timezone.utc).isoformat()}
    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
                existing[str(o["date"])[:10]] = o
            except Exception:
                continue
    existing[gasday] = row
    with history_path.open("w", encoding="utf-8") as f:
        for d in sorted(existing.keys()):
            f.write(json.dumps(existing[d], ensure_ascii=False) + "\n")
    notes.append(f"Gassco NCS exit: {gasday}={value} MSm3/d (n={len(existing)})")
    return row, notes


def gassco_points_from_history(history_path: Path) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    pts = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        pts.append({"date": str(o["date"])[:10], "value": float(o["value"])})
    return sorted(pts, key=lambda p: p["date"])


def gassco_weekly_avg(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket daily MSm³/d into week-ending-Sunday averages."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for p in points:
        d = date.fromisoformat(str(p["date"])[:10])
        week_end = d + timedelta(days=(6 - d.weekday()))
        buckets[week_end.isoformat()].append(float(p["value"]))
    return [
        {"date": d, "value": round(sum(vs) / len(vs), 2)}
        for d, vs in sorted(buckets.items())
        if vs
    ]


# ---------------------------------------------------------------------------
# UK DESNZ Energy Trends 3.1
# ---------------------------------------------------------------------------


def fetch_uk_indigenous_oil(*, start_year: int = 2015) -> tuple[list[dict[str, Any]], list[str]]:
    """UK indigenous crude oil production (kt/quarter) from latest ET 3.1 workbook."""
    notes: list[str] = []
    try:
        import openpyxl
    except ImportError:
        return [], ["UK ET 3.1: openpyxl missing"]

    html = _http_text(ENERGY_TRENDS_OIL, timeout=60)
    urls = [
        ("https://www.gov.uk" + h if h.startswith("/") else h)
        for h in re.findall(r'href="([^"]*ET_3\.1[^"]+\.xlsx)"', html, re.I)
    ]
    if not urls:
        return [], ["UK ET 3.1: no workbook link on Energy Trends page"]
    url = urls[0]
    notes.append(f"UK ET 3.1: {url.split('/')[-1]}")
    raw = _http_bytes(url, timeout=120)
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)

    pts: list[dict[str, Any]] = []
    # Prefer Annual sheet for long history if structured; else Main Table quarterly
    if "Annual" in wb.sheetnames:
        ws = wb["Annual"]
        # Heuristic: find row with Crude oil and year headers
        headers: list[Any] = []
        for i, row in enumerate(ws.iter_rows(max_row=80, values_only=True)):
            vals = list(row)
            if not headers and any(isinstance(c, int) and 1990 <= c <= 2100 for c in vals):
                headers = vals
                continue
            label = str(vals[0] or "").strip().lower() if vals else ""
            if label.startswith("crude oil") and headers:
                for h, v in zip(headers, vals):
                    if isinstance(h, int) and h >= start_year and v is not None:
                        try:
                            pts.append(
                                {
                                    "date": f"{h:04d}-12-31",
                                    "value": round(float(v), 2),
                                }
                            )
                        except (TypeError, ValueError):
                            pass
                break

    # Quarterly from Main Table (overrides/extends recent)
    if "Main Table" in wb.sheetnames:
        ws = wb["Main Table"]
        header = None
        for row in ws.iter_rows(max_row=40, values_only=True):
            vals = list(row)
            cells = [str(c or "") for c in vals]
            joined = " ".join(cells).lower()
            if header is None and "quarter" in joined and any(str(y) in joined for y in ("2024", "2025", "2026")):
                header = vals
                continue
            label = str(vals[0] or "").strip().lower() if vals else ""
            if header and label == "crude oil":
                for h, v in zip(header[1:], vals[1:]):
                    hs = str(h or "").replace("\n", " ")
                    # e.g. "2025 2nd quarter"
                    m = re.search(r"(20\d{2}).*([1-4])(?:st|nd|rd|th)\s*quarter", hs, re.I)
                    if not m or v is None:
                        continue
                    y, q = int(m.group(1)), int(m.group(2))
                    if y < start_year:
                        continue
                    try:
                        pts.append(
                            {
                                "date": _quarter_end(y, q),
                                "value": round(float(v), 2),
                            }
                        )
                    except (TypeError, ValueError):
                        pass
                break

    # de-dupe by date (quarterly wins over annual same year-end if both)
    by: dict[str, float] = {}
    for p in sorted(pts, key=lambda x: x["date"]):
        by[p["date"]] = p["value"]
    out = [{"date": d, "value": v} for d, v in sorted(by.items())]
    notes.append(f"UK indigenous crude: {len(out)} points")
    return out, notes


# ---------------------------------------------------------------------------
# Abicom (soft-fail)
# ---------------------------------------------------------------------------


def scrape_abicom_parity(history_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Best-effort Abicom diesel/gasoline parity gap (%). Soft-fails on 403/captcha."""
    notes: list[str] = []
    urls = [
        "https://www.abicom.com.br/",
        "https://abicom.com.br/",
    ]
    gap = None
    for url in urls:
        try:
            html = _http_text(url, timeout=30)
        except Exception as e:
            notes.append(f"Abicom {url}: {e}")
            continue
        # look for percentage near defasagem / parity
        m = re.search(
            r"defasagem[^%]{0,80}(-?\d+(?:[.,]\d+)?)\s*%",
            html,
            re.I | re.S,
        )
        if not m:
            m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%[^%]{0,40}defasagem", html, re.I | re.S)
        if m:
            gap = float(m.group(1).replace(",", "."))
            notes.append(f"Abicom gap={gap}% from {url}")
            break
        notes.append(f"Abicom {url}: no gap pattern (len={len(html)})")

    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    o = json.loads(line)
                    existing[str(o["date"])[:10]] = o
                except Exception:
                    pass
    if gap is not None:
        d = datetime.now(timezone.utc).date().isoformat()
        existing[d] = {
            "date": d,
            "value": gap,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        with history_path.open("w", encoding="utf-8") as f:
            for k in sorted(existing.keys()):
                f.write(json.dumps(existing[k]) + "\n")
    pts = [
        {"date": str(o["date"])[:10], "value": float(o["value"])}
        for o in existing.values()
    ]
    pts.sort(key=lambda p: p["date"])
    return pts, notes


# ---------------------------------------------------------------------------
# Wikipedia
# ---------------------------------------------------------------------------


def wikipedia_brand_and_basket(
    *, start_year: int = 2018
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    brands: dict[str, list[dict[str, Any]]] = {}
    start = f"{start_year:04d}0101"
    end = datetime.now(timezone.utc).strftime("%Y%m%d")
    basket_by_month: dict[str, float] = defaultdict(float)

    for key, article in WIKI_ARTICLES:
        url = (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/user/"
            f"{urllib.parse.quote(article, safe='')}/"
            f"monthly/{start}/{end}"
        )
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": WIKI_UA, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            notes.append(f"wiki {key}: {e}")
            brands[key] = []
            continue
        pts = []
        for item in body.get("items") or []:
            ts = str(item.get("timestamp") or "")[:8]
            if len(ts) < 6:
                continue
            d = f"{ts[:4]}-{ts[4:6]}-01"
            v = float(item.get("views") or 0)
            pts.append({"date": d, "value": v})
            basket_by_month[d] += v
        brands[key] = pts
        notes.append(f"wiki {key}: {len(pts)} months")
        time.sleep(0.15)

    basket = [{"date": d, "value": round(v, 1)} for d, v in sorted(basket_by_month.items())]
    return brands, basket, notes
