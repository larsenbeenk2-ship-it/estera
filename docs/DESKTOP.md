# Estera for Mac — 0.1.1 preview

Estera is a free, GPL-3.0-or-later desktop iPhone location simulator. The Mac
download includes its native window, Python runtime, backend and web interface.
Node.js, Python, Homebrew and a terminal are not needed to use this download.

## Install and open

1. Download `Estera-0.1.1-macos-arm64.zip` from the GitHub release.
2. Unzip it and drag **Estera.app** to **Applications**.
3. Open Estera. If macOS blocks this preview because it is not notarized, review
   the release and use **System Settings → Privacy & Security → Open Anyway**.
   Do not disable Gatekeeper globally.
4. Connect your iPhone with a USB data cable, unlock it, and complete the app's
   Trust and Developer Mode setup. Keep the cable connected throughout the session.

This preview targets **Apple Silicon Macs running macOS 15 or later** and iPhone
USB developer services on **iOS 17.4 or later**. Those version targets do not
guarantee every device/build combination. The final desktop package has not been
validated for location set/reset on physical iPhone hardware. Intel Mac and Windows
installers are not included. Windows remains a [source preview](WINDOWS.md).

The app has a local window and starts its own private server on port 3000. It
refuses to adopt an unrelated service. If the source/browser version is already
running, stop it from that checkout with `npm run stop`, then reopen the desktop app.
Quit Estera or close its last window to shut down its server and helper. Shutdown
attempts to clear simulated location; an unplugged or unavailable phone cannot
confirm reset. Use Stop/reset before quitting when possible.

Maps, address search, directions and initial developer-file setup need internet.
Device control stays on your Mac over USB. The public website is a demo/download
site; it does not connect to your phone. No account or GitHub star is required.
If you find Estera useful, a star on GitHub is appreciated.

## Preview limitations

- The app is locally ad-hoc signed, **not Developer ID signed or notarized**.
- No automatic updater: install a newer release manually from the repository.
- Real-device compatibility and clean-machine installation remain unverified.
- The website illustrates behavior; routes and provider data are simulations,
  not guarantees of road speed limits or live traffic-light timing.

## Build from source

On Apple Silicon macOS, install Node.js/npm, uv, and Apple Command Line Tools.
From the repository root:

```sh
npm run bootstrap
npm --prefix web ci
npm run build:desktop
```

The build creates `dist/Estera.app`, a ZIP download, and its SHA-256 checksum.
Source lives in `desktop/macos`, `web`, `backend`, `packaging`, and `scripts`.
The native shell uses Apple's AppKit and WebKit; the bundled backend uses the
same USB policy as the source app. Dependency locks, patches, licenses and a
dependency-source archive accompany the release.
