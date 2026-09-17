"""Focused new web contracts: loopback authorization, geography, provider and GPX."""
import asyncio
import json

import httpx
import pytest

from openlocation_backend import gpx
from openlocation_backend.coverage import require_coordinate, require_route
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Coordinate, Route
from openlocation_backend.providers import Directions, Providers
from openlocation_backend.web import create_app


def test_geography_accepts_worldwide_coordinates_and_cross_border_routes():
    for lat, lon in [(37.79549,-122.39371),(49.2827,-123.1207),(19.4326,-99.1332),(18.4655,-66.1057),(51.5074,-.1278),(0.,0.),(-33.8688,151.2093),(90.,180.)]:
        require_coordinate(Coordinate(latitude=lat,longitude=lon))
    require_route(Route(source='drawn',points=[{'latitude':47.6062,'longitude':-122.3321},{'latitude':61.2181,'longitude':-149.9003}]))
    with pytest.raises(ValueError):
        require_coordinate(Coordinate.model_construct(latitude=91., longitude=0.))


def test_coverage_accepts_playback_arcs_across_borders():
    route = Route(source='drawn', points=[{'latitude':48.9,'longitude':-114.}, {'latitude':48.9,'longitude':-102.}])
    require_route(route)


def test_gpx_timing_offer_matches_import_validation():
    def document(a,b,seconds):
        return f'<gpx version="1.1"><trk><trkseg><trkpt lat="0" lon="{a}"><time>2026-01-01T00:00:00Z</time></trkpt><trkpt lat="0" lon="{b}"><time>2026-01-01T00:00:{seconds:02}Z</time></trkpt></trkseg></trk></gpx>'
    assert not gpx.segments(document(0,1,1))[0]['recorded_timing_available']
    assert not gpx.segments(document(0,0,1))[0]['recorded_timing_available']
    valid=document(0,.001,20)
    assert gpx.segments(valid)[0]['recorded_timing_available']
    assert gpx.import_segment(valid,0,'recorded').timing=='recorded'


class Helper:
    def __init__(self):
        self.listeners=set()
        self.calls=[]
    async def start(self): pass
    async def close(self): pass
    async def call(self,op,params=None,session_id=None,request_id=None):
        self.calls.append((op,params,session_id))
        if op=='connect':return {'session_id':'test-session'}
        if op=='status':return {'session_id':None,'transport_state':'disconnected'}
        return {}


async def test_web_boundary_authorization_and_worldwide_device_coordinates():
    helper=Helper()
    providers=Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(500))))
    app=create_app(helper,providers)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:3000') as client:
        assert (await client.get('/api/bootstrap',headers={'host':'evil.example'})).status_code==403
        assert (await client.get('/api/bootstrap',headers={'origin':'https://evil.example'})).status_code==403
        token=(await client.get('/api/bootstrap')).json()['token']
        assert (await client.post('/api/command',json={'op':'devices.list'})).status_code==403
        client.headers.update({'origin':'http://localhost:3000','x-openlocation-token':token})
        assert (await client.post('/api/command',json={'op':'connect','params':{'device_id':'test-device'}})).status_code==400
        assert not any(op=='connect' for op,_,_ in helper.calls)
        assert (await client.post('/api/command',json={'op':'connect','params':{'device_id':'test-device'},'authorized':True})).status_code==200
        count=len(helper.calls)
        result=await client.post('/api/command',json={'op':'location.set','session_id':'test-session','params':{'coordinate':{'latitude':51.5074,'longitude':-.1278}}})
        assert result.status_code==200 and len(helper.calls)==count+1
        count=len(helper.calls)
        result=await client.post('/api/command',json={'op':'location.set','session_id':'another-session','params':{'coordinate':{'latitude':40.7711,'longitude':-73.9742}}})
        assert result.status_code==400 and len(helper.calls)==count
        result=await client.post('/api/command',json={'op':'location.set','session_id':'test-session','params':{'coordinate':{'latitude':40.7711,'longitude':-73.9742}}})
        assert result.status_code==200 and helper.calls[-1][0]=='location.set'
        assert (await client.post('/api/command',json={'op':'shell.execute'})).status_code==400
        assert (await client.post('/api/coordinate',content='{"latitude":NaN,"longitude":0}')).status_code==400
        assert (await client.post('/api/command',json={'op':'store.put','params':{'bucket':'locations','name':'Ocean','payload':{'latitude':0.,'longitude':0.},'revision':0}})).status_code==200
        assert (await client.post('/api/command',json={'op':'store.put','params':{'bucket':'locations','name':'Invalid','payload':{'latitude':91.,'longitude':0.},'revision':0}})).status_code==400
    await providers.close()


@pytest.mark.parametrize('transport', [None, 'usb', 'wifi', 'auto'])
async def test_web_preserves_explicit_transport_choice(transport):
    helper = Helper()
    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    app = create_app(helper, providers)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:3000') as client:
            token = (await client.get('/api/bootstrap')).json()['token']
            client.headers.update({'origin': 'http://localhost:3000', 'x-openlocation-token': token})
            params = {'device_id': 'test-device', 'prepare': True}
            if transport is not None:
                params['transport'] = transport
            response = await client.post('/api/command', json={'op': 'connect', 'params': params, 'authorized': True})
            assert response.status_code == 200
            sent = next(arguments for op, arguments, _ in helper.calls if op == 'connect')
            assert sent == params
    finally:
        await providers.close()


async def test_providers_cache_global_search_and_distinct_travel_modes():
    requests=[]
    def response(req):
        requests.append(req)
        if '/search' in req.url.path:
            return httpx.Response(200,json=[{'lat':'40.7711','lon':'-73.9742','display_name':'Central Park, New York','name':'Central Park'}])
        return httpx.Response(200,json={'trip':{'status':442}})
    providers=Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    try:
        first=await providers.search('Central Park New York')
        assert await providers.search('Central Park New York')==first
        assert len(requests)==1 and 'countrycodes' not in requests[0].url.params
        for mode,costing in [('walking','pedestrian'),('cycling','bicycle'),('driving','auto')]:
            providers.last['directions']=0
            with pytest.raises(BackendError,match='No road'):
                await providers.directions(Directions(mode=mode,stops=[{'name':'A','latitude':40.7711,'longitude':-73.9742},{'name':'B','latitude':40.775,'longitude':-73.975}]))
            assert requests[-1].method == 'POST'
            assert json.loads(requests[-1].content)['costing']==costing
    finally:await providers.close()


async def test_server_shutdown_finishes_waiting_event_stream():
    helper = Helper()
    providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    app = create_app(helper, providers)
    endpoint = next(route.endpoint for route in app.routes if route.path == '/api/events')
    response = await endpoint()
    stream = response.body_iterator
    assert '"event": "state"' in await anext(stream)
    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    app.state.stopping.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(waiting, timeout=1)
    assert not helper.listeners
    await providers.close()
