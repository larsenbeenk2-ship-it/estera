"""Failure-only Wi-Fi setup reads never change the selected phone's settings."""

import asyncio
from types import SimpleNamespace

import pytest

from openlocation_backend import adapter
from openlocation_backend.errors import BackendError
from openlocation_backend.host import HostPolicy
from openlocation_backend.models import Connect


@pytest.fixture
def probe(monkeypatch):
    from pymobiledevice3.lockdown import UsbmuxLockdownClient
    from pymobiledevice3.service_connection import ServiceConnection

    state = SimpleNamespace(value=False, identity="selected-phone", paired=True,
                            device_class="iPhone", events=[], failure=None,
                            blocked=None, close_blocked=False, entered=asyncio.Event())

    class Service:
        async def close(self):
            state.events.append("socket_closed")
            if state.close_blocked:
                await asyncio.Future()

    class Lockdown:
        @property
        def udid(self):
            return state.identity

        @property
        def session_id(self):
            return "trusted" if state.paired else None

        @property
        def all_values(self):
            return {"DeviceClass": state.device_class}

        async def get_value(self, **kwargs):
            assert kwargs == {"domain": "com.apple.mobile.wireless_lockdown", "key": "EnableWifiDebugging"}
            state.events.append("read_setting")
            state.entered.set()
            if state.blocked == "read":
                await asyncio.Future()
            if state.failure:
                raise state.failure
            return state.value

        async def close(self):
            state.events.append("lockdown_closed")
            if state.close_blocked:
                await asyncio.Future()

    async def socket(identifier, port, **kwargs):
        assert (identifier, port, kwargs) == ("selected-phone", 62078, {"connection_type": "USB"})
        state.events.append("open_usb")
        return Service()

    async def create(service, **kwargs):
        assert isinstance(service, Service)
        assert kwargs == {"identifier": "selected-phone", "autopair": False}
        state.events.append("authenticate")
        if state.blocked == "authenticate":
            state.entered.set()
            await asyncio.Future()
        return Lockdown()

    monkeypatch.setattr(adapter, "HOST_POLICY", HostPolicy("macos", "arm64"))
    monkeypatch.setattr(ServiceConnection, "create_using_usbmux", socket)
    monkeypatch.setattr(UsbmuxLockdownClient, "create", create)
    return state


class WirelessFailure(adapter.DeviceAdapter):
    initial_code = "wifi_path_unverified"
    successful_path = None

    async def _wifi_attempt(self, options, path, endpoint, progress):
        error = BackendError(self.initial_code if path == "network_lockdown" else "device_unavailable", "test failure")
        self.connection_attempts.append({"path": path, "status": "failed", "error": error.as_dict()})
        return None if path == self.successful_path else error

    async def _mobdev_candidates(self, identifier):
        assert identifier == "selected-phone"
        return [("192.0.2.1", 62078, {})]

    async def _wifi_candidates(self, identifier):
        assert identifier == "selected-phone"
        return [("192.0.2.1", 49152)]


OPTIONS = Connect(device_id="selected-phone", transport="wifi")


async def failed(device):
    with pytest.raises(BackendError) as caught:
        await device._open_wifi(OPTIONS, lambda *_: None)
    return caught.value


async def test_confirmed_disabled_setting_explains_failure_after_all_paths(probe):
    device = WirelessFailure()
    error = await failed(device)
    assert error.code == "wifi_setup_required"
    assert "Wi-Fi debugging is disabled" in error.message
    assert "Set up Wi-Fi over USB" in error.message
    assert not error.retryable
    assert [attempt["path"] for attempt in device.connection_attempts] == [
        "network_lockdown", "lan_lockdown", "remote_pairing"]
    assert probe.events == ["open_usb", "authenticate", "read_setting", "lockdown_closed", "socket_closed"]


@pytest.mark.parametrize("value", [True, None, 0, "false", ""])
async def test_only_literal_false_changes_primary_error(probe, value):
    probe.value = value
    assert (await failed(WirelessFailure())).code == "wifi_unreachable"


@pytest.mark.parametrize("failure", [KeyError("MissingValue"), ConnectionError(), PermissionError()])
async def test_optional_read_failure_preserves_primary_error_and_closes(probe, failure):
    probe.failure = failure
    assert (await failed(WirelessFailure())).code == "wifi_unreachable"
    assert probe.events[-2:] == ["lockdown_closed", "socket_closed"]


@pytest.mark.parametrize("field,value", [("identity", "different-phone"), ("paired", False),
                                         ("device_class", "iPad"), ("device_class", None)])
async def test_only_authenticated_selected_iphone_setting_is_read(probe, field, value):
    setattr(probe, field, value)
    assert (await failed(WirelessFailure())).code == "wifi_unreachable"
    assert "read_setting" not in probe.events
    assert probe.events[-2:] == ["lockdown_closed", "socket_closed"]


@pytest.mark.parametrize("path", ["network_lockdown", "lan_lockdown", "remote_pairing"])
async def test_successful_wireless_paths_never_probe_usb(probe, path):
    device = WirelessFailure()
    device.successful_path = path
    await device._open_wifi(OPTIONS, lambda *_: None)
    assert probe.events == []


async def test_no_usb_probe_without_initial_selected_usb_evidence(probe):
    device = WirelessFailure()
    device.initial_code = "device_unavailable"
    assert (await failed(device)).code == "wifi_unreachable"
    assert probe.events == []


async def test_probe_authentication_timeout_is_bounded_and_closes_owned_socket(probe, monkeypatch):
    probe.blocked = "authenticate"
    original_timeout = asyncio.timeout
    limits = []

    def timeout(seconds):
        limits.append(seconds)
        return original_timeout(0.01 if seconds == 1 else seconds)

    monkeypatch.setattr(adapter.asyncio, "timeout", timeout)
    assert (await failed(WirelessFailure())).code == "wifi_unreachable"
    assert limits == [39, 12, 1, 1]
    assert probe.events == ["open_usb", "authenticate", "socket_closed"]


async def test_probe_cancellation_propagates_and_closes(probe):
    probe.blocked = "read"
    task = asyncio.create_task(failed(WirelessFailure()))
    await probe.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert probe.events[-2:] == ["lockdown_closed", "socket_closed"]


async def test_optional_probe_cleanup_has_its_own_bounded_deadlines(probe, monkeypatch):
    probe.value = None
    probe.close_blocked = True
    original_timeout = asyncio.timeout
    limits = []

    def timeout(seconds):
        limits.append(seconds)
        return original_timeout(0.01 if seconds == 1 else seconds)

    monkeypatch.setattr(adapter.asyncio, "timeout", timeout)
    assert (await failed(WirelessFailure())).code == "wifi_unreachable"
    assert limits == [39, 12, 1, 1, 1]
    assert probe.events[-2:] == ["lockdown_closed", "socket_closed"]


async def test_wireless_timeout_preserves_original_error_when_setting_unknown(probe):
    class TimedOut(WirelessFailure):
        async def _wifi_candidates(self, identifier):
            raise TimeoutError()

    probe.value = None
    assert (await failed(TimedOut())).code == "wifi_timeout"
    assert "read_setting" in probe.events


@pytest.mark.parametrize("value,expected", [(False, "wifi_setup_required"), (None, "wifi_lan_not_found")])
async def test_cable_only_discovery_reaches_failure_diagnostic(probe, value, expected):
    class CableOnly(WirelessFailure):
        async def _wifi_candidates(self, identifier):
            raise BackendError("wifi_lan_not_found", "Only cable interfaces were found.", True)

    probe.value = value
    assert (await failed(CableOnly())).code == expected
    assert "read_setting" in probe.events


async def test_identity_failure_is_not_replaced_by_setup_diagnostic(probe):
    class WrongDevice(WirelessFailure):
        async def _wifi_candidates(self, identifier):
            raise BackendError("wrong_device", "Selected identity differs.")

    assert (await failed(WrongDevice())).code == "wrong_device"
    assert probe.events == []
