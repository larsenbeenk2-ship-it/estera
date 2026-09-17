# Original local web beta plan (historical)

This records the original US-only release. Its geographic restrictions and visual
direction have been superseded by the worldwide app described in
[WEB_BETA.md](WEB_BETA.md) and [FRONTEND_DIRECTION.md](FRONTEND_DIRECTION.md).

The current goal adds a complete US-only web interface at localhost:3000. This
supersedes the earlier native-only frontend decision; the private Python helper
remains the device owner. No Apple account, map key, VPN, or cloud server is needed
for the normal local workflow. Live device actions require the user's explicit
selection and authorization in the interface.

## Direction

Primary surface: product task; modifier: high trust. The user plans a journey,
previews it, then deliberately applies it to their selected phone. Device state
comes only from the helper. Preview state is local and always labeled.

Visual thesis: an open road atlas, with warm paper controls, clear black typography,
pale water and park cartography, and a vermilion route running through the map.
Interaction thesis: the same itinerary moves from editable stops to a scrubbable
preview to a separately authorized phone session, retaining geographic context.

Compared a dark navigation console and a light atlas. The atlas wins for road-label
legibility and long planning sessions. No acquisition hero or dashboard tiles.
Intensity is subtle. System sans for copy, compact serif headings for geographic
editorial character, tabular monospaced coordinates and timing.

Palette: paper #f7f7f2, white surfaces, ink #252c29, secondary #65716b, boundaries
#dfe3dc. Orange-red #c94e32 is the route and primary application action, recalling
annotated road maps. Moss #597354 is geographic context; blue is map water and
keyboard focus. Errors use dark red, warnings ochre, successful contact green.
Status always also has a word or icon. Color is concentrated on map geometry and
the current action. No decorative gradients or animated background.

First act: choose a point or search a US place, with editable numeric coordinates.
Middle: build named stops, calculate road geometry, inspect distance and timing,
scrub a clearly labeled preview. Saved itineraries retain the actual path.
Final: choose and authorize a device, then set/start, pause/resume, or stop/reset.
The second focal interaction is loading a saved journey and restoring all settings.

Desktop uses a 340px planning rail and flexible map, with a stable transport bar.
Narrow screens place the map above the planner, keeping search, stops and primary
action reachable by scrolling. Dialogs use native focus containment. Controls have
visible labels/focus, map selection has numeric alternatives, and stop reordering
has buttons. Reduced motion removes map flights and marker transitions. The map
has ordinary pan/zoom controls and manual follow; it never re-centers against a pan.
No location permissions are requested.

## Foundations and provenance

- Adopt MapLibre GL JS 6.9.0, BSD-3-Clause. Keyless OpenFreeMap Liberty style
  supplies detailed OSM vector roads. Attribution stays visible. Leaflet was
  considered; vector labels and road styling favor MapLibre for this product.
  https://maplibre.org/maplibre-gl-js/docs/
  https://openfreemap.org/quick_start/
- Wrap the public FOSSGIS Valhalla service for this private, low-volume beta.
  Pedestrian/bicycle/auto are distinct profiles. Cache results and bound requests;
  public distribution must use an appropriately provisioned endpoint and follow
  provider policy. https://github.com/valhalla/valhalla#demo-server
- Wrap Nominatim for explicit submitted searches only, never autocomplete; use
  identifying headers, cache and one request per second. A separate, low-volume
  Photon endpoint supplies up to five real worldwide type-ahead suggestions after a
  one-second pause, without a bundled place database. Queries leave the computer.
  https://operations.osmfoundation.org/policies/nominatim/
- US coverage is 50 states plus DC (including Alaska/Hawaii), excluding territories,
  using US Census 2025 TIGER jurisdiction boundaries. These geographic boundaries are
  a product coverage boundary, not a legal/survey-grade national-border authority.
  https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip
- Bounded public X search identified OpenFreeMap creator @hyperknot; primary
  project docs establish technical/licensing decisions. No proprietary UI copied.
  https://x.com/hyperknot

## Integration contract

FastAPI serves the built React/Vite assets and API only on 127.0.0.1:3000. One
private helper process owns all device writes. Strict Host/Origin checks and a
per-process CSRF token protect API requests; there is no permissive CORS. Bodies,
provider results, pending work and event queues are bounded. Provider errors do
not become invented straight-line road routes. Drawn tracks are explicitly chosen.

The browser stores only preferences locally. Places/routes/history use the existing
atomic backend store. No active session auto-restores from browser storage.
The bridge validates US coverage on route planning, GPX, saved data and phone actions.
Stopping and bridge shutdown use the existing bounded cancellation/clear path.

## Acceptance

- Build and serve localhost:3000; no frontend placeholders.
- Real US search, road map, route geometry in all three modes; errors/cancel handled.
- Pin/coordinate editing, route stops/reorder/dwell/reverse, preview/scrub, speeds,
  repetitions/closed loops, hold/clear, timers, GPX and complete saved settings.
- Device discovery/setup/selection, USB/Wi-Fi/Auto and gated experimental remote;
  live state/progress, pause/resume/stop and honest loss/reset evidence.
- US restriction on every consequential route/coordinate entry path; local-only
  request boundary. Meaningful focused tests plus actual browser integration.
- Document startup, provider limitations, backend/hardware verification evidence.
