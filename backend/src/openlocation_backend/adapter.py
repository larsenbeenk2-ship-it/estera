"""Narrow adapter to the reviewed pymobiledevice3 11.12.4 APIs.

Uses explicit providers rather than PreferredRsdTunnel's opaque transport fallback:
USB means USB; Wi-Fi uses LAN lockdown or Bonjour RemotePairing. One userspace
stack is permitted per helper. No root, tunneld, arbitrary network scan or HTTP API.
"""
import asyncio
import contextlib
import errno
import ipaddress
import plistlib
from contextlib import AsyncExitStack
from itertools import islice
from pathlib import Path

from packaging.version import Version

from .errors import BackendError, public_error, transport_unavailable
from .models import Connect, Prepare
from .host import HOST_POLICY
from .network_interfaces import local_network_interfaces


async def discover(transport="usb", *, policy=None):
    policy = policy or HOST_POLICY
    transport = policy.normalize_transport(transport)
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux

    connection_types = {"auto": {"USB", "Network"}, "usb": {"USB"}, "wifi": {"Network"}}[transport]
    devices = {}
    warnings = []
    try:
        async with asyncio.timeout(3):
            mux_devices = await list_devices(**policy.mux_options)
    except Exception:
        mux_devices = []
        warnings.append("USB discovery unavailable; check the cable and Apple device support on this computer.")
    metadata_deadline = asyncio.get_running_loop().time() + 8
    for device in [device for device in mux_devices if device.connection_type in connection_types][:32]:
        item = devices.setdefault(device.serial, {"device_id": device.serial, "name": "iPhone (unlock for details)",
                    "os_version": None, "transports": [], "state": "discovered"})
        mode = "usb" if device.connection_type == "USB" else "wifi"
        if mode not in item["transports"]:
            item["transports"].append(mode)
        if mode == "wifi":
            item["wifi_reachability"] = "advertised"
        if transport != "usb" and asyncio.get_running_loop().time() >= metadata_deadline:
            continue
        try:
            async with asyncio.timeout(2):
                async with await create_using_usbmux(serial=device.serial, connection_type=device.connection_type, autopair=False, **policy.mux_options) as lockdown:
                    device_class = lockdown.all_values.get("DeviceClass")
                    if device_class and device_class != "iPhone":
                        devices.pop(device.serial, None)
                        continue
                    if not device_class:
                        item["state"] = "setup_required"
                        continue
                    item.update(name=lockdown.all_values.get("DeviceName", "iPhone"), os_version=lockdown.product_version,
                                os_build=lockdown.all_values.get("BuildVersion"), model=lockdown.all_values.get("ProductType"),
                                paired=bool(lockdown.session_id))
                    if not lockdown.session_id:
                        item["state"] = "setup_required"
                    else:
                        item["developer_mode"] = await lockdown.get_developer_mode_status()
                        if item["developer_mode"] is not True:
                            item["state"] = "setup_required"
        except Exception:
            item["state"] = "setup_required"
    if transport == "usb":
        # USB setup does not need network discovery or stored remote pairing identities.
        return {"devices": list(devices.values()), "warnings": warnings, "selection_changed": False}

    try:
        from .bonjour import browse_remotepairing

        advertisements = await browse_remotepairing(timeout=2)
        if not advertisements:
            warnings.append("No RemotePairing service was discovered. Saved phones can still be tried through network lockdown; unlock the iPhone and keep both devices on the same network.")
    except Exception:
        advertisements = []
        warnings.append("Wi-Fi discovery unavailable. Check Local Network permission and network isolation.")
    # Saved pairing is a selectable candidate even without Bonjour. It is never
    # evidence that an unrelated advertisement belongs to that phone.
    try:
        from pymobiledevice3.pair_records import iter_remote_paired_identifiers
        from .models import DeviceTarget
        for identifier in islice(iter_remote_paired_identifiers(), 32):
            try:
                DeviceTarget(device_id=identifier)
            except ValueError:
                continue
            devices.setdefault(identifier, {"device_id": identifier, "name": "Previously paired Apple device",
                "os_version": None, "transports": ["wifi"], "state": "paired", "wifi_reachability": "not_checked"})
    except Exception:
        warnings.append("Saved Wi-Fi pairing records could not be read. Reconnect with USB to prepare Wi-Fi again.")
    return {"devices": list(devices.values()), "warnings": warnings, "selection_changed": False}


async def check_setup(device_id, *, policy=None):
    """Read readiness for an explicitly selected USB phone; no setup side effects."""
    policy = policy or HOST_POLICY
    policy.ensure_supported()
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.mobile_image_mounter import PersonalizedImageMounter

    result = {"usb_connected": False, "paired": False, "developer_mode": None,
              "image_mounted": None, "next_step": "reconnect_usb",
              "message": "Plug this iPhone back into this computer with USB and unlock it, then check again."}
    async def selected_usb_present():
        # Each call opens a fresh mux connection; numeric attachment IDs change on reboot.
        async with asyncio.timeout(2):
            return any(d.serial == device_id and d.connection_type == "USB" for d in await list_devices(**policy.mux_options))

    try:
        result["usb_connected"] = await selected_usb_present()
    except Exception:
        raise BackendError("usb_discovery_unavailable", "This computer could not check USB devices. Reconnect the cable, unlock the iPhone and check that it appears in Apple Devices (Windows) or Finder (Mac), then try again.", True) from None
    if not result["usb_connected"]:
        return result
    try:
        # Leave time within the protocol deadline to distinguish a detached phone from
        # one that is visible over USB but whose services have not returned after reboot.
        async with asyncio.timeout(8):
            async with await create_using_usbmux(serial=device_id, connection_type="USB", autopair=False, **policy.mux_options) as lockdown:
                device_class = lockdown.all_values.get("DeviceClass")
                if (lockdown.udid and lockdown.udid != device_id) or (device_class and device_class != "iPhone"):
                    raise BackendError("wrong_device", "The connected device does not match the selected iPhone.")
                if not lockdown.udid or not device_class:
                    return {**result, "next_step": "wait_usb", "message": "This computer can see this USB device, but its iPhone identity is not available yet. Unlock the selected iPhone, reconnect its cable and check again."}
                result["paired"] = bool(lockdown.session_id)
                if not result["paired"]:
                    return {**result, "next_step": "trust", "message": "Unlock the iPhone and choose Trust when you pair it with this computer."}
                if Version(lockdown.product_version) < Version("17.4"):
                    raise BackendError("unsupported_version", "USB developer connections require iOS 17.4 or newer.")
                result["developer_mode"] = await lockdown.get_developer_mode_status()
                if result["developer_mode"] is not True:
                    return {**result, "next_step": "developer_mode", "message": "Enable Developer Mode in iPhone Settings, restart, then unlock and confirm Turn On. If the setting is missing, use Show Developer Mode setting."}
                async with PersonalizedImageMounter(lockdown) as mounter:
                    result["image_mounted"] = await mounter.is_image_mounted("Personalized")
                if not result["image_mounted"]:
                    return {**result, "next_step": "developer_image", "message": "Developer Mode is on. Prepare the developer services needed for this connection."}
                return {**result, "next_step": "connect", "message": "USB Trust, Developer Mode and the developer image are ready. Connecting will check the location service."}
    except Exception as exc:
        error = public_error(exc)
        if type(exc).__name__ in {"PasswordRequiredError", "DeviceLockedError"}:
            return {**result, "next_step": "unlock", "message": "The iPhone is connected over USB but is locked. After a restart, unlock it with its passcode, confirm Turn On if Developer Mode asks, then check again. A new Trust prompt may not be needed."}
        if error.code in {"setup_required", "unlock_required"}:
            return {**result, "next_step": "trust", "message": error.message}
        if error.code == "preparation_required" and result["developer_mode"] is True:
            return {**result, "next_step": "developer_image", "message": "Developer Mode is on, but its developer services are not available yet. Prepare them again after restarting."}
        if transport_unavailable(exc):
            try:
                result["usb_connected"] = await selected_usb_present()
            except Exception:
                return {**result, "next_step": "wait_usb", "message": "This computer detected this iPhone, but could not refresh its USB connection. Unplug the cable, plug it back in, unlock the iPhone and check again."}
            if result["usb_connected"]:
                return {**result, "next_step": "wait_usb", "message": "This computer can see this iPhone over USB, but the phone is not ready to communicate. After a restart, unplug the cable, plug it back in, unlock the iPhone and check again."}
            return {**result, "next_step": "reconnect_usb", "message": "This iPhone disconnected while checking. After it restarts, unplug the cable, plug it back in, unlock it and check again."}
        raise


async def prepare(options: Prepare, cache: Path, progress, *, policy=None):
    policy = policy or HOST_POLICY
    policy.validate_preparation(options)
    from pymobiledevice3.lockdown import create_using_usbmux
    from .preparation import mount, reveal_developer_mode

    progress("usb_setup", {"notice": "Unlock the selected iPhone. Trust and Developer Mode remain user-controlled."})
    async with await create_using_usbmux(serial=options.device_id, connection_type="USB", autopair=options.pair, pair_timeout=60, **policy.mux_options) as lockdown:
        if lockdown.udid != options.device_id or lockdown.all_values.get("DeviceClass") != "iPhone":
            raise BackendError("wrong_device", "The connected device does not match the selected iPhone.")
        if not lockdown.session_id:
            raise BackendError("setup_required", "Unlock your iPhone, choose Pair this iPhone, then approve Trust on the phone.")
        if (options.mount_image or options.enable_wifi) and Version(lockdown.product_version) < Version("17.4"):
            raise BackendError("unsupported_version", "Developer connections require iOS 17.4 or newer.")
        result = {"paired": True, "developer_mode": await lockdown.get_developer_mode_status()}
        if options.reveal_developer_mode:
            result["developer_mode_option_revealed"] = False
            if result["developer_mode"] is not True:
                result["developer_mode_option_revealed"] = await reveal_developer_mode(lockdown, progress)
        if options.mount_image:
            if not result["developer_mode"]:
                raise BackendError("developer_mode_required", "Turn on Developer Mode in iPhone Settings, restart, then confirm Turn On on the phone.")
            result.update(await mount(lockdown, cache, options.allow_download, progress))
        if options.enable_wifi:
            if result["developer_mode"] is not True:
                raise BackendError("developer_mode_required", "Enable Developer Mode on the iPhone before preparing Wi-Fi.")
            result.update(await _prepare_wifi(lockdown, progress))
        result["setup_complete"] = (result.get("developer_mode") is True and
            result.get("image") in {"mounted", "already_mounted"} and
            (not options.enable_wifi or result.get("wifi_pairing_verified") is True))
        return result


async def _prepare_wifi(lockdown, progress):
    from pymobiledevice3.exceptions import RemotePairingCompletedError
    from pymobiledevice3.remote.tunnel_service import RemotePairingLockdownService

    progress("network_pairing", {})
    async with asyncio.timeout(30):
        await lockdown.set_enable_wifi_connections(True)
        if await lockdown.get_enable_wifi_connections() is not True:
            raise BackendError("wifi_setup_failed", "The iPhone did not enable network connections. Keep it unlocked and connected by USB, then retry.", True)
        # Wi-Fi syncing and developer access are separate iPhone settings.
        # Enabling only connections can advertise a phone whose debug service
        # still refuses LAN connections.
        await lockdown.set_value(True, domain="com.apple.mobile.wireless_lockdown", key="EnableWifiDebugging")
        if await lockdown.get_value(domain="com.apple.mobile.wireless_lockdown", key="EnableWifiDebugging") is not True:
            raise BackendError("wifi_setup_failed", "The iPhone did not enable Wi-Fi debugging. Keep it unlocked and connected by USB, then retry Wi-Fi setup.", True)
        # First-time pairing saves its record and deliberately closes the channel.
        # This exception means success, not a new Trust failure. Reopen once and
        # verify the saved record; never repeat pair-setup blindly.
        remote = await RemotePairingLockdownService.create(lockdown)
        try:
            try:
                await remote.connect(autopair=True)
            except RemotePairingCompletedError:
                pass
        finally:
            await _close_provider(remote)
        progress("wireless_pairing_verify", {})
        remote = await RemotePairingLockdownService.create(lockdown)
        try:
            await remote.connect(autopair=False)
            # This pinned upstream control-channel API returns normally even when
            # pair verification fails with autopair=False. Successful verification
            # initializes both encrypted control-channel ciphers.
            if not hasattr(remote, "client_cip") or not hasattr(remote, "server_cip"):
                raise BackendError("wifi_pairing_required", "Wi-Fi pairing could not be verified. Keep the iPhone unlocked and connected by USB, then prepare Wi-Fi again.")
        finally:
            await _close_provider(remote)
    return {"wifi_enabled": True, "wifi_pairing_verified": True,
            "wifi_pairing": "saved; establish a Wi-Fi connection before unplugging USB"}


async def _close_provider(provider):
    # Cleanup must neither replace the original failure nor consume the next
    # candidate's whole connection budget. Cancellation still propagates.
    with contextlib.suppress(Exception):
        async with asyncio.timeout(1):
            await provider.close()


class DeviceAdapter:
    def __init__(self, *, policy=None):
        self.policy = policy or HOST_POLICY
        self.stack = AsyncExitStack()
        self.rsd = None
        self.location = None
        self._device_info = None
        self._loss_task = None
        self.tunnel_client = None
        self.transport = None
        self.options = None
        self._userspace = False
        self.connection_path = None
        self.connection_attempts = []
        self._wireless_stage = None
        self.loss_diagnostics = None

    async def connect(self, options: Connect, progress):
        options = options.model_copy(update={"transport": self.policy.normalize_transport(options.transport)})
        self.options = options
        self.connection_attempts = []
        self.loss_diagnostics = None
        modes = [options.transport]
        error = None
        for mode in modes:
            try:
                progress("connecting", {"transport": mode})
                await self._open(options, mode, progress)
                self.transport = mode
                return {"transport": mode, "device_id": self.rsd.udid,
                        "os_version": self.rsd.product_version, "os_build": self.rsd.product_build_version,
                        "model": self.rsd.product_type, "experimental": mode == "remote",
                        "connection_path": self.connection_path,
                        "connection_attempts": list(self.connection_attempts)}
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as exc:
                error = exc
                # Cleanup must not replace the connection failure with another error.
                with contextlib.suppress(Exception):
                    await self.close()
                if mode != "usb" or not transport_unavailable(exc):
                    raise public_error(exc) from None
        raise public_error(error) from None

    async def _open(self, options, mode, progress):
        mode = self.policy.normalize_transport(mode)
        from pymobiledevice3.remote import tunnel_service

        if mode == "wifi":
            await self._open_wifi(options, progress)
            return
        if mode == "usb":
            provider = await self._lockdown_provider(options, "USB")
            self.stack.push_async_callback(provider.close)
            self.connection_path = "usb"
        else:
            config = options.remote
            progress("remote_gate", {"gate": 1, "status": "connecting_to_private_address"})
            provider = tunnel_service.RemotePairingTunnelService(config.pairing_identifier, config.address, config.port)
            self.stack.push_async_callback(provider.close)
            await provider.connect(autopair=False)
            progress("remote_gate", {"gate": 2, "status": "pairing_authenticated"})
            self.connection_path = "remote_pairing"
        await self._open_services(provider, options, mode, progress)

    async def _lockdown_provider(self, options, connection_type):
        self.policy.normalize_transport("usb" if connection_type == "USB" else "wifi")
        from pymobiledevice3.lockdown import create_using_usbmux

        if connection_type == "Network" and await self.usb_present(options.device_id):
            # usbmux hides the underlying IP/interface. While the cable is in,
            # use Bonjour's explicit LAN interface instead of trusting this label.
            raise BackendError("wifi_path_unverified", "The cable is connected, so this network path cannot be verified as Wi-Fi. If Wi-Fi is already set up for this iPhone, unplug USB and retry; otherwise finish Wi-Fi setup over USB first.", True)
        lockdown = await create_using_usbmux(serial=options.device_id, autopair=False,
                                            connection_type=connection_type, **self.policy.mux_options)
        return await self._validated_lockdown_provider(lockdown, options)

    async def _tcp_lockdown_provider(self, options, endpoint):
        from pymobiledevice3.lockdown import TcpLockdownClient
        from pymobiledevice3.service_connection import ServiceConnection

        address, port, pair_record = endpoint
        service = await ServiceConnection.create_using_tcp(address, port, keep_alive=True)
        # Own the socket before initialization: upstream's high-level TCP factory
        # does not close it on CancelledError during the pairing handshake.
        self.stack.push_async_callback(service.close)
        lockdown = await TcpLockdownClient.create(
            service, hostname=address, identifier=options.device_id, port=port,
            pair_record=pair_record, autopair=False, keep_alive=True,
        )
        return await self._validated_lockdown_provider(lockdown, options)

    async def _validated_lockdown_provider(self, lockdown, options):
        from pymobiledevice3.remote.tunnel_service import CoreDeviceTunnelProxy

        self.stack.push_async_callback(lockdown.close)
        if not lockdown.session_id:
            raise BackendError("setup_required", "Pair this iPhone over USB first.")
        if lockdown.all_values.get("DeviceClass") != "iPhone":
            raise BackendError("unsupported_device", "Select a physical iPhone.")
        if lockdown.udid != options.device_id:
            raise BackendError("wrong_device", "The connected device does not match the selected iPhone.")
        if Version(lockdown.product_version) < Version("17.4"):
            raise BackendError("unsupported_version", "The explicit USB/Network proxy path requires iOS 17.4 or newer.")
        if await lockdown.get_developer_mode_status() is not True:
            raise BackendError("developer_mode_required", "This iPhone trusts this computer, but Developer Mode is off. On the iPhone, open Settings > Privacy & Security > Developer Mode, enable it, restart, and confirm Turn On after unlocking. Then retry the connection.")
        return await CoreDeviceTunnelProxy.create(lockdown)

    async def _open_services(self, provider, options, mode, progress):
        from pymobiledevice3.remote import tunnel_service
        from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane
        from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        if mode == "wifi":
            self._wifi_progress("wireless_tunnel", progress)
        # One provider owns the global userspace stack, including across fallbacks.
        tunnel_service.USE_USERSPACE_TUNNEL = True
        self._userspace = True
        result = await self.stack.enter_async_context(provider.start_tcp_tunnel())
        self.tunnel_client = result.client
        tun = result.client.tun
        tun.set_peer(result.address)
        dial = await self.stack.enter_async_context(UserspaceDialPlane(tun, result.address))
        self.rsd = RemoteServiceDiscoveryService((result.address, result.port), open_connection=dial.dial,
                                                auxiliary_metadata=result.auxiliary_metadata)
        self.stack.push_async_callback(self.rsd.close)
        await self.rsd.connect()
        if not self.rsd.product_type.startswith("iPhone"):
            raise BackendError("unsupported_device", "Select a physical iPhone.")
        if self.rsd.udid != options.device_id:
            raise BackendError("wrong_device", "Authenticated device identity differs from the selected iPhone; connection closed.")
        if mode == "remote":
            progress("remote_gate", {"gate": 3, "status": "rsd_connected"})
        if mode == "wifi":
            self._wifi_progress("wireless_service", progress)
        dvt = await self.stack.enter_async_context(DvtProvider(self.rsd))
        self.location = await self.stack.enter_async_context(LocationSimulation(dvt))
        await self._prepare_health_check(dvt)
        if mode == "remote":
            progress("remote_gate", {"gate": 4, "status": "dvt_open_command_not_yet_sent", "phone_observation": "not_tested"})

    def _wifi_progress(self, stage, progress):
        self._wireless_stage = stage
        if self.connection_attempts and self.connection_attempts[-1]["status"] == "connecting":
            self.connection_attempts[-1]["stage"] = stage
        progress(stage, {"transport": "wifi", "path": self.connection_path})

    async def _wifi_attempt(self, options, path, endpoint, progress):
        from pymobiledevice3.remote.tunnel_service import RemotePairingTunnelService

        self.connection_path = path
        self._wifi_progress("wireless_connecting", progress)
        attempt = {"path": path, "stage": self._wireless_stage, "status": "connecting"}
        self.connection_attempts.append(attempt)
        try:
            async with asyncio.timeout(10):
                if path == "network_lockdown":
                    provider = await self._lockdown_provider(options, "Network")
                    self.stack.push_async_callback(provider.close)
                elif path == "lan_lockdown":
                    provider = await self._tcp_lockdown_provider(options, endpoint)
                    self.stack.push_async_callback(provider.close)
                else:
                    address, port = endpoint
                    provider = RemotePairingTunnelService(options.device_id, address, port)
                    self.stack.push_async_callback(_close_provider, provider)
                    await provider.connect(autopair=False)
                await self._open_services(provider, options, "wifi", progress)
            attempt.update(stage="wireless_service", status="connected")
            return None
        except asyncio.CancelledError:
            attempt.update(stage=self._wireless_stage, status="cancelled")
            raise
        except Exception as exc:
            error = public_error(exc)
            attempt.update(stage=self._wireless_stage, status="failed", error=error.as_dict())
            progress("wireless_attempt_failed", {"path": path, "code": error.code})
            try:
                await self.close()
            except Exception:
                raise BackendError("wifi_cleanup_failed", "The previous Wi-Fi connection did not close cleanly. Restart the local Estera server before reconnecting.") from None
            # Independent network providers can have different availability/trust.
            # Never hide a proven wrong device, disabled Developer Mode, or a bug.
            network_error = transport_unavailable(exc) or (
                isinstance(exc, OSError) and exc.errno in {
                    errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNREFUSED,
                    errno.ECONNRESET, errno.ENETDOWN, errno.EHOSTDOWN, errno.ETIMEDOUT,
                }) or isinstance(exc, asyncio.IncompleteReadError)
            alternate_setup = path in {"network_lockdown", "lan_lockdown"} and self._wireless_stage == "wireless_connecting" and (
                error.code in {"setup_required", "wifi_path_unverified"} or type(exc).__name__ in {"InvalidServiceError", "StartServiceError"})
            if not network_error and not alternate_setup:
                raise error from None
            return error

    async def _wifi_candidates(self, identifier):
        from .bonjour import browse_remotepairing
        from pymobiledevice3.pair_records import iter_remote_paired_identifiers

        if identifier not in islice(iter_remote_paired_identifiers(), 128):
            raise BackendError("wifi_pairing_required", "No saved Wi-Fi pairing exists for this iPhone. Connect it by USB and use guided Wi-Fi setup once.")
        interfaces = local_network_interfaces()
        if interfaces == set():
            raise BackendError("wifi_interface_unavailable", "Could not identify a local Wi-Fi or Ethernet connection. Connect this computer to your iPhone’s Wi-Fi network, then retry.", True)
        try:
            answers = await browse_remotepairing(timeout=3)
        except Exception as exc:
            if isinstance(exc, (ImportError, PermissionError)):
                raise public_error(exc) from None
            raise BackendError("wifi_discovery_failed", "Wi-Fi discovery did not complete. Check whether the app running Estera has Local Network access, keep both devices on the same network, then retry.", True) from None
        candidates, excluded_links = self._lan_candidates(answers, interfaces)
        if not candidates and excluded_links:
            raise BackendError("wifi_lan_not_found", "The iPhone’s discovery service appeared only on cable or unverified network links, not on this computer’s local network. Unlock the iPhone and join the same Wi-Fi network as this computer. If Wi-Fi is already set up, unplug USB and retry to check wireless access.", True)
        return candidates

    async def _mobdev_candidates(self, identifier):
        from .bonjour import browse_mobdev2
        from .wireless_identity import matches_paired_service
        from pymobiledevice3.common import get_home_folder
        from pymobiledevice3.lockdown import SERVICE_PORT
        from pymobiledevice3.pair_records import get_preferred_pair_record

        record = await get_preferred_pair_record(identifier, get_home_folder(), **self.policy.mux_options)
        if not record:
            return []
        interfaces = local_network_interfaces()
        if interfaces == set():
            return []
        answers = await browse_mobdev2(timeout=3)
        selected = [answer for answer in answers[:32] if matches_paired_service(answer, record)]
        candidates, _ = self._lan_candidates(selected, interfaces)
        # Like upstream get_mobdev2_lockdowns, use lockdown's fixed TCP port;
        # the mobdev2 advertisement can publish 32498 instead of 62078.
        # Leave room for RemotePairing fallback inside the overall Wi-Fi deadline.
        return [(address, SERVICE_PORT, record) for address, _ in candidates[:2]]

    @staticmethod
    def _lan_candidates(answers, interfaces):
        # Round-robin across service instances so one host with many IPv6 addresses
        # cannot consume the candidate limit. DNS-SD preserves link-local scopes.
        groups = []
        excluded_links = False
        for answer in answers[:32]:
            if not 1 <= answer.port <= 65535:
                continue
            addresses = []
            for address in answer.addresses[:16]:
                # iPhone USB exposes Bonjour over temporary Ethernet interfaces.
                # A successful RemotePairing handshake there still depends on the
                # cable. Require a configured LAN interface before calling it Wi-Fi.
                if interfaces is not None and address.iface not in interfaces:
                    excluded_links = True
                    continue
                try:
                    ip = ipaddress.ip_address(address.ip)
                except ValueError:
                    continue
                if ip.is_loopback or ip.is_multicast or ip.is_unspecified:
                    continue
                if ip.version == 6 and ip.is_link_local and not address.iface:
                    continue
                addresses.append(address)
            addresses.sort(key=lambda address: ":" in address.ip)
            groups.append([(address.full_ip, answer.port) for address in addresses[:16]])
        candidates = []
        for index in range(16):
            for group in groups:
                if index < len(group) and group[index] not in candidates:
                    candidates.append(group[index])
                if len(candidates) == 8:
                    return candidates, excluded_links
        return candidates, excluded_links

    async def _wifi_setup_diagnostic(self, options):
        # Only inspect USB after wireless failure and when this attempt already
        # established that the selected iPhone was attached by cable.
        if not self.connection_attempts:
            return None
        first = self.connection_attempts[0]
        if first.get("path") != "network_lockdown" or first.get("error", {}).get("code") != "wifi_path_unverified":
            return None
        try:
            from pymobiledevice3.lockdown import SERVICE_PORT, UsbmuxLockdownClient
            from pymobiledevice3.service_connection import ServiceConnection

            # One second for the optional read and at most one second for each
            # owned resource's cleanup: at most three seconds in total.
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(1):
                    service = await ServiceConnection.create_using_usbmux(
                        options.device_id, SERVICE_PORT, connection_type="USB", **self.policy.mux_options)
                    # Own the socket before authentication, including cancellation.
                    stack.push_async_callback(_close_provider, service)
                    lockdown = await UsbmuxLockdownClient.create(
                        service, identifier=options.device_id, autopair=False, **self.policy.mux_options)
                    stack.push_async_callback(_close_provider, lockdown)
                    if (lockdown.udid != options.device_id or not lockdown.session_id
                            or lockdown.all_values.get("DeviceClass") != "iPhone"):
                        return None
                    disabled = await lockdown.get_value(
                        domain="com.apple.mobile.wireless_lockdown", key="EnableWifiDebugging") is False
        except Exception:
            # A disconnected/locked phone or unavailable setting is unknown,
            # never evidence that Wi-Fi debugging is disabled.
            return None
        if disabled:
            return BackendError("wifi_setup_required", "Wi-Fi debugging is disabled on this iPhone. Keep it connected by USB and choose Set up Wi-Fi over USB, then retry the Wi-Fi connection.")
        return None

    async def _open_wifi(self, options, progress):
        from .bonjour import BonjourError

        self.policy.normalize_transport("wifi")
        try:
            # Leave room inside Session's 45 seconds for a three-second read-only
            # setup diagnostic and cancellation cleanup.
            async with asyncio.timeout(39):
                error = await self._wifi_attempt(options, "network_lockdown", None, progress)
                if error is None:
                    return
                self._wifi_progress("wireless_discovery", progress)
                try:
                    # Bound discovery and all direct-LAN attempts together so
                    # RemotePairing retains a useful part of the 39-second budget.
                    async with asyncio.timeout(12):
                        lan_candidates = await self._mobdev_candidates(options.device_id)
                        for endpoint in lan_candidates:
                            error = await self._wifi_attempt(options, "lan_lockdown", endpoint, progress)
                            if error is None:
                                return
                except (BonjourError, ConnectionError, TimeoutError, FileNotFoundError, plistlib.InvalidFileException):
                    # This additional discovery path must not disable an otherwise
                    # usable RemotePairing connection. Cancellation still propagates.
                    await self.close()
                    self.connection_attempts.append({"path": "lan_lockdown", "stage": "wireless_discovery",
                        "status": "failed", "error": BackendError("wifi_discovery_failed",
                            "Direct Wi-Fi discovery was unavailable; trying the alternate wireless service.", True).as_dict()})
                candidates = await self._wifi_candidates(options.device_id)
                for endpoint in candidates:
                    error = await self._wifi_attempt(options, "remote_pairing", endpoint, progress)
                    if error is None:
                        return
        except TimeoutError:
            if self.connection_attempts and self.connection_attempts[-1]["status"] == "cancelled":
                self.connection_attempts[-1]["status"] = "timeout"
            error = BackendError("wifi_timeout", "Wi-Fi connection attempts timed out. Unlock the iPhone, keep both devices on the same network, then retry. Connection-attempt details identify the last stage reached.", True)
        except BackendError as exc:
            if exc.code not in {"wifi_lan_not_found", "wifi_interface_unavailable", "wifi_discovery_failed"}:
                raise
            error = exc
        else:
            if candidates:
                error = BackendError("wifi_unreachable", "We couldn’t reach your iPhone over Wi-Fi. Nearby Wi-Fi services were found, but the selected iPhone did not connect. Retry, use USB, or set up Wi-Fi over USB.", True)
            elif error.code != "wifi_path_unverified":
                error = BackendError("wifi_not_found", "We couldn’t reach your iPhone over Wi-Fi. Unlock it and make sure both devices are on the same network. Retry, use USB, or set up Wi-Fi over USB.", True)
        diagnostic = await self._wifi_setup_diagnostic(options)
        raise diagnostic or error from None

    async def usb_present(self, device_id):
        return await usb_present(device_id, policy=self.policy)

    async def set(self, coordinate):
        await self.location.set(coordinate.latitude, coordinate.longitude)

    async def clear(self):
        await self.location.clear()

    async def _prepare_health_check(self, dvt):
        # Watch the DVT connection that owns location simulation. The separate
        # remote-lockdown service can disappear while DVT is still healthy, and
        # its upstream client cannot reestablish that optional remote service.
        from pymobiledevice3.services.dvt.instruments.device_info import DeviceInfo
        self._device_info = await self.stack.enter_async_context(DeviceInfo(dvt))
        # Establish that the chosen probe works before reporting connected.
        await self._check_health()

    async def _check_health(self):
        async with asyncio.timeout(5):
            await self._device_info.mach_time_info()

    async def wait_lost(self):
        # The adapter owns monitoring for the connection's whole lifetime.
        # Session may replace a watcher after Stop; cancelling that observer must
        # not cancel upstream wait_closed(), which also cancels the socket reader.
        if self._loss_task is None:
            self._loss_task = asyncio.create_task(self._monitor_connection())
        await asyncio.shield(self._loss_task)

    async def _monitor_connection(self):
        # A read-only round trip also detects silent loss while paused/holding.
        started = asyncio.get_running_loop().time()
        successful_heartbeats = 0
        async def heartbeat():
            nonlocal successful_heartbeats
            while True:
                await asyncio.sleep(5)
                await self._check_health()
                successful_heartbeats += 1
        closed = asyncio.create_task(self.tunnel_client.wait_closed())
        health = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait({closed, health}, return_when=asyncio.FIRST_COMPLETED)
            self.loss_diagnostics = {
                "source": "tunnel_and_health_check" if len(done) == 2 else
                          "health_check" if health in done else "tunnel_closed",
                "health_probe": "developer_clock",
                "after_seconds": round(asyncio.get_running_loop().time() - started, 2),
                "successful_heartbeats": successful_heartbeats,
            }
            # Prefer the probe's error if both observers finish in the same tick.
            if health in done:
                health.result()
            if closed in done:
                closed.result()
        finally:
            closed.cancel()
            health.cancel()
            await asyncio.gather(closed, health, return_exceptions=True)

    async def close(self):
        from pymobiledevice3.remote import tunnel_service
        try:
            async with asyncio.timeout(5):
                monitor, self._loss_task = self._loss_task, None
                if monitor is not None:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
                await self.stack.aclose()
        finally:
            self.stack = AsyncExitStack()
            self.rsd = self.location = self._device_info = self.tunnel_client = None
            if self._userspace:
                tunnel_service.USE_USERSPACE_TUNNEL = False
                self._userspace = False


async def usb_present(device_id, *, policy=None):
    """Cheap USB-only presence probe; no lockdown, trust or wireless discovery."""
    policy = policy or HOST_POLICY
    policy.ensure_supported()
    from pymobiledevice3.usbmux import list_devices
    async with asyncio.timeout(2):
        return any(d.serial == device_id and d.connection_type == "USB"
                   for d in await list_devices(**policy.mux_options))
