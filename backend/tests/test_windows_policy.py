"""USB-only policy and platform prerequisites; no real device I/O."""
import asyncio
from types import SimpleNamespace

import pytest

from openlocation_backend import adapter, preparation
from openlocation_backend.errors import BackendError
from openlocation_backend.host import HostPolicy, check_host
from openlocation_backend.models import Connect, Prepare, Request
from openlocation_backend.protocol import Server, capabilities_for

WINDOWS = HostPolicy("windows", "x64", True, "10.0.19045")
MAC = HostPolicy("macos", "arm64")


@pytest.mark.parametrize("build,machine,bits,supported", [
    (19041, "AMD64", 64, False), (19045, "AMD64", 64, True),
    (22631, "AMD64", 64, True), (22631, "ARM64", 64, False),
    (19045, "x86", 32, False),
])
def test_windows_host_floor_and_architecture(monkeypatch, build, machine, bits, supported):
    from openlocation_backend import host
    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="win32", maxsize=2**(bits-1)-1,
        getwindowsversion=lambda: SimpleNamespace(major=10, minor=0, build=build)))
    monkeypatch.setattr(host, "platform", SimpleNamespace(machine=lambda: machine))
    monkeypatch.setattr(host, "os", SimpleNamespace(environ={}))
    assert host.detect_host().supported is supported


def test_service_missing_pywin32_error_closes_manager(monkeypatch):
    import sys
    from openlocation_backend.host import _apple_service_state
    closed = []
    class ServiceError(Exception):
        winerror = 1060
    def missing(*args): raise ServiceError()
    fake = SimpleNamespace(SC_MANAGER_CONNECT=1, SERVICE_QUERY_STATUS=4,
        OpenSCManager=lambda *args: "manager", OpenService=missing,
        CloseServiceHandle=lambda handle: closed.append(handle))
    monkeypatch.setitem(sys.modules, "win32service", fake)
    assert _apple_service_state() == "missing" and closed == ["manager"]


def test_platform_capabilities_are_usb_only_without_changing_mac_runtime():
    windows = capabilities_for(WINDOWS)
    assert windows["transports"] == ["usb"]
    assert windows["initial_setup"]["transports"] == ["usb"]
    assert windows["experimental_transports"] == []
    assert windows["recovery"]["automatic"] and not windows["wifi"]["usb_bootstrap"]
    mac = capabilities_for(MAC)
    assert mac["transports"] == ["usb"] and mac["cable_required"]
    assert mac["initial_setup"]["transports"] == ["usb"]
    assert mac["experimental_transports"] == [] and not mac["wifi"]["usb_bootstrap"]
    assert not mac["recovery"]["automatic"]
    assert MAC.mux_options == {}


@pytest.mark.parametrize("transport", ["wifi", "remote"])
@pytest.mark.parametrize("policy", [WINDOWS, MAC])
async def test_hosts_reject_wireless_before_open_or_discovery(monkeypatch, transport, policy):
    async def forbidden(*args, **kwargs):
        pytest.fail("A rejected transport performed I/O")
    from pymobiledevice3 import usbmux
    monkeypatch.setattr(usbmux, "list_devices", forbidden)
    device = adapter.DeviceAdapter(policy=policy)
    monkeypatch.setattr(device, "_open", forbidden)
    params = {"device_id": "selected", "transport": transport}
    if transport == "remote":
        params["remote"] = {"enabled": True, "address": "10.0.0.2", "port": 1234, "pairing_identifier": "selected"}
    with pytest.raises(BackendError) as error:
        await device.connect(Connect(**params), lambda *_: None)
    assert error.value.code == "unsupported_transport"
    with pytest.raises(BackendError):
        await adapter.discover(transport, policy=policy)


async def test_windows_auto_and_presence_pin_local_mux_and_exclude_network(monkeypatch):
    from pymobiledevice3 import usbmux, lockdown
    from openlocation_backend import bonjour
    calls = []
    async def devices(**kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(serial="wifi-only", connection_type="Network"),
                SimpleNamespace(serial="selected", connection_type="USB")]
    async def details(**kwargs):
        assert kwargs["usbmux_address"] == "127.0.0.1:27015"
        assert kwargs["connection_type"] == "USB" and kwargs["autopair"] is False
        raise ConnectionError()
    async def forbidden(**kwargs):
        pytest.fail("Windows performed Bonjour discovery")
    monkeypatch.setenv("USBMUXD_SOCKET_ADDRESS", "192.0.2.1:27015")
    monkeypatch.setattr(usbmux, "list_devices", devices)
    monkeypatch.setattr(lockdown, "create_using_usbmux", details)
    monkeypatch.setattr(bonjour, "browse_remotepairing", forbidden)
    found = await adapter.discover("auto", policy=WINDOWS)
    assert [d["device_id"] for d in found["devices"]] == ["selected"]
    assert await adapter.usb_present("selected", policy=WINDOWS)
    assert not await adapter.usb_present("wifi-only", policy=WINDOWS)
    assert calls and all(c == {"usbmux_address": "127.0.0.1:27015"} for c in calls)
    device = adapter.DeviceAdapter(policy=WINDOWS)
    async def open_usb(options, mode, progress):
        assert mode == options.transport == "usb"
        device.rsd = SimpleNamespace(udid="selected", product_version="17.4", product_build_version="test", product_type="iPhone")
    monkeypatch.setattr(device, "_open", open_usb)
    assert (await device.connect(Connect(device_id="selected"), lambda *_: None))["transport"] == "usb"


@pytest.mark.parametrize("policy", [WINDOWS, MAC])
async def test_usb_setup_completes_without_pairing_wifi(monkeypatch, tmp_path, policy):
    from pymobiledevice3 import lockdown
    calls = []
    class Phone:
        udid = "selected"
        session_id = "trusted"
        product_version = "17.4"
        all_values = {"DeviceClass": "iPhone"}
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get_developer_mode_status(self): return True
    async def create(**kwargs):
        calls.append(kwargs)
        return Phone()
    async def mount(*args): return {"image": "already_mounted"}
    async def wifi(*args): pytest.fail("USB setup enabled wireless pairing")
    monkeypatch.setattr(lockdown, "create_using_usbmux", create)
    monkeypatch.setattr(preparation, "mount", mount)
    monkeypatch.setattr(adapter, "_prepare_wifi", wifi)
    result = await adapter.prepare(Prepare(device_id="selected", mount_image=True), tmp_path, lambda *_: None, policy=policy)
    assert result["setup_complete"] is True and "wifi_pairing_verified" not in result
    assert calls[0].get("usbmux_address") == policy.mux_options.get("usbmux_address") and calls[0]["autopair"] is False
    with pytest.raises(BackendError) as error:
        await adapter.prepare(Prepare(device_id="selected", enable_wifi=True), tmp_path, lambda *_: None, policy=policy)
    assert error.value.code == "unsupported_transport" and len(calls) == 1


async def test_host_check_reports_service_evidence_without_needing_a_phone():
    async def mux(**kwargs):
        assert kwargs == WINDOWS.mux_options
        return []
    async def missing(**kwargs): raise ConnectionRefusedError()
    ready = await check_host(WINDOWS, service_probe=lambda: "running", mux_probe=mux)
    assert ready["usb_service"] == "ready" and ready["supported"]
    for state in ("missing", "stopped", "unavailable"):
        result = await check_host(WINDOWS, service_probe=lambda: state, mux_probe=missing)
        assert result["usb_service"] == state
    # A compatible Apple stack may have a different service registration.
    assert (await check_host(WINDOWS, service_probe=lambda: "missing", mux_probe=mux))["usb_service"] == "ready"
    result = await check_host(HostPolicy("windows", "arm64", False), mux_probe=mux)
    assert not result["supported"] and result["usb_service"] == "unavailable"


@pytest.mark.parametrize("policy", [WINDOWS, MAC])
async def test_protocol_usb_preparation_callback_never_enables_wifi(monkeypatch, tmp_path, policy):
    calls = []
    async def prepare(options, cache, progress, *, policy):
        calls.append(options)
        return {"setup_complete": True}
    monkeypatch.setattr(adapter, "prepare", prepare)
    server = Server(asyncio.StreamReader(), None, directory=tmp_path, policy=policy)
    async def connect(options, *, prepare_device):
        server.session.options = options
        await prepare_device(options.device_id)
        return {"session_id": "test"}
    monkeypatch.setattr(server.session, "connect", connect)
    await server.dispatch(Request(id="test", op="connect"), Connect(device_id="selected", prepare=True))
    assert len(calls) == 1 and not calls[0].enable_wifi
