"""Shutdown must address the private instance, never a process found by port."""
import asyncio
import json

import httpx
import pytest

from openlocation_backend.errors import BackendError
from openlocation_backend.instance import WebInstance, stop_instance
from openlocation_backend.providers import Providers
from openlocation_backend.web import create_app


class Helper:
    listeners = set()
    async def call(self, op, *args): return {}
    async def start(self): pass
    async def close(self): pass


async def test_instance_shutdown_auth_identity_and_origin(tmp_path):
    instance = WebInstance(tmp_path / "project with spaces", directory=tmp_path / "private")
    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    app = create_app(Helper(), providers, instance=instance)
    exits = []
    app.state.request_exit = lambda: exits.append(True)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000") as client:
            token = (await client.get("/api/bootstrap")).json()["token"]
            body = {"instance_id": instance.id}
            assert (await client.post("/api/shutdown", json=body)).status_code == 403
            headers = {"Origin": "http://localhost:3000", "X-OpenLocation-Token": token}
            assert (await client.post("/api/shutdown", json=body, headers={**headers, "Origin": "https://example.com"})).status_code == 403
            assert (await client.post("/api/shutdown", json={"instance_id": "wrong-instance"}, headers=headers)).status_code == 400
            assert not exits
            with instance.claim(token):
                assert "Stopping Estera" in await stop_instance(tmp_path / "project with spaces", directory=instance.directory, client=client)
                assert exits == [True] and app.state.stopping.is_set()
            assert not instance.path.exists()
    finally:
        await providers.close()


def test_claim_excludes_second_instance_and_preserves_owner_record(tmp_path):
    first = WebInstance(tmp_path / "checkout", directory=tmp_path / "private")
    second = WebInstance(tmp_path / "checkout", directory=tmp_path / "private")
    with first.claim("a" * 43):
        with pytest.raises(BackendError) as error:
            with second.claim("b" * 43):
                pytest.fail("A second owner acquired the active instance")
        assert error.value.code == "store_busy"
        assert first.read()["instance_id"] == first.id
    with second.claim("b" * 43):
        assert second.read()["instance_id"] == second.id


async def test_stop_missing_corrupt_and_foreign_instance_never_follows_redirect(tmp_path):
    root = tmp_path / "checkout"
    instance = WebInstance(root, directory=tmp_path / "private")
    assert "No running" in await stop_instance(root, directory=instance.directory)
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == "http://localhost:3000/api/shutdown"
        return httpx.Response(302, headers={"Location": "https://example.com"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with instance.claim("a" * 43):
            with pytest.raises(BackendError):
                await stop_instance(root, directory=instance.directory, client=client)
        assert len(calls) == 1
        instance.path.write_text(json.dumps({"version": 1, "token": "bad"}), encoding="utf-8")
        with pytest.raises(BackendError):
            await stop_instance(root, directory=instance.directory, client=client)
        assert len(calls) == 1
