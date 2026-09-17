"""Offline contracts for exact-route speed limits and provider terrain metadata."""
import asyncio
import copy

import pytest

from openlocation_backend import route_metadata
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Point, Route
from openlocation_backend.providers import Directions, Providers, Stop, _assemble_walking_access, decode_shape
from openlocation_backend.route_metadata import enrich_route_metadata, trace_segments
from test_world_routes import shape


def points(count=4):
    return [Point(latitude=0., longitude=i * .001) for i in range(count)]


def response(route_points, edges):
    return {'units': 'kilometers', 'shape': shape([(p.latitude, p.longitude) for p in route_points]), 'edges': edges}


def edge(start, end, **changes):
    return {'begin_shape_index': start, 'end_shape_index': end, 'speed_limit': 40.,
            'road_class': 'residential', 'use': 'road', 'weighted_grade': 5.,
            'max_upward_grade': 4, 'max_downward_grade': 0, **changes}


def test_edge_spans_keep_limit_changes_ramps_and_directional_grades_on_their_segments():
    route_points = points(5)
    value = response(route_points, [edge(0, 2), edge(2, 3, speed_limit=90., use='ramp', road_class='motorway',
                                                               weighted_grade=-5., max_upward_grade=0, max_downward_grade=-4),
                                    edge(3, 4, speed_limit=0, speed=110., road_class='unknown-road')])
    limits, classes, grades = trace_segments(value, route_points, decode_shape, terrain=True)
    assert limits[:3] == pytest.approx([40 / 3.6, 40 / 3.6, 90 / 3.6])
    assert limits[3] is None  # Estimated edge.speed is not a posted speed limit.
    assert classes == ['residential', 'residential', 'ramp', 'unknown']
    assert grades == pytest.approx([.05, .05, -.05, .05])


@pytest.mark.parametrize('fault', ['reverse', 'parallel', 'simplified', 'overlap', 'backward_span', 'boolean_index', 'wrong_units'])
def test_other_geometry_or_ambiguous_spans_never_receive_metadata(fault):
    route_points = points()
    value = response(route_points, [edge(0, 2), edge(2, 3)])
    if fault == 'reverse':
        value['shape'] = response(list(reversed(route_points)), [])['shape']
    elif fault == 'parallel':
        value['shape'] = shape([(.000001, p.longitude) for p in route_points])
    elif fault == 'simplified':
        value['shape'] = response([route_points[0], route_points[-1]], [])['shape']
    elif fault == 'overlap':
        value['edges'][1]['begin_shape_index'] = 1
    elif fault == 'backward_span':
        value['edges'][1].update(begin_shape_index=3, end_shape_index=2)
    elif fault == 'boolean_index':
        value['edges'][0]['begin_shape_index'] = False
    else:
        value['units'] = 'miles'
    assert trace_segments(value, route_points, decode_shape, terrain=True) is None


@pytest.mark.parametrize('limit', [None, 0, 255, 'unlimited', '25', True, -1., float('nan'), float('inf')])
def test_missing_and_invalid_limits_are_unknown(limit):
    route_points = points(2)
    value = response(route_points, [edge(0, 1, speed_limit=limit, speed=35.)])
    assert trace_segments(value, route_points, decode_shape, terrain=False)[0] == [None]


@pytest.mark.parametrize('changes', [
    {'weighted_grade': None}, {'weighted_grade': True}, {'weighted_grade': float('nan')},
    {'weighted_grade': 101.}, {'max_upward_grade': 32768}, {'max_downward_grade': 32768},
    {'max_upward_grade': None}, {'max_downward_grade': None},
])
def test_absent_or_invalid_elevation_does_not_become_flat_terrain(changes):
    route_points = points(2)
    value = response(route_points, [edge(0, 1, **changes)])
    assert trace_segments(value, route_points, decode_shape, terrain=True)[2] == [None]
    flat = response(route_points, [edge(0, 1, weighted_grade=0., max_upward_grade=0, max_downward_grade=0)])
    assert trace_segments(flat, route_points, decode_shape, terrain=True)[2] == [0.]


def test_gaps_and_stationary_segments_remain_unknown():
    route_points = points(4)
    route_points.insert(1, route_points[0])
    limits, classes, grades = trace_segments(response(route_points, [edge(0, 2), edge(3, 4)]),
                                             route_points, decode_shape, terrain=True)
    assert limits[0] is None and limits[2] is None
    assert classes == ['unknown', 'residential', 'unknown', 'residential']
    assert grades == [None, .05, None, .05]


async def test_enrichment_merges_known_limits_conservatively_and_has_a_request_cap(monkeypatch):
    monkeypatch.setattr(route_metadata, 'TRACE_BATCH_POINTS', 3)
    monkeypatch.setattr(route_metadata, 'MAX_TRACE_REQUESTS', 2)

    class Provider:
        route_url = 'https://example.test/custom/route'
        calls = []

        async def request(self, kind, url, params):
            self.calls.append((kind, url, copy.deepcopy(params)))
            batch = [Point(latitude=p['lat'], longitude=p['lon']) for p in params['shape']]
            return response(batch, [edge(0, len(batch) - 1)])

    provider = Provider()
    limits, classes, grades, source = await enrich_route_metadata(provider, points(7), 'cycling',
                                                               [5., 20., None, None, None, 7.], decode_shape)
    assert len(provider.calls) == 2
    assert provider.calls[0][0:2] == ('directions', 'https://example.test/custom/trace_attributes')
    assert provider.calls[0][2]['shape_match'] == 'edge_walk'
    assert provider.calls[0][2]['costing'] == 'bicycle'
    assert provider.calls[0][2]['shape'][-1] == provider.calls[1][2]['shape'][0]
    assert limits[:4] == pytest.approx([5., 40 / 3.6, 40 / 3.6, 40 / 3.6])
    assert limits[4:] == [None, 7.]
    assert classes == ['residential'] * 4 + ['unknown'] * 2
    assert grades == [.05] * 4 + [None] * 2 and source == 'partial'


@pytest.mark.parametrize('failure', [BackendError('provider_busy', 'busy', True), TimeoutError(), ValueError('invalid')])
async def test_enrichment_failure_preserves_earlier_batches_and_existing_limits(monkeypatch, failure):
    monkeypatch.setattr(route_metadata, 'TRACE_BATCH_POINTS', 3)

    class Provider:
        route_url = 'https://example.test/route'
        calls = 0

        async def request(self, kind, url, params):
            self.calls += 1
            if self.calls == 2:
                raise failure
            return response(points(3), [edge(0, 2)])

    provider = Provider()
    limits, classes, grades, source = await enrich_route_metadata(provider, points(5), 'walking',
                                                               [None, None, 6., None], decode_shape)
    assert provider.calls == 2 and limits[2:] == [6., None]
    assert classes == ['residential', 'residential', 'unknown', 'unknown']
    assert grades == [.05, .05, None, None] and source == 'partial'


async def test_enrichment_deadline_and_cancellation_propagation(monkeypatch):
    monkeypatch.setattr(route_metadata, 'TRACE_BUDGET_SECONDS', .001)

    class WaitingProvider:
        route_url = 'https://example.test/route'

        async def request(self, kind, url, params):
            await asyncio.Event().wait()

    assert await enrich_route_metadata(WaitingProvider(), points(2), 'walking', [7.], decode_shape) == (
        [7.], ['unknown'], [None], 'unavailable')

    async def disconnected():
        return True

    with pytest.raises(BackendError) as failure:
        await enrich_route_metadata(WaitingProvider(), points(2), 'walking', [7.], decode_shape,
                                    is_disconnected=disconnected)
    assert failure.value.code == 'cancelled'

    class CancelledProvider(WaitingProvider):
        async def request(self, kind, url, params):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await enrich_route_metadata(CancelledProvider(), points(2), 'walking', [7.], decode_shape)


async def test_driving_requests_no_terrain_and_invalid_shape_keeps_known_limits():
    class Provider:
        route_url = 'https://example.test/route'

        async def request(self, kind, url, params):
            assert params['costing'] == 'auto'
            assert not any('grade' in key for key in params['filters']['attributes'])
            return response(list(reversed(points(2))), [edge(0, 1)])

    assert await enrich_route_metadata(Provider(), points(2), 'driving', [7.], decode_shape) == (
        [7.], ['unknown'], [], 'none')


async def test_directions_attach_trace_metadata_to_route_before_walking_access():
    class FixtureProvider(Providers):
        route_url = 'https://example.test/route'

        def __init__(self):
            pass

        async def request(self, kind, url, params):
            if url.endswith('/trace_attributes'):
                return response(points(3), [edge(0, 1), edge(1, 2, speed_limit=20., road_class='service_other')])
            assert url.endswith('/route')
            if params['costing'] == 'pedestrian':
                coords = [(value['lat'], value['lon']) for value in params['locations']]
                return {'trip': {'status': 0, 'summary': {'time': 8.}, 'legs': [
                    {'shape': shape([start, end]), 'maneuvers': []} for start, end in zip(coords, coords[1:])]}}
            return {'trip': {'status': 0, 'summary': {'time': 60.}, 'legs': [{
                'shape': response(points(3), [])['shape'], 'maneuvers': [],
                'shape_attributes': {'speed': [54., 54.], 'speed_limit': [60., 0.]},
            }]}}

    result = await FixtureProvider().directions(Directions(mode='driving', stops=[
        {'name': 'Start', 'latitude': .0001, 'longitude': 0.},
        {'name': 'Finish', 'latitude': .0001, 'longitude': .002},
    ]))
    route = Route.model_validate(result['route'])
    assert route.segment_modes == ['walking', 'driving', 'driving', 'walking']
    assert route.segment_speed_limits_mps[0] is None and route.segment_speed_limits_mps[-1] is None
    assert route.segment_speed_limits_mps[1:3] == pytest.approx([40 / 3.6, 20 / 3.6])
    assert route.segment_speeds_mps[1:3] == pytest.approx([40 / 3.6, 20 / 3.6])
    assert route.segment_road_classes == ['unknown', 'residential', 'service_other', 'unknown']
    assert route.segment_grades == [] and route.terrain_source == 'none'


def test_walking_access_remaps_available_terrain_and_marks_connectors_unknown():
    road = Route(source='road', mode='driving', points=points(3), segment_speeds_mps=[10., 10.],
                 segment_speed_limits_mps=[12., 12.], segment_road_classes=['residential', 'service_other'],
                 segment_grades=[.05, -.05], terrain_source='provider',
                 waypoints=[{'name': 'Start', 'point_index': 0}, {'name': 'Finish', 'point_index': 2}])
    stops = [Stop(name=waypoint.name, latitude=.0001, longitude=road.points[waypoint.point_index].longitude)
             for waypoint in road.waypoints]
    paths = {0: [([Point(**stops[0].model_dump(include={'latitude', 'longitude'})), road.points[0]], [0])],
             1: [([road.points[-1], Point(**stops[-1].model_dump(include={'latitude', 'longitude'}))], [0])]}
    route = Route.model_validate(_assemble_walking_access(dict(road), stops, paths))
    assert route.segment_grades == [None, .05, -.05, None]
    assert route.segment_road_classes == ['unknown', 'residential', 'service_other', 'unknown']
    assert route.terrain_source == 'partial'
