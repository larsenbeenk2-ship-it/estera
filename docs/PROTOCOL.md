# Private helper protocol, version 1

The local web bridge (or a future native app) launches the backend using argument
arrays and owns its stdin/stdout pipes. The helper has no listening server or
shell-command endpoint. First send `{"id":"1","op":"hello","params":{"version":1}}`.
Each request has a unique string ID (1–64 characters), an allowlisted operation,
an object `params`, and, for device/session actions, the returned `session_id`.
Every response has `id`, `ok`, and either `result` or a structured `error`.
Events carry `event`, `session_id`, `route_revision` and a monotonic sequence.
Status snapshots also include `sequence`; discard queued events older than that
checkpoint. A fresh helper process starts a new sequence space.
Status also includes `requested_transport`, `ever_connected`, and a redacted
`connection_error` (or null). Preserve the actual setup error on initial failure;
only describe a connection as lost when it had previously been established.
Status additionally includes `connection_path` and `connection_attempts` for
compatibility. Current connections use USB only; Wi-Fi and private VPN requests
fail with `unsupported_transport` before any device or network operation.
An authorized web connection attempt retains authorization for its matching failed
session so an explicit retry is possible; an unrelated session is not authorized.
Successful `connect` and `retry` results include a complete `status` snapshot with
its `sequence`. Clients apply that snapshot immediately instead of requiring a
second status request before entering the connected interface.
The `connection_preparation: true` capability advertises this integrated connection
flow. The web client checks it before sending `prepare: true`; an older running
service gets an explicit restart message instead of an invalid-input error.
The `initial_setup` capability with `version: 2` and `transports: ["usb"]`
means preparation sets up USB developer services only. The web client
requires version 2 before local `prepare: true` connections, so an older helper
cannot silently enable Wi-Fi during USB setup. Request protocol version 1 and
browser preference version 1 remain unchanged.
Status `setup_complete` becomes true after USB developer services are ready. It survives a subsequent initial connection failure and
retry, avoiding repeated preparation, and resets on disconnect or a new session.
It describes preparation in this session, not a persistent reachability guarantee.
Successful writes and clears report transport-level evidence only; phone output
is never reported as independently observed.

Limits: 8 MiB per JSON line, 16 in-flight requests, 32 queued responses, 256 IDs
remembered against accidental retries. Use a new ID per action; duplicate IDs
are rejected, never replayed. Playback events are coalesced. No coordinate queue.
Input NaN/Infinity, unknown fields/actions and invalid sessions are rejected.
The pipes are the authorization boundary: never expose them over the network.

Device mutation is single-owner. Busy actions fail rather than accumulate.
Stop/disconnect/cancel preempt active work and cancel future coordinates before
attempting clear. A canceled or timed-out write may have reached the phone and
must be treated as uncertain. EOF, SIGTERM and parent death attempt bounded clear
and cleanup. The parent should close stdin, wait 12 seconds, then terminate and
finally kill a nonresponsive helper. Never restore a live session at launch.

Discovery timeout: 20 s; preparation: 300 s; opening the connection: 45 s.
Connection/retry operations allow 355 s for preparation, opening and cleanup;
the web helper waits 365 s for their response. Device write: 5 s;
stop/disconnect: 10 s; content operations: 15 s. Recovery retries after
1, 2, 4, 8, 16, 30 seconds, then stops on macOS; Mac recovery requires manual Resume. Windows keeps waiting for USB and restores committed intent automatically, as described below.
Non-retryable setup errors end automatic recovery early. Legacy `auto` requests
resolve to USB; an absent cable never triggers a wireless fallback.

Route/search preview is independent of device state. The frontend supplies real
resolved geometry from the web bridge's Valhalla provider, MapKit, or an explicitly
drawn/imported track. This helper
never generates fake road directions or silently connects GPX segments.

## Operation parameters

All parameter objects reject unknown fields. Empty means `{}`. `hello` is handled
in order before other input. `params` may be omitted for empty operations. The
machine-readable request schema is `docs/protocol.schema.json`.

| Operation | Parameters | Result |
| --- | --- | --- |
| hello | `version: 1` | Versions, operations and limits |
| capabilities / status | Empty | Supported functionality / current session state |
| devices.list | Optional `transport: usb`, default `usb`; legacy `auto` aliases USB | USB iPhones, available Trust/Developer Mode evidence, warnings; selection unchanged |
| device.check | `device_id`, plus current envelope `session_id` when selected | Read-only USB/Trust/Developer Mode/image readiness and next setup step |
| device.prepare | `device_id`, optional `pair`, `reveal_developer_mode`, `mount_image`, `allow_download` booleans; current envelope `session_id` when selected; `enable_wifi: true` is rejected | Actual USB setup results; async preparation events |
| connect | `device_id`, optional `transport: usb` and `prepare: false`; legacy `auto` aliases USB | New `session_id`, USB transport, device OS/build/model, sequenced `status` |
| disconnect / retry | Empty + envelope `session_id` | Cleanup evidence / connected with platform-specific recovery outcome and sequenced `status` |
| location.set | `coordinate: {latitude, longitude}` + session | Set-result evidence |
| route.preview / route.reverse | `route` | Offline preview or labeled raw reversal; never moves device |
| route.start | `route` + session | Initial set result, then playback events |
| pause / resume | Empty + session | Playback state |
| speed | `speed_mps: [0.01,100]` + session | Updated timeline; recorded timing rejects this |
| stop | Empty + session | `clear_sent`, `restoration_confirmed: false`, guidance |
| timer | `seconds: (0,86400]` or null + session | Stop-after timer; null cancels |
| cancel | `request_id` | Whether a pending request was canceled |
| shutdown | Empty | Stop/disconnect, then exit |
| gpx.inspect | `xml` | Explicit segment indexes, points, timing availability |
| gpx.import | `xml`, `segment_index`, optional `timing: constant/recorded` | One complete route object |
| gpx.export | `route` | XML; no arbitrary filesystem writes |
| store.list | `bucket`, optional `offset`, `limit` (1–100) | Summaries plus current revision |
| store.get | `bucket`, `item_id` | Full payload and revision |
| store.put | `bucket`, `name`, `payload`, `revision`, optional existing `item_id` | Saved item ID and new revision |
| store.delete | `bucket`, `item_id`, `revision` | Whether deleted and new revision |
| store.clear | `bucket`, `revision` | Deleted count and new revision |

A device ID is the real selected UDID returned by discovery, carried only on
private pipes. The authenticated RSD UDID must match device_id. Older wireless
parameter shapes remain parseable for a structured `unsupported_transport`
response; they do not enable wireless access.

`connect` with `prepare: true` includes developer-image preparation and any
necessary downloads for the selected USB-connected iPhone. Preparation leaves
wireless settings and saved pairings unchanged. The initial connection
authorization must disclose downloads and Apple personalization. Preparation runs
inside the connection's serialized, cancellable operation, with `transport_state`
remaining `connecting` until the actual location service opens. A failed initial
retry repeats incomplete preparation but reuses completed preparation for the same
request; retrying or recovering an established USB connection skips it. Omitting the flag preserves
the existing connection-only behavior. Trust, unlocking, enabling Developer Mode
and restart confirmation remain phone-controlled actions.

`devices.list` inspects only USB usbmux entries and returns
before Bonjour or remote pairing-record discovery. Omitted/`auto` also means USB;
`wifi` is rejected. Network entries do not
consume the USB device limit. Discovery uses `autopair=false`, does not change the
selected identity, and reports `paired`/`developer_mode` only when observed. An
already trusted phone does not need another Trust prompt. `device.check` reads the
explicitly selected phone before the USB wizard offers its next setup action.
Readiness `next_step` is `reconnect_usb`, `wait_usb`, `unlock`, `trust`,
`developer_mode`, `developer_image` or `connect`. `wait_usb` preserves the USB
observation when phone services are unready; `unlock` does not request new Trust.
After a reachability failure, one fresh enumeration distinguishes a detached
phone from a still-visible one. Checks keep the selected serial and never pair.
Missing metadata remains visible for retry but cannot establish authenticated
identity. The 15-second protocol deadline covers bounded 2-second enumeration,
8-second readiness and at most one 2-second enumeration retry.

Setup may continue within the same failed initial session, before any developer
connection or location write. Established sessions must disconnect first. All
flags default to false. `reveal_developer_mode` only reveals the Settings option;
the phone's enable, restart and final confirmation remain user actions. See
IPHONE_SETUP.md for the complete guided setup contract.

A route has:

```json
{
  "name": "Example imported track",
  "points": [
    {"latitude": 37.0, "longitude": -122.0, "time": null},
    {"latitude": 37.001, "longitude": -122.001, "time": null}
  ],
  "waypoints": [{"name": "Start", "point_index": 0, "dwell_seconds": 2.0}],
  "source": "gpx",
  "mode": "walking",
  "speed_mps": 1.4,
  "timing": "constant",
  "repeat_count": 1,
  "loop": false,
  "completion": "hold"
}
```

At least two points and 1 cm of usable movement are required. Latitude is [-90,90],
longitude [-180,180]; only finite numeric values are accepted. Custom speed is 0.01–100 m/s. Timestamps are UTC
Unix seconds; recorded mode requires all timestamps strictly increasing and travel
at most 100 m/s. A repeated/looping route's endpoints must be within 0.5 m. Up to
1,000 named waypoints can reference resolved geometry indexes, each with up to
24 hours of dwell. The frontend may rename/reorder/remove waypoints and submit a
new preview without affecting the current session. Geometry must be recalculated
by its actual provider if reordered waypoints change the road route.

`source` is `road`, `mapkit`, `gpx`, `drawn` or `reversed_track`. `mode` is walking/cycling/
driving. Mode changes do not fabricate routing geometry or metadata. mph = m/s ×
2.2369362921; km/h = m/s × 3.6. Keep those conversions in the frontend. A missing
MapKit cycling route must be surfaced there rather than replaced with driving.

Content buckets are `locations`, `routes`, `location_history`, `route_history`.
Location payloads are plain coordinates; route payloads preserve the entire route.
Bookmark capacity is 200 per kind; history retains the newest 50 locations and 20
routes. The JSON file is limited to 64 MiB. Writes require the latest revision
from a list/get result. A full route's geometry is returned only by get to keep
list replies small. Persistence is atomic but not encrypted; it is local user data.

Examples of response and asynchronous event:

```json
{"id":"12","ok":true,"result":{"evidence":"set_result_received","phone_observation":"not_observed"}}
{"id":"13","ok":false,"error":{"code":"stale_session","message":"This action belongs to a replaced or missing device session.","retryable":false}}
{"event":"progress","session_id":"SESSION_ID","route_revision":0,"sequence":7,"data":{"stage":"connecting","transport":"wifi"}}
```

The frontend should read structured validation errors without logging the original
payload. Device error messages omit raw upstream exceptions. Treat cancellation
and timeouts of side effects as unknown outcomes, not guaranteed rollback. A
canceled content write may finish atomically in its worker; reread the revision
before retrying. Never repeat a device write automatically on a timeout.


## Host capabilities and Windows USB recovery (additive IPC v1)

`hello` and `capabilities` now include `host` with `os` (`macos`, `windows`, or
`unsupported`), `architecture`, `supported`, and `os_version`; `cable_required`;
and `recovery` with `automatic`, `restore_location`, `resume_playback`, and
`retry_until_stopped`. Windows transport lists contain only `usb`, including
`initial_setup.transports`; experimental transports are empty and Wi-Fi flags are
false. A client must not require Wi-Fi to accept complete USB setup.

`host.check` takes `{}` and returns `{host, supported, usb_service, message}`.
`usb_service` is `ready`, `missing`, `stopped`, `unavailable`, or `not_required`.
It is read-only, requires no selected session, and never installs drivers or pairs.
Windows `auto` is accepted as USB; Wi-Fi/remote and `enable_wifi:true` preparation
are rejected with `unsupported_transport` before effects. Unsupported host device
operations return `unsupported_host`. The protocol version remains 1.

Status adds `recovery: {state, action}`. State is `idle`, `waiting_usb`, `connecting`,
`restoring`, or `needs_attention`. Action is `none`, `restore_location`,
`resume_route`, or `keep_paused`. In an interrupted Windows route with pending
`resume_route`, `pause` is allowed with the selected session while disconnected;
it changes intent to `keep_paused` without sending a device write.

`reconnected` retains `resume_required` and adds `restored` and `resumed` booleans.
On Windows they reflect completed restore writes; on Mac manual Resume is still
required. Evidence remains `set_result_received`, `clear_sent`, or unknown, never
independent observation of the phone's actual location. Clients must reconcile
session IDs, route revisions, and monotonic event sequence numbers as before.

The source web launcher also exposes `POST /api/shutdown` with `{instance_id}`,
protected by the existing same-origin and token checks plus private instance
identity. It shuts down the web owner and helper after acknowledging, and is
separate from the helper-only IPC `shutdown` operation. Instance records contain
secrets and must never be included in source archives or diagnostics.
