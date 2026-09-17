# Transport findings and API evidence

Current product policy (September 17, 2026): Estera is USB-only on every host.
Wireless discovery, connections and setup are unavailable through the app/API.
The wireless findings below are historical implementation research, not offered
setup options. Existing Auto requests resolve to USB without fallback.

Reviewed September 12–13, 2026. Library pin:
[pymobiledevice3 11.12.4](https://github.com/doronz88/pymobiledevice3/releases/tag/v11.12.4),
commit `40795f0`, GPL-3.0-or-later. Chosen verdict: **wrap**. The original workspace
research already compared go-ios and idevice; their alternative language/runtime
tradeoffs do not justify replacing the selected current DVT implementation.
No competing application code or remote scripts were executed.

## USB and local Wi-Fi

The [preferred tunnel](https://github.com/doronz88/pymobiledevice3/blob/v11.12.4/pymobiledevice3/remote/rsd_tunnel.py)
API is `PreferredRsdTunnel(serial, autopair=True, prefer_native=True)`, yielding
connected RSD from `aopen`/an async context manager. It tries native macOS remoted
then userspace. It does **not** expose an explicit USB-vs-Wi-Fi selector, and its
userspace provider can pick an available lockdown connection. We therefore compose
its inspected userspace mechanisms with explicit provider selection instead of
claiming an opaque native connection is necessarily USB.

USB uses `create_using_usbmux(serial=selected, connection_type="USB", autopair=False)`
and `CoreDeviceTunnelProxy.create(lockdown)`. This path requires iOS 17.4+, and that
constraint is enforced. Wi-Fi first tries `connection_type="Network"`, then
Bonjour `_remotepairing._tcp` plus the selected saved identity with `autopair=False`.
On macOS, the local Bonjour wrapper uses the system DNS-SD daemon to browse,
resolve SRV/TXT, and resolve addresses on the original interface. It preserves
IPv6 scope, handles service removal, and bounds services, addresses and time.
Wi-Fi additionally tries direct `_apple-mobdev2._tcp` lockdown on configured LAN
interfaces, using the selected saved pairing and lockdown's fixed port 62078
(matching upstream; RemotePairing continues using its advertised port). Legacy
MAC matching and newer iOS paired-host TXT tags select candidates; authenticated
lockdown and RSD identity checks still gate developer services. Cable-backed
interfaces are excluded. While USB is attached, the opaque usbmux Network path
is skipped in favor of these explicit LAN endpoints. The added discovery and
connection phase has a shared 12-second budget so RemotePairing can still run.
Each Wi-Fi candidate must complete its tunnel, RSD identity check and DVT opening;
a failed Network proxy or tunnel can fall through to RemotePairing. The overall
Wi-Fi deadline is 39 seconds within the existing 45-second connection budget.
It never scans arbitrary subnets or initiates trust during discovery. Auto tries
USB then Wi-Fi only while establishing/recovering, never while healthy.

Explicit Wi-Fi setup enables and reads back both `EnableWifiConnections` and
`EnableWifiDebugging` in `com.apple.mobile.wireless_lockdown` before verifying
RemotePairing. These are separate settings: wireless syncing can be enabled
while debugging is disabled. Setup must not report readiness in that state.
This persists developer access over the local network on the selected trusted
iPhone; ordinary discovery and connection do not change either setting.

The explicit provider's `start_tcp_tunnel()` uses upstream's `USE_USERSPACE_TUNNEL`
factory and `UserspaceDialPlane`. RSD receives the per-instance dialer. This is a
single in-process PyTCP stack with no root requirement or shared tunneld server.
Resource contexts unwind in reverse order, including failed/canceled handshakes.
The authenticated RSD UDID must equal the selected phone before DVT is opened.

[DVT APIs](https://github.com/doronz88/pymobiledevice3/blob/v11.12.4/pymobiledevice3/services/dvt/instruments/location_simulation.py):
`async with DvtProvider(rsd)` followed by `async with LocationSimulation(dvt)`,
`await location.set(latitude, longitude)` and `await location.clear()`. The clear
selector is marked `expects_reply=False`; we report **clear sent**, never a
confirmed restore. No legacy `com.apple.dt.simulatelocation` integration is used.

[Preparation](https://github.com/doronz88/pymobiledevice3/blob/v11.12.4/pymobiledevice3/services/mobile_image_mounter.py)
uses `PersonalizedImageMounter.mount(image, manifest, trustcache)`. Its reachable
[TSS implementation](https://github.com/doronz88/pymobiledevice3/blob/v11.12.4/pymobiledevice3/restore/tss.py)
has an HTTP default and `verify=False`. The build applies a hash-guarded replacement
of that single method; our async HTTPS client verifies certificates, rejects
redirects, bounds requests to 30 seconds/2 MiB and does not trust proxy environment
configuration. No broad networking monkey patch is used. The upstream synchronous,
unbounded DDI downloader is not called. Our async downloader uses the same known
paths at [DDI revision 5423e4e](https://github.com/doronz88/DeveloperDiskImage/tree/5423e4e955fbb3a9eef3e1212acfbfc6e7a26236),
checks the pinned backend's expected `27A5228h` image build, bounds asset sizes,
and performs verified HTTPS only. This is upstream's chosen DDI build, not a claim
of hardware compatibility with every stable iOS release.

## Bluetooth: unavailable

The bounded review of upstream network stacks and location/tunnel code found no
Bluetooth transport to the stock iPhone's DVT location service, with the required
pairing, service endpoint and version contract. [Apple AirDrop security](https://support.apple.com/guide/security/airdrop-security-sec2261183f4/web)
describes BLE discovery and peer-to-peer Wi-Fi transfer; neither establishes
Bluetooth developer-service access. BLE messaging, AWDL and a theoretical PAN are
not interchangeable with this service. No Bluetooth control is offered. Apple's
[Personal Hotspot instructions](https://support.apple.com/en-us/111785) and
[Bluetooth profiles](https://support.apple.com/en-us/102842) describe Bluetooth
PAN networking, but developer-service access through it still needs hardware proof.
The experiment and delivery contract are in [WIRELESS_REBUILD.md](WIRELESS_REBUILD.md). This is
a lack of an established integration, not proof that no future mechanism can exist.

## Private VPN / cellular: implemented, experimental, unverified

The [wda-mcp tunnel example](https://github.com/Qizhan7/wda-mcp/blob/main/scripts/create_tunnel.py)
shows `RemotePairingTunnelService` over a known private address/port. Its script
requires root for its kernel tunnel; we reuse the transport concept with our
userspace TCP tunnel, not its script, WebDriverAgent or AI server.

Remote config requires `enabled=true`, a private IP literal, actual current pairing
port and saved pairing identifier. Allowed address ranges are RFC1918, RFC6598
(Tailscale CGNAT) and IPv6 ULA. No public, loopback, multicast, link-local or DNS
address is accepted. Private IP validation is not authentication: RemotePairing
and a matching RSD device identity are both required. No VPN is installed or
configured by OpenLocation; none is needed for local use.

Gate evidence remains distinct:

1. Attempt TCP reachability to the configured private endpoint.
2. Saved RemotePairing authentication succeeds.
3. Negotiated tunnel and RSD open; identity matches.
4. DVT opens; set/clear results are separately reported.
5. Independently observed fresh phone location and reset: **not tested**.
6. Recreating a new connection while still on cellular: **not tested**.

The selected pairing port is not universal; the service negotiates an additional
TCP tunnel port internally. VPN policies must allow that authenticated path.
Bonjour need not cross the VPN because the endpoint is explicit. An existing
Tailscale installation can be configured by the user; see [iOS VPN On Demand](https://tailscale.com/docs/features/client/ios-vpn-on-demand)
and [other VPN conflicts](https://tailscale.com/docs/reference/faq/other-vpns).
The Mac must stay awake and online. Reports that iPhone Wi-Fi must remain enabled
or that only a Wi-Fi-started session survives are version-specific research leads,
not established behavior here. A VPN ping or hotspot is not evidence for fresh
cellular developer-location control. Do not expose developer services publicly.
