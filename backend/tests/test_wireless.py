"""Wireless regressions using fake services; no phone, multicast or location writes."""
import asyncio
import ctypes
import os
import socket
import sys
from types import SimpleNamespace

import pytest

from openlocation_backend import adapter, bonjour
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Connect, Prepare, Request
from openlocation_backend.protocol import Server
from openlocation_backend.session import Session


@pytest.fixture(autouse=True)
def no_real_lan_pairing_lookup(monkeypatch):
    from pymobiledevice3 import pair_records

    async def no_record(*_, **__):
        return None

    monkeypatch.setattr(pair_records, "get_preferred_pair_record", no_record)


@pytest.mark.parametrize("fresh", [False, True])
async def test_pairing_completion_reopens_and_verifies_saved_pairing(monkeypatch, fresh):
    from pymobiledevice3.exceptions import RemotePairingCompletedError
    from pymobiledevice3.remote import tunnel_service

    calls = []

    class Remote:
        async def connect(self, autopair):
            calls.append(("connect", autopair))
            if fresh and autopair:
                raise RemotePairingCompletedError()
            self.client_cip = self.server_cip = object()

        async def close(self):
            calls.append("close")

    async def create(_):
        calls.append("create")
        return Remote()

    class Lockdown:
        async def set_enable_wifi_connections(self, enabled):
            assert enabled is True

        async def get_enable_wifi_connections(self):
            return True

        async def set_value(self, value, *, domain, key):
            assert (value, domain, key) == (True, "com.apple.mobile.wireless_lockdown", "EnableWifiDebugging")
            calls.append("enable_debugging")

        async def get_value(self, *, domain, key):
            assert (domain, key) == ("com.apple.mobile.wireless_lockdown", "EnableWifiDebugging")
            calls.append("verify_debugging")
            return True

    monkeypatch.setattr(tunnel_service.RemotePairingLockdownService, "create", create)
    result = await adapter._prepare_wifi(Lockdown(), lambda *_: None)
    assert result["wifi_enabled"] and result["wifi_pairing_verified"]
    assert calls == ["enable_debugging", "verify_debugging", "create", ("connect", True), "close", "create", ("connect", False), "close"]


async def test_unverified_pairing_is_not_reported_as_ready(monkeypatch):
    from pymobiledevice3.remote import tunnel_service

    closed = []

    class Remote:
        async def connect(self, autopair):
            pass  # Upstream can return normally on failed verification.

        async def close(self):
            closed.append(True)

    async def create(_):
        return Remote()

    async def enabled(*_, **__):
        return True

    monkeypatch.setattr(tunnel_service.RemotePairingLockdownService, "create", create)
    with pytest.raises(BackendError, match="could not be verified"):
        await adapter._prepare_wifi(SimpleNamespace(set_enable_wifi_connections=enabled,
                                                   get_enable_wifi_connections=enabled,
                                                   set_value=enabled, get_value=enabled), lambda *_: None)
    assert len(closed) == 2


@pytest.mark.parametrize("debugging_value", [False, None])
async def test_wifi_setup_stops_before_pairing_when_debugging_is_not_enabled(monkeypatch, debugging_value):
    from pymobiledevice3.remote import tunnel_service

    async def enabled(*_, **__):
        return True

    async def read_debugging(**_):
        return debugging_value

    async def forbidden(*_):
        pytest.fail("Pairing must not report success when wireless debugging is disabled")

    monkeypatch.setattr(tunnel_service.RemotePairingLockdownService, "create", forbidden)
    lockdown = SimpleNamespace(set_enable_wifi_connections=enabled,
        get_enable_wifi_connections=enabled, set_value=enabled, get_value=read_debugging)
    with pytest.raises(BackendError) as caught:
        await adapter._prepare_wifi(lockdown, lambda *_: None)
    assert caught.value.code == "wifi_setup_failed"
    assert caught.value.retryable is True


@pytest.mark.parametrize("failure_stage", ["proxy", "tunnel"])
async def test_wifi_falls_back_after_complete_network_path_failure(monkeypatch, failure_stage):
    from pymobiledevice3.exceptions import InvalidServiceError
    from pymobiledevice3.remote import tunnel_service

    events = []

    class Provider:
        def __init__(self, *args):
            self.kind = "remote" if args else "network"

        async def connect(self, autopair):
            assert autopair is False
            events.append("remote_auth")

        async def close(self):
            events.append(f"close_{self.kind}")

    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, options, connection_type):
            assert connection_type == "Network"
            if failure_stage == "proxy":
                raise InvalidServiceError("unavailable", "selected-phone", "26.0")
            return Provider()

        async def _wifi_candidates(self, identifier):
            assert identifier == "selected-phone"
            return [("192.0.2.1", 12345)]

        async def _open_services(self, provider, options, mode, progress):
            self._wifi_progress("wireless_tunnel", progress)
            if provider.kind == "network":
                raise ConnectionResetError("private endpoint detail")
            assert "close_network" in events or failure_stage == "proxy"
            self.rsd = SimpleNamespace(udid=options.device_id, product_version="26.0",
                product_build_version="test", product_type="iPhone-test")

    monkeypatch.setattr(tunnel_service, "RemotePairingTunnelService", Provider)
    device = Adapter()
    try:
        result = await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
        assert result["connection_path"] == "remote_pairing" and result["transport"] == "wifi"
        assert [item["status"] for item in result["connection_attempts"]] == ["failed", "connected"]
        assert "private endpoint" not in str(result)
    finally:
        await device.close()
    assert events.count("close_remote") == 1


@pytest.mark.parametrize("code", ["wrong_device", "developer_mode_required", "unsupported_device"])
async def test_wifi_never_falls_back_past_identity_or_device_setup_failure(code):
    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, *_):
            raise BackendError(code, "Selected device needs attention.")

        async def _wifi_candidates(self, _):
            pytest.fail("Must not hide a selected-device failure")

    device = Adapter()
    with pytest.raises(BackendError) as caught:
        await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
    assert caught.value.code == code


async def test_cancelled_wireless_attempt_closes_provider_and_keeps_stage(monkeypatch):
    from pymobiledevice3.remote import tunnel_service

    started = asyncio.Event()
    closed = []

    class Provider:
        def __init__(self, *_):
            pass

        async def connect(self, autopair):
            started.set()
            await asyncio.Future()

        async def close(self):
            closed.append(True)

    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, *_):
            raise ConnectionRefusedError()

        async def _wifi_candidates(self, _):
            return [("192.0.2.1", 12345)]

    monkeypatch.setattr(tunnel_service, "RemotePairingTunnelService", Provider)
    device = Adapter()
    task = asyncio.create_task(device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True] and device.rsd is None
    assert device.connection_attempts[-1]["status"] == "cancelled"


async def test_saved_phone_listing_does_not_require_or_claim_an_advertisement(monkeypatch):
    from pymobiledevice3 import pair_records, usbmux

    async def empty(*_, **__):
        return []

    monkeypatch.setattr(usbmux, "list_devices", empty)
    monkeypatch.setattr(bonjour, "browse_remotepairing", empty)
    monkeypatch.setattr(pair_records, "iter_remote_paired_identifiers", lambda: iter(["saved-phone", "../invalid"]))
    result = await adapter.discover("wifi")
    assert len(result["devices"]) == 1
    assert result["devices"][0]["wifi_reachability"] == "not_checked"
    assert result["devices"][0]["state"] == "paired"
    assert result["warnings"]


async def test_remote_candidates_preserve_advertised_port_scopes_and_skip_invalid_addresses(monkeypatch):
    from pymobiledevice3 import pair_records
    from pymobiledevice3.bonjour import Address, ServiceInstance

    async def browse(**_):
        return [ServiceInstance("phone", "phone.local", 12345, [
            Address("127.0.0.1", "lo0"), Address("::1", "lo0"), Address("::", "en0"),
            Address("fe80::1", None), Address("fe80::1", "en0"),
            Address("192.0.2.1", "en0"), Address("192.0.2.1", "en0"),
        ])]

    monkeypatch.setattr(bonjour, "browse_remotepairing", browse)
    monkeypatch.setattr(adapter, "local_network_interfaces", lambda: {"en0"})
    monkeypatch.setattr(pair_records, "iter_remote_paired_identifiers", lambda: iter(["selected-phone"]))
    candidates = await adapter.DeviceAdapter()._wifi_candidates("selected-phone")
    assert candidates == [("192.0.2.1", 12345), ("fe80::1%en0", 12345)]
    with pytest.raises(BackendError) as caught:
        await adapter.DeviceAdapter()._wifi_candidates("unpaired-phone")
    assert caught.value.code == "wifi_pairing_required"


@pytest.mark.parametrize("lan_address", ["192.0.2.1", "fe80::1"])
async def test_wifi_candidates_exclude_usb_ethernet_before_lan(monkeypatch, lan_address):
    from pymobiledevice3 import pair_records
    from pymobiledevice3.bonjour import Address, ServiceInstance

    async def browse(**_):
        return [ServiceInstance("phone", "phone.local", 12345, [
            Address("169.254.250.63", "en11"), Address("fe80::2", "en8"),
            Address(lan_address, "en4"),
        ])]

    monkeypatch.setattr(bonjour, "browse_remotepairing", browse)
    monkeypatch.setattr(adapter, "local_network_interfaces", lambda: {"en4"})
    monkeypatch.setattr(pair_records, "iter_remote_paired_identifiers", lambda: iter(["selected-phone"]))
    assert await adapter.DeviceAdapter()._wifi_candidates("selected-phone") == [
        (lan_address + ("%en4" if ":" in lan_address else ""), 12345),
    ]


@pytest.mark.parametrize("interface", ["en11", None, "unknown"])
async def test_cable_only_discovery_never_opens_a_wifi_tunnel(monkeypatch, interface):
    from pymobiledevice3 import pair_records
    from pymobiledevice3.bonjour import Address, ServiceInstance

    async def browse(**_):
        return [ServiceInstance("phone", "phone.local", 12345,
                                [Address("169.254.250.63", interface)])]

    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, *_):
            raise ConnectionRefusedError()

        async def _open_services(self, *_):
            pytest.fail("A cable-only discovery must not open a Wi-Fi tunnel")

    monkeypatch.setattr(bonjour, "browse_remotepairing", browse)
    monkeypatch.setattr(adapter, "local_network_interfaces", lambda: {"en0"})
    monkeypatch.setattr(pair_records, "iter_remote_paired_identifiers", lambda: iter(["selected-phone"]))
    device = Adapter()
    with pytest.raises(BackendError) as caught:
        await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
    assert caught.value.code == "wifi_lan_not_found"
    assert device.transport is None and device.location is None
    assert "169.254" not in caught.value.message


async def test_unknown_lan_metadata_fails_before_discovery(monkeypatch):
    from pymobiledevice3 import pair_records

    async def forbidden(**_):
        pytest.fail("Unavailable interface metadata must not silently accept USB links")

    monkeypatch.setattr(adapter, "local_network_interfaces", lambda: set())
    monkeypatch.setattr(pair_records, "iter_remote_paired_identifiers", lambda: iter(["selected-phone"]))
    monkeypatch.setattr(bonjour, "browse_remotepairing", forbidden)
    with pytest.raises(BackendError) as caught:
        await adapter.DeviceAdapter()._wifi_candidates("selected-phone")
    assert caught.value.code == "wifi_interface_unavailable"


@pytest.mark.parametrize("transport", ["wifi", "usb", "auto", None])
async def test_initial_setup_only_enables_explicit_wireless_choice(monkeypatch, tmp_path, transport):
    preparations = []

    async def prepare(options, *_, **kwargs):
        preparations.append(options)
        return {"setup_complete": True}

    class Device:
        def __init__(self):
            self.transport = "wifi" if transport == "wifi" else "usb"

        async def connect(self, *_):
            return {"transport": self.transport}

        async def close(self):
            pass

        async def wait_lost(self):
            await asyncio.Future()

    monkeypatch.setattr(adapter, "prepare", prepare)
    server = Server(asyncio.StreamReader(), None, directory=tmp_path,
        session_factory=lambda emit: Session(emit, adapter_factory=Device,
            power=SimpleNamespace(acquire=lambda: None, release=lambda: None)))
    try:
        options = {"device_id": "selected-phone", "prepare": True}
        if transport is not None:
            options["transport"] = transport
        result = await server.dispatch(Request(id="wifi", op="connect"),
            Connect(**options))
        assert server.capabilities["initial_setup"]["version"] == 2
        assert result["status"]["transport_state"] == "connected"
        assert len(preparations) == 1
        assert preparations[0].enable_wifi is (transport in {"wifi", "auto"})
        assert preparations[0].mount_image and not preparations[0].pair
        assert result["status"]["setup_complete"] is True
    finally:
        await server.session._close_adapter()


@pytest.mark.parametrize("enable_wifi,image,verified,complete", [
    (False, "mounted", False, True),
    (False, "already_mounted", False, True),
    (False, None, False, False),
    (True, "mounted", True, True),
    (True, "already_mounted", False, False),
])
async def test_preparation_completion_matches_requested_access(
    monkeypatch, tmp_path, enable_wifi, image, verified, complete,
):
    from pymobiledevice3 import lockdown
    from openlocation_backend import preparation

    wireless_calls = []

    class Lockdown:
        udid = "selected-phone"
        session_id = "trusted"
        all_values = {"DeviceClass": "iPhone"}
        product_version = "26.0"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get_developer_mode_status(self):
            return True

    async def create(**options):
        assert options["serial"] == "selected-phone"
        assert options["connection_type"] == "USB" and options["autopair"] is False
        return Lockdown()

    async def mount(*_):
        return {"image": image}

    async def wifi(*_):
        assert enable_wifi, "USB setup must not enable, disable, or pair wireless access"
        wireless_calls.append(True)
        return {"wifi_enabled": True, "wifi_pairing_verified": verified}

    monkeypatch.setattr(lockdown, "create_using_usbmux", create)
    monkeypatch.setattr(preparation, "mount", mount)
    monkeypatch.setattr(adapter, "_prepare_wifi", wifi)
    result = await adapter.prepare(Prepare(device_id="selected-phone",
        mount_image=image is not None, enable_wifi=enable_wifi), tmp_path, lambda *_: None)
    assert result["setup_complete"] is complete
    assert wireless_calls == ([True] if enable_wifi else [])
    if not enable_wifi:
        assert "wifi_enabled" not in result and "wifi_pairing_verified" not in result


@pytest.mark.parametrize("saved_pairing,code", [
    (False, "wifi_pairing_required"), (True, "wifi_not_found"),
])
async def test_missing_pairing_and_unreachable_phone_remain_distinct(monkeypatch, saved_pairing, code):
    from pymobiledevice3 import pair_records

    async def empty(**_):
        return []

    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, *_):
            raise ConnectionRefusedError("sensitive endpoint")

    monkeypatch.setattr(pair_records, "iter_remote_paired_identifiers",
        lambda: iter(["selected-phone"] if saved_pairing else []))
    monkeypatch.setattr(bonjour, "browse_remotepairing", empty)
    device = Adapter()
    with pytest.raises(BackendError) as caught:
        await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
    assert caught.value.code == code
    if saved_pairing:
        assert caught.value.message.startswith("We couldn’t reach your iPhone over Wi-Fi.")
    assert "sensitive endpoint" not in str(device.connection_attempts)
    assert device.connection_attempts[0]["path"] == "network_lockdown"
    assert device.connection_attempts[0]["status"] == "failed"


async def test_network_identity_mismatch_stops_before_developer_tunnel(monkeypatch):
    from pymobiledevice3 import lockdown
    from pymobiledevice3.remote.tunnel_service import CoreDeviceTunnelProxy

    closed = []

    async def close():
        closed.append(True)

    async def create(**options):
        assert options["serial"] == "selected-phone" and options["autopair"] is False
        assert options["connection_type"] == "Network"
        return SimpleNamespace(session_id="trusted", all_values={"DeviceClass": "iPhone"},
            udid="different-phone", close=close)

    async def forbidden(*_):
        pytest.fail("A different phone must not reach developer services or discovery fallback")

    async def absent(*_):
        return False

    monkeypatch.setattr(lockdown, "create_using_usbmux", create)
    monkeypatch.setattr(CoreDeviceTunnelProxy, "create", forbidden)
    device = adapter.DeviceAdapter()
    monkeypatch.setattr(device, "usb_present", absent)
    monkeypatch.setattr(device, "_wifi_candidates", forbidden)
    with pytest.raises(BackendError) as caught:
        await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
    assert caught.value.code == "wrong_device"
    assert closed == [True]


async def test_network_mux_label_is_not_wifi_proof_while_usb_is_present(monkeypatch):
    from pymobiledevice3 import lockdown

    async def present(identifier):
        assert identifier == "selected-phone"
        return True

    async def forbidden(**_):
        pytest.fail("The opaque Network mux must not be opened while USB is attached")

    device = adapter.DeviceAdapter()
    monkeypatch.setattr(device, "usb_present", present)
    monkeypatch.setattr(lockdown, "create_using_usbmux", forbidden)
    with pytest.raises(BackendError) as caught:
        await device._lockdown_provider(Connect(device_id="selected-phone", transport="wifi"), "Network")
    assert caught.value.code == "wifi_path_unverified"


async def test_network_only_pairing_explains_unplugged_retry():
    class Adapter(adapter.DeviceAdapter):
        async def usb_present(self, _):
            return True

        async def _wifi_candidates(self, _):
            return []

        async def _wifi_setup_diagnostic(self, _):
            return None

    with pytest.raises(BackendError) as caught:
        await Adapter().connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
    assert caught.value.code == "wifi_path_unverified"
    assert "unplug USB and retry" in caught.value.message


async def test_diagnostics_survive_failed_adapter_cleanup():
    class Device:
        transport = None
        connection_attempts = [{"path": "remote_pairing", "stage": "wireless_tunnel", "status": "failed"}]

        async def connect(self, *_):
            raise BackendError("wifi_unreachable", "Retry Wi-Fi.", True)

        async def close(self):
            pass

    session = Session(lambda *_: None, adapter_factory=Device,
                      power=SimpleNamespace(acquire=lambda: None, release=lambda: None))
    with pytest.raises(BackendError):
        await session.connect(Connect(device_id="selected-phone", transport="wifi"))
    assert session.adapter is None
    assert session.status()["connection_attempts"] == Device.connection_attempts
    await session.disconnect()
    assert session.status()["connection_attempts"] == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS DNS-SD reader uses a POSIX event loop")
async def test_native_reference_is_retired_after_callback_and_reader_removed():
    read_fd, write_fd = os.pipe()
    deallocated = []
    loop = asyncio.get_running_loop()

    def create(output, callback, _):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 1
        return 0

    def process(_):
        operation.callback()
        assert not deallocated
        return 0

    native = SimpleNamespace(DNSServiceRefSockFD=lambda _: read_fd,
        DNSServiceProcessResult=process, DNSServiceRefDeallocate=lambda _: deallocated.append(True))
    owner = SimpleNamespace(native=native, loop=loop, operations=set(), closed=False, fail=lambda exc: pytest.fail(str(exc)))
    try:
        operation = bonjour._Operation(owner, ctypes.CFUNCTYPE(None), lambda op: op.close(), create)
        operation._readable()
        assert deallocated == [True] and not owner.operations
        assert loop.remove_reader(read_fd) is False
        operation.close()
        assert deallocated == [True]
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_darwin_address_parser_preserves_ipv6_scope(monkeypatch):
    raw = bytes([28, socket.AF_INET6]) + bytes(6) + socket.inet_pton(socket.AF_INET6, "fe90::1") + (7).to_bytes(4, sys.byteorder)
    buffer = ctypes.create_string_buffer(raw)
    monkeypatch.setattr(socket, "if_indextoname", lambda index: f"en{index}")
    assert bonjour._address(ctypes.addressof(buffer), 3).full_ip == "fe90::1%en7"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS DNS-SD symbols")
def test_native_dnssd_library_exports_required_symbols_without_browsing():
    assert callable(bonjour._NativeDNS().DNSServiceBrowse)
