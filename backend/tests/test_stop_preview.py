"""Stop metadata survives the real preview API and local content store."""
import httpx
import pytest

from openlocation_backend.models import Route
from openlocation_backend.providers import Providers
from openlocation_backend.route import Timeline
from openlocation_backend.storage import Store
from openlocation_backend.web import create_app
from test_stop_phases import parked_itinerary


class NoPhoneHelper:
    listeners = set()

    async def call(self, op, *args):
        assert op in {'status', 'capabilities'}, 'Preview must not call a phone operation'
        return {}


async def test_saved_stop_route_reopens_with_same_preview_phases_and_vehicle(tmp_path):
    original = parked_itinerary()
    store = Store(tmp_path / 'content')
    saved = store.execute('put', 'routes', name=original.name,
                          payload=original.model_dump(), revision=0)
    reopened = Store(tmp_path / 'content').execute('get', 'routes', item_id=saved['id'])
    route = Route.model_validate(reopened['item']['payload'])
    assert route == original
    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))))
    try:
        app = create_app(helper=NoPhoneHelper(), providers=providers)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:3000') as client:
            token = (await client.get('/api/bootstrap')).json()['token']
            response = await client.post('/api/preview', json=route.model_dump(), headers={
                'Origin': 'http://localhost:3000', 'X-OpenLocation-Token': token})
        assert response.status_code == 200
        preview = response.json()
        timeline = Timeline(route)
        assert preview['duration_seconds'] == pytest.approx(timeline.duration)
        for index, leg in enumerate(preview['legs']):
            sample = timeline.sample(leg['end_seconds'] - leg['duration'] / 2)
            assert (leg['phase'], leg['stop_index'], leg['parked_coordinate']) == (
                sample['phase'], sample['stop_index'], sample['parked_coordinate'])
        pause = next(leg for leg in preview['legs'] if leg['phase'] == 'paused_at_stop')
        assert pause['duration'] == 3601.
        assert pause['stop_index'] == 1
        assert pause['start'] == route.waypoints[1].requested_coordinate.model_dump()
        assert preview['legs'][-1]['parked_coordinate'] == route.waypoints[-1].access.roadside_coordinate.model_dump()
    finally:
        await providers.close()
