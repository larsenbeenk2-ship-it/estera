# Wireless backend rebuild

## Delivery plan

1. Preserve explicit USB selection and USB-first automatic connections.
2. Treat the upstream first-pair completion signal as success, reopen the USB
   pairing channel and verify the saved pairing before reporting setup complete.
3. Use macOS system Bonjour discovery, which resolves services on their originating
   interfaces, with bounded time and resource cleanup.
4. Try complete Wi-Fi connections through Network lockdown and authenticated
   RemotePairing. A failed candidate releases all resources before another starts.
5. Let already paired phones reconnect without a cable. Separate saved pairing,
   discovery, authenticated connection and usable location-service states.
6. Expose bounded, sanitized connection-attempt details for later hardware diagnosis.

## Backend contract

- `connect`: existing `device_id`, `transport`, `prepare`, and remote configuration
  remain compatible. USB stays on the explicit USB provider. Auto tries USB first
  and switches only on transport absence/loss.
- `connect(transport="usb", prepare=true)` prepares developer services only.
  Explicit `transport="auto"|"wifi"` additionally enables and verifies wireless
  pairing over trusted USB, then opens the chosen connection. An omitted transport
  may connect using existing pairing but does not authorize new wireless setup.
  Existing pairings are preserved when choosing USB.
  `prepare=false` uses saved pairing and never requires or modifies USB setup.
- Complete preparation for the requested connection sets session `setup_complete`, even if opening the chosen
  connection later fails. Retry skips that completed preparation. The browser
  remembers the phone only after this backend result and offers a compact Connect
  screen on later visits. The browser record contains no pairing secrets and never
  substitutes for authentication or implies Wi-Fi readiness after USB setup.
  iOS maintenance can require a targeted repair. `initial_setup.version=2` identifies
  this opt-in policy; the updated UI rejects older helpers for guided preparation.
- `device.prepare(enable_wifi=true)` enables network lockdown, saves/validates the
  RemotePairing record and returns separate setup results. Pairing alone never
  reports a connected session. Existing trust/ownership checks remain required.
- `devices.list(transport="wifi")` can include saved pairing identities. They are
  explicitly unverified candidates; discovery does not authenticate the phone.
- A successful connection must open the tunnel, authenticate the exact selected
  iPhone through RSD and open its DVT location service. Connecting sends no location.
  A read-only DVT clock probe must also succeed before reporting connected. Monitoring
  checks the same authenticated developer connection used for location simulation,
  with a five-second interval and timeout. Loss of the separate optional remote
  lockdown service must not trigger disconnection of a healthy DVT connection.
  The adapter owns its monitor for the connection's lifetime. Cancelling or replacing
  a session observer cannot cancel the tunnel's socket reader; adapter close still
  cancels monitoring and releases every owned service.
  Session status retains `last_connection_loss` across recovery attempts, including
  the observer that detected loss, elapsed time, successful heartbeat count, and
  sanitized error. This prevents a later discovery failure from hiding the original
  disconnect. No upstream exception text, endpoints, or pairing material is retained.
- One session owns pairing and connection work. Cancellation unwinds all owned
  resources. Retries reuse saved pairing; established recovery does not repeat USB
  preparation or resume location playback.
- Each Wi-Fi candidate has a deadline within the existing 45-second connection
  budget. Attempts are sequential because the userspace tunnel stack is global.
  Errors distinguish missing pairing, discovery failure, reachability, setup,
  identity and dependency failures. Public diagnostics omit endpoints and secrets.
- Pair records remain in the existing upstream store; no database migration is
  needed. The later live-setup fixes add pinned native certificate support without
  upgrading existing dependencies. Source and hardware evidence are recorded below.

## Bluetooth boundary and next experiment

The pinned pymobiledevice3 library has no Bluetooth provider for iPhone DVT location
services. Apple documents Bluetooth Personal Hotspot/PAN, which carries IP traffic;
it does not document access to iPhone developer services through that interface.

Before offering Bluetooth as working, the hardware experiment must establish:

1. This Mac and iPhone can create an actual Bluetooth PAN network interface.
2. The selected iPhone advertises or exposes lockdown/RemotePairing on that link.
3. Every service and tunnel socket is routed through that PAN interface.
4. A fresh selected-device-authenticated DVT connection works with Wi-Fi and USB
   unavailable, followed by an independently observed set and clear.

If any gate fails, Bluetooth-only control is not supported by this backend/platform.
An ordinary Bluetooth pairing or Wi-Fi connection cannot substitute for these gates.
No working Bluetooth transport is claimed by this rebuild.

Sources: [upstream tunnel guide](https://doronz88.github.io/pymobiledevice3/guides/ios17-tunnels/),
[Apple Personal Hotspot](https://support.apple.com/en-us/111785),
[Apple Bluetooth profiles](https://support.apple.com/en-us/102842),
[Xcode network connections](https://help.apple.com/xcode/mac/current/en.lproj/dev3e2f4ee6d.html).

## Evidence

### Architecture assessment

Keep the existing pymobiledevice3 services and explicit USB/Network/RemotePairing
providers. The library's `PreferredRsdTunnel` is simpler and prefers macOS's native
`remoted` tunnel, but its public constructor does not select USB versus Wi-Fi.
Using that opaque choice would weaken the explicit transport contract. The native
path is a future comparison candidate if its actual bearer can be established;
upstream describes performance advantages, not evidence measured in this app.
[Upstream tunnel guide](https://doronz88.github.io/pymobiledevice3/guides/ios17-tunnels/).

System DNS-SD is the preferred discovery foundation for the current macOS target.
The maintained [python-zeroconf asyncio API](https://python-zeroconf.readthedocs.io/en/latest/_modules/zeroconf/asyncio.html)
would reduce application-level glue and improve portability, but it owns its own
multicast sockets and adds a dependency. The existing upstream raw browser retains
the interface weaknesses addressed here. Keep the native wrapper isolated behind
its small async API; a larger rewrite is not justified without live evidence.

The custom native bridge remains a maintenance cost. Its application timeout is
not an absolute operating-system deadline: synchronous DNS-SD IPC can wait on a
stalled daemon before the event loop resumes. Apple's client code has its own
daemon-response safeguard. Current default browsing covers the same-network
requirement; peer-to-peer interfaces are not enabled. Neither choice establishes
Bluetooth support. [Apple DNS-SD client](https://raw.githubusercontent.com/apple-oss-distributions/mDNSResponder/main/mDNSShared/dnssd_clientstub.c),
[P2P flag](https://developer.apple.com/documentation/dnssd/kdnsserviceflagsincludep2p).

Optimality is still unproven. Next hardware evidence should measure fresh Wi-Fi
setup, a new connection without USB, cable removal, reconnection after network
loss, and set/clear observation. Tune retry order/deadlines from those results;
do not infer performance or reliability from fake-service tests.

### Live diagnosis and app integration — September 14, 2026

The reported older “Paired iPhone unreachable” message came from a server/helper
started before the source rebuild. Its session was failed, had never connected,
and had sent no location commands. The main frontend was built successfully and
that server was gracefully restarted. The new helper advertises the wireless
capability and returns connection-attempt diagnostics. A frontend compatibility
guard now rejects explicit Wi-Fi connection against an older helper.

One connection-only attempt to the previously selected phone returned
`wifi_pairing_required`: no saved Wi-Fi pairing exists and Network lockdown did
not connect. A selected-device USB readiness check confirmed USB connected,
existing Trust, Developer Mode enabled, and developer image not mounted. The
developer files are already cached locally.

The proposed next operation mounts those cached files and enables/verifies Wi-Fi
pairing, with downloads disabled and no location commands. Automatic approval
review rejected it because on-phone setup changes and possible device-data
disclosure to Apple's personalization service require specific user approval.
That preparation operation did not run. The new server remains available on
port 3000; Wi-Fi setup and live connection success are still outstanding.

### Earlier unified initial setup follow-up (superseded by explicit Wi-Fi choice)

Guided setup now prepares both USB and Wi-Fi for every local transport choice,
reuses developer files, and remembers the completed phone in optional browser
preferences. Returning phones get Connect with USB/Auto/Wi-Fi choices. An explicit
cached-only repair handles missing developer services without repeating the wizard.

Six focused setup/retry cases passed on the first invocation. The added disconnect
case exposed the fake device's intentional rejection of clear commands; after
closing that fake connection before disconnect, that remaining case passed in
0.16 seconds. All seven selected cases passed; no successful case was rerun.
The TypeScript/Vite frontend build succeeded, with the existing MapLibre chunk-size
warning. No app, browser, server restart or phone operation ran for this follow-up.
The running backend needs an orderly restart and page reload to load the changes;
the frontend rejects an older backend for unified setup. Live Wi-Fi and Bluetooth
success remain unverified.

### Live setup investigation

The user subsequently approved restarting the backend, which now advertises
unified initial setup v1. The same selected phone is connected by USB, trusted,
and in Developer Mode, with its image not mounted. After separate explicit user
approval for phone setup and possible Apple personalization, a cached-only
preparation attempt ran and failed with `developer_image_cache_invalid` before
mounting or Wi-Fi pairing.

Read-only inspection found the cached DMG and trust cache match both the SHA-1
and SHA-384 digests in the pinned manifest. The manifest contains 141 identities,
one without payload digests. The validator incorrectly requires every identity
to contain a digest, misclassifying these valid cached files as damaged. The parser
now accepts applicable byte digests and preserves SHA-384 preference. Five focused
cases pass, including corruption, stronger-hash mismatch and missing-digest rejection.
No redownload was needed for the inspected files.

The next attempt failed certificate verification against Apple's TSS endpoint.
Both certifi and Python's default CA bundle rejected the chain, while macOS curl
accepted it with certificate verification enabled. Pinned `truststore==0.10.4`
now supplies native system verification to preparation's HTTP clients. A HEAD
request with no device data confirmed TLS and hostname checks succeeded; eight
focused TSS/download cases pass. Existing dependency versions are unchanged and
the hashed requirements export was regenerated. See the
[truststore native-context API](https://truststore.readthedocs.io/en/latest/).

With those failures resolved, the phone rejected the image upload with
`ReceiveBytes: DeviceLocked`. The upstream wrapper hid that response in a generic
exception, producing misleading developer-image guidance. This now maps to
`unlock_required` without exposing the raw response; five focused cases pass.
The user has explicitly approved cached-file mounting, Wi-Fi pairing, and Apple
authorization; that approval remains valid. Downloads remain disabled and no
location command has been sent.

After the user unlocked the phone, the same cached-only preparation succeeded:
`image=mounted`, `wifi_enabled=true`, `wifi_pairing_verified=true`, and
`setup_complete=true`. No developer-file download was needed. The corrected backend
was started, and an explicit `connect(transport="wifi", prepare=false)` succeeded
through `network_lockdown`. The returned identity matched the selected iPhone and
the connection reached `wireless_service` with status `connected`. This opens the
authenticated DVT location service without sending any location command.

The backend was left running with that Wi-Fi connection. Cable-removal recovery,
physical observation of set/clear, and Bluetooth remain untested. The earlier
approval block is resolved by the user's explicit consent; no phone authorization
is outstanding for the setup just completed.

Browser setup preferences now also record an authenticated successful Wi-Fi
connection from bootstrap/status/connection snapshots and state events, before
React can unmount the dialog. This covers a phone prepared outside the wizard;
USB-only quick success still does not claim Wi-Fi setup is complete. The final
TypeScript/Vite build passed with the existing MapLibre chunk-size warning.
Reload the page to use the new frontend; the live backend session was preserved.

### Earlier source-only evidence

Implemented in source: verified USB Wi-Fi bootstrap, native macOS DNS-SD,
complete network-provider fallback, cable-free quick reconnect and sanitized
attempt diagnostics. Bluetooth remains unavailable pending the experiment above.

One focused offline pytest invocation passed 26 cases in 0.36 seconds. It covered
first-time and existing pairing, failed verification, network proxy/tunnel fallback,
identity/setup failures, cancellation, saved-device discovery, scoped addresses,
integrated preparation, retained diagnostics, native-reference cleanup, required
macOS library symbols, and existing explicit-USB/Auto behavior. Services were fake;
the native symbol check loaded the system library without starting discovery.

During that initial source-only work, no app, browser, server, device session, multicast discovery, location command,
frontend build or packaged-helper build was run. Existing built artifacts need a
rebuild and an orderly server restart to use all source changes. No Wi-Fi or
Bluetooth hardware success is established by these local checks.
