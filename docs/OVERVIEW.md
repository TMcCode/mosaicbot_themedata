# Theme data overview

Series that **inform themes** (macro / industry), not ticker text tables.

## Planned / live series

1. **AISI weekly raw steel** — production + capability utilization (+ districts). Public page = latest week only; history is whatever we append.
2. **Census C-30 construction** — manufacturing, highway/street (and later power / other end markets) via FRED.
3. **Lab economics** — BLS PPI / CES / ECI / QCEW + Census HS `382219` import value/kg/$/kg. Attaches to **MedTech Long '26: Diagnostics & Early Detection** (theme) and **CSTL** (TX QCEW ticker proxy).

## Runtime

- **VPS (Contabo)** — temporary home for API packs (`THEMEDATA_HOST=vps`). Daily dispatcher at 08:00 UTC via `/etc/cron.d/mosaic-themedata`.
- **Mac Mini / residential** — AISI (Cloudflare) and eventual permanent home for this repo (`THEMEDATA_HOST=macmini`).

Cadence: `config/update_schedule.json` + `jobs/dispatch_due.py` (due window **or** stale `as_of`).


## Storage

Private R2: `r2://mosaic-themes/themedata/<pack>/`  
Mapping DB seed: `config/series_attachments.json` (`scope`: `theme` | `ticker`).

## Later

- Phase B Census trade imports
- launchd / cron for monthly lab pack + Monday AISI + monthly Census
- Optional public JSON mirrors on `stockthemes-public`
