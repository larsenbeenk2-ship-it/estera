# Estera 0.1.0 preview status

September 17, 2026.

The release targets an Apple Silicon macOS 15+ desktop app with a native
AppKit/WKWebView window and bundled Python runtime, web interface and backend.
Windows remains a source preview. All active device connections and setup requests
are **USB only**. There is no paid Pro tier or GitHub-star requirement.

| Area | Current state |
| --- | --- |
| Desktop implementation | Native shell, packaged web/helper entrypoint, launch-identity check and bounded parent-owned cleanup implemented |
| Desktop build/runtime | Mac bundle built; strict bundle signature verification passed; packaged app opened its native window and displayed the USB connection screen |
| USB policy | Wireless discovery/connection/setup rejected; old Auto preferences use USB without fallback |
| Device authorization | Loopback host/origin/token checks and explicit selected-phone consent retained |
| Frontend | Worldwide map, search, Pin/Walk/Bike/Drive, route preview, GPX, saved journeys and session controls |
| Download target | `Estera-0.1.0-macos-arm64.zip` on GitHub tag `v0.1.0` |
| Signing | Ad-hoc preview; no Developer ID signing or notarization |
| Windows | USB source preview; no installer or native Windows/iPhone evidence |
| Real hardware | Location set/reset and physical-location recovery remain unverified |
| Fresh installation | Clean-machine installation remains unverified |

## Checks completed for this revision

- **4 focused desktop/backend tests passed**: per-launch identity remains distinct
  from API authorization; origin and device-consent rules hold; source bootstrap
  remains compatible; parent exit and parent-identity mismatch are handled.
- **9 focused USB policy checks passed** for the preceding USB-only change.
- App and website production builds passed. The native application was launched
  on the build Mac and its USB connection screen was observed. No phone connection
  or location change was performed by this release task.

These are software checks. They do not establish packaged installation, every
Mac/iPhone combination, or physical location reset. No broader pass is implied.

The public repository is
[larsenbeenk2-ship-it/estera](https://github.com/larsenbeenk2-ship-it/estera).
The public website is a demo/download site; device control runs locally.

See [DESKTOP.md](DESKTOP.md) for the user guide,
[DISTRIBUTION.md](DISTRIBUTION.md) for release packaging,
[COMPATIBILITY.md](COMPATIBILITY.md) for hardware acceptance, and
[WINDOWS.md](WINDOWS.md) for the Windows source preview.
