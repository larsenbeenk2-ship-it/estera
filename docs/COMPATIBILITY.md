# Compatibility and hardware evidence

Estera 0.1.0 is a Mac desktop preview with a separate Windows source preview.
Version targets describe the implementation; they do not establish compatibility
with every device or operating-system build.

| Component | Preview target | Evidence or limitation |
| --- | --- | --- |
| Mac desktop | Apple Silicon, macOS 15 or later | Build/runtime status in [STATUS.md](STATUS.md); clean-machine installation unverified |
| iPhone connection | USB developer services, iOS 17.4 or later | Real location set/reset remains unverified |
| iOS 17.0–17.3 | Outside the USB target | Required USB proxy is unavailable |
| Intel Mac | No packaged release | No Intel or universal artifact |
| Windows | Windows 10 22H2 / Windows 11 x64 source preview | No installer or native Windows/iPhone validation |
| Wi-Fi, VPN, cellular, Bluetooth | Not offered | Active connection/setup policy permits USB only |
| Stop/reset | Sends clear through the developer service | Physical-location recovery is not independently confirmed |
| Signing | Local ad-hoc signature | No Developer ID signature or notarization |

There are **no hardware-verified location set/reset combinations for this desktop
preview**. Software tests, a successful build, and device-service acknowledgments
are different evidence from observing fresh location output on an iPhone.

Maps, search, directions and developer-file setup may need internet. Public
providers have capacity limits and no availability guarantee. Route timing is a
simulation; it does not guarantee live traffic-light timing or road speed limits.

## Optional hardware acceptance checklist

Run with the device owner's authorization. Record model, exact iOS/macOS or
Windows build, application version, date and an independent observation method.
Keep full device identifiers, pairing records and private route history out of
public reports.

- Connect the selected phone over USB, complete Trust/Developer Mode/setup, set A,
  change to B, clear, and independently observe location output and recovery.
- Preview and start a route; exercise pause, speed, resume, dwell, repeat, timer,
  finish/hold and Stop/reset.
- Lose the cable during playback and clear; reconnect the same phone and reset.
  Confirm the platform's documented recovery behavior and wrong-phone rejection.
- Close the last window or quit during playback; confirm the private processes
  exit and record reset uncertainty if the phone cannot be reached.
- Exercise deliberate sleep/wake and a fresh installation on another target Mac.

Mark each result observed, failed or not tested. Do not infer all-iPhone support
from a moving map pin. Windows-specific prerequisites and pending native checks
are in [WINDOWS.md](WINDOWS.md).
