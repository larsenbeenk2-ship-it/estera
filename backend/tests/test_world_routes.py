"""Offline contracts for worldwide places and physically paced road playback."""
import json

import httpx
import pytest
from pydantic import ValidationError

from openlocation_backend.errors import BackendError
from openlocation_backend.models import Coordinate, Route, distance
from openlocation_backend.providers import Directions, Providers, _native_leg, _osrm_leg
from openlocation_backend.route import Timeline, reverse_track
from openlocation_backend.web import create_app
from test_web import Helper


def shape(points):
    encoded, previous = '', (0, 0)
    for point in points:
        current = tuple(round(value * 1e6) for value in point)
        for value, before in zip(current, previous):
            delta = value - before
            number = ~(delta << 1) if delta < 0 else delta << 1
            while number >= 32:
                encoded += chr((32 | (number & 31)) + 63)
                number >>= 5
            encoded += chr(number + 63)
        previous = current
    return encoded


POINTS = [(51.5074, -.1278), (51.5074, -.1268), (51.5074, -.1258)]


def road_leg():
    return {'steps': [
        {'geometry': shape(POINTS), 'duration': 30., 'name': 'London road', 'maneuver': {'type': 'depart'},
         'intersections': [{'location': [POINTS[1][1], POINTS[1][0]], 'geometry_index': 1,
                            'bearings': [0, 90, 270], 'in': 2, 'out': 1}]},
        {'geometry': shape([POINTS[-1], POINTS[-1]]), 'maneuver': {'type': 'arrive'}}],
        'annotation': {'speed': [12., 22.], 'duration': [7., 3.],
                       'maxspeed': [{'speed': 20., 'unit': 'mph'}, {'unknown': True}]}}


async def test_global_postal_search_reverse_coordinates_and_cache():
    calls = []
    def response(request):
        calls.append(request)
        place = {'lat': '35.6812', 'lon': '139.7671', 'name': 'Tokyo Station', 'display_name': 'Tokyo Station, 100-0005, Japan'}
        return httpx.Response(200, json=[place] if request.url.path.endswith('/search') else place)
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(response)))
    try:
        assert (await provider.search('100-0005 Japan'))['places'][0]['longitude'] == 139.7671
        assert 'countrycodes' not in calls[-1].url.params
        assert calls[-1].url.params['q'] == '100-0005 Japan'
        point = Coordinate(latitude=35.6813, longitude=139.7672)
        provider.last['search'] = 0
        result = await provider.reverse(point)
        assert result == {'name': 'Tokyo Station', 'label': 'Tokyo Station, 100-0005, Japan', **point.model_dump()}
        assert await provider.reverse(point) == result
        assert len(calls) == 2
    finally:
        await provider.close()


async def test_worldwide_suggestions_use_real_photon_places_without_country_filter():
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={
            'type': 'FeatureCollection',
            'features': [{
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [-119.9509, 39.2491]},
                'properties': {
                    'name': 'Incline Village', 'city': 'Incline Village',
                    'state': 'Nevada', 'country': 'United States', 'type': 'city',
                    'extent': [-119.96, 39.26, -119.94, 39.24],
                },
            }],
        })
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(response)))
    try:
        result = await provider.suggest('incline')
        assert result == {'places': [{
            'name': 'Incline Village', 'label': 'Incline Village, Nevada, United States',
            'latitude': 39.2491, 'longitude': -119.9509, 'zoom': 11,
            'bounds': [-119.96, 39.24, -119.94, 39.26],
        }], 'attribution': '© OpenStreetMap contributors; Photon'}
        assert calls[-1].url.params['q'] == 'incline' and calls[-1].url.params['limit'] == '5'
        assert 'countrycodes' not in calls[-1].url.params
    finally:
        await provider.close()


async def test_suggestion_api_is_local_authenticated_and_validates_query_length():
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'type': 'FeatureCollection', 'features': [],
    }))))
    app = create_app(Helper(), provider)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:3000') as client:
            assert (await client.post('/api/suggest', json={'query': 'incline'})).status_code == 403
            token = (await client.get('/api/bootstrap')).json()['token']
            client.headers.update({'origin': 'http://localhost:3000', 'x-openlocation-token': token})
            assert (await client.post('/api/suggest', json={'query': 'in'})).status_code == 400
            assert (await client.post('/api/suggest', json={'query': 'incline'})).json()['places'] == []
    finally:
        await provider.close()


async def test_suggestions_discard_malformed_features_without_caching_them():
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'type': 'FeatureCollection',
        'features': [
            {'geometry': {'type': 'Point', 'coordinates': [False, 39.]}, 'properties': {'name': 'Not a place'}},
            {'geometry': {'type': 'Point', 'coordinates': [151.2093, -33.8688]},
             'properties': {'name': 'Sydney', 'country': 'Australia'}},
        ],
    }))))
    try:
        result = await provider.suggest('sydney')
        assert result['places'] == [{
            'name': 'Sydney', 'label': 'Australia', 'latitude': -33.8688, 'longitude': 151.2093,
        }]
        assert not provider.cache
    finally:
        await provider.close()


async def test_reverse_api_is_local_authenticated_and_preserves_clicked_point():
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'display_name': 'Sydney, Australia', 'lat': '-33.86', 'lon': '151.2'}))))
    app = create_app(Helper(), provider)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://localhost:3000') as client:
            point = {'latitude': -33.8688, 'longitude': 151.2093}
            assert (await client.post('/api/reverse', json=point)).status_code == 403
            token = (await client.get('/api/bootstrap')).json()['token']
            client.headers.update({'origin': 'http://localhost:3000', 'x-openlocation-token': token})
            result = await client.post('/api/reverse', json=point)
            assert result.status_code == 200 and result.json()['latitude'] == point['latitude']
            assert (await client.post('/api/reverse', json={'latitude': 91., 'longitude': 0.})).status_code == 400
    finally:
        await provider.close()


@pytest.mark.parametrize('response,code', [(httpx.Response(429), 'provider_busy'), (httpx.Response(200, json={'error': 'Unable to geocode'}), 'address_not_found')])
async def test_reverse_provider_failures_remain_actionable(response, code):
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)))
    try:
        with pytest.raises(BackendError) as failure:
            await provider.reverse(Coordinate(latitude=0., longitude=0.))
        assert failure.value.code == code
    finally:
        await provider.close()


async def test_road_directions_use_worldwide_geometry_provider_paces_and_time_cost():
    calls = []
    def response(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'code': 'Ok', 'routes': [{'duration': 30., 'legs': [road_leg()]}]})
    provider = Providers(httpx.AsyncClient(transport=httpx.MockTransport(response)))
    try:
        result = await provider.directions(Directions(mode='driving', stops=[
            {'name': 'A', 'latitude': POINTS[0][0], 'longitude': POINTS[0][1]},
            {'name': 'B', 'latitude': POINTS[-1][0], 'longitude': POINTS[-1][1]}]))
        route = Route.model_validate(result['route'])
        assert route.segment_speeds_mps == pytest.approx([20 * .44704, 22.])
        assert route.speed_source == 'provider' and route.realistic_motion
        assert route.intersections[0].point_index == 1
        assert calls[0]['costing_options']['auto'] == {'shortest': False, 'use_distance': 0}
        assert 'shape_attributes.speed_limit' in calls[0]['filters']['attributes']
        assert result['road_seconds'] == 30.
    finally:
        await provider.close()


def test_step_timing_native_fallback_mode_caps_and_malformed_annotations():
    leg = road_leg()
    del leg['annotation']
    points, _, _, (speeds, limits, measured) = _osrm_leg(leg, 'driving')
    expected = sum(distance(a, b) for a, b in zip(points, points[1:])) / 30
    assert speeds == pytest.approx([expected, expected]) and all(measured) and limits == [None, None]
    leg['steps'][0].pop('duration')
    assert _osrm_leg(leg, 'cycling')[-1][0] == [4.2, 4.2]
    assert not any(_osrm_leg(leg, 'driving')[-1][2])
    native = {'shape': shape(POINTS), 'maneuvers': [{'begin_shape_index': 0, 'end_shape_index': 2, 'time': 1., 'type': 1}]}
    assert _native_leg(native, 'walking')[-1][0] == [2.2, 2.2]
    leg['annotation'] = {'speed': [2.]}
    with pytest.raises(ValueError, match='do not match'):
        _osrm_leg(leg, 'driving')


def paced_route(**changes):
    return Route.model_validate({'source': 'road', 'mode': 'driving', 'speed_mps': 13.4,
        'realistic_motion': True, 'speed_source': 'provider',
        'points': [{'latitude': 0., 'longitude': lon} for lon in (0., .001, .002)],
        'segment_speeds_mps': [15., 8.], 'segment_speed_limits_mps': [16., 10.],
        'intersections': [{'point_index': 1, 'control': 'stop_sign'}], 'intersection_pause_seconds': 3., **changes})


def test_acceleration_braking_limits_stop_duration_and_position_preserving_speed_change():
    timeline = Timeline(paced_route())
    assert timeline.sample(0)['speed_mps'] == 0
    assert timeline.sample(1)['speed_mps'] == pytest.approx(1.6)
    assert timeline.sample(1)['distance_meters'] == pytest.approx(.8)
    assert timeline.sample(2)['distance_meters'] == pytest.approx(3.2)
    assert timeline.distance == pytest.approx(distance(timeline.route.points[0], timeline.route.points[-1]))
    for leg in timeline.legs:
        if not leg.dwell:
            assert -2.4 - 1e-9 <= (leg.end_speed_mps - leg.start_speed_mps) / leg.duration <= 1.6 + 1e-9
            assert leg.start_speed_mps <= 16. and leg.end_speed_mps <= 16.
            assert leg.seconds_at_distance(leg.distance * .3) >= 0
    stop_index = next(i for i, leg in enumerate(timeline.legs) if leg.dwell)
    stop_start = timeline.ends[stop_index - 1]
    assert timeline.sample(stop_start + 1)['dwelling'] and timeline.sample(stop_start + 1)['speed_mps'] == 0
    assert timeline.sample(stop_start + 2.9)['dwelling']
    assert not timeline.sample(stop_start + 3)['dwelling']
    timeline.elapsed = 4.
    before = timeline.sample()
    timeline.set_speed(7.)
    assert timeline.sample()['coordinate'] == pytest.approx(before['coordinate'], abs=1e-10)
    assert timeline.sample()['distance_meters'] == pytest.approx(before['distance_meters'])
    stop_index = next(i for i, leg in enumerate(timeline.legs) if leg.dwell)
    timeline.elapsed = timeline.ends[stop_index - 1] + 1.25
    timeline.set_speed(20.)
    new_stop = next(i for i, leg in enumerate(timeline.legs) if leg.dwell)
    assert timeline.elapsed - timeline.ends[new_stop - 1] == pytest.approx(1.25)
    assert timeline.sample(timeline.duration)['coordinate'] == timeline.route.points[-1].model_dump(exclude={'time'})
    assert timeline.sample(timeline.duration)['speed_mps'] == 0


def test_pace_contract_legacy_and_recorded_timing_and_reversal():
    old = Route(source='drawn', points=[{'latitude': 0., 'longitude': 0.}, {'latitude': 0., 'longitude': .001}], speed_mps=10.)
    assert Timeline(old).sample(3)['distance_meters'] == pytest.approx(30.)
    assert not old.realistic_motion and not old.segment_speeds_mps
    reversed_route = reverse_track(paced_route())
    assert reversed_route.segment_speeds_mps == [8., 15.]
    assert reversed_route.segment_speed_limits_mps == []
    with pytest.raises(ValidationError, match='match every'):
        paced_route(segment_speeds_mps=[10.])
    with pytest.raises(ValidationError, match='recorded timing'):
        paced_route(timing='recorded')
