# Architecture

The current frontend is React/Vite in `web/src`, served by the loopback-only
FastAPI bridge in `web.py`. `web_client.py` owns one private helper child and
streams its events to the browser. `providers.py` wraps worldwide search,
reverse geocoding and directions; `coverage.py` validates coordinates and route
geometry without a country restriction. UI state never impersonates a phone session.
The web frontend uses the helper's atomic content store. A future native client
can embed the standalone helper and replace this web presentation.

The helper owns transport resources, monotonic playback timing, the coordinate
writer, stop/reset, a session timer and an idle-sleep assertion. `adapter.py` is the
only device API integration. `session.py` separates transport and playback state;
`route.py` is pure timeline math; `models.py` validates the entire boundary.
`protocol.py` owns request concurrency and preemption. `storage.py` uses atomic
replace, fsync, mode 0600 files, advisory locking and a revision precondition.
`preparation.py` owns verified network requests and cancelable image downloads.

The helper itself has no listening server. The web bridge binds only loopback,
checks Host/Origin/CSRF and selected-device authorization, and exposes a narrow
operation allowlist. No WDA, relay, database server, subscription, account, root
process or coordinate-per-process command invocation is included. Upstream has many
capabilities and dependencies; the wrapper exposes only the documented allowlist.
Installed upstream requirements are not removed. The frozen import graph omits
unused CLI, IPython and testing paths deliberately. Images and pair records live
outside the immutable helper distribution.

## State and evidence

Each new connection gets a random session ID; the frontend supplies it on every
consequential session command. Selecting another device requires disconnect first.
Discovery never selects a phone. Errors and state events expose the session ID
even if opening its connection failed, so retry/disconnect can target it explicitly.
Transport advertisements are deduplicated by full device identity in private IPC;
full identifiers and precise locations never enter routine logs.

Events contain monotonically increasing sequence numbers and route revisions.
Discard events for any replaced session and any older revision. Device actions
are serialized; stop/disconnect cancel an in-flight action before clearing. A
late/stale stop cannot cancel new session work. Mutations fail `busy` instead of
queuing. Output responses are bounded and playback events coalesced, so a slow
consumer cannot create an unbounded coordinate queue.

Route progress commits after a successful transport write. Time elapsed during
pause or a disconnected interval does not advance it. During healthy playback,
slow writes skip intermediate updates rather than send a catch-up burst. A pause
or cancellation freezes at the last committed point; an interrupted write may
have reached the phone and is uncertain. Mac Resume explicitly reapplies that point. Windows automatic USB recovery reapplies the committed point before restoring prior playback intent.
Speed changes preserve the current leg/fraction, including dwell. Recorded timing
uses GPX timestamps and intentionally rejects custom speed changes. Duplicate
points take no time at constant speed, but timestamped stationary intervals remain.

The backend supports 1–10,000 repetitions or indefinite loops of closed tracks.
An open track must include an explicitly previewed return geometry before repeat
is accepted. There is no invisible end-to-start teleport. Raw reversal is clearly
labeled; the frontend must recalculate road-aware directions when needed.

`set_result_received`, `clear_sent`, `unknown` and `not_observed` are literal
levels of evidence. The backend cannot inspect another app's displayed location.
It never claims altitude, heading, velocity/accuracy metadata or sensor simulation.
Location simulation does not alter IP addresses or all location signals.

## Bundling contract

Copy the **entire** `OpenLocationBackend` directory, with symlinks preserved, into
`YourApp.app/Contents/Helpers/OpenLocationBackend`. Resolve the executable relative
to `Bundle.main.bundleURL`, not a checkout or working directory. Launch it with
`Foundation.Process`, `.executableURL`, and `.arguments = []`; use two private
`Pipe` objects. Await `hello` before enabling actions. Read stdout while commands
run, match response IDs, and drain/redact stderr separately. Do not use a shell.

The parent closes stdin on quit, waits up to 12 seconds, then terminates/kills a
stuck helper. The helper observes EOF, signals and parent identity changes. If a
phone is unreachable, cleanup cannot prove reset; retain an uncertainty message.
Keep no simulated session in launch restoration data. Missing executable,
`protocol_mismatch`, process exit and startup dependency errors must be visible
in the future app. Prompt text and all controls remain frontend work.

## Privacy and network notices

Before `device.prepare` with pairing, Wi-Fi changes or downloads, or `connect`
with `prepare: true`, the frontend must explain those exact requested steps.
Connection can own image preparation under its existing selected-session lock;
success returns the confirmed status to enter the map immediately. USB Trust, Developer Mode, phone unlock,
Local Network access and private-VPN setup are user-controlled. Preparation can
contact the pinned GitHub image repository and Apple's verified HTTPS TSS endpoint;
Apple may receive device-specific preparation data. The future MapKit search and
directions features also involve Apple. Do not claim every setup step is offline.

The helper creates local data with umask 077 and reuses supported upstream pairing
storage. It does not copy records into its own content store, export credentials,
change unrelated records, remove passcodes or browse phone personal content.
An idle-sleep assertion is held only for active/paused/holding simulation, and
released on stop, disconnect or loss. Closing the lid, shutdown and lost connectivity
can still interrupt playback; no global sleep settings are changed.


## Windows host boundary

`host.py` owns immutable backend host policy. Windows advertises USB only and
normalizes legacy auto requests to USB; every discovery/setup/connect/recovery
path pins the local Apple mux endpoint. macOS retains USB/Wi-Fi/experimental remote
behavior. `host.check` reads service/protocol prerequisites without pairing.

Windows uses bounded threaded binary helper pipes, a retained parent process
handle, private LOCALAPPDATA storage/ACLs, Windows file locks, and scoped power
notifications. The source web launcher claims a private per-checkout instance
record; authenticated `/api/shutdown` requests normal shutdown without locating or
killing arbitrary port owners. The existing loopback/Host/Origin/token gates apply.

Session generations invalidate stale writes and recovery before terminal actions
await cancellation. Successful writes alone commit recovery intent. Windows
restores that intent and resumes only previously playing routes after USB returns;
Mac reconnect continues to require manual Resume. Offline Pause cancels pending
Windows route resumption. The stop timer is not extended. Nothing resumes after
helper restart. See [Windows behavior and validation](WINDOWS.md).
