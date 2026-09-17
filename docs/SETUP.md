# Build and use the backend

For Windows 10/11 x64 source development and USB-only setup, see [WINDOWS.md](WINDOWS.md).
The portable commands also work on Mac; the host-specific build notes below describe the existing macOS artifact.

For the current localhost application, start with [WEB_BETA.md](WEB_BETA.md).
This document covers the device helper, phone prerequisites and standalone build.

## Development

The build used macOS 26.6.2 (25G83), arm64, SDK 26.5, Swift 6.3.3, and project-local
CPython 3.13.14. Full Xcode is not installed and is not required for this Python
helper. No native UI target is built. The intended later application floor is
macOS 15; only the current host build is established here.

From the source root:

```sh
./scripts/bootstrap
./scripts/build
./scripts/package
```

`bootstrap` uses existing uv and installs Python only under `.toolchains/python`
and dependencies in `backend/.venv`. The committed lock pins the complete package
graph, sources and hashes. Sync uses `--locked`, which fails if metadata differs;
normal scripts never resolve an upgrade. A reproducible source patch validates
the exact upstream TSS module hash before modifying it. The runtime fails closed
if the patch is absent. `build` freezes the patched helper with PyInstaller's
one-directory format. `package` creates binary and project source `.tar.gz`
archives with SHA-256 sidecars. No signing identity or Apple account is used.

If uv is unavailable, use an existing Python **3.13.14**, create `backend/.venv`
with `python3.13 -m venv backend/.venv`, install `backend/requirements.lock.txt`
using `pip install --require-hashes -r backend/requirements.lock.txt`, then install
our package with `pip install --no-deps --no-build-isolation ./backend` (the export
includes the pinned hatchling build backend). Apply `scripts/patch_tss.py` with
that interpreter and invoke PyInstaller using `packaging/backend.spec`. Do not
install into system Python. This fallback is documented, not exercised here.

To intentionally update dependencies, edit metadata, run `uv lock --project
backend`, inspect the new sources/lock and regenerate the requirements export.
A pymobiledevice3 change also requires source review and a new TSS fingerprint.
Do not bypass the patch guard or disable TLS verification.

Developer-file downloads and Apple personalization use the pinned `truststore`
library with the operating system's native certificate trust checks. Hostname and
certificate verification remain required; the app does not add trusted roots or
change the device pairing TLS context. This supports macOS-managed trusted
certificates that Python's static CA bundle does not include.

Tests are explicit and separate: `./scripts/check`. They use fake devices and a
fake clock, never an iPhone. See STATUS.md for checks actually executed.

## Calling the helper

The frozen executable is `dist/OpenLocationBackend/openlocation-backend`.
`--version` prints build/protocol information without discovering or modifying a
device. With no arguments it uses NDJSON stdin/stdout. It has no public socket.
For frontend integration use PROTOCOL.md and `protocol.schema.json`.

Connect USB, unlock the intended iPhone, and choose it from `devices.list`.
Discovery is not authorization to change its location. Ask the user to perform
Trust on the phone when needed. `device.prepare` uses explicit booleans:
`pair`, `reveal_developer_mode`, `enable_wifi`, `mount_image`, `allow_download` all default to false.
Do not silently set these flags. Developer Mode must be enabled on the phone by
the user; it can require a restart. No passcode removal is needed. The connection
dialog now provides this guided setup; `reveal_developer_mode` makes a missing
toggle visible and `device.check` reads readiness after the user restarts.
See IPHONE_SETUP.md. Preparation can continue in the same selected failed initial
session, but never in an established or previously active session.

Preparation downloads the pinned upstream personalized DDI, checks the expected
build ID and requests verified Apple personalization where necessary. A device's
existing mounted image is reused. Download progress is in bytes; cancellation
removes partial downloads. Assets stay under `~/Library/Application Support/
OpenLocation/developer-images`, outside a future signed bundle. Preparation is
bounded to five minutes. If an image is incompatible with an exact iOS build,
report that hardware failure and update reviewed assets rather than bypassing
Apple signature checks. No Apple images are redistributed in this artifact.

Estera has one USB setup flow. It reuses or prepares developer files without
changing wireless settings or pairings. An omitted transport defaults to USB;
legacy Auto requests also resolve to USB without fallback. Wireless connection
requests and `device.prepare(enable_wifi=true)` are rejected before device I/O.

After complete preparation the browser remembers the phone.
Later visits offer a compact Connect screen using `prepare=false`, without
repeating the setup wizard. This browser preference contains no pairing secrets;
the backend still authenticates every connection. If connection fails after
preparation completes, retry reuses that preparation. Keep the phone unlocked
when connecting and keep its USB cable attached throughout the session.

iOS updates, restarts, or trust resets can require a specific repair. Missing
developer services can be prepared again over USB using cached files with downloads
disabled; Apple personalization may still be needed. This is a separate explicit
repair, not a repeat of normal initial setup. Removed pairing requires pairing again.

Phone discovery inspects USB devices only. It does not browse Bonjour, read
wireless pairing records, or ask for private VPN details.

After connection loss the backend freezes progress, attempts bounded transport
recovery, and requires `resume` or a new explicit location action. `retry` is manual
recovery after failures; stop/disconnect cancels recovery. If stop cannot reach
the phone, reconnect and stop/reset; restarting the phone is a practical fallback.
A successful clear send still does not independently confirm physical-location
reacquisition. Hardware testing needs explicit authorization for the selected phone.

## Local content

`content.json` lives under Application Support/OpenLocation. It contains bookmarks,
complete route settings/geometry and explicit 50-location/20-route history buckets.
The frontend records history through `store.put` after the action it wants to
remember; the helper never silently restarts stored sessions. Use the latest store
revision on every put/delete/clear. List returns summaries; get returns one item.
Saved routes retain geometry and can replay offline without recalculating directions.
GPX export preserves points/timestamps; save full route JSON to retain mode, speed,
waypoint names/dwell, loops and completion behavior. Users' input files are never
modified: import/export takes and returns content, not arbitrary filesystem paths.

### macOS native build prerequisite

The cached cryptography 50.0.1 extension carried downloaded-file quarantine metadata:
its ad-hoc signature was valid, but macOS refused to load it for build analysis.
`scripts/prepare_native.py` makes a private bytes-and-mode copy of that exact
locked extension, then ad-hoc signs the local copy. It removes only `com.apple.quarantine` from that private copy after signing,
because this host can tag newly generated native files too. Build finalization
likewise removes that attribute only from generated Mach-O files inside
`dist/OpenLocationBackend`. Neither step touches uv's cache inode or changes
system Gatekeeper policy. Bootstrap/build run this narrowly scoped prerequisite. A
standard-library-venv fallback must run the same script before packaging.
