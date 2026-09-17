# Desktop preview distribution

Estera 0.1.1 targets **Apple Silicon Macs running macOS 15 or later**. The native
AppKit/WKWebView shell bundles its Python runtime, web interface and USB backend.
The download is
[Estera-0.1.1-macos-arm64.zip](https://github.com/larsenbeenk2-ship-it/estera/releases/download/v0.1.1/Estera-0.1.1-macos-arm64.zip)
on the [v0.1.1 release](https://github.com/larsenbeenk2-ship-it/estera/releases/tag/v0.1.1).
See [DESKTOP.md](DESKTOP.md) for installation and [STATUS.md](STATUS.md) for actual
build/runtime evidence and remaining checks.

## Build and bundle

On Apple Silicon macOS with Node.js/npm, uv and Apple Command Line Tools:

```sh
npm run bootstrap
npm --prefix web ci
npm run build:desktop
```

`scripts/build-desktop` creates `dist/Estera.app`, the release ZIP and a SHA-256
checksum. The native executable launches
`Contents/Resources/EsteraBackend/estera-desktop`; keep that runtime's executable,
`_internal` directory, native libraries and symlinks together. The installed app
needs no checkout, external Python, Node.js or Homebrew.

The runtime starts a server bound to `127.0.0.1:3000` and a private helper child.
The native window checks a per-launch identity before loading it. Host/origin
checks, API tokens and explicit selected-device authorization remain enforced.
A conflicting process is never adopted or killed. Closing the last window or
quitting attempts bounded cleanup; the backend and helper also watch their parent.

Saved content, developer images and pairing records remain outside the bundle.
Pair records, private diagnostics, signing credentials and Apple developer images
must not be included in published artifacts. The public website contains no
phone-control backend.

## Signing and release limits

This preview is **locally ad-hoc signed, not Developer ID signed or notarized**.
It is not a universal binary. Intel Mac and Windows installers are not included.
Clean-machine installation and real iPhone set/reset remain unverified. There is
no automatic updater; install subsequent releases manually.

A future notarized release requires an authorized Developer ID identity, deliberate
inside-out signing of nested code, assessment of the completed app, successful
notary submission, and stapling before final packaging. A successful local build
alone does not establish any of those outcomes. Do not disable Gatekeeper globally.

## Licenses and corresponding source

The project is GPL-3.0-or-later. The bundle includes dependency metadata and license
notices; release delivery also includes dependency inventory, corresponding source,
locked build inputs and patches. The repository is
[larsenbeenk2-ship-it/estera](https://github.com/larsenbeenk2-ship-it/estera).
Dependency URLs or hashes alone do not replace applicable GPL corresponding-source
obligations. Downloads and use require neither payment nor a GitHub star.

The older `scripts/build` / `scripts/package` workflow creates the standalone
`dist/OpenLocationBackend` developer helper. It is distinct from the desktop app.
Windows remains a [source preview](WINDOWS.md); a Windows installer needs a native
Windows build, dependency packaging, signing and clean-machine validation.
