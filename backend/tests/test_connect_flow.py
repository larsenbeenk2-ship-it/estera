"""Connection preparation uses fake services only; no phone or Apple requests."""
import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from openlocation_backend import adapter, web_client
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Connect
from openlocation_backend.protocol import Server, TIMEOUTS
from openlocation_backend.providers import Providers
from openlocation_backend.session import Session
from openlocation_backend.web import create_app


@pytest.fixture
def connection(monkeypatch, tmp_path):
    state = SimpleNamespace(calls=[], preparation_error=None, prepare_started=None, open_error=None,
                            open_started=None, release_open=None, devices=[], server=None)

    async def prepare(options, cache, progress, **kwargs):
        assert options.device_id == state.server.session.device_id == 'selected-phone'
        assert options.mount_image and options.allow_download
        assert not options.pair and not options.reveal_developer_mode
        assert options.enable_wifi is (state.server.session.options.transport in {'auto', 'usb', 'wifi'})
        assert cache == tmp_path
        assert state.server.session.transport == 'connecting'
        state.calls.append('prepare')
        progress('checking_developer_image', {})
        try:
            if state.prepare_started:
                state.prepare_started.set()
                await asyncio.Future()
            if state.preparation_error:
                raise state.preparation_error
            return {'image': 'mounted', 'setup_complete': True}
        finally:
            state.calls.append('prepare_closed')

    class Device:
        transport = None

        def __init__(self):
            state.devices.append(self)

        async def connect(self, options, progress):
            assert options.device_id == 'selected-phone'
            assert state.server.session.transport == 'connecting'
            state.calls.append('open')
            if state.open_started:
                state.open_started.set()
                await state.release_open.wait()
            if state.open_error:
                raise state.open_error
            self.transport = 'wifi' if options.transport == 'wifi' else 'usb'
            return {'transport': self.transport, 'device_id': options.device_id, 'os_version': '26.0'}

        async def wait_lost(self):
            await asyncio.Future()

        async def close(self):
            state.calls.append('device_closed')

        async def set(self, _):
            pytest.fail('Connecting must not set a location')

        async def clear(self):
            pytest.fail('Connecting must not clear a location')

    monkeypatch.setattr(adapter, 'prepare', prepare)
    state.server = Server(asyncio.StreamReader(), None, directory=tmp_path,
        session_factory=lambda emit: Session(emit, adapter_factory=Device,
            power=SimpleNamespace(acquire=lambda: None, release=lambda: None)))
    state.server.handshake = True
    return state


async def command(server, op, params=None, session_id=None, request_id=None):
    identifier = request_id or uuid.uuid4().hex
    await server.accept(json.dumps({'id': identifier, 'op': op, 'params': params or {},
                                    'session_id': session_id}).encode())
    if task := server.tasks.get(identifier):
        await task
    return server.responses.get_nowait()


def test_prepare_is_optional_and_strict():
    assert Connect(device_id='selected-phone').prepare is False
    with pytest.raises(ValidationError):
        Connect(device_id='selected-phone', prepare='true')


async def test_prepare_then_actual_service_then_authoritative_status(connection):
    state, server = connection, connection.server
    state.open_started, state.release_open = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(command(server, 'connect', {'device_id': 'selected-phone', 'prepare': True},
                                       request_id='connect-phone'))
    await state.open_started.wait()
    try:
        assert state.calls == ['prepare', 'prepare_closed', 'open']
        assert server.session.status()['transport_state'] == 'connecting'
        assert not server.session.ever_connected and not server.session.last_contact
        assert server.events['preparation']['data']['request_id'] == 'connect-phone'
        state.release_open.set()
        response = await task
        assert response['ok']
        result = response['result']
        assert result['transport'] == 'usb' and result['os_version'] == '26.0'
        assert result['session_id'] == server.session.id
        assert result['status'] == {**server.session.status(), 'sequence': server.sequence}
        assert result['status']['transport_state'] == 'connected'
        assert result['status']['last_contact'] and result['status']['ever_connected']
        assert result['status']['requested_coordinate'] is None
        assert result['status']['sequence'] == server.events['state']['sequence']
    finally:
        state.release_open.set()
        await task
        await server.session._close_adapter()


async def test_failed_preparation_remains_selected_and_initial_retry_prepares(connection):
    state, server = connection, connection.server
    state.preparation_error = BackendError('developer_mode_required', 'Enable Developer Mode on the iPhone.')
    failed = await command(server, 'connect', {'device_id': 'selected-phone', 'prepare': True})
    assert failed['error']['code'] == 'developer_mode_required'
    assert not state.devices and server.session.transport == 'failed'
    assert not server.session.ever_connected and server.session.last_contact is None
    assert server.session.connection_error == failed['error']
    sid = server.session.id
    stale = await command(server, 'retry', session_id='another-session')
    assert stale['error']['code'] == 'stale_session'
    state.preparation_error = None
    try:
        retried = await command(server, 'retry', session_id=sid)
        assert retried['ok'] and retried['result']['resume_required']
        assert state.calls == ['prepare', 'prepare_closed', 'prepare', 'prepare_closed', 'open']
        assert retried['result']['status']['session_id'] == sid
        assert retried['result']['status']['transport_state'] == 'connected'
        assert retried['result']['status']['sequence'] == server.sequence
        assert server.session.connection_error is None
    finally:
        await server.session._close_adapter()


async def test_completed_setup_survives_connection_failure_and_retry(connection):
    state, server = connection, connection.server
    state.open_error = BackendError('wifi_unreachable', 'The saved iPhone is unavailable.', True)
    failed = await command(server, 'connect', {'device_id': 'selected-phone',
                                             'transport': 'wifi', 'prepare': True})
    assert failed['error']['code'] == 'wifi_unreachable'
    assert server.session.status()['setup_complete'] is True
    assert server.session.ever_connected is False
    state.open_error = None
    try:
        retried = await command(server, 'retry', session_id=server.session.id)
        assert retried['ok'] and retried['result']['status']['setup_complete'] is True
        assert state.calls.count('prepare') == 1
        assert state.calls.count('open') == 2
        # Simulate a closed connection before disconnect; this fixture rejects
        # location clearing, which belongs to explicit stop/disconnect behavior.
        await server.session._close_adapter()
        await command(server, 'disconnect', session_id=server.session.id)
        assert server.session.status()['setup_complete'] is False
        reconnected = await command(server, 'connect', {'device_id': 'selected-phone',
                                                      'transport': 'wifi', 'prepare': False})
        assert reconnected['ok']
        assert state.calls.count('prepare') == 1
        assert reconnected['result']['status']['setup_complete'] is False
    finally:
        await server.session._close_adapter()


@pytest.mark.parametrize('transport,prepare,expected_preparations', [('wifi', False, 0), ('auto', True, 1)])
async def test_established_retry_never_adds_usb_preparation(connection, transport, prepare, expected_preparations):
    state, server = connection, connection.server
    try:
        assert (await command(server, 'connect', {'device_id': 'selected-phone', 'transport': transport,
                                                 'prepare': prepare}))['ok']
        await server.session._close_adapter()
        server.session.transport = 'failed'
        assert (await command(server, 'retry', session_id=server.session.id))['ok']
        assert state.calls.count('prepare') == expected_preparations
        assert state.calls.count('open') == 2
    finally:
        await server.session._close_adapter()


@pytest.mark.parametrize('stage', ['prepare', 'open'])
async def test_cancel_owns_preparation_and_open_resources(connection, stage):
    state, server = connection, connection.server
    started = asyncio.Event()
    if stage == 'prepare':
        state.prepare_started = started
    else:
        state.open_started, state.release_open = started, asyncio.Event()
    await server.accept(json.dumps({'id': 'connecting-phone', 'op': 'connect',
        'params': {'device_id': 'selected-phone', 'prepare': True}}).encode())
    await started.wait()
    # The preparation shares connection ownership, so a second device mutation
    # cannot interleave before the explicit cancel completes.
    busy = await command(server, 'connect', {'device_id': 'other-phone'})
    assert busy['error']['code'] == 'busy'
    await server.accept(b'{"id":"cancel-connect","op":"cancel","params":{"request_id":"connecting-phone"}}')
    await asyncio.gather(*list(server.tasks.values()))
    responses = [server.responses.get_nowait(), server.responses.get_nowait()]
    by_id = {response['id']: response for response in responses}
    assert by_id['connecting-phone']['error']['code'] == 'cancelled'
    assert by_id['cancel-connect']['result']['cancelled']
    assert server.session.adapter is None and server.session.transport == 'failed'
    assert server.session.connection_error['code'] == 'cancelled'
    assert not server.session.ever_connected and not server.session.last_contact
    assert state.calls[-1] == ('prepare_closed' if stage == 'prepare' else 'device_closed')


@pytest.mark.parametrize('op', ['connect', 'retry'])
async def test_helper_waits_for_complete_preparation_and_connection(monkeypatch, op):
    helper = web_client.HelperClient()
    deadlines = []
    real_timeout = asyncio.timeout

    def timeout(seconds):
        deadlines.append(seconds)
        return real_timeout(seconds)

    class Input:
        def write(self, frame):
            request = json.loads(frame)
            helper.pending[request['id']].set_result({'ok': True, 'result': {'connected': True}})

        async def drain(self):
            pass

    monkeypatch.setattr(web_client.asyncio, 'timeout', timeout)
    helper.process = SimpleNamespace(returncode=None, stdin=Input())
    assert await helper.call(op, {'device_id': 'selected-phone', 'prepare': True} if op == 'connect' else {}) == {'connected': True}
    assert deadlines == [5, TIMEOUTS[op] + 10]
    assert TIMEOUTS[op] >= 300 + 45 + 5
    assert not helper.pending


async def test_web_requires_authorization_before_integrated_preparation(connection, monkeypatch, tmp_path):
    state, server = connection, connection.server

    class Helper:
        listeners = set()

        async def call(self, op, params=None, session_id=None, request_id=None):
            response = await command(server, op, params, session_id, request_id)
            if not response['ok']:
                error = response['error']
                raise BackendError(error['code'], error['message'], error.get('retryable', False))
            return response['result']

    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    # API coverage does not depend on the frontend build directory being present.
    monkeypatch.setattr('openlocation_backend.web.WEB_DIST', tmp_path / 'unused-frontend')
    app = create_app(Helper(), providers)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:3000') as client:
            body = {'op': 'connect', 'params': {'device_id': 'selected-phone', 'prepare': True}}
            client.headers['origin'] = 'http://localhost:3000'
            assert (await client.post('/api/command', json={**body, 'authorized': True})).status_code == 403
            assert not state.calls
            client.headers['x-openlocation-token'] = (await client.get('/api/bootstrap')).json()['token']
            denied = await client.post('/api/command', json=body)
            assert denied.json()['error']['code'] == 'authorization_required' and not state.calls
            connected = await client.post('/api/command', json={**body, 'authorized': True})
            assert connected.status_code == 200
            status = connected.json()['status']
            assert status['transport_state'] == 'connected' and status['device_id'] == 'selected-phone'
            assert status['sequence'] == server.sequence and state.calls == ['prepare', 'prepare_closed', 'open']
    finally:
        await server.session._close_adapter()
        await providers.close()
