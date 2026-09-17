"""LAN lockdown regressions using fake pairing, discovery, sockets and phones."""
import asyncio
import plistlib
from types import SimpleNamespace

import pytest

from openlocation_backend import adapter, bonjour
from openlocation_backend.errors import BackendError
from openlocation_backend.host import HostPolicy
from openlocation_backend.models import Connect


@pytest.fixture(autouse=True)
def isolate_lan_io(monkeypatch, tmp_path):
    from pymobiledevice3 import common, pair_records
    from openlocation_backend import wireless_identity

    async def no_record(*_, **__):
        return None

    async def no_services(**_):
        return []

    monkeypatch.setattr(adapter, "HOST_POLICY", HostPolicy("macos", "arm64"))
    monkeypatch.setattr(adapter, "local_network_interfaces", lambda: {"en0"})
    monkeypatch.setattr(common, "get_home_folder", lambda: tmp_path)
    monkeypatch.setattr(pair_records, "get_preferred_pair_record", no_record)
    monkeypatch.setattr(pair_records, "iter_remote_paired_identifiers", lambda: iter(()))
    monkeypatch.setattr(bonjour, "browse_mobdev2", no_services)
    monkeypatch.setattr(bonjour, "browse_remotepairing", no_services)
    monkeypatch.setattr(wireless_identity, "matches_paired_service", lambda *_: False)


async def test_mobdev_candidates_use_fixed_lockdown_port_and_only_selected_paired_lan(monkeypatch, tmp_path):
    from pymobiledevice3 import pair_records
    from pymobiledevice3.bonjour import Address, ServiceInstance
    from openlocation_backend import wireless_identity

    record = {"HostID": "selected-pairing"}
    selected = ServiceInstance("selected", "phone.local", 32498, [
        Address("169.254.1.2", "en11"), Address("192.0.2.3", None),
        Address("fe80::1", "en0"), Address("192.0.2.1", "en0"),
        Address("192.0.2.1", "en0"),
    ])
    unrelated = ServiceInstance("other-phone", "other.local", 12345,
                                [Address("192.0.2.2", "en0")])
    lookups, matches = [], []

    async def saved(identifier, folder, **options):
        lookups.append((identifier, folder, options))
        return record

    async def browse(*, timeout):
        assert timeout == 3
        return [unrelated, selected]

    def matching(service, pairing):
        assert pairing is record
        matches.append(service)
        return service is selected

    monkeypatch.setattr(pair_records, "get_preferred_pair_record", saved)
    monkeypatch.setattr(bonjour, "browse_mobdev2", browse)
    monkeypatch.setattr(wireless_identity, "matches_paired_service", matching)
    device = adapter.DeviceAdapter()
    candidates = await device._mobdev_candidates("selected-phone")
    assert lookups == [("selected-phone", tmp_path, device.policy.mux_options)]
    assert matches == [unrelated, selected]
    assert candidates == [("192.0.2.1", 62078, record), ("fe80::1%en0", 62078, record)]
    assert all(candidate[2] is record for candidate in candidates)


@pytest.mark.parametrize("saved_pairing", [False, True])
async def test_mobdev_discovery_requires_saved_pairing_and_known_lan(monkeypatch, saved_pairing):
    from pymobiledevice3 import pair_records

    async def saved(*_, **__):
        return {"HostID": "saved"} if saved_pairing else None

    async def forbidden(**_):
        pytest.fail("Discovery requires both saved pairing and a known LAN interface")

    monkeypatch.setattr(pair_records, "get_preferred_pair_record", saved)
    monkeypatch.setattr(adapter, "local_network_interfaces", lambda: set())
    monkeypatch.setattr(bonjour, "browse_mobdev2", forbidden)
    assert await adapter.DeviceAdapter()._mobdev_candidates("selected-phone") == []


@pytest.mark.parametrize("identity", ["selected-phone", "different-phone"])
async def test_tcp_lockdown_uses_advertised_port_saved_pairing_and_checks_identity(monkeypatch, identity):
    from pymobiledevice3.lockdown import TcpLockdownClient
    from pymobiledevice3.remote.tunnel_service import CoreDeviceTunnelProxy
    from pymobiledevice3.service_connection import ServiceConnection

    events = []
    pairing = {"HostID": "selected-pairing"}

    async def close_socket():
        events.append("socket_closed")

    async def close_lockdown():
        events.append("lockdown_closed")

    async def developer_mode():
        events.append("developer_mode")
        return True

    service = SimpleNamespace(close=close_socket)
    lockdown = SimpleNamespace(session_id="trusted", all_values={"DeviceClass": "iPhone"},
        udid=identity, product_version="26.0", close=close_lockdown,
        get_developer_mode_status=developer_mode)
    proxy = object()

    async def open_socket(address, port, **options):
        assert (address, port, options) == ("192.0.2.1", 54321, {"keep_alive": True})
        events.append("socket_opened")
        return service

    async def create(connection, **options):
        assert connection is service
        assert options == {"hostname": "192.0.2.1", "identifier": "selected-phone",
            "port": 54321, "pair_record": pairing, "autopair": False, "keep_alive": True}
        assert options["pair_record"] is pairing
        events.append("paired")
        return lockdown

    async def create_proxy(selected):
        assert selected is lockdown
        assert selected.udid == "selected-phone"
        events.append("proxy_created")
        return proxy

    monkeypatch.setattr(ServiceConnection, "create_using_tcp", open_socket)
    monkeypatch.setattr(TcpLockdownClient, "create", create)
    monkeypatch.setattr(CoreDeviceTunnelProxy, "create", create_proxy)
    device = adapter.DeviceAdapter()
    options = Connect(device_id="selected-phone", transport="wifi")
    try:
        if identity == "selected-phone":
            assert await device._tcp_lockdown_provider(options, ("192.0.2.1", 54321, pairing)) is proxy
            assert events == ["socket_opened", "paired", "developer_mode", "proxy_created"]
        else:
            with pytest.raises(BackendError) as caught:
                await device._tcp_lockdown_provider(options, ("192.0.2.1", 54321, pairing))
            assert caught.value.code == "wrong_device"
            assert events == ["socket_opened", "paired"]
    finally:
        await device.close()
    assert events[-2:] == ["lockdown_closed", "socket_closed"]


async def test_cancelled_tcp_pairing_leaves_socket_owned_until_adapter_close(monkeypatch):
    from pymobiledevice3.lockdown import TcpLockdownClient
    from pymobiledevice3.service_connection import ServiceConnection

    started = asyncio.Event()
    closed = []

    async def close():
        closed.append("socket")

    async def open_socket(*_, **__):
        return SimpleNamespace(close=close)

    async def initialize(*_, **__):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(ServiceConnection, "create_using_tcp", open_socket)
    monkeypatch.setattr(TcpLockdownClient, "create", initialize)
    device = adapter.DeviceAdapter()
    options = Connect(device_id="selected-phone", transport="wifi")
    task = asyncio.create_task(device._tcp_lockdown_provider(options,
                              ("192.0.2.1", 54321, {"HostID": "saved"})))
    try:
        async with asyncio.timeout(1):
            await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await device.close()
    assert closed == ["socket"]


async def test_usb_present_phone_connects_through_explicit_lan_before_remote_fallback(monkeypatch):
    from pymobiledevice3 import lockdown

    events = []
    endpoint = ("192.0.2.1", 54321, {"HostID": "saved"})

    async def close():
        events.append("close_lan")

    async def forbidden(*_, **__):
        pytest.fail("A verified LAN service must connect without opaque usbmux or RemotePairing")

    class Adapter(adapter.DeviceAdapter):
        async def usb_present(self, identifier):
            assert identifier == "selected-phone"
            return True

        async def _mobdev_candidates(self, identifier):
            assert identifier == "selected-phone"
            events.append("lan_discovery")
            return [endpoint]

        async def _tcp_lockdown_provider(self, options, candidate):
            assert candidate is endpoint
            events.append("lan_connect")
            return SimpleNamespace(close=close)

        async def _wifi_candidates(self, _):
            await forbidden()

        async def _open_services(self, provider, options, mode, progress):
            assert mode == "wifi"
            self._wifi_progress("wireless_service", progress)
            self.rsd = SimpleNamespace(udid=options.device_id, product_version="26.0",
                product_build_version="test", product_type="iPhone-test")

    monkeypatch.setattr(lockdown, "create_using_usbmux", forbidden)
    device = Adapter()
    try:
        result = await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
        assert result["connection_path"] == "lan_lockdown" and result["transport"] == "wifi"
        assert [(item["path"], item["status"]) for item in result["connection_attempts"]] == [
            ("network_lockdown", "failed"), ("lan_lockdown", "connected")]
        assert result["connection_attempts"][0]["error"]["code"] == "wifi_path_unverified"
        assert events == ["lan_discovery", "lan_connect"]
    finally:
        await device.close()
    assert events[-1] == "close_lan"


@pytest.mark.parametrize("failure", ["unreachable", "unpaired", "service", "tunnel", "identity"])
async def test_lan_failures_fall_back_only_when_remote_pairing_is_appropriate(monkeypatch, failure):
    from pymobiledevice3.exceptions import InvalidServiceError
    from pymobiledevice3.remote import tunnel_service

    events = []

    class Provider:
        def __init__(self, *args):
            self.kind = "remote" if args else "lan"

        async def connect(self, autopair):
            assert autopair is False
            events.append("remote_authenticated")

        async def close(self):
            events.append(f"close_{self.kind}")

    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, *_):
            raise ConnectionRefusedError()

        async def _mobdev_candidates(self, _):
            return [("192.0.2.1", 54321, {"HostID": "saved"})]

        async def _tcp_lockdown_provider(self, *_):
            events.append("lan_attempt")
            if failure == "unreachable":
                raise ConnectionRefusedError()
            if failure == "unpaired":
                raise BackendError("setup_required", "Saved pairing is stale.")
            if failure == "service":
                raise InvalidServiceError("unavailable", "selected-phone", "26.0")
            if failure == "identity":
                raise BackendError("wrong_device", "Another phone answered.")
            return Provider()

        async def _wifi_candidates(self, identifier):
            assert identifier == "selected-phone"
            assert failure != "identity", "A proven identity mismatch must stop fallback"
            if failure == "tunnel":
                assert "close_lan" in events
            events.append("remote_discovery")
            return [("192.0.2.2", 12345)]

        async def _open_services(self, provider, options, mode, progress):
            self._wifi_progress("wireless_tunnel", progress)
            if provider.kind == "lan":
                raise ConnectionResetError()
            self.rsd = SimpleNamespace(udid=options.device_id, product_version="26.0",
                product_build_version="test", product_type="iPhone-test")

    monkeypatch.setattr(tunnel_service, "RemotePairingTunnelService", Provider)
    device = Adapter()
    try:
        if failure == "identity":
            with pytest.raises(BackendError) as caught:
                await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
            assert caught.value.code == "wrong_device"
            assert events == ["lan_attempt"]
        else:
            result = await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
            assert result["connection_path"] == "remote_pairing"
            assert [(item["path"], item["status"]) for item in result["connection_attempts"]] == [
                ("network_lockdown", "failed"), ("lan_lockdown", "failed"), ("remote_pairing", "connected")]
            assert events.index("lan_attempt") < events.index("remote_discovery") < events.index("remote_authenticated")
    finally:
        await device.close()
    if failure != "identity":
        assert events.count("close_remote") == 1


@pytest.mark.parametrize("failure,stage,fallback", [
    (bonjour.BonjourError, "discovery", True),
    (ConnectionError, "discovery", True),
    (TimeoutError, "discovery", True),
    (FileNotFoundError, "pairing", True),
    (plistlib.InvalidFileException, "pairing", True),
    (PermissionError, "pairing", False),
    (ValueError, "discovery", False),
    (AttributeError, "discovery", False),
])
async def test_optional_lan_discovery_failure_preserves_remote_pairing(monkeypatch, failure, stage, fallback):
    from pymobiledevice3 import pair_records
    from pymobiledevice3.remote import tunnel_service

    events = []

    async def saved(*_, **__):
        if stage == "pairing":
            raise failure("private pairing details")
        return {"HostID": "saved"}

    async def browse(**_):
        raise failure("private discovery details")

    class Provider:
        def __init__(self, identifier, address, port):
            assert (identifier, address, port) == ("selected-phone", "192.0.2.2", 12345)

        async def connect(self, autopair):
            assert autopair is False
            events.append("remote_authenticated")

        async def close(self):
            events.append("remote_closed")

    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, *_):
            raise ConnectionRefusedError()

        async def _wifi_candidates(self, identifier):
            assert identifier == "selected-phone"
            assert fallback, "Permissions and programming failures must stop fallback"
            events.append("remote_discovery")
            return [("192.0.2.2", 12345)]

        async def _open_services(self, provider, options, mode, progress):
            self.rsd = SimpleNamespace(udid=options.device_id, product_version="26.0",
                product_build_version="test", product_type="iPhone-test")

    monkeypatch.setattr(pair_records, "get_preferred_pair_record", saved)
    monkeypatch.setattr(bonjour, "browse_mobdev2", browse)
    monkeypatch.setattr(tunnel_service, "RemotePairingTunnelService", Provider)
    device = Adapter()
    try:
        if fallback:
            result = await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
            assert result["connection_path"] == "remote_pairing"
            assert [(item["path"], item["status"]) for item in result["connection_attempts"]] == [
                ("network_lockdown", "failed"), ("lan_lockdown", "failed"), ("remote_pairing", "connected")]
            assert events == ["remote_discovery", "remote_authenticated"]
            assert "private" not in str(result["connection_attempts"])
        else:
            with pytest.raises(BackendError) as caught:
                await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
            assert caught.value.code == ("permission_denied" if failure is PermissionError else "device_unavailable")
            assert events == []
    finally:
        await device.close()
    if fallback:
        assert events.count("remote_closed") == 1


async def test_slow_lan_pairing_is_cancelled_and_socket_closed_before_remote_fallback(monkeypatch):
    from pymobiledevice3.lockdown import TcpLockdownClient
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.service_connection import ServiceConnection

    events = []
    timeouts = []
    original_timeout = asyncio.timeout

    def scaled_lan_timeout(delay):
        timeouts.append(delay)
        return original_timeout(0.02 if delay == 12 else delay)

    async def close_socket():
        events.append("socket_closed")

    async def open_socket(*_, **__):
        events.append("socket_opened")
        return SimpleNamespace(close=close_socket)

    async def initialize(*_, **__):
        events.append("lan_pairing_started")
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            events.append("lan_pairing_cancelled")
            raise

    class Provider:
        def __init__(self, *_):
            pass

        async def connect(self, autopair):
            assert autopair is False
            events.append("remote_authenticated")

        async def close(self):
            events.append("remote_closed")

    class Adapter(adapter.DeviceAdapter):
        async def _lockdown_provider(self, *_):
            raise ConnectionRefusedError()

        async def _mobdev_candidates(self, _):
            return [("192.0.2.1", 54321, {"HostID": "saved"})]

        async def _wifi_candidates(self, _):
            assert events == ["socket_opened", "lan_pairing_started", "lan_pairing_cancelled", "socket_closed"]
            events.append("remote_discovery")
            return [("192.0.2.2", 12345)]

        async def _open_services(self, provider, options, mode, progress):
            self.rsd = SimpleNamespace(udid=options.device_id, product_version="26.0",
                product_build_version="test", product_type="iPhone-test")

    monkeypatch.setattr(asyncio, "timeout", scaled_lan_timeout)
    monkeypatch.setattr(ServiceConnection, "create_using_tcp", open_socket)
    monkeypatch.setattr(TcpLockdownClient, "create", initialize)
    monkeypatch.setattr(tunnel_service, "RemotePairingTunnelService", Provider)
    device = Adapter()
    try:
        result = await device.connect(Connect(device_id="selected-phone", transport="wifi"), lambda *_: None)
        assert result["connection_path"] == "remote_pairing"
        assert timeouts.count(12) == 1
        assert any(item["path"] == "lan_lockdown" and item["status"] == "cancelled"
                   for item in result["connection_attempts"])
        assert result["connection_attempts"][-1]["status"] == "connected"
        assert events[-2:] == ["remote_discovery", "remote_authenticated"]
    finally:
        await device.close()
    assert events.count("socket_closed") == 1
    assert events[-1] == "remote_closed"
