"""Connection setup regressions. No phone, network, pairing or location actions."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from openlocation_backend.adapter import DeviceAdapter
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Connect
from openlocation_backend.providers import Providers
from openlocation_backend.session import Session
from openlocation_backend.web import create_app


@pytest.mark.parametrize('failure,code', [
    (BackendError('developer_mode_required', 'Enable Developer Mode.'), 'developer_mode_required'),
    (BackendError('wrong_device', 'Wrong selected phone.'), 'wrong_device'),
    (ModuleNotFoundError('private diagnostic'), 'dependency_missing'),
    (AttributeError('private diagnostic'), 'device_unavailable'),
])
async def test_auto_does_not_hide_setup_identity_or_implementation_errors(failure, code):
    class Adapter(DeviceAdapter):
        def __init__(self):
            super().__init__()
            self.attempts = []
            self.closed = 0

        async def _open(self, options, mode, progress):
            self.attempts.append(mode)
            if mode == 'usb':
                raise failure
            raise BackendError('wifi_unreachable', 'Wrong error to show.')

        async def close(self):
            self.closed += 1

    adapter = Adapter()
    with pytest.raises(BackendError) as caught:
        await adapter.connect(Connect(device_id='test-phone'), lambda *_: None)
    assert caught.value.code == code
    assert 'private diagnostic' not in caught.value.message
    assert adapter.attempts == ['usb'] and adapter.closed == 1


async def test_legacy_auto_stays_usb_only_when_cable_is_absent():
    from pymobiledevice3.exceptions import NoDeviceConnectedError

    class Adapter(DeviceAdapter):
        def __init__(self):
            super().__init__()
            self.attempts = []

        async def _open(self, options, mode, progress):
            self.attempts.append(mode)
            if mode == 'usb':
                raise NoDeviceConnectedError()
            self.rsd = SimpleNamespace(udid=options.device_id, product_version='26.6.2',
                                       product_build_version='test-build', product_type='iPhone-test')

        async def close(self):
            pass

    adapter = Adapter()
    with pytest.raises(BackendError) as caught:
        await adapter.connect(Connect(device_id='test-phone', transport='auto'), lambda *_: None)
    assert caught.value.code == 'device_unavailable'
    assert adapter.attempts == ['usb']


@pytest.mark.parametrize('developer_mode,expected', [(False, 'developer_mode_required'), (True, 'preparation_required')])
async def test_usb_preflight_and_service_error_survive_auto(monkeypatch, developer_mode, expected):
    from pymobiledevice3 import lockdown
    from pymobiledevice3.remote.tunnel_service import CoreDeviceTunnelProxy
    from pymobiledevice3.exceptions import InvalidServiceError
    calls = []

    class Lockdown:
        udid = 'test-phone'
        session_id = 'trusted-test-session'
        product_version = '26.6.2'
        all_values = {'DeviceClass': 'iPhone'}

        async def get_developer_mode_status(self):
            calls.append('developer_mode')
            return developer_mode

        async def close(self):
            calls.append('close')

    async def create(**kwargs):
        assert kwargs['serial'] == 'test-phone' and kwargs['autopair'] is False
        assert kwargs['connection_type'] == 'USB'
        calls.append('usb')
        return Lockdown()

    async def proxy(cls, provider):
        calls.append('proxy')
        raise InvalidServiceError('Service unavailable', 'test-phone', '26.6.2')

    monkeypatch.setattr(lockdown, 'create_using_usbmux', create)
    monkeypatch.setattr(CoreDeviceTunnelProxy, 'create', classmethod(proxy))
    with pytest.raises(BackendError) as caught:
        await DeviceAdapter().connect(Connect(device_id='test-phone'), lambda *_: None)
    assert caught.value.code == expected
    assert calls == ['usb', 'developer_mode', *(['proxy'] if developer_mode else []), 'close']


async def test_initial_failure_status_retry_and_return_to_setup():
    events = []

    class Device:
        transport = 'usb'
        ready = False
        clears = 0

        async def connect(self, options, progress):
            if not self.ready:
                raise BackendError('developer_mode_required', 'Enable Developer Mode.')
            return {'transport': 'usb'}

        async def wait_lost(self):
            await asyncio.Future()

        async def close(self):
            pass

        async def clear(self):
            self.clears += 1

    device = Device()
    power = SimpleNamespace(acquire=lambda: None, release=lambda: None)
    owner = Session(events.append, adapter_factory=lambda: device, power=power)
    with pytest.raises(BackendError):
        await owner.connect(Connect(device_id='test-phone'))
    status = owner.status()
    assert status['requested_transport'] == 'auto'
    assert status['connection_error']['code'] == 'developer_mode_required'
    assert not status['ever_connected'] and not status['last_contact']
    assert status['playback_state'] == 'idle'
    sid = owner.id
    device.ready = True
    await owner.retry()
    assert owner.id == sid and owner.status()['ever_connected']
    assert owner.status()['connection_error'] is None
    await owner.disconnect()
    assert events[-1]['data']['session_id'] is None

    device.ready = False
    with pytest.raises(BackendError):
        await owner.connect(Connect(device_id='test-phone'))
    clears = device.clears
    result = await owner.disconnect()
    assert 'No location command' in result['message']
    assert device.clears == clears
    assert events[-1]['data']['connection_error'] is None


async def test_failed_connect_authorizes_only_matching_retry_session():
    class Helper:
        def __init__(self):
            self.listeners = set()
            self.calls = []
            self.status = {'session_id': None, 'device_id': None}

        async def call(self, op, params=None, session_id=None, request_id=None):
            self.calls.append(op)
            if op == 'status':
                return self.status
            if op == 'connect':
                self.status = {'session_id': 'selected-session', 'device_id': 'selected-phone'}
                raise BackendError('developer_mode_required', 'Enable Developer Mode.')
            return {}

    helper = Helper()
    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    app = create_app(helper, providers)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:3000') as client:
            token = (await client.get('/api/bootstrap')).json()['token']
            client.headers.update({'origin': 'http://localhost:3000', 'x-openlocation-token': token})
            async def request(op, **kwargs):
                return await client.post('/api/command', json={'op': op, **kwargs})
            # An error from an unrelated session cannot authorize that session.
            assert (await request('connect', params={'device_id': 'different-phone'}, authorized=True)).status_code == 400
            assert (await request('retry', session_id='selected-session')).status_code == 400
            assert 'retry' not in helper.calls
            assert (await request('connect', params={'device_id': 'selected-phone'}, authorized=True)).status_code == 400
            assert (await request('retry', session_id='selected-session')).status_code == 200
            assert (await request('timer', session_id='selected-session', params={'seconds': None})).status_code == 200
            assert (await request('retry', session_id='other-session')).status_code == 400
            assert (await request('disconnect', session_id='selected-session')).status_code == 200
            assert (await request('retry', session_id='selected-session')).status_code == 400
    finally:
        await providers.close()
