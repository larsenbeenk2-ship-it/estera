"""Read-only connection monitoring, with no phone, sockets or pairing writes."""
import asyncio
from types import SimpleNamespace

import pytest

from openlocation_backend.adapter import DeviceAdapter


@pytest.fixture
def health_adapter(monkeypatch):
    from pymobiledevice3.services.dvt.instruments import device_info

    def make(has_lockdown):
        adapter = DeviceAdapter()
        dvt = object()
        state = SimpleNamespace(calls=[], failure=None, lockdown_failure=None, closed=False)
        probed = asyncio.Event()

        async def probe(service):
            state.calls.append(service)
            if state.failure is not None:
                raise state.failure
            probed.set()

        async def get_date():
            assert has_lockdown, 'Remote lockdown is optional for DVT connections'
            if state.lockdown_failure is not None:
                raise state.lockdown_failure
            await probe('lockdown')

        class DeviceInfo:
            def __init__(self, provider):
                assert provider is dvt

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                state.closed = True

            async def mach_time_info(self):
                await probe('dvt')

        monkeypatch.setattr(device_info, 'DeviceInfo', DeviceInfo)
        adapter.rsd = SimpleNamespace(lockdown=object() if has_lockdown else None,
                                      get_date=get_date)
        return adapter, dvt, state, probed

    return make


async def test_optional_lockdown_loss_does_not_close_healthy_developer_connection(health_adapter):
    adapter, dvt, state, _ = health_adapter(True)
    try:
        await adapter._prepare_health_check(dvt)
        state.lockdown_failure = ConnectionError('independent lockdown service closed')
        await adapter._check_health()
        assert state.calls == ['dvt', 'dvt']
    finally:
        await adapter.close()


async def test_replacing_connection_observer_does_not_cancel_tunnel_reader():
    # Use the pinned dependency's real wait_closed cancellation behavior, without
    # sockets or a phone. Session replaces this observer when starting after Stop.
    from pymobiledevice3.remote.tunnel_service import RemotePairingTcpTunnel
    observed = asyncio.Event()
    peer_closed = asyncio.Event()

    class Tunnel(RemotePairingTcpTunnel):
        async def wait_closed(self):
            observed.set()
            await super().wait_closed()

    tunnel = Tunnel()
    tunnel._sock_read_task = asyncio.create_task(peer_closed.wait())
    adapter = DeviceAdapter()
    adapter.tunnel_client = tunnel
    adapter.stack.push_async_callback(tunnel.stop_tunnel)
    observer = asyncio.create_task(adapter.wait_lost())
    replacement = None
    try:
        await asyncio.wait_for(observed.wait(), timeout=1)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
        assert not tunnel._sock_read_task.done(), 'Observer cancellation killed the actual connection'
        replacement = asyncio.create_task(adapter.wait_lost())
        peer_closed.set()
        await asyncio.wait_for(replacement, timeout=1)
        assert adapter.loss_diagnostics['source'] == 'tunnel_closed'
    finally:
        observer.cancel()
        if replacement is not None:
            replacement.cancel()
        await asyncio.gather(observer, *([replacement] if replacement else []), return_exceptions=True)
        await adapter.close()
    assert tunnel._sock_read_task.done()


@pytest.mark.parametrize('has_lockdown', [False, True])
@pytest.mark.parametrize('failure', [ConnectionError, TimeoutError])
async def test_health_survives_heartbeats_until_real_loss(monkeypatch, health_adapter,
                                                        has_lockdown, failure):
    adapter, dvt, state, probed = health_adapter(has_lockdown)
    await adapter._prepare_health_check(dvt)
    assert len(state.calls) == 1  # A round trip is required before connected.
    ticks = asyncio.Queue()
    closed_waiter_cancelled = asyncio.Event()

    async def next_tick(delay):
        assert delay == 5
        await ticks.get()

    async def wait_closed():
        try:
            await asyncio.Future()
        finally:
            closed_waiter_cancelled.set()

    adapter.tunnel_client = SimpleNamespace(wait_closed=wait_closed)
    monkeypatch.setattr('openlocation_backend.adapter.asyncio.sleep', next_tick)
    watcher = asyncio.create_task(adapter.wait_lost())
    try:
        for _ in range(3):
            probed.clear()
            ticks.put_nowait(None)
            await asyncio.wait_for(probed.wait(), timeout=1)
            assert not watcher.done()
        assert state.calls == ['dvt'] * 4
        state.failure = failure('simulated loss')
        ticks.put_nowait(None)
        with pytest.raises(failure):
            await asyncio.wait_for(watcher, timeout=1)
        assert closed_waiter_cancelled.is_set()
        assert adapter.loss_diagnostics['source'] == 'health_check'
        assert adapter.loss_diagnostics['health_probe'] == 'developer_clock'
        assert adapter.loss_diagnostics['successful_heartbeats'] == 3
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await adapter.close()
    assert adapter._device_info is None
    assert state.closed is True


async def test_tunnel_closure_is_distinct_from_health_check():
    adapter = DeviceAdapter()

    async def wait_closed():
        return

    adapter.tunnel_client = SimpleNamespace(wait_closed=wait_closed)
    await asyncio.wait_for(adapter.wait_lost(), timeout=1)
    assert adapter.loss_diagnostics['source'] == 'tunnel_closed'
    assert adapter.loss_diagnostics['successful_heartbeats'] == 0


async def test_recovery_preserves_original_loss_without_private_exception_text():
    from openlocation_backend.errors import BackendError
    from openlocation_backend.models import Connect
    from openlocation_backend.session import Session

    events = []
    failed = asyncio.Event()
    attempts = 0

    class Device:
        transport = 'wifi'
        loss_diagnostics = {'source': 'health_check', 'health_probe': 'remote_lockdown',
                            'after_seconds': 5.01, 'successful_heartbeats': 0}

        async def connect(self, *_):
            nonlocal attempts
            attempts += 1
            if attempts > 1:
                raise BackendError('wifi_not_found', 'Phone unavailable.', True)
            return {'transport': 'wifi'}

        async def wait_lost(self):
            await failed.wait()
            raise ValueError('private upstream details')

        async def close(self):
            pass

    async def immediate(_):
        pass

    owner = Session(events.append, adapter_factory=Device, sleep=immediate,
                    power=SimpleNamespace(acquire=lambda: None, release=lambda: None))
    await owner.connect(Connect(device_id='test-phone', transport='wifi'))
    failed.set()
    await asyncio.wait_for(owner.monitor, timeout=1)
    await asyncio.wait_for(owner.recovery, timeout=1)
    status = owner.status()
    assert status['connection_error']['code'] == 'wifi_not_found'
    original = status['last_connection_loss']
    assert original['error']['code'] == 'device_unavailable'
    assert original['exception_type'] == 'ValueError'
    assert original['source'] == 'health_check'
    assert original['after_seconds'] == 5.01
    assert 'private upstream details' not in str(events)
    assert next(e for e in events if e['event'] == 'connection_lost')['data']['diagnostics'] == original
    await owner.disconnect()
    assert owner.status()['last_connection_loss'] is None


@pytest.mark.parametrize('has_lockdown', [False, True])
async def test_unusable_health_probe_rejects_preflight(health_adapter, has_lockdown):
    adapter, dvt, state, _ = health_adapter(has_lockdown)
    state.failure = ConnectionError('simulated unavailable service')
    try:
        with pytest.raises(ConnectionError):
            await adapter._prepare_health_check(dvt)
    finally:
        await adapter.close()
    assert state.closed is True
