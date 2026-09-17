"""Guided setup: explicit reveal only, read-only readiness and selected-phone gates."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from openlocation_backend import adapter
from openlocation_backend.errors import BackendError
from openlocation_backend.models import DeviceTarget, Prepare, Request
from openlocation_backend.protocol import Server
from openlocation_backend.providers import Providers
from openlocation_backend.web import create_app


@pytest.fixture
def phone(monkeypatch):
    from pymobiledevice3 import lockdown, usbmux
    from pymobiledevice3.services import mobile_image_mounter

    state = SimpleNamespace(usb=True, paired=True, developer_mode=False, image=False,
                            identity='test-phone', actions=[], connections=[], closed=0,
                            reveal_success=True, image_checks=0, block=None,
                            device_class='iPhone', discovery_calls=0)

    class Service:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            state.closed += 1

        async def send_recv_plist(self, request):
            state.actions.append(request)
            if state.block:
                state.block.set()
                await asyncio.Future()
            return {'success': state.reveal_success}

    class Lockdown:
        product_version = '26.6.2'

        @property
        def all_values(self):
            return {'DeviceClass': state.device_class} if state.device_class else {}

        @property
        def udid(self):
            return state.identity

        @property
        def session_id(self):
            return 'trusted' if state.paired else None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            state.closed += 1

        async def get_developer_mode_status(self):
            return state.developer_mode

        async def start_lockdown_service(self, name):
            assert name == 'com.apple.amfi.lockdown'
            return Service()

    async def devices():
        state.discovery_calls += 1
        return [SimpleNamespace(serial='test-phone', connection_type='USB')] if state.usb else []

    async def create(**kwargs):
        assert kwargs['serial'] == 'test-phone' and kwargs['connection_type'] == 'USB'
        state.connections.append(kwargs)
        if kwargs.get('autopair'):
            state.paired = True
        return Lockdown()

    class Mounter:
        def __init__(self, device):
            assert state.developer_mode

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            state.closed += 1

        async def is_image_mounted(self, image_type):
            assert image_type == 'Personalized'
            state.image_checks += 1
            return state.image

    monkeypatch.setattr(usbmux, 'list_devices', devices)
    monkeypatch.setattr(lockdown, 'create_using_usbmux', create)
    monkeypatch.setattr(mobile_image_mounter, 'PersonalizedImageMounter', Mounter)
    return state


@pytest.mark.parametrize('usb,paired,mode,image,next_step', [
    (False, True, False, False, 'reconnect_usb'),
    (True, False, False, False, 'trust'),
    (True, True, False, False, 'developer_mode'),
    (True, True, True, False, 'developer_image'),
    (True, True, True, True, 'connect'),
])
async def test_readiness_has_no_setup_side_effects(phone, usb, paired, mode, image, next_step):
    phone.usb, phone.paired, phone.developer_mode, phone.image = usb, paired, mode, image
    result = await adapter.check_setup('test-phone')
    assert result['next_step'] == next_step
    assert result['usb_connected'] is usb
    assert not phone.actions
    assert all(c['autopair'] is False for c in phone.connections)
    assert phone.image_checks == int(usb and paired and mode)


async def test_reveal_is_explicit_and_sends_only_action_zero(phone, tmp_path):
    await adapter.prepare(Prepare(device_id='test-phone'), tmp_path, lambda *_: None)
    assert not phone.actions
    result = await adapter.prepare(Prepare(device_id='test-phone', reveal_developer_mode=True), tmp_path, lambda *_: None)
    assert phone.actions == [{'action': 0}]
    assert result['developer_mode_option_revealed'] is True
    assert result['developer_mode'] is False
    assert not phone.developer_mode and phone.closed == 3
    phone.developer_mode = True
    await adapter.prepare(Prepare(device_id='test-phone', reveal_developer_mode=True), tmp_path, lambda *_: None)
    assert phone.actions == [{'action': 0}]


async def test_unplug_during_check_returns_reconnect_step(phone, monkeypatch):
    from pymobiledevice3 import lockdown

    async def interrupted(**kwargs):
        phone.usb = False
        raise ConnectionResetError()

    monkeypatch.setattr(lockdown, 'create_using_usbmux', interrupted)
    result = await adapter.check_setup('test-phone')
    assert result['next_step'] == 'reconnect_usb'
    assert result['usb_connected'] is False and result['developer_mode'] is None
    assert phone.discovery_calls == 2
    assert not phone.actions


async def test_restart_waits_for_present_phone_then_reads_enabled_mode(phone, monkeypatch):
    from pymobiledevice3 import lockdown

    original = lockdown.create_using_usbmux

    async def restarting(**kwargs):
        assert kwargs['serial'] == 'test-phone' and kwargs['autopair'] is False
        raise ConnectionResetError()

    monkeypatch.setattr(lockdown, 'create_using_usbmux', restarting)
    result = await adapter.check_setup('test-phone')
    assert result['usb_connected'] is True and result['next_step'] == 'wait_usb'
    assert result['developer_mode'] is None and phone.discovery_calls == 2
    # A fresh check after the phone returns must skip enabling Developer Mode again.
    monkeypatch.setattr(lockdown, 'create_using_usbmux', original)
    phone.developer_mode, phone.image = True, True
    result = await adapter.check_setup('test-phone')
    assert result['next_step'] == 'connect' and result['developer_mode'] is True
    assert phone.discovery_calls == 3 and not phone.actions
    assert all(c['autopair'] is False for c in phone.connections)


async def test_restart_locked_phone_requests_unlock_not_pairing(phone, monkeypatch):
    from pymobiledevice3 import lockdown
    from pymobiledevice3.exceptions import PasswordRequiredError

    async def locked(**kwargs):
        assert kwargs['serial'] == 'test-phone' and kwargs['autopair'] is False
        raise PasswordRequiredError()

    monkeypatch.setattr(lockdown, 'create_using_usbmux', locked)
    result = await adapter.check_setup('test-phone')
    assert result['usb_connected'] is True and result['next_step'] == 'unlock'
    assert result['developer_mode'] is None and not phone.actions


async def test_restart_keeps_enabled_mode_when_image_service_is_unavailable(phone, monkeypatch):
    from pymobiledevice3.exceptions import InvalidServiceError
    from pymobiledevice3.services import mobile_image_mounter

    class UnavailableMounter:
        def __init__(self, _):
            pass

        async def __aenter__(self):
            raise InvalidServiceError('InvalidService', 'test-phone', '26.6.2')

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(mobile_image_mounter, 'PersonalizedImageMounter', UnavailableMounter)
    phone.developer_mode = True
    result = await adapter.check_setup('test-phone')
    assert result['usb_connected'] is True and result['paired'] is True
    assert result['developer_mode'] is True and result['next_step'] == 'developer_image'
    assert result['image_mounted'] is None and not phone.actions


async def test_restart_incomplete_metadata_is_not_removed_or_authenticated(phone):
    phone.device_class = None
    discovery = await adapter.discover('usb')
    assert len(discovery['devices']) == 1
    assert discovery['devices'][0]['device_id'] == 'test-phone'
    assert discovery['devices'][0]['state'] == 'setup_required'
    result = await adapter.check_setup('test-phone')
    assert result['usb_connected'] is True and result['next_step'] == 'wait_usb'
    assert result['paired'] is False and result['developer_mode'] is None
    assert not phone.actions and phone.image_checks == 0
    assert all(c['autopair'] is False for c in phone.connections)


async def test_reveal_failure_and_cancellation_close_services(phone, tmp_path):
    phone.reveal_success = False
    options = Prepare(device_id='test-phone', reveal_developer_mode=True)
    with pytest.raises(BackendError) as error:
        await adapter.prepare(options, tmp_path, lambda *_: None)
    assert error.value.code == 'developer_mode_reveal_failed' and phone.closed == 2
    phone.block = asyncio.Event()
    task = asyncio.create_task(adapter.prepare(options, tmp_path, lambda *_: None))
    await phone.block.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert phone.closed == 4
    assert all(action == {'action': 0} for action in phone.actions)


async def test_setup_checks_identity_and_trust_before_revealing(phone, tmp_path):
    phone.paired = False
    with pytest.raises(BackendError) as error:
        await adapter.prepare(Prepare(device_id='test-phone', reveal_developer_mode=True), tmp_path, lambda *_: None)
    assert error.value.code == 'setup_required' and not phone.actions
    phone.paired = True
    phone.identity = 'different-phone'
    for call in (adapter.check_setup('test-phone'), adapter.prepare(Prepare(device_id='test-phone', reveal_developer_mode=True), tmp_path, lambda *_: None)):
        with pytest.raises(BackendError) as error:
            await call
        assert error.value.code == 'wrong_device'
    assert not phone.actions


async def test_prepare_during_failed_initial_session_preserves_selection(monkeypatch, tmp_path):
    calls = []

    async def prepare(options, cache, progress, **kwargs):
        calls.append(options)
        return {'developer_mode': False, 'developer_mode_option_revealed': True}

    monkeypatch.setattr(adapter, 'prepare', prepare)
    server = Server(asyncio.StreamReader(), None, directory=tmp_path)
    server.session.id, server.session.device_id = 'selected-session', 'test-phone'
    server.session.transport = 'failed'
    request = Request(id='setup', op='device.prepare', session_id='selected-session')
    options = Prepare(device_id='test-phone', reveal_developer_mode=True)
    assert (await server.dispatch(request, options))['developer_mode_option_revealed']
    assert server.session.id == 'selected-session' and len(calls) == 1
    with pytest.raises(BackendError) as error:
        await server.dispatch(Request(id='stale', op='device.prepare', session_id='old-session'), options)
    assert error.value.code == 'stale_session'
    with pytest.raises(BackendError) as error:
        await server.dispatch(request, Prepare(device_id='different-phone', reveal_developer_mode=True))
    assert error.value.code == 'wrong_device'
    server.session.ever_connected = True
    with pytest.raises(BackendError) as error:
        await server.dispatch(request, options)
    assert error.value.code == 'session_exists' and len(calls) == 1


async def test_web_reveal_requires_explicit_and_matching_session_authorization():
    class Helper:
        listeners = set()

        def __init__(self):
            self.calls = []

        async def call(self, op, params=None, session_id=None, request_id=None):
            self.calls.append((op, params, session_id))
            if op == 'connect':
                return {'session_id': 'selected-session'}
            return {}

    helper = Helper()
    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    app = create_app(helper, providers)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:3000') as client:
            token = (await client.get('/api/bootstrap')).json()['token']
            client.headers.update({'origin': 'http://localhost:3000', 'x-openlocation-token': token})
            connect = {'op': 'connect', 'params': {'device_id': 'test-phone'}, 'authorized': True}
            assert (await client.post('/api/command', json=connect)).status_code == 200
            reveal = {'op': 'device.prepare', 'params': {'device_id': 'test-phone', 'reveal_developer_mode': True}, 'session_id': 'selected-session'}
            count = len(helper.calls)
            assert (await client.post('/api/command', json=reveal)).status_code == 400
            assert (await client.post('/api/command', json={**reveal, 'authorized': True, 'session_id': 'wrong-session'})).status_code == 400
            assert len(helper.calls) == count
            assert (await client.post('/api/command', json={**reveal, 'authorized': True})).status_code == 200
            assert helper.calls[-1][1]['reveal_developer_mode'] is True
    finally:
        await providers.close()
