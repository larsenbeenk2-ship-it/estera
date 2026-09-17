# Estera — world after dark

September 13, 2026. Local web delivery at localhost:3000. This direction supersedes the earlier Location Studio left-panel design.

Visual thesis: a near-black globe with spectral mint street light, quiet geographic detail, and a small travel instrument on the right. Interaction thesis: connect one selected iPhone, find a real place, then follow a road journey at a believable pace.

The primary surfaces are a focused onboarding sequence and a spatial product workspace. The shared motif is the mint ghost and the route line. No new rendering framework, paid map account, imagery, remote storage, or tracking was introduced. The installed MapLibre and existing OpenFreeMap/Valhalla/Nominatim integration remain the foundation.

## Direction and palette

Considered a black/acid-green navigation display, a pale atlas over dark seas, and a graphite/mint globe. The mint globe preserves the user's nighttime green cue while allowing road hierarchy, house footprints, and labels to remain readable. Black is the geographic canvas; mint belongs to road illumination, selected travel modes, and the main action. Warm amber is reserved for simulated stops and caution. Error and success remain separate semantic roles.

CSS tokens own UI colors: canvas `#090e11`, panels `#111b1c`, raised surfaces `#192727`, text `#edf6f1`, secondary text `#9eafa7`, borders `#2b3c37`, and action mint `#a1f6ce`. MapView declares the corresponding cartographic palette, atmosphere, route and junction colors. The favicon is a brand asset using the same canvas/mint pair. System sans supplies headings and body; monospace is limited to speed and stop identifiers.

Desktop dedicates the viewport to geography below the search header. A 336px right panel contains four travel choices, address stops, route summary and primary action. Extra trip settings are disclosed only when requested. Search results and selected-place cards use real place names and full address context. Numeric coordinate forms and the left settings panel were removed. The phone status is available from the header and panel footer.

Mobile keeps search above a usable map, then places the travel panel below it. Playback controls remain available at the bottom of the task. Essential controls never depend on hover; search, stop replacement, and fit/recenter buttons provide alternatives to dragging.

## User journey

1. Welcome: Connect your phone. No map editing or pin movement is available before a real connection.
2. Setup: choose guided help or the quick path, then USB, Auto, same Wi-Fi or private VPN. Every route starts with the cable, explicit device selection and authorization, and actual readiness checks. Existing Trust/Developer Mode skip unnecessary instructions. Wi-Fi pairing precedes the actual Wi-Fi connection; only a confirmed Wi-Fi session permits the unplug message.
3. Explore: the globe opens after the selected phone connects. Scroll/pinch from countries to roads and houses; use the top search for a city, address or postal code. Explicit `area code 415` queries use the bundled NANPA lookup with visible precision labels.
4. Move: choose Pin, Walk, Bike or Drive. Drop start/destination pins or use search results as stops. Street/address names replace raw coordinates. Find route requests mode-specific roads or paths. Start on iPhone is separate from map preview.
5. Follow or recover: normal preview speed is real time. Both preview and phone playback use the same acceleration-aware timeline. Pause, resume and stop/reset remain explicit. A lost connection returns to the gate with recovery controls; it does not silently resume.

## Interaction contract

Globe gestures use MapLibre's native input. Globe projection blends into Mercator at street zooms, preserving the provider's road/building/house-number labels. Search camera movement respects reduced motion and reserves space for the travel controls. New input interrupts camera animation; manual panning exits follow. Dateline routes are split for rendering and unwrapped for fit bounds. The globe, worker and observers are disposed on unmount; missing WebGL/data leaves an address-search path and retry controls.

After a one-second pause, location search offers up to five worldwide suggestions from Photon; it only shows provider-backed places and cancels stale type-ahead work. Submitted searches remain separate and use Nominatim, because its public API does not permit autocomplete. Pin address lookups preserve the selected coordinates; absent data leaves a usable labeled map point. Planning changes are locked during active phone playback. Route calculation cancellation never becomes a straight-line fallback. Saved and GPX tracks remain available in secondary controls, with explicit recalculation to use current roads.

Preview starts at 1×; optional 5×/10× speeds are explicitly labeled preview-only. It pauses when the page becomes hidden. Driving uses provider timing or a disclosed fallback, with acceleration, braking and simulated intersection holds. Exact speed limits and live signal timing are never invented. See [DRIVING_MODEL.md](DRIVING_MODEL.md).

## Foundation and provenance

Verdict: serious fit found; adapt the existing maintained mapping and routing foundation. No third-party branded interface or proprietary code was copied.

- [MapLibre projection specification](https://maplibre.org/maplibre-style-spec/projection/)
- [MapLibre globe implementation guide](https://github.com/maplibre/maplibre-gl-js/blob/main/developer-guides/globe.md)
- [MapLibre atmosphere settings](https://maplibre.org/maplibre-style-spec/sky/)
- [OpenFreeMap quick start](https://openfreemap.org/quick_start/)
- [NANPA geographic area-code reports](https://www.nanpa.com/reports/npa-reports)
- [CPUC 415/628 coverage](https://www.cpuc.ca.gov/415areacode/)

## Delivery evidence

`npm --prefix web run build` passed TypeScript and produced the updated `web/dist` assets. Vite reported its existing large MapLibre chunk warning; the map is lazy-loaded behind the phone gate. The backend's focused offline checks passed 17 tests. Source inspection covers worldwide validation, the gate, connection methods, map projection, address handling, route modes and matching acceleration interpolation. UI tokens and cartographic colors are declared separately.

No app, dev server, browser, screenshot, or real phone session was started, following the user's execution preference. Desktop/mobile composition, live tile/provider responses and actual phone playback remain unverified on hardware. An already-running Python server must be restarted to load the backend changes, then its browser page refreshed.

### Integration follow-up

Region search results now carry validated bounding boxes, so cities, countries and area-code regions fit the visible map instead of zooming to a single centroid. Exact address markers retain their selected coordinates. Phone-service startup and stream failures retry without reopening the map until fresh device status arrives; a visible Retry now action is available. Paused phone routes display zero speed. Imported tracks no longer claim road acceleration they do not contain.

The production TypeScript/Vite build passed again after these functional changes. No previous successful backend tests were repeated. Browser rendering, complete simulated setup flows and actual phone playback remain pending; implementation/build evidence alone is not proof of those runtime outcomes. The active goal remains open for that remaining evidence.
