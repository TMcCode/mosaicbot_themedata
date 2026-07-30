# Theme data overview

Series that **inform themes** (macro / industry), not ticker text tables.

## Planned series

1. **AISI weekly raw steel** — production + capability utilization (+ districts). Public page = latest week only; history is whatever we append.
2. **Census C-30 construction** — manufacturing, highway/street (and later power / other end markets) via FRED.

## Runtime

Prefer **Mac Mini / residential** for AISI (Cloudflare). Census/FRED is fine anywhere.

## Later

- Theme ↔ dataset attachment map
- Publish JSON to R2 (`stockthemes-public` or private `mosaic-themes`)
- launchd plist for Monday AISI + monthly Census
