"""Focused offline behavior contracts for automatic driving; no phone/network."""
import copy

import pytest

from openlocation_backend.errors import BackendError
from openlocation_backend.models import Route
from openlocation_backend.providers import Directions, Providers, decode_shape, _native_leg
from openlocation_backend.road_metadata import control_at_location, driving_controls
from openlocation_backend.route import CRUISE_MARGIN_MPS, Timeline, reverse_track
from test_world_routes import shape


def drive(**changes):
    return Route.model_validate({
        'source': 'road', 'mode': 'driving', 'speed_mps': 13.4,
        'points': [{'latitude': 0., 'longitude': lon} for lon in (0., .04)],
        'segment_speeds_mps': [7.], 'segment_speed_limits_mps': [30 * .44704],
        'speed_variation': .06, 'realistic_motion': True,
        **changes,
    })


def test_sparse_road_varies_near_two_below_limit_without_manual_speed():
    timeline = Timeline(drive(speed_mps=1.))
    assert timeline.duration == pytest.approx(Timeline(drive(speed_mps=99.)).duration)
    cruising = [leg for leg in timeline.legs if leg.start_speed_mps == leg.end_speed_mps]
    speeds = [leg.start_speed_mps for leg in cruising]
    assert max(speeds) - min(speeds) > .8
    average = sum(leg.distance for leg in cruising) / sum(leg.duration for leg in cruising)
    assert average == pytest.approx(30 * .44704 - CRUISE_MARGIN_MPS, abs=.15)
    assert all(max(leg.start_speed_mps, leg.end_speed_mps) <= 30 * .44704 for leg in timeline.legs)
    assert timeline.sample(0)['speed_mps'] == timeline.sample(timeline.duration)['speed_mps'] == 0


def test_brakes_before_lower_limit_and_preserves_acceleration_bounds():
    timeline = Timeline(drive(
        points=[{'latitude': 0., 'longitude': lon} for lon in (0., .01, .02)],
        segment_speed_limits_mps=[60 * .44704, 20 * .44704], segment_speeds_mps=[7., 7.]))
    for leg in timeline.legs:
        acceleration = (leg.end_speed_mps - leg.start_speed_mps) / leg.duration
        assert -2.4 - 1e-8 <= acceleration <= 1.6 + 1e-8
        if leg.start.longitude >= .01 - 1e-10:
            assert max(leg.start_speed_mps, leg.end_speed_mps) <= 20 * .44704


def test_stops_at_controls_but_passes_unknown_crossings_and_entrances():
    route = drive(
        points=[{'latitude': 0., 'longitude': n * .001} for n in range(6)],
        segment_speeds_mps=[], segment_speed_limits_mps=[],
        intersections=[{'point_index': n, 'control': control} for n, control in enumerate(
            ['unknown', 'stop_sign', 'uncontrolled', 'traffic_signal'], 1)],
        intersection_pause_seconds=3.)
    timeline = Timeline(route)
    dwells = [leg for leg in timeline.legs if leg.dwell]
    assert [leg.start.longitude for leg in dwells] == pytest.approx([.002, .004])
    assert [leg.duration for leg in dwells] == [3., 3.]
    zero_wait = Timeline(route.model_copy(update={'intersection_pause_seconds': 0.}))
    assert not any(leg.dwell for leg in zero_wait.legs)
    for longitude in (.002, .004):
        approach = next(leg for leg in zero_wait.legs if abs(leg.end.longitude - longitude) < 1e-10)
        assert approach.end_speed_mps == 0.


def test_unknown_limit_uses_estimate_and_speed_command_selects_manual():
    timeline = Timeline(drive(segment_speed_limits_mps=[None], speed_variation=0.))
    assert max(leg.end_speed_mps for leg in timeline.legs) == 7.
    timeline.elapsed = 30.
    before = timeline.sample()
    timeline.set_speed(10.)
    assert timeline.route.driving_speed_mode == 'manual'
    assert timeline.sample()['distance_meters'] == pytest.approx(before['distance_meters'])
    assert timeline.sample()['coordinate'] == pytest.approx(before['coordinate'], abs=1e-9)


def test_reversal_does_not_reuse_directional_limits_or_stop_signs():
    route = drive(points=[{'latitude': 0., 'longitude': lon} for lon in (0., .01, .02)],
                  segment_speeds_mps=[7., 9.], segment_speed_limits_mps=[10., 12.],
                  intersections=[{'point_index': 1, 'control': 'stop_sign'}], traffic_controls_source='provider')
    reversed_route = reverse_track(route)
    assert reversed_route.segment_speed_limits_mps == []
    assert reversed_route.intersections[0].control == 'unknown'
    assert reversed_route.traffic_controls_source == 'none'


def test_recorded_driving_timestamps_remain_authoritative():
    timeline = Timeline(drive(
        points=[{'latitude': 0., 'longitude': lon, 'time': n * 120.} for n, lon in enumerate((0., .001))],
        timing='recorded', speed_variation=0., realistic_motion=False,
        segment_speeds_mps=[], segment_speed_limits_mps=[]))
    assert timeline.duration == 120.
    assert timeline.sample(60.)['distance_meters'] == pytest.approx(timeline.distance / 2)


def located(stop=False):
    return {
        'input_lat': 0., 'input_lon': .001,
        'nodes': [{'node_id': {'value': 10}, 'traffic_signal': False}],
        'edges': [{
            'edge': {'forward': True, 'stop_sign': stop, 'traffic_signal': False,
                     'access': {'car': True}, 'end_node': {'value': 10}},
            'edge_info': {'shape': shape([(0., 0.), (0., .001)])},
        }],
    }


def control_points():
    return drive(points=[{'latitude': 0., 'longitude': lon} for lon in (0., .001, .002)],
                 segment_speeds_mps=[], segment_speed_limits_mps=[]).points


def test_stop_control_matches_approach_and_rejects_side_road_and_reverse_signs():
    points = control_points()
    value = located()
    side = copy.deepcopy(located(True)['edges'][0])
    side['edge_info']['shape'] = shape([(.001, .001), (0., .001)])
    value['edges'].append(side)
    assert control_at_location(value, points, 1, decode_shape) == 'uncontrolled'
    value['edges'][0]['edge']['stop_sign'] = True
    assert control_at_location(value, points, 1, decode_shape) == 'stop_sign'
    value['edges'][0]['edge']['forward'] = False
    assert control_at_location(value, points, 1, decode_shape) == 'unknown'
    value['edges'][0]['edge_info']['shape'] = shape([(0., .001), (0., 0.)])
    assert control_at_location(value, points, 1, decode_shape) == 'stop_sign'


def test_signal_uses_matching_node_and_malformed_metadata_stays_unknown():
    points, value = control_points(), located()
    value['nodes'][0]['traffic_signal'] = True
    assert control_at_location(value, points, 1, decode_shape) == 'traffic_signal'
    value['nodes'][0]['node_id']['value'] = 11
    assert control_at_location(value, points, 1, decode_shape) == 'unknown'
    value['input_lat'] = True
    assert control_at_location(value, points, 1, decode_shape) == 'unknown'


async def test_control_lookup_coverage_failure_and_cancellation():
    class Provider:
        route_url = 'https://example.test/route'
        failure = None
        values = [located(True)]
        calls = []

        async def request(self, kind, url, params):
            self.calls.append((kind, url, params))
            if self.failure:
                raise self.failure
            return self.values

    provider, points = Provider(), control_points()
    assert await driving_controls(provider, points, {1: None}, decode_shape) == ({1: 'stop_sign'}, 'provider')
    assert provider.calls[0][1] == 'https://example.test/locate'
    provider.values = [{}]
    assert await driving_controls(provider, points, {1: None}, decode_shape) == ({}, 'unavailable')
    provider.failure = BackendError('provider_busy', 'busy', True)
    assert await driving_controls(provider, points, {1: None}, decode_shape) == ({}, 'unavailable')

    async def disconnected():
        return True

    with pytest.raises(BackendError, match='canceled'):
        await driving_controls(provider, points, {1: None}, decode_shape, is_disconnected=disconnected)


def test_native_speed_units_keep_limit_and_estimate_separate():
    leg = {'shape': shape([(0., 0.), (0., .001)]), 'maneuvers': [],
           'shape_attributes': {'speed': [36.], 'speed_limit': [50.]}}
    _, _, _, (speeds, limits, measured) = _native_leg(leg, 'driving')
    assert speeds == [10.] and limits == pytest.approx([50 / 3.6]) and measured == [True]


async def test_directions_enrich_controls_and_feed_the_shared_timeline():
    class FixtureProvider(Providers):
        route_url = 'https://example.test/route'

        def __init__(self):
            pass

        async def request(self, kind, url, params):
            if url.endswith('/locate'):
                return [located(True)]
            return {'trip': {'status': 0, 'summary': {'time': 30.}, 'legs': [{
                'shape': shape([(0., 0.), (0., .001), (0., .002)]),
                'maneuvers': [{'begin_shape_index': 0, 'end_shape_index': 1, 'time': 15., 'type': 1},
                              {'begin_shape_index': 1, 'end_shape_index': 2, 'time': 15., 'type': 10}],
                'shape_attributes': {'speed_limit': [50., 30.]},
            }]}}

    result = await FixtureProvider().directions(Directions(mode='driving', stops=[
        {'name': 'Start', 'latitude': 0., 'longitude': 0.},
        {'name': 'Finish', 'latitude': 0., 'longitude': .002},
    ]))
    route = Route.model_validate(result['route'])
    assert route.driving_speed_mode == 'adaptive' and route.stop_policy == 'mapped'
    assert route.intersections[0].control == 'stop_sign'
    assert route.traffic_controls_source == 'partial'  # Native turns lack full graph coverage.
    assert route.segment_speed_limits_mps == pytest.approx([50 / 3.6, 30 / 3.6])
    assert sum(leg.dwell for leg in Timeline(route).legs) == 1
