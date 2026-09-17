"""Offline contracts for mapped pedestrian access, ordered visits, and arrival dwell."""
import asyncio

import pytest
from pydantic import ValidationError

from openlocation_backend import providers
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Coordinate, Point, Route, distance
from openlocation_backend.providers import Directions, Providers, Stop, _walking_access, _decode_leg
from openlocation_backend.route import Timeline, reverse_track
from test_world_routes import shape


def itinerary():
    road = Route.model_validate({
        'source': 'road', 'mode': 'driving', 'realistic_motion': True,
        'points': [{'latitude': 0., 'longitude': lon} for lon in (0., .005, .01)],
        'segment_speeds_mps': [12., 12.], 'segment_speed_limits_mps': [15., 15.],
        'waypoints': [{'name': name, 'point_index': i, 'dwell_seconds': 3661. if i == 1 else 0.}
                      for i, name in enumerate(('Start', 'Shop', 'Finish'))],
        'intersections': [{'point_index': 1, 'control': 'stop_sign'}],
    })
    stops = [Stop(name=w.name, latitude=.0002, longitude=road.points[w.point_index].longitude,
                  dwell_seconds=w.dwell_seconds) for w in road.waypoints]
    return road, stops


class MappedProvider(Providers):
    route_url = 'https://example.test/route'

    def __init__(self, road_points=None, gap=0., missing=False):
        self.calls, self.road_points, self.gap, self.missing = [], road_points, gap, missing

    async def request(self, kind, url, params):
        if url.endswith('/trace_attributes'):
            return {}
        self.calls.append(params)
        if params['costing'] == 'pedestrian' and self.missing:
            raise BackendError('no_route', 'No path available')
        locations = [(value['lat'], value['lon']) for value in params['locations']]
        if params['costing'] == 'auto':
            locations = self.road_points or [(0., lon) for _, lon in locations]
        legs = []
        for index, (start, end) in enumerate(zip(locations, locations[1:])):
            if params['costing'] == 'pedestrian':
                # Both directions are provider paths; the return takes a different bend.
                offset = .00003 if index == 0 else -.00004
                decoded = [(start[0] + self.gap, start[1]),
                           ((start[0] + end[0]) / 2, start[1] + offset),
                           (end[0] + self.gap, end[1])]
            else:
                decoded = [start, end]
            legs.append({'shape': shape(decoded), 'maneuvers': [],
                         'summary': {'length': 0. if start == end else .5, 'time': 0. if start == end else 30.}})
        return {'trip': {'status': 0, 'summary': {'time': 60.}, 'legs': legs}}


async def access_route(road=None, stops=None, provider=None):
    if road is None:
        road, stops = itinerary()
    return Route.model_validate(await _walking_access(provider or MappedProvider(), dict(road), stops))


async def test_start_in_car_then_walk_to_pin_wait_and_return_without_losing_road_metadata():
    road, stops = itinerary()
    provider = MappedProvider()
    route = await access_route(road, stops, provider)
    assert route.walking_access_version == 1
    assert len(provider.calls) == 2
    assert [len(call['locations']) for call in provider.calls] == [3, 2]
    assert all(call['costing'] == 'pedestrian' for call in provider.calls)
    assert all(call['costing_options']['pedestrian']['walking_speed'] == pytest.approx(5.04) for call in provider.calls)
    assert [limit for limit in route.segment_speed_limits_mps if limit is not None] == [15., 15.]
    assert route.intersections[0].point_index == route.waypoints[1].access.arrival_index
    assert route.points[0] == road.points[0]
    assert route.segment_modes[0] == 'driving'
    assert route.waypoints[0].point_index == 0
    assert route.waypoints[0].access is None
    assert route.waypoints[0].requested_coordinate == Coordinate(
        latitude=stops[0].latitude, longitude=stops[0].longitude)
    for waypoint, stop in zip(route.waypoints[1:], stops[1:]):
        assert distance(route.points[waypoint.point_index], stop) < .001
        assert waypoint.access.estimated_segments == []
    access = route.waypoints[1].access
    assert route.points[access.walking_to.start_index + 1] != route.points[access.walking_back.end_index - 1]
    timeline = Timeline(route)
    assert timeline.sample(0)['travel_mode'] == 'driving'
    transfers = [leg for leg in timeline.legs if leg.transition]
    assert [leg.transition for leg in transfers] == ['exit_car', 'enter_car', 'exit_car']
    assert all(leg.duration == 3. for leg in transfers)
    for i, leg in enumerate(timeline.legs):
        if leg.travel_mode == 'walking' and not leg.dwell:
            assert leg.distance / leg.duration == pytest.approx(1.4)
        if leg.transition == 'exit_car':
            assert timeline.legs[i - 1].end_speed_mps == 0.
    wait = next(leg for leg in timeline.legs if leg.duration == 3661.)
    assert wait.dwell and wait.travel_mode == 'walking'
    assert distance(wait.start, stops[1]) < .001
    finish = timeline.sample(timeline.duration)
    assert finish['complete'] and finish['travel_mode'] == 'walking'
    assert finish['speed_mps'] == 0.
    assert finish['coordinate'] == pytest.approx({'latitude': stops[-1].latitude, 'longitude': stops[-1].longitude}, abs=1e-9)
    assert reverse_track(route).segment_modes == list(reversed(route.segment_modes))


async def test_speed_change_preserves_walking_position_and_stop_countdown():
    route = await access_route()
    timeline = Timeline(route)
    for predicate in (lambda leg: leg.travel_mode == 'walking' and not leg.dwell,
                      lambda leg: leg.duration == 3661., lambda leg: leg.transition == 'exit_car'):
        index = next(i for i, leg in enumerate(timeline.legs) if predicate(leg))
        elapsed = timeline.legs[index].duration / 2
        timeline.elapsed = (timeline.ends[index - 1] if index else 0.) + elapsed
        before = timeline.sample()
        timeline.set_speed(22.)
        after = timeline.sample()
        assert after['coordinate'] == pytest.approx(before['coordinate'], abs=1e-9)
        assert after['travel_mode'] == before['travel_mode']
        assert after['transition'] == before['transition']
        assert after['speed_mps'] == before['speed_mps']
        assert after['phase'] == before['phase']
        assert after['parked_coordinate'] == before['parked_coordinate']
        current = next(i for i, end in enumerate(timeline.ends) if end > timeline.elapsed)
        assert timeline.elapsed - (timeline.ends[current - 1] if current else 0.) == pytest.approx(elapsed)


async def test_walking_disabled_has_no_requests_or_access_and_duration_bounds():
    road, stops = itinerary()
    provider = MappedProvider()
    route = await access_route(road, [stop.model_copy(update={'walk_to_stop': False}) for stop in stops], provider)
    assert route.points == road.points
    assert set(route.segment_modes) == {'driving'}
    assert not provider.calls and all(waypoint.access is None for waypoint in route.waypoints)
    with pytest.raises(ValidationError):
        Route.model_validate({**road.model_dump(), 'segment_modes': ['walking']})
    with pytest.raises(ValidationError):
        Stop(name='Invalid wait', latitude=0., longitude=0., dwell_seconds=86401.)
    assert Stop(name='All day', latitude=0., longitude=0., dwell_seconds=86400.).dwell_seconds == 86400.
    assert Stop(name='No wait', latitude=0., longitude=0., dwell_seconds=0.).dwell_seconds == 0.


async def test_long_mapped_walk_allowed_and_short_estimated_endpoints_labeled():
    road, stops = itinerary()
    stops[-1] = stops[-1].model_copy(update={'latitude': .01})
    route = await access_route(road, stops, MappedProvider(gap=.00005))
    access = route.waypoints[-1].access
    assert distance(access.roadside_coordinate, stops[-1]) > 250
    assert access.estimated_segments
    assert all(distance(route.points[i], route.points[i + 1]) <= 30 for w in route.waypoints if w.access for i in w.access.estimated_segments)
    assert distance(route.points[route.waypoints[-1].point_index], stops[-1]) < .001


@pytest.mark.parametrize('provider', [MappedProvider(gap=.0004), MappedProvider(missing=True)])
async def test_missing_paths_and_connectors_over_30m_reach_pin_from_road_and_preserve_settings(provider):
    road, stops = itinerary()
    stops[-1] = stops[-1].model_copy(update={'latitude': .001})
    before = [stop.model_dump() for stop in stops]
    route = await access_route(road, stops, provider)
    assert route.points[0] == road.points[0]
    assert route.waypoints[0].access is None
    assert route.segment_modes[0] == 'driving'
    for original, waypoint, stop in zip(road.waypoints[1:], route.waypoints[1:], stops[1:]):
        access = waypoint.access
        assert distance(access.roadside_coordinate, road.points[original.point_index]) < .001
        assert distance(route.points[waypoint.point_index], stop) < .001
        assert access.estimated_segments
        assert all(route.segment_modes[i] == 'walking' for i in access.estimated_segments)
    assert any(distance(route.points[i], route.points[i + 1]) > 30
               for waypoint in route.waypoints if waypoint.access for i in waypoint.access.estimated_segments)
    timeline = Timeline(route)
    assert [leg.transition for leg in timeline.legs if leg.transition] == [
        'exit_car', 'enter_car', 'exit_car']
    for i, leg in enumerate(timeline.legs):
        if leg.transition == 'exit_car':
            assert timeline.legs[i - 1].end_speed_mps == 0.
            assert leg.duration == 3.
    wait = next(leg for leg in timeline.legs if leg.duration == 3661.)
    assert wait.dwell and wait.travel_mode == 'walking'
    assert distance(wait.start, stops[1]) < .001
    finish = timeline.sample(timeline.duration)
    assert finish['complete'] and finish['travel_mode'] == 'walking'
    assert finish['coordinate'] == pytest.approx({
        'latitude': stops[-1].latitude, 'longitude': stops[-1].longitude}, abs=1e-9)
    assert Route.model_validate_json(route.model_dump_json()) == route
    assert reverse_track(reverse_track(route)).points == route.points
    assert [stop.model_dump() for stop in stops] == before


async def test_walking_budget_and_cancellation_stop_additional_requests(monkeypatch):
    class SlowProvider(MappedProvider):
        async def request(self, *args):
            await asyncio.sleep(1)
    monkeypatch.setattr(providers, 'WALKING_BUDGET_SECONDS', .001)
    with pytest.raises(BackendError, match='Shop:.*45-second'):
        await access_route(provider=SlowProvider())
    async def disconnected():
        return True
    provider = MappedProvider()
    road, stops = itinerary()
    with pytest.raises(BackendError) as failure:
        await _walking_access(provider, dict(road), stops, is_disconnected=disconnected)
    assert failure.value.code == 'cancelled' and not provider.calls
    class CancelProvider(MappedProvider):
        async def request(self, *args):
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await access_route(provider=CancelProvider())


async def test_shared_roadside_visits_can_form_valid_route_before_final_validation():
    provider = MappedProvider(road_points=[(0., 0.)] * 3)
    stops = [Stop(name=name, latitude=lat, longitude=0., dwell_seconds=wait)
             for name, lat, wait in [('Start', .0001, 0.), ('Shop', .0002, 11.), ('Finish', .0003, 0.)]]
    route = Route.model_validate((await provider.directions(Directions(mode='driving', stops=stops)))['route'])
    assert len({w.point_index for w in route.waypoints}) == 3
    assert route.points[0] == Point(latitude=0., longitude=0.)
    assert route.waypoints[0].access is None
    assert all(distance(w.access.roadside_coordinate, Coordinate(latitude=0., longitude=0.)) < .001 for w in route.waypoints[1:])
    assert all(distance(route.points[w.point_index], stop) < .001 for w, stop in zip(route.waypoints[1:], stops[1:]))
    timeline = Timeline(route)
    assert not [leg.transition for leg in timeline.legs if leg.transition]
    assert next(leg for leg in timeline.legs if leg.duration == 11.).dwell


@pytest.mark.parametrize('osrm', [False, True])
def test_only_provider_confirmed_stationary_legs_are_normalized(osrm):
    point = (0., 0.)
    leg = ({'distance': 0., 'duration': 0., 'steps': [{'geometry': shape([point]), 'maneuver': {'type': 'arrive'}}]}
           if osrm else {'summary': {'length': 0., 'time': 0.}, 'shape': shape([point])})
    assert len(_decode_leg(leg, osrm, 'driving')[0]) == 2
    if osrm:
        leg.pop('distance')
        with pytest.raises(ValueError):
            _decode_leg(leg, osrm, 'driving')
    else:
        leg['shape'] = ''
        assert len(_decode_leg(leg, osrm, 'driving')[0]) == 0  # Appending this malformed leg still fails.


async def test_access_round_trip_save_reload_and_reverse_preserve_stop_settings():
    route = await access_route()
    restored = Route.model_validate_json(route.model_dump_json())
    assert restored == route
    reversed_route = reverse_track(restored)
    reversed_twice = reverse_track(reversed_route)
    for original, reopened in zip(route.waypoints, reversed_twice.waypoints):
        assert reopened.access == original.access
        assert reopened.dwell_seconds == original.dwell_seconds
        assert reopened.requested_coordinate == original.requested_coordinate
        assert reopened.walk_to_stop == original.walk_to_stop


async def test_access_metadata_rejects_outside_ranges_wrong_mode_and_misplaced_estimates():
    route = await access_route()
    for mutate in ('outside', 'mode', 'estimate'):
        value = route.model_dump()
        access = value['waypoints'][1]['access']
        if mutate == 'outside':
            access['walking_to']['end_index'] = len(route.points)
        elif mutate == 'mode':
            value['segment_modes'][access['walking_to']['start_index']] = 'driving'
        else:
            access['estimated_segments'] = [0]
        with pytest.raises(ValidationError):
            Route.model_validate(value)


@pytest.mark.parametrize('speed_mode', ['adaptive', 'manual'])
def test_sharp_turn_slows_before_corner_and_reaccelerates(speed_mode):
    route = Route.model_validate({
        'source': 'road', 'mode': 'driving', 'driving_speed_mode': speed_mode,
        'speed_mps': 15., 'realistic_motion': True,
        'points': [{'latitude': lat, 'longitude': lon} for lat, lon in [(0., 0.), (0., .005), (.005, .005)]],
        'segment_speeds_mps': [15., 15.],
    })
    timeline = Timeline(route)
    approach = next(leg for leg in timeline.legs if distance(leg.end, route.points[1]) < .001)
    assert 0. < approach.end_speed_mps < 4.1
    assert approach.start_speed_mps > approach.end_speed_mps
    for leg in timeline.legs:
        acceleration = (leg.end_speed_mps - leg.start_speed_mps) / leg.duration
        assert -1.6 - 1e-7 <= acceleration <= 1.6 + 1e-7
