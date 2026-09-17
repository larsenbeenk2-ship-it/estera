"""Desktop ownership must not weaken the loopback or device authorization rules."""
import threading

import httpx
import pytest

from openlocation_backend import desktop
from openlocation_backend.providers import Providers
from openlocation_backend.web import create_app


class Helper:
    listeners = set()

    async def call(self, op, *args):
        return {}


async def test_desktop_identity_is_not_api_authorization():
    desktop_token = 'desktop-launch-identity-1234567890'
    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    try:
        app = create_app(Helper(), providers, desktop_instance=desktop_token)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:3000') as client:
            bootstrap = (await client.get('/api/bootstrap')).json()
            assert bootstrap['desktop_instance'] == desktop_token
            assert bootstrap['token'] != desktop_token
            assert (await client.get('/api/bootstrap', headers={'origin': 'https://example.com'})).status_code == 403
            assert (await client.get('/api/status', headers={'x-openlocation-token': desktop_token})).status_code == 403
            client.headers.update({'origin': 'http://localhost:3000', 'x-openlocation-token': bootstrap['token']})
            assert (await client.get('/api/status')).status_code == 200
            response = await client.post('/api/command', json={'op': 'connect', 'params': {'device_id': 'test-device'}})
            assert response.status_code == 400
            assert response.json()['error']['code'] == 'authorization_required'
        source_app = create_app(Helper(), providers)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=source_app), base_url='http://localhost:3000') as client:
            assert 'desktop_instance' not in (await client.get('/api/bootstrap')).json()
    finally:
        await providers.close()


def test_parent_exit_requests_shutdown_and_closes_observer(monkeypatch):
    class Parent:
        pid = 100
        closed = False
        running = True

        def alive(self):
            return self.running

        def close(self):
            self.closed = True

    parent = Parent()
    monkeypatch.setattr(desktop, 'ParentProcess', lambda: parent)
    stopped = threading.Event()
    with desktop.watch_parent(100, stopped.set):
        parent.running = False
        assert stopped.wait(2)
    assert parent.closed


def test_parent_identity_mismatch_rejected_without_signal(monkeypatch):
    class Parent:
        pid = 100
        closed = False

        def alive(self):
            return True

        def close(self):
            self.closed = True

    parent = Parent()
    monkeypatch.setattr(desktop, 'ParentProcess', lambda: parent)
    with pytest.raises(RuntimeError, match='parent'):
        with desktop.watch_parent(200, lambda: pytest.fail('Unrelated process must not be signalled')):
            pytest.fail('Wrong parent must not start the server')
    assert parent.closed
