# Windows source preview

The 0.1.0 downloadable desktop app is for Mac. Windows shares the source map,
location controls, route engine, GPX and local saved content, using **USB only**.
There is no Windows installer, bundled runtime, dedicated desktop window or updater.
The Mac app also permits USB only.

## Target and evidence

Targets are Windows 10 22H2 and Windows 11 on Intel/AMD x64, with iPhone USB
developer services on iOS 17.4 or later. Windows ARM, x64 emulation on ARM and
32-bit Windows are outside this preview. These are code targets, not claims of
operating-system vendor support or every iPhone/build combination working.

Native Windows execution, Apple driver/service compatibility, real iPhone location
set/reset and physical Windows sleep/wake remain unverified. Tests using fake
devices and injected Windows policy do not establish native Windows behavior.

## Run from source

Install Node.js/npm and uv. In PowerShell or Command Prompt at the repository root:

```text
npm run bootstrap
npm run build
npm start
```

Bootstrap installs the pinned local Python/dependency environment and applies the
project's guarded Apple-personalization patch. It does not install Apple drivers
or change phone settings. No WSL or Git Bash is required.

Open http://localhost:3000 and keep the terminal running. Closing the browser tab
leaves its server and session running. Use Stop/reset before quitting when possible,
then run `npm run stop` from the same checkout or Ctrl-C in the original terminal.
Stop uses a private instance token; it never kills an unrelated port owner.
Rebuild after frontend changes; restart after backend changes.

## USB setup

1. Complete the app's local Apple USB service check. If software is missing, follow
   [Apple's Windows instructions](https://support.apple.com/en-us/118290).
2. Attach a USB data cable, unlock the phone and select the intended device.
3. Approve Trust and complete Developer Mode's on-phone restart/confirmation steps.
4. Complete developer-file preparation, which reuses existing files where possible.
5. Keep the cable connected while applying a location or playing a route.

The app does not replace drivers, manage Apple services or require running the
whole application as Administrator. Maps, search, directions and developer-file
setup can use internet; the selected phone connection stays USB-only.

## Recovery and cleanup

While the same session is running, cable loss waits for the selected phone until
Stop, Disconnect or exit. Recovery restores only successful committed state:

| Previous state | Reconnection behavior |
| --- | --- |
| Fixed location | Reapply and hold |
| Playing route | Restore the committed point and resume |
| Paused route | Restore the point and stay paused |
| Finished route holding its endpoint | Restore the endpoint and hold |
| No successful location command | Reconnect without setting a location |
| Stop, Disconnect or exit | Do not restore the previous simulation |

Offline time does not advance the route. **Keep paused** cancels pending automatic
playback resumption. A stop timer retains its deadline. Recovery is memory-only;
restarting the helper or application does not restore a previous simulation.

Content and developer caches use private storage under `%LOCALAPPDATA%\OpenLocation`.
Pairing records are not copied into saved journeys or logs. Active simulation can
prevent idle sleep; deliberate sleep/shutdown is not overridden. The helper uses
private bounded pipes and a parent-loss watchdog. Cleanup attempts to clear location;
only independent phone observation can confirm physical-location recovery.

## Remaining native work

Explicit software commands are `npm run check` and `npm run check:web`; startup
never runs them automatically. Before claiming Windows support, validate installation,
helper lifecycle, storage and power APIs on each target Windows version, then test
actual Apple service/Trust/setup behavior, set/change/clear, routes, cable loss,
wrong-phone rejection and sleep/wake with authorized hardware.

A Windows binary must be built on Windows x64 and preserve the required native DLL
layout, including `pytun_pmd3/wintun/bin/amd64/wintun.dll`, pywin32, qh3, cryptography
and certificate dependencies. Installer/uninstaller, signing and clean-machine
validation remain future work. Apple developer images are not redistributed.

See [STATUS.md](STATUS.md) for completed checks and
[COMPATIBILITY.md](COMPATIBILITY.md) for hardware evidence limits.
