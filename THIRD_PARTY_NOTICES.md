# Third-party software notices

OpenLocation (backend and web frontend) is GPL-3.0-or-later. The complete GPL v3 text is in LICENSE;
all original source may be redistributed and modified under GPL version 3 or later.
Copyright 2026 OpenLocation contributors. No warranty is provided.

pymobiledevice3 11.12.4 is Copyright Doron G. and contributors, GPL-3.0-or-later.
Upstream: https://github.com/doronz88/pymobiledevice3/tree/v11.12.4 (40795f0).
Its TSS request implementation is modified by scripts/patch_tss.py to use the
OpenLocation async HTTPS client with verified certificates, bounded response size,
timeouts, and no redirects or insecure fallback. Upstream API code and comments
were consulted for the explicit userspace tunnel composition in adapter.py.

Other direct runtime dependencies: gpxpy 1.6.2 (Apache-2.0), defusedxml 0.7.1
(PSF), httpx 0.28.1 (BSD-3-Clause), pydantic 2.11.7 (MIT). The Python interpreter
is CPython 3.13.14 under the Python Software Foundation license. PyInstaller
6.22.3 uses GPL-2.0-or-later with its distribution exception; this does not change
OpenLocation's GPL-3.0-or-later obligations.

backend/uv.lock contains exact package versions, PyPI artifact URLs and hashes.
Each generated package includes DEPENDENCIES.json (the full locked build
inventory, a superset of frozen imports) and distribution-supplied license texts
under licenses/. The source archive provides our corresponding source and build
scripts. A public distributor must also make complete corresponding source for
redistributed GPL dependencies available; a PyPI URL alone is not a substitute
for fulfilling the chosen GPL distribution method.

Apple frameworks, developer disk images and device services belong to Apple.
No developer disk images, device records or Apple signing material are included.
Preparation downloads only at explicit runtime request and caches outside the
helper. The optional image source is the pinned DeveloperDiskImage repository
revision documented in docs/TRANSPORTS.md; OpenLocation does not claim ownership
or an independent redistribution license for those proprietary assets.

No WDA, GeoPort, Bluetooth accessory or VPN implementation is incorporated.

## Web beta

React / React DOM 19.3.0 (MIT), MapLibre GL JS 6.9.0 (BSD-3-Clause), and
Lucide React 1.45.0 (ISC) form the frontend. Build tools include Vite 8.3.0 (MIT),
TypeScript 7.0.2 (Apache-2.0), the React Vite plugin 6.1.1 (MIT), and Prettier
3.6.2 (MIT). `web/package-lock.json` records exact sources/integrities;
`web/DEPENDENCIES.json` inventories installed packages, including build-only tools,
and `web/licenses` preserves their supplied notices. No proprietary UI assets
or copied branded compositions are used.

Additional Python runtime dependencies for the loopback bridge are FastAPI 0.141.1
(MIT), uvicorn 0.52.4 (BSD-3-Clause), and Shapely 2.1.2 (BSD-3-Clause; its GEOS
distribution notices also apply). All Python transitive versions are locked.

MapLibre loads the OpenFreeMap Liberty/Positron styles and OpenMapTiles vector
tiles as an online service. Required OpenFreeMap/OpenMapTiles/OpenStreetMap
attribution remains visible in the map. OpenStreetMap data is under ODbL:
https://www.openstreetmap.org/copyright. Online services are not bundled software
and their availability is independent of this project.

US coverage uses public US Census Bureau 2025 TIGER state jurisdiction geometry,
filtered to 50 states and DC. Source URL, hashes and included states are recorded
in `backend/src/openlocation_backend/data/us-source.json`. `scripts/us_boundary.py`
documents reproducible derivation with pyshp 3.1.6 (MIT). No proprietary Apple
map data or developer images are bundled with this web application.

## Geographic area-code facts

The frontend's geographic telephone area-code index is derived from NANPA's public
NPA CSV report dated September 13, 2026: https://reports.nanpa.com/public/npa_report.csv.
Source index: https://www.nanpa.com/reports/npa-reports. This is a factual
state/province/country lookup, not a precise coverage boundary. The generated data
records the source SHA-256 and includes a refresh script under `web/src/data`.
The representative 415/628 query uses CPUC coverage information at
https://www.cpuc.ca.gov/415areacode/. No proprietary map imagery or area-code
boundary artwork is redistributed.
