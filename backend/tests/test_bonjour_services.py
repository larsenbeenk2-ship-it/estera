"""Service selection must preserve bounded native discovery and platform fallback."""

import asyncio
from types import SimpleNamespace

import pytest

from openlocation_backend import bonjour


@pytest.mark.parametrize("service_type", [bonjour._REMOTEPAIRING, bonjour._MOBDEV2])
async def test_native_browser_requests_the_selected_service(monkeypatch, service_type):
    calls = []
    native = SimpleNamespace(DNSServiceBrowse=object())
    monkeypatch.setattr(bonjour, "_NativeDNS", lambda: native)
    monkeypatch.setattr(bonjour, "_Operation", lambda *args: calls.append(args))
    browser = bonjour._Browser(service_type)
    try:
        browser.start()
        assert calls[0][3:] == (native.DNSServiceBrowse, 0, 0, service_type, b"local.")
    finally:
        browser.close()


async def test_native_browser_default_remains_remote_pairing(monkeypatch):
    monkeypatch.setattr(bonjour, "_NativeDNS", lambda: SimpleNamespace())
    browser = bonjour._Browser()
    try:
        assert browser.service_type == bonjour._REMOTEPAIRING
    finally:
        browser.close()


@pytest.mark.parametrize("name", ["browse_remotepairing", "browse_mobdev2"])
async def test_non_macos_uses_matching_upstream_browser(monkeypatch, name):
    from pymobiledevice3 import bonjour as upstream

    calls = []
    expected = [object()]

    async def browse(*, timeout):
        calls.append(timeout)
        return expected

    async def wrong_service(**kwargs):
        pytest.fail("discovery selected the wrong upstream service")

    monkeypatch.setattr(bonjour.sys, "platform", "linux")
    other = "browse_mobdev2" if name == "browse_remotepairing" else "browse_remotepairing"
    monkeypatch.setattr(upstream, name, browse)
    monkeypatch.setattr(upstream, other, wrong_service)
    assert await getattr(bonjour, name)(timeout=2) is expected
    assert calls == [2.0]


@pytest.mark.parametrize("name,service_type", [
    ("browse_remotepairing", bonjour._REMOTEPAIRING),
    ("browse_mobdev2", bonjour._MOBDEV2),
])
@pytest.mark.parametrize("outcome", ["timeout", "cancel", "error"])
async def test_native_wrappers_preserve_result_and_cleanup(monkeypatch, name, service_type, outcome):
    instances = []
    expected = [object()]
    failure = bonjour.BonjourError(-65570)

    class Browser:
        def __init__(self, requested):
            assert requested == service_type
            self.error = asyncio.get_running_loop().create_future()
            self.closed = False
            instances.append(self)

        def start(self):
            if outcome == "cancel":
                self.error.cancel()
            else:
                self.error.set_exception(TimeoutError() if outcome == "timeout" else failure)

        def results(self):
            return expected

        def close(self):
            self.closed = True

    monkeypatch.setattr(bonjour.sys, "platform", "darwin")
    monkeypatch.setattr(bonjour, "_Browser", Browser)
    if outcome == "timeout":
        assert await getattr(bonjour, name)(timeout=2) is expected
    elif outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await getattr(bonjour, name)(timeout=2)
    else:
        with pytest.raises(bonjour.BonjourError) as raised:
            await getattr(bonjour, name)(timeout=2)
        assert raised.value is failure
    assert instances[0].closed


@pytest.mark.parametrize("name", ["browse_remotepairing", "browse_mobdev2"])
async def test_zero_timeout_does_not_start_discovery(monkeypatch, name):
    def forbidden(*args):
        pytest.fail("zero timeout must not start discovery")

    monkeypatch.setattr(bonjour, "_Browser", forbidden)
    assert await getattr(bonjour, name)(timeout=0) == []
