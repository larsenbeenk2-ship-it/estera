"""Offline motion contracts: conservative roads, terrain, and sustained bends."""
from math import cos, pi, sin

import pytest

from openlocation_backend.models import Route
from openlocation_backend.route import CRUISE_MARGIN_MPS, Timeline, reverse_track


MPH = .44704


def road(**changes):
    return Route.model_validate({
        'source': 'road', 'mode': 'driving', 'speed_mps': 13.4,
        'points': [{'latitude': 0., 'longitude': lon} for lon in (0., .02)],
        'segment_speeds_mps': [35.], 'realistic_motion': True,
        **changes,
    })


def speed_at_distance(timeline, meters):
    for i, leg in enumerate(timeline.legs):
        if not leg.dwell and timeline.distances[i] <= meters < timeline.distances[i + 1]:
            start = timeline.ends[i - 1] if i else 0.
            return timeline.sample(start + leg.seconds_at_distance(meters - timeline.distances[i]))['speed_mps']
    raise AssertionError('distance outside movement')


@pytest.mark.parametrize(('road_class', 'ceiling_mph'), [
    ('unknown', 25.), ('residential', 25.), ('service_other', 15.),
    ('ramp', 25.), ('motorway', 55.),
])
def test_missing_limit_uses_conservative_class_ceiling_without_inventing_a_limit(road_class, ceiling_mph):
    timeline = Timeline(road(segment_road_classes=[road_class], speed_variation=.5))
    peak = max(max(leg.start_speed_mps, leg.end_speed_mps) for leg in timeline.legs)
    assert (ceiling_mph - 3.) * MPH < peak < ceiling_mph * MPH
    sample = timeline.sample(timeline.duration / 2)
    assert sample['speed_limit_mps'] is None
    assert sample['road_class'] == road_class
    assert sample['grade'] is None


def test_supplied_limit_controls_cruise_and_braking_before_lower_boundary():
    timeline = Timeline(road(
        points=[{'latitude': 0., 'longitude': lon} for lon in (0., .01, .02)],
        segment_speeds_mps=[35., 35.], segment_speed_limits_mps=[55. * MPH, 25. * MPH],
        segment_road_classes=['unknown', 'motorway'], speed_variation=.5))
    assert max(leg.end_speed_mps for leg in timeline.legs if leg.end.longitude < .008) > 50. * MPH
    approach = [leg for leg in timeline.legs if leg.end.longitude <= .01 + 1e-10]
    assert approach[-1].end_speed_mps <= 25. * MPH
    assert any(leg.start_speed_mps > leg.end_speed_mps for leg in approach[-4:])
    for leg in timeline.legs:
        assert max(leg.start_speed_mps, leg.end_speed_mps) <= leg.speed_limit_mps
        if leg.start.longitude >= .01 - 1e-10:
            assert leg.speed_limit_mps == 25. * MPH


@pytest.mark.parametrize(('mode', 'selected'), [('cycling', 4.2), ('walking', 1.4)])
def test_known_flat_grade_uses_selected_pace_and_unknown_keeps_provider_pace(mode, selected):
    unknown = road(mode=mode, realistic_motion=False, speed_mps=selected,
                   segment_speeds_mps=[selected / 2], segment_grades=[None])
    flat = unknown.model_copy(update={'segment_grades': [0.]})
    assert Timeline(unknown).sample(10.)['speed_mps'] == pytest.approx(selected / 2)
    assert Timeline(flat).sample(10.)['speed_mps'] == pytest.approx(selected)
    assert Timeline(flat).sample(10.)['grade'] == 0.


def test_hills_slow_cycling_more_than_walking_and_do_not_double_count_provider_eta():
    ratios = {}
    for mode, selected in [('cycling', 4.2), ('walking', 1.4)]:
        flat = road(mode=mode, realistic_motion=False, speed_mps=selected,
                    segment_speeds_mps=[.4], segment_grades=[0.])
        uphill = flat.model_copy(update={'segment_grades': [.1]})
        ratios[mode] = Timeline(uphill).duration / Timeline(flat).duration
    assert ratios['cycling'] == pytest.approx(2.4)
    assert ratios['walking'] == pytest.approx(1.4)


@pytest.mark.parametrize(('mode', 'selected', 'ceiling'), [('cycling', 4.2, 1.35), ('walking', 1.4, 1.1)])
def test_downhill_gain_is_bounded_even_with_variation(mode, selected, ceiling):
    timeline = Timeline(road(mode=mode, realistic_motion=False, speed_mps=selected,
                             segment_speeds_mps=[35.], segment_grades=[-.3], speed_variation=.5))
    peak = max(max(leg.start_speed_mps, leg.end_speed_mps) for leg in timeline.legs)
    assert selected < peak <= selected * ceiling + 1e-9


def test_hill_boundaries_decelerate_and_recover_continuously():
    timeline = Timeline(road(mode='cycling', realistic_motion=False, speed_mps=4.2,
        points=[{'latitude': 0., 'longitude': lon} for lon in (0., .004, .008, .012)],
        segment_speeds_mps=[.5, .5, .5], segment_grades=[0., .1, 0.]))
    for previous, following in zip(timeline.legs, timeline.legs[1:]):
        assert previous.end_speed_mps == pytest.approx(following.start_speed_mps, abs=1e-8)
    for leg in timeline.legs:
        acceleration = (leg.end_speed_mps - leg.start_speed_mps) / leg.duration
        assert -.6 - 1e-8 <= acceleration <= .45 + 1e-8
    assert any(leg.start.longitude < .004 and leg.start_speed_mps > leg.end_speed_mps for leg in timeline.legs)
    assert any(leg.start.longitude >= .008 and leg.start_speed_mps < leg.end_speed_mps for leg in timeline.legs)


def test_driving_completely_ignores_grades_and_recorded_timing_keeps_timestamps():
    original = road(segment_speed_limits_mps=[65. * MPH], speed_variation=.06)
    baseline = Timeline(original)
    graded = Timeline(original.model_copy(update={'segment_grades': [.2]}))
    assert graded.duration == baseline.duration
    assert graded.sample(60.)['speed_mps'] == baseline.sample(60.)['speed_mps']
    recorded = Route.model_validate({
        'source': 'gpx', 'mode': 'cycling', 'timing': 'recorded',
        'points': [{'latitude': 0., 'longitude': 0., 'time': 10.},
                   {'latitude': 0., 'longitude': .001, 'time': 100.}],
        'segment_grades': [.2], 'segment_road_classes': ['residential'],
    })
    timeline = Timeline(recorded)
    assert timeline.duration == 90.
    assert timeline.sample(45.)['distance_meters'] == pytest.approx(timeline.distance / 2)


def curved_road(radius, arc_pieces):
    xy = [(-1000., 0.), (0., 0.)]
    xy += [(radius * sin(i * pi / (2 * arc_pieces)), radius * (1 - cos(i * pi / (2 * arc_pieces))))
           for i in range(1, arc_pieces + 1)]
    xy.append((radius, radius + 1000.))
    count = len(xy) - 1
    return road(points=[{'latitude': y / 111195., 'longitude': x / 111195.} for x, y in xy],
                segment_speeds_mps=[35.] * count, segment_speed_limits_mps=[70. * MPH] * count,
                segment_road_classes=['motorway'] * count)


def test_broad_freeway_arc_keeps_cruise_across_geometry_sampling_densities():
    sparse, dense = [Timeline(curved_road(700., pieces)) for pieces in (12, 48)]
    cruise = 70. * MPH - CRUISE_MARGIN_MPS
    for timeline in (sparse, dense):
        for arc_fraction in (.2, .5, .8):
            assert speed_at_distance(timeline, 1000. + 700. * pi / 2 * arc_fraction) >= .95 * cruise
    assert sparse.duration == pytest.approx(dense.duration, rel=.025)


def test_tight_highway_curve_brakes_early_stays_slow_through_bend_and_recovers():
    timeline = Timeline(curved_road(60., 24))
    cruise = 70. * MPH - CRUISE_MARGIN_MPS
    for arc_fraction in (.2, .5, .8):
        assert speed_at_distance(timeline, 1000. + 60. * pi / 2 * arc_fraction) < .65 * cruise
    assert speed_at_distance(timeline, 950.) < .85 * cruise
    curve_end = 1000. + 60. * pi / 2
    assert speed_at_distance(timeline, curve_end + 50.) < speed_at_distance(timeline, curve_end + 400.)
    for previous, following in zip(timeline.legs, timeline.legs[1:]):
        assert previous.end_speed_mps == pytest.approx(following.start_speed_mps, abs=1e-8)


def test_submeter_freeway_geometry_noise_does_not_cause_braking():
    xy = [(x, .4 * sin(x / 7.)) for x in range(0, 2001, 20)]
    count = len(xy) - 1
    timeline = Timeline(road(
        points=[{'latitude': y / 111195., 'longitude': x / 111195.} for x, y in xy],
        segment_speeds_mps=[35.] * count, segment_speed_limits_mps=[70. * MPH] * count,
        segment_road_classes=['motorway'] * count))
    cruise = 70. * MPH - CRUISE_MARGIN_MPS
    for meters in (600., 900., 1200., 1400.):
        assert speed_at_distance(timeline, meters) == pytest.approx(cruise)


def test_reversed_grades_and_live_speed_changes_preserve_cycle_distance_and_dwell():
    route = road(mode='cycling', realistic_motion=False, speed_mps=4.2, repeat_count=2,
        points=[{'latitude': 0., 'longitude': lon} for lon in (0., .005, 0.)],
        segment_speeds_mps=[2., 3.], segment_grades=[.05, -.03],
        segment_road_classes=['residential', 'service_other'],
        waypoints=[{'name': 'Rest', 'point_index': 1, 'dwell_seconds': 10.}])
    reversed_route = reverse_track(route)
    assert reversed_route.segment_grades == [.03, -.05]
    assert reversed_route.segment_road_classes == ['service_other', 'residential']
    timeline = Timeline(route)
    timeline.elapsed = timeline.duration + 20.
    before = timeline.sample()
    timeline.set_speed(3.)
    assert timeline.sample()['distance_meters'] == pytest.approx(before['distance_meters'])
    assert timeline.sample()['coordinate'] == pytest.approx(before['coordinate'], abs=1e-9)
    dwell_index = next(i for i, leg in enumerate(timeline.legs) if leg.dwell)
    timeline.elapsed = timeline.duration + timeline.ends[dwell_index - 1] + 1.25
    before = timeline.sample()
    timeline.set_speed(5.)
    replacement_dwell = next(i for i, leg in enumerate(timeline.legs) if leg.dwell)
    assert timeline.sample()['dwelling'] and timeline.sample()['cycle'] == 2
    assert timeline.sample()['distance_meters'] == pytest.approx(before['distance_meters'])
    assert timeline.elapsed - timeline.duration - timeline.ends[replacement_dwell - 1] == pytest.approx(1.25)
