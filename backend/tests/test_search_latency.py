"""Offline checks for suggestion replacement, cleanup, and provider spacing."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from openlocation_backend import providers
from openlocation_backend.errors import BackendError
from openlocation_backend.providers import Providers


class SlowSuggestions(httpx.AsyncByteStream):
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        yield b'{}'

    async def aclose(self):
        self.closed.set()


async def test_latest_suggestion_cancels_old_stream_and_returns_without_provider_busy():
    slow = SlowSuggestions()
    queries = []

    def response(request):
        query = request.url.params['q']
        queries.append(query)
        if query == 'lon':
            return httpx.Response(200, stream=slow)
        return httpx.Response(200, json={'type': 'FeatureCollection', 'features': []})

    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(response)))
    old = asyncio.create_task(provider.suggest('lon'))
    try:
        await asyncio.wait_for(slow.started.wait(), 1)
        result = await asyncio.wait_for(provider.suggest('london'), 1)
        assert result['places'] == []
        with pytest.raises(BackendError) as failure:
            await old
        assert failure.value.code == 'suggestion_superseded'
        assert failure.value.retryable
        assert queries == ['lon', 'london']
        assert slow.cancelled.is_set() and slow.closed.is_set()
        assert not provider.locks['suggest'].locked()
        assert provider._suggest_task is None and not provider.cache
    finally:
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        await provider.close()


@pytest.mark.parametrize('shutdown', [False, True])
async def test_suggestion_caller_cancellation_and_shutdown_close_the_stream(shutdown):
    slow = SlowSuggestions()
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=slow))))
    pending = asyncio.create_task(provider.suggest('london'))
    try:
        await asyncio.wait_for(slow.started.wait(), 1)
        if shutdown:
            await provider.close()
        else:
            pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        assert slow.cancelled.is_set() and slow.closed.is_set()
        assert not provider.locks['suggest'].locked()
        assert provider._suggest_task is None
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await provider.close()


async def test_only_suggestions_use_short_spacing_and_repeated_queries_are_not_cached(monkeypatch):
    now = 10.
    starts = []
    original_sleep = asyncio.sleep

    async def advance(delay):
        nonlocal now
        now += delay
        await original_sleep(0)

    def response(request):
        starts.append(now)
        return httpx.Response(200, json={})

    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(response)))
    monkeypatch.setattr(providers, 'time', SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(providers.asyncio, 'sleep', advance)
    try:
        for kind, url in [('suggest', provider.suggest_url), ('search', provider.search_url),
                          ('directions', provider.route_url)]:
            await provider.request(kind, url, {'q': 'london'})
            await provider.request(kind, url, {'q': 'london' if kind == 'suggest' else 'paris'})
        assert starts == pytest.approx([10., 10.25, 10.25, 11.3, 11.3, 12.35])
        assert all(key[0] != 'suggest' for key in provider.cache)
    finally:
        await provider.close()
