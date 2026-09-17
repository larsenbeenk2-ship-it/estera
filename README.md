# Estera

A free, open-source desktop iPhone location simulator. The **0.1.0 Mac preview**
bundles a native AppKit/WebKit window, Python runtime, device backend and worldwide
map interface. It targets **Apple Silicon Macs on macOS 15 or later** and uses
**USB only** to connect to an iPhone.

[Download for Mac](https://github.com/larsenbeenk2-ship-it/estera/releases/download/v0.1.0/Estera-0.1.0-macos-arm64.zip)
· [Release notes](https://github.com/larsenbeenk2-ship-it/estera/releases/tag/v0.1.0)
· [Install and use](docs/DESKTOP.md)
· [GitHub](https://github.com/larsenbeenk2-ship-it/estera)
· [Website](https://estera-virid.vercel.app)

Unzip the download, move **Estera.app** to Applications, and open it. The preview
is ad-hoc signed and **not notarized**; macOS may require approval in System
Settings → Privacy & Security → Open Anyway. No Node.js, Python, Homebrew or
terminal is needed to use the download. See the [desktop guide](docs/DESKTOP.md)
for setup, shutdown and port-conflict guidance.

Estera is GPL-3.0-or-later. There is no paid Pro tier, account requirement or
GitHub-star requirement. If it is useful to you, a star is appreciated.

## What it does

- Search worldwide addresses and places, set a pin, or plan Walk, Bike and Drive routes.
- Preview routes, pause/resume, change pace, add timers, import/export GPX and save journeys locally.
- Guide USB Trust, Developer Mode and developer-file setup before explicitly authorized device actions.
- Attempt location reset through Stop/reset and bounded shutdown cleanup.

Driving follows provider road geometry with acceleration, braking and simulated
intersection waits. Available provider data informs pace; live traffic-light
timing and exact road speed limits are not guaranteed. Maps, search, directions
and initial developer-file setup need internet. Device control stays on your
computer. The public website is a demo and download site.

Keep a USB data cable connected throughout the session. Both Mac and Windows
source code reject wireless connection/setup requests; old Auto preferences use
USB without fallback. Existing wireless settings and pairings are left unchanged.
No location is sent until you explicitly set a location or start a route.

This is a preview: **physical iPhone location set/reset and clean-machine
installation remain unverified**. An accepted clear command does not independently
confirm physical-location recovery. See [compatibility](docs/COMPATIBILITY.md)
and [current evidence](docs/STATUS.md). Windows is a [source preview](docs/WINDOWS.md);
Intel Mac and Windows installers are not included.

## Run from source

Install Node.js/npm and uv, then run from the repository root:

```sh
npm run bootstrap
npm run build
npm start
```

Open http://localhost:3000 and keep the terminal running. Closing the browser tab
leaves the server running. Use Stop/reset before quitting when possible; use
`npm run stop` from this checkout or Ctrl-C in its original terminal to shut down.
After frontend changes, rebuild and refresh. After backend changes, restart.

To build the Mac desktop preview on Apple Silicon with Apple Command Line Tools:

```sh
npm run bootstrap
npm --prefix web ci
npm run build:desktop
```

The desktop build creates `dist/Estera.app` and its downloadable ZIP. See
[distribution](docs/DISTRIBUTION.md) for bundle layout, signing and source delivery.
The separate `scripts/build` and `scripts/package` commands still produce the
standalone backend helper for developers; that helper is not the desktop download.

Source lives in `desktop/macos`, `web/src` and `backend/src/openlocation_backend`.
Dependencies are pinned and locked. Explicit software checks are `npm run check`
and `npm run check:web`; ordinary startup does not run them.

Further technical detail: [web workflows](docs/WEB_BETA.md),
[driving model](docs/DRIVING_MODEL.md), [IPC](docs/PROTOCOL.md), and
[architecture](docs/ARCHITECTURE.md).
