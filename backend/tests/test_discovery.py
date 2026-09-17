"""USB setup discovery stays on USB and does not discover or pair over Wi-Fi."""
import asyncio
import builtins
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openlocation_backend.models import Request
from openlocation_backend.protocol import PARAMETERS, Server


async def test_usb_discovery_never_imports_network_discovery_or_remote_pair_records(monkeypatch, tmp_path):
    from pymobiledevice3 import lockdown, usbmux

    connections = []
    forbidden_imports = []
    original_import = builtins.__import__

    def usb_only_import(name, *args, **kwargs):
        if name in {"pymobiledevice3.bonjour", "pymobiledevice3.pair_records"}:
            forbidden_imports.append(name)
            raise AssertionError("USB discovery must not load Wi-Fi discovery or pairing records")
        return original_import(name, *args, **kwargs)

    async def devices():
        # Network advertisements cannot consume the 32-device USB discovery limit.
        return [SimpleNamespace(serial=f"network-{index}", connection_type="Network") for index in range(32)] + [
            SimpleNamespace(serial="test-phone", connection_type="USB"),
            SimpleNamespace(serial="test-phone", connection_type="Network"),
        ]

    class Lockdown:
        all_values = {"DeviceClass": "iPhone", "DeviceName": "Test iPhone"}
        product_version = "26.6.2"
        session_id = "already-trusted"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get_developer_mode_status(self):
            return False

    async def create(**kwargs):
        connections.append(kwargs)
        return Lockdown()

    monkeypatch.setattr(usbmux, "list_devices", devices)
    monkeypatch.setattr(lockdown, "create_using_usbmux", create)
    monkeypatch.setattr(builtins, "__import__", usb_only_import)

    model = PARAMETERS["devices.list"]
    assert model.model_validate({}).transport == "auto"
    assert model.model_validate({"transport": "wifi"}).transport == "wifi"
    with pytest.raises(ValidationError):
        model.model_validate({"transport": "remote"})
    with pytest.raises(ValidationError):
        model.model_validate({"transport": "usb", "pair": True})

    server = Server(asyncio.StreamReader(), None, directory=tmp_path)
    server.session.id, server.session.device_id = "selected-session", "previous-phone"
    result = await server.dispatch(Request(id="discover", op="devices.list"), model(transport="usb"))

    assert not forbidden_imports
    assert connections == [{"serial": "test-phone", "connection_type": "USB", "autopair": False}]
    assert result["warnings"] == [] and result["selection_changed"] is False
    assert len(result["devices"]) == 1
    phone = result["devices"][0]
    assert phone["device_id"] == "test-phone" and phone["transports"] == ["usb"]
    assert phone["paired"] is True and phone["developer_mode"] is False
    assert phone["state"] == "setup_required"
    assert server.session.id == "selected-session" and server.session.device_id == "previous-phone"
