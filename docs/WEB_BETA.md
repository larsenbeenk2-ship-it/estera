# Estera localhost guide

Estera runs locally at **http://localhost:3000**. Its web interface now begins with phone connection and opens a worldwide night globe once the selected iPhone connects.

## Start or update

For a first installation, run these commands from the project root:

```sh
./scripts/bootstrap
npm run build
npm run dev
```

The updated frontend is already built in `web/dist`, so an existing setup only needs `npm run dev` (or `npm start`) from the project root, then open http://localhost:3000. The server serves that build without hot reload. After changing frontend source, run `npm run build` and refresh the browser. If the server is already running when you update the checkout, stop/reset the phone session, restart the local server, then refresh the browser to load both the new backend and frontend. Python changes require a server restart. Closing the browser tab does not stop a phone session.

If connection reports that the phone service is running the previous version, restart the local service and refresh the page. The app checks the service’s connection capability before sending the request; this message does not indicate an iPhone setup problem.

The npm commands use the portable `scripts/tasks.mjs` dispatcher. Run `npm run stop` from the project root to stop this checkout's instance after checking its identity. Keep the server and computer awake while using a phone session. Estera uses USB only; no account, paid map key, cloud database or VPN is needed.

## Connect your phone

1. Select **Connect your phone** on the welcome screen, then **Set up my iPhone**.
2. Plug in and unlock the phone using a USB data cable, find it, select it and authorize setup.
3. Follow any required Trust, Developer Mode or restart steps. Existing readiness skips completed work. Connection prepares missing developer files with the download and Apple personalization notice beside authorization.
4. Keep the USB cable connected throughout the session. Remembered phones use the same USB connection; there is no transport or setup-method selection.
5. The backend returns the confirmed phone state with the connection result. The globe opens immediately and the dialog closes. The header's phone button manages retry, stop/disconnect and setup.

Old saved Wi-Fi or Auto choices are converted to USB. The backend rejects wireless
connections and Wi-Fi setup requests, including requests from an older browser.
USB setup does not modify existing wireless settings or pairings on the phone.
Apple warns that Developer Mode reduces device security; keep your passcode enabled.

## Find a place

Search the top bar for a full address, city, ZIP or postal code in any country, including names in local scripts. Add the city/country when a query is ambiguous, for example **Shinjuku, Tokyo, Japan**, **SW1A 1AA, United Kingdom**, or **Sydney, Australia**. Results display their full geographic context. Choose a result to fly to it, then use **Start here**, **Add stop here**, or **Use this location**.

For North American telephone area codes, type an explicit query such as **area code 415**. A bundled official NANPA snapshot resolves geographic codes. Most results are state/province/country-level; the label states the precision. Bare three-digit numbers remain place/postal searches, because some countries use three-digit postal codes. Nongeographic/future area codes do not map to an invented location.

The globe, location pins, saved places, GPX tracks and route playback accept valid coordinates worldwide, including territories and routes that cross national borders or the date line. Scroll or pinch to zoom into streets, buildings and house numbers recorded in OpenStreetMap. Missing map data cannot provide exact addresses. A dropped pin uses the nearest known address label without moving the selected point to that address's center. If the geocoder is unavailable, the selected map location still works.

Distance units initially follow your browser's locale; a saved miles/kilometers choice takes precedence. The units control changes both trip measurements and the map scale.

## Pin, walk, bike, drive

Use the four controls on the right. **Pin** sets one chosen location when you explicitly press **Set phone location**. The other modes follow mode-specific roads or paths. Drop a starting pin and destination pin, optionally add intermediate stops, then choose **Find route**.

Selecting a stop's address searches for a replacement. Dragging moves a pin. Stop pauses are available below each address. Reversing stops recalculates directions, respecting one-way roads. **Make a round trip** requests a road return to the start.

Driving uses road travel estimates or a reasonable fallback. The simulation accelerates, brakes before slower segments and stops, and pauses at mapped junction candidates. Known supplied speed limits cap the pace. Live traffic signals and complete posted speed limits are unavailable; waits are simulated. Walk/bike use accessible streets and paths at their appropriate slower paces. Routing favors practical travel time for the selected mode without claiming real-time traffic optimization.

Driving starts in the car on the road near your starting pin, without a walk to the car. For destination stops, **Walk to this stop** is on by default. The car stops by a nearby drivable road, then you get out and walk to the exact pin. Mapped walking paths appear as gold dashes; missing approaches use estimated dotted lines, with a direct road-to-pin walk when the provider finds no walking route. There is no 30-meter mapped-path proximity requirement. The destination stop pause begins at the pin. At intermediate stops, you walk back to the parked car before continuing; the final stop ends at the pin. Turn off the switch to remain by the road. Recalculate older saved routes that still begin with a walk to the car.

**Start drive/ride/walk on iPhone** applies the journey to the selected device. **Preview route** only moves the map marker. Preview defaults to real time; optional 5×/10× rates affect only preview. Pause/resume and **Stop & reset** remain in the playback bar. Editing is locked during an active session. The speed readout follows the same timing model as the backend.

## Saved routes and trip options

Saved places, routes and history remain on this Mac. Saving a route preserves its geometry, pace and stop settings. Under **Trip options** are timer, destination stay/reset, repeats, round trips, recalculation and GPX import/export. Repeats require a closed route.

GPX imports are limited to 5 MiB and 50,000 points and require segment selection. Recorded timestamps are supported only when valid. Imported geometry may not follow roads; recalculate using roads when that is the intended journey. GPX exports preserve geometry/timestamps; saved routes preserve settings GPX cannot encode.

## Availability and privacy

OpenFreeMap supplies map tiles; Nominatim receives submitted searches and selected pins for address lookup; Valhalla receives routing stops. Search uses [Nominatim without a country filter](https://nominatim.org/release-docs/5.0/api/Search/), and directions use the [FOSSGIS global Valhalla service](https://fossgis.de/news/2021-11-12_funding_valhalla/). These public services need internet and may be incomplete or unavailable. Online itineraries allow 25 stops, 500 km total, and 50,000 geometry points. Longer trips must be split. An unavailable route never becomes an invented straight line.

No analytics or accounts were added. Selected-device authorization, local-only server access, strict input validation and storage revisions remain enforced. Acknowledged commands do not independently prove what another iPhone app sees. Simulation changes coordinates, not the phone's IP address.

If connection is lost after the phone has connected, the globe stays visible with
the current route and pins. Phone controls stay disabled, and a banner identifies
the connection that needs attention. Use **Retry connection**, or stop/reset and
disconnect. Automatic recovery is described only on hosts that support it.
Clear/reset requests cannot independently confirm physical-location reacquisition.
Real phone playback remains unverified.

See [DRIVING_MODEL.md](DRIVING_MODEL.md) for timing and provider details and [FRONTEND_DIRECTION.md](FRONTEND_DIRECTION.md) for the design and build evidence.
