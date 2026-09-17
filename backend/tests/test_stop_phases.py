"""Shared timeline contracts for parked vehicles, stop pauses, and seeking."""
import pytest

from openlocation_backend.models import Route, distance
from openlocation_backend.route import Timeline, reverse_track


def parked_itinerary():
    locations = [(.0001, 0.), (0., 0.), (0., .002), (.00015, .002),
                 (.0002, .00205), (.0001, .0021), (0., .002),
                 (0., .004), (.0002, .004), (.00025, .00405)]
    points = [{'latitude': lat, 'longitude': lon} for lat, lon in locations]
    return Route.model_validate({
        'source': 'road', 'mode': 'driving', 'speed_mps': 13.4,
        'realistic_motion': True, 'walking_access_version': 1,
        'points': points,
        'segment_modes': ['walking', 'driving', 'walking', 'walking', 'walking',
                          'walking', 'driving', 'walking', 'walking'],
        'waypoints': [
            {'name': 'Home', 'point_index': 0, 'requested_coordinate': points[0], 'access': {
                'roadside_coordinate': points[1], 'arrival_index': 1, 'departure_index': 1,
                'walking_back': {'start_index': 0, 'end_index': 1}, 'estimated_segments': [0]}},
            {'name': 'Shop', 'point_index': 4, 'requested_coordinate': points[4],
             'dwell_seconds': 3601., 'access': {
                 'roadside_coordinate': points[2], 'arrival_index': 2, 'departure_index': 6,
                 'walking_to': {'start_index': 2, 'end_index': 4},
                 'walking_back': {'start_index': 4, 'end_index': 6}, 'estimated_segments': [3, 5]}},
            {'name': 'Office', 'point_index': 9, 'requested_coordinate': points[9], 'access': {
                'roadside_coordinate': points[7], 'arrival_index': 7, 'departure_index': 7,
                'walking_to': {'start_index': 7, 'end_index': 9}, 'estimated_segments': [8]}},
        ],
    })


def sample_leg(timeline, index, fraction=.5):
    start = timeline.ends[index - 1] if index else 0.
    return timeline.sample(start + timeline.legs[index].duration * fraction)


def test_car_parks_person_walks_pause_at_pin_and_returns_before_driving():
    route = parked_itinerary()
    timeline = Timeline(route)
    phases = []
    for i, leg in enumerate(timeline.legs):
        if not phases or phases[-1] != leg.phase:
            phases.append(leg.phase)
        sample = sample_leg(timeline, i)
        assert sample['phase'] == leg.phase
        if leg.phase in {'parking', 'walking_to_stop', 'paused_at_stop', 'walking_back', 'entering_car'}:
            stop = route.waypoints[sample['stop_index']]
            assert sample['parked_coordinate'] == stop.access.roadside_coordinate.model_dump()
        if leg.transition:
            assert leg.duration == 3.
        if leg.transition == 'exit_car':
            assert timeline.legs[i - 1].end_speed_mps == 0.
        if leg.transition == 'enter_car':
            assert timeline.legs[i + 1].start_speed_mps == 0.
    assert phases == ['walking_back', 'entering_car', 'driving', 'parking',
                      'walking_to_stop', 'paused_at_stop', 'walking_back',
                      'entering_car', 'driving', 'parking', 'walking_to_stop']
    wait = next(i for i, leg in enumerate(timeline.legs) if leg.phase == 'paused_at_stop')
    paused = sample_leg(timeline, wait)
    assert paused['stop_index'] == 1
    assert paused['pause_remaining_seconds'] == pytest.approx(1800.5)
    assert distance(timeline.legs[wait].start, route.points[4]) < .001
    final = timeline.sample(timeline.duration)
    assert final['complete'] and final['phase'] == 'paused_at_stop'
    assert final['stop_index'] == 2 and final['travel_mode'] == 'walking'
    assert final['pause_remaining_seconds'] == 0.
    assert final['parked_coordinate'] == route.waypoints[-1].access.roadside_coordinate.model_dump()
    assert final['coordinate'] == pytest.approx(route.waypoints[-1].requested_coordinate.model_dump())


def test_shared_roadside_keeps_car_parked_between_two_distinct_destinations():
    points = [{'latitude': lat, 'longitude': lon} for lat, lon in
              [(0., 0.), (0., .002), (.0001, .002), (0., .002),
               (0., .002), (-.0001, .002), (0., .002), (0., .004)]]
    route = Route.model_validate({
        'source': 'road', 'mode': 'driving', 'walking_access_version': 1,
        'points': points,
        'segment_modes': ['driving', 'walking', 'walking', 'driving', 'walking', 'walking', 'driving'],
        'waypoints': [
            {'name': 'Start', 'point_index': 0},
            {'name': 'North entrance', 'point_index': 2, 'dwell_seconds': 30., 'access': {
                'roadside_coordinate': points[1], 'arrival_index': 1, 'departure_index': 3,
                'walking_to': {'start_index': 1, 'end_index': 2},
                'walking_back': {'start_index': 2, 'end_index': 3}}},
            {'name': 'South entrance', 'point_index': 5, 'dwell_seconds': 45., 'access': {
                'roadside_coordinate': points[4], 'arrival_index': 4, 'departure_index': 6,
                'walking_to': {'start_index': 4, 'end_index': 5},
                'walking_back': {'start_index': 5, 'end_index': 6}}},
            {'name': 'Finish', 'point_index': 7},
        ],
    })
    timeline = Timeline(route)
    assert [leg.transition for leg in timeline.legs if leg.transition] == ['exit_car', 'enter_car']
    waits = [i for i, leg in enumerate(timeline.legs) if leg.phase == 'paused_at_stop']
    assert [sample_leg(timeline, i)['stop_index'] for i in waits] == [1, 2]
    for i in range(waits[0], waits[-1] + 1):
        assert sample_leg(timeline, i)['parked_coordinate'] == route.waypoints[1].access.roadside_coordinate.model_dump()


@pytest.mark.parametrize('phase', ['parking', 'walking_to_stop', 'paused_at_stop', 'walking_back', 'entering_car'])
def test_seek_and_speed_change_reconstruct_stop_and_vehicle_state(phase):
    timeline = Timeline(parked_itinerary())
    index = next(i for i, leg in enumerate(timeline.legs) if leg.phase == phase and leg.stop_index == 1)
    timeline.elapsed = (timeline.ends[index - 1] if index else 0.) + timeline.legs[index].duration * .4
    before = timeline.sample()
    timeline.set_speed(22.)
    after = timeline.sample()
    for key in ('phase', 'stop_index', 'parked_coordinate', 'transition', 'travel_mode'):
        assert after[key] == before[key]
    assert after['coordinate'] == pytest.approx(before['coordinate'], abs=1e-9)
    assert after['pause_remaining_seconds'] == pytest.approx(before['pause_remaining_seconds'])


def test_save_reload_and_reverse_preserve_stop_intent_and_access_ranges():
    route = parked_itinerary()
    restored = Route.model_validate_json(route.model_dump_json())
    assert restored == route
    reversed_route = reverse_track(restored)
    assert reversed_route.walking_access_version == 1
    assert [w.name for w in reversed_route.waypoints] == ['Office', 'Shop', 'Home']
    assert reversed_route.waypoints[1].dwell_seconds == 3601.
    assert reversed_route.waypoints[0].access.walking_to is None
    assert reversed_route.waypoints[0].access.walking_back.start_index == 0
    assert reversed_route.waypoints[-1].access.walking_back is None
    assert reversed_route.waypoints[-1].access.walking_to.end_index == len(route.points) - 1
    assert reversed_route.waypoints[1].access.estimated_segments == [3, 5]
    assert reverse_track(reversed_route).waypoints == route.waypoints
    final = Timeline(reversed_route).sample(Timeline(reversed_route).duration)
    assert final['parked_coordinate'] == route.waypoints[0].access.roadside_coordinate.model_dump()


def test_disabled_walking_and_zero_pause_add_no_person_or_stop_wait():
    route = Route.model_validate({
        'source': 'road', 'mode': 'driving', 'walking_access_version': 1,
        'points': [{'latitude': 0., 'longitude': lon} for lon in (0., .002)],
        'waypoints': [{'name': 'Start', 'point_index': 0, 'walk_to_stop': False},
                      {'name': 'Finish', 'point_index': 1, 'walk_to_stop': False}],
    })
    timeline = Timeline(route)
    assert not any(leg.dwell or leg.transition for leg in timeline.legs)
    assert all(sample_leg(timeline, i)['parked_coordinate'] is None for i in range(len(timeline.legs)))
