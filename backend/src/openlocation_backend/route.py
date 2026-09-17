"""Pure distance/time timeline. Device writes are owned by Session, never here."""
from bisect import bisect_right
from dataclasses import dataclass
from math import atan2, ceil, cos, radians, sin, sqrt
from typing import Literal

from .models import DEFAULT_SPEEDS, Coordinate, Route, distance, interpolate


CRUISE_MARGIN_MPS = 2 * 0.44704
CAR_TRANSITION_SECONDS = 3.
# Conservative cruise ceilings when no posted limit is supplied. These are
# estimates, never legal speed limits, and cannot override an actual limit.
FALLBACK_CRUISE_MPH = {
    'motorway': 55., 'trunk': 45., 'primary': 35., 'secondary': 30.,
    'tertiary': 25., 'unclassified': 25., 'residential': 25.,
    'service_other': 15., 'ramp': 25., 'unknown': 25.,
}


def _grade_multiplier(mode, grade):
    """Scale the selected flat-ground pace once, independently of road ETA."""
    if grade is None or mode == 'driving':
        return 1.
    if grade >= 0:
        return max(.15 if mode == 'cycling' else .3,
                   1 / (1 + (14. if mode == 'cycling' else 4.) * grade))
    return min(1.35 if mode == 'cycling' else 1.1,
               1 - (4. if mode == 'cycling' else 1.) * grade)


def _bearing(a, b):
    lat1, lat2 = radians(a.latitude), radians(b.latitude)
    longitude = radians(b.longitude - a.longitude)
    return atan2(sin(longitude) * cos(lat2), cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(longitude))


def _turn_speed_caps(points, lengths, modes, road_classes, samples):
    """Measure bends in meters, using several spans to suppress geometry noise.

    Each sample is (distance along the route, original segment index). Sampling
    both motion nodes and segment midpoints maintains a cap through a bend,
    instead of accelerating toward cruise between every pair of curve points.
    """
    cumulative = [0.]
    for meters in lengths:
        cumulative.append(cumulative[-1] + meters)

    def at(meters):
        index = max(0, min(bisect_right(cumulative, meters) - 1, len(lengths) - 1))
        return interpolate(points[index], points[index + 1],
                           (meters - cumulative[index]) / lengths[index] if lengths[index] else 0.)

    bounds = [None] * len(modes)
    corners = {}
    start = 0
    while start < len(modes):
        end = start + 1
        while end < len(modes) and modes[end] == modes[start]:
            end += 1
        if modes[start] == 'driving':
            for i in range(start, end):
                bounds[i] = (cumulative[start], cumulative[end])
            for i in range(start + 1, end):
                before, after = lengths[i - 1], lengths[i]
                if min(before, after) <= .1:
                    continue
                angle = _bearing(points[i], points[i + 1]) - _bearing(points[i - 1], points[i])
                bend = abs(sin(angle / 2))
                # Only unmistakable corners use a local vertex. Smaller heading
                # changes on sparse freeway arcs belong to the metric windows.
                if bend > .5 and min(before, after) >= 8.:
                    corners[cumulative[i]] = max(2., sqrt(1.1 * min(before, after, 20.) / (2 * bend)))
        start = end
    caps = []
    for meters, segment in samples:
        if bounds[segment] is None:
            caps.append(100.)
            continue
        lower, upper = bounds[segment]
        center = at(meters)
        radii = []
        for span in (25., 60., 120.):
            window = min(span, meters - lower, upper - meters)
            if window < span * .8:
                continue
            left, right = at(meters - window), at(meters + window)
            before, after = distance(left, center), distance(center, right)
            angle = _bearing(center, right) - _bearing(left, center)
            bend = abs(sin(angle / 2))
            # Sub-meter centerline jitter has no useful curvature evidence.
            if bend > .01 and min(before, after) * bend > 1.:
                radii.append(min(before, after) / (2 * bend))
        cap = 100.
        if radii:
            radii.sort()
            radius = radii[-1]
            # A long window can cancel opposite bends in an S curve. Retain
            # shorter-window evidence when the broad estimate diverges greatly.
            if len(radii) == 3 and radius > 2 * radii[-2]:
                radius = radii[-2]
            lateral = 2. if road_classes[segment] in {'motorway', 'trunk'} else 1.5 if road_classes[segment] == 'ramp' else 1.1
            cap = max(2., sqrt(lateral * radius))
        caps.append(min(cap, corners.get(meters, 100.)))
    return caps


@dataclass(frozen=True)
class Leg:
    start: Coordinate
    end: Coordinate
    distance: float
    duration: float
    dwell: bool
    start_speed_mps: float | None = None
    end_speed_mps: float | None = None
    travel_mode: Literal['walking', 'cycling', 'driving'] = 'walking'
    transition: str | None = None
    speed_limit_mps: float | None = None
    road_class: str = 'unknown'
    grade: float | None = None
    phase: str = 'walking'
    stop_index: int | None = None
    parked_coordinate: Coordinate | None = None

    def distance_fraction(self, seconds):
        seconds = min(self.duration, max(0, seconds))
        if self.start_speed_mps is None or self.distance == 0:
            return seconds / self.duration
        acceleration = (self.end_speed_mps - self.start_speed_mps) / self.duration
        return min(1., max(0., (self.start_speed_mps * seconds + .5 * acceleration * seconds ** 2) / self.distance))

    def seconds_at_distance(self, meters):
        fraction = min(1., max(0., meters / self.distance))
        if self.start_speed_mps is None:
            return fraction * self.duration
        acceleration = (self.end_speed_mps - self.start_speed_mps) / self.duration
        speed = sqrt(max(0, self.start_speed_mps ** 2 + 2 * acceleration * fraction * self.distance))
        return 2 * fraction * self.distance / (self.start_speed_mps + speed) if self.start_speed_mps + speed else 0.


class Timeline:
    def __init__(self, route: Route):
        self.route = route
        self.legs: list[Leg] = []
        dwell = {w.point_index: w.dwell_seconds for w in route.waypoints}
        driving = route.mode == 'driving' and route.timing == 'constant'
        adaptive = driving and route.driving_speed_mode == 'adaptive'
        original_modes = route.segment_modes or [route.mode] * (len(route.points) - 1)
        road_classes = route.segment_road_classes or ['unknown'] * len(original_modes)
        grades = route.segment_grades or [None] * len(original_modes)
        original_lengths = [distance(a, b) for a, b in zip(route.points, route.points[1:])]
        # Distinct stops can share the same parked car. Ignore stationary road
        # links so walking back to that car does not briefly get in and out.
        moving_segments = [i for i, meters in enumerate(original_lengths) if meters > 1e-6]
        transitions = {following: 'exit_car' if original_modes[previous] == 'driving' else 'enter_car'
                       for previous, following in zip(moving_segments, moving_segments[1:])
                       if original_modes[previous] != original_modes[following]}
        segment_context = []
        next_stop = 0
        for i, mode in enumerate(original_modes):
            while next_stop < len(route.waypoints) and route.waypoints[next_stop].point_index <= i:
                next_stop += 1
            segment_context.append({'phase': mode, 'stop_index': next_stop if next_stop < len(route.waypoints) else None,
                                    'parked_coordinate': None})
        waypoint_context = {}
        for stop_index, waypoint in enumerate(route.waypoints):
            access = waypoint.access
            waypoint_context[waypoint.point_index] = {
                'phase': 'paused_at_stop', 'stop_index': stop_index,
                'parked_coordinate': access.roadside_coordinate if access else None,
            }
            if access is None:
                continue
            for span, phase in ((access.walking_to, 'walking_to_stop'), (access.walking_back, 'walking_back')):
                if span is not None:
                    for i in range(span.start_index, span.end_index):
                        segment_context[i] = {'phase': phase, 'stop_index': stop_index,
                                              'parked_coordinate': access.roadside_coordinate}
        transition_context = {}
        for i, transition in transitions.items():
            # The walking side owns the stopped vehicle, destination, and phase.
            walking_segment = i if transition == 'exit_car' else next(
                previous for previous in reversed(moving_segments) if previous < i)
            transition_context[i] = {**segment_context[walking_segment],
                                     'phase': 'parking' if transition == 'exit_car' else 'entering_car'}
        smooth_driving = driving and (route.realistic_motion or adaptive or bool(transitions))
        # Smooth the whole connected walk/ride when it contains terrain, so a
        # known hill meets an unknown/flat segment without a speed discontinuity.
        hill_segments = [False] * len(original_modes)
        start = 0
        while start < len(original_modes):
            end = start + 1
            while end < len(original_modes) and original_modes[end] == original_modes[start]:
                end += 1
            if route.timing == 'constant' and original_modes[start] != 'driving' and any(g is not None for g in grades[start:end]):
                hill_segments[start:end] = [True] * (end - start)
            start = end
        original_smooth = [smooth_driving if mode == 'driving' else hill_segments[i]
                           for i, mode in enumerate(original_modes)]
        stops = {0, len(route.points) - 1, *(w.point_index for w in route.waypoints)}
        stops.update(transitions)
        if driving:
            for crossing in route.intersections:
                if 'driving' not in original_modes[crossing.point_index - 1:crossing.point_index + 1]:
                    continue
                controlled = crossing.control in {'stop_sign', 'traffic_signal'}
                if controlled or (route.stop_policy == 'all' and route.intersection_pause_seconds > 0):
                    # A zero added wait still reaches zero at a mapped stop sign.
                    stops.add(crossing.point_index)
                    dwell[crossing.point_index] = max(dwell.get(crossing.point_index, 0), route.intersection_pause_seconds)
        if any(meters > 20_015_114 - 1 for meters in original_lengths):
            raise ValueError("antipodal endpoints require an intermediate point")
        # Long straight segments need multiple speed targets too. Keep at most
        # about 50,000 extra subdivisions even on very long imported tracks.
        spacing = max(40., sum(original_lengths) / 50_000)
        points, lengths, segments, indices = [route.points[0]], [], [], [0]
        positions, original_traveled = [0.], 0.
        for i, meters in enumerate(original_lengths):
            pieces = max(1, ceil(meters / spacing)) if original_smooth[i] else 1
            for j in range(1, pieces + 1):
                points.append(route.points[i + 1] if j == pieces else interpolate(route.points[i], route.points[i + 1], j / pieces))
                lengths.append(meters / pieces)
                segments.append(i)
                positions.append(original_traveled + (meters if j == pieces else meters * j / pieces))
            indices.append(len(points) - 1)
            original_traveled += meters
        dwell = {indices[i]: seconds for i, seconds in dwell.items()}
        stops = {indices[i] for i in stops}
        transitions = {indices[i]: kind for i, kind in transitions.items()}
        transition_context = {indices[i]: context for i, context in transition_context.items()}
        waypoint_context = {indices[i]: context for i, context in waypoint_context.items()}
        modes = [original_modes[i] for i in segments]
        smooth_segments = [original_smooth[i] for i in segments]
        node_caps, segment_caps = [100.] * len(points), [100.] * len(lengths)
        if smooth_driving:
            samples = [(meters, segments[min(i, len(segments) - 1)]) for i, meters in enumerate(positions)]
            samples += [((a + b) / 2, segments[i]) for i, (a, b) in enumerate(zip(positions, positions[1:]))]
            caps = _turn_speed_caps(route.points, original_lengths, original_modes, road_classes, samples)
            node_caps, segment_caps = caps[:len(points)], caps[len(points):]
        speeds, traveled = [], 0.0
        for segment, (i, meters) in enumerate(zip(segments, lengths)):
            mode, grade = original_modes[i], grades[i]
            if driving and mode == 'walking' and grade is None:
                # Walking access always uses a normal walking pace, including
                # while the user adjusts driving speed or road variation.
                speeds.append(DEFAULT_SPEEDS['walking'])
                traveled += meters
                continue
            midpoint = traveled + meters / 2
            wave = 0.65 * sin(midpoint / 180) + 0.35 * sin(midpoint / 61)
            limit = route.segment_speed_limits_mps[i] if route.segment_speed_limits_mps else None
            if mode != 'driving' and grade is not None and route.timing == 'constant':
                # Provider ETA may already include hills. Known terrain always
                # starts from the user's flat-ground speed to avoid counting it twice.
                base = DEFAULT_SPEEDS['walking'] if driving else route.speed_mps
                target = base * _grade_multiplier(mode, grade) * (1 + route.speed_variation * wave)
                if grade < 0:
                    target = min(target, base * (1.35 if mode == 'cycling' else 1.1))
            elif adaptive and mode == 'driving':
                estimate = route.segment_speeds_mps[i] if route.segment_speeds_mps else DEFAULT_SPEEDS['driving']
                fallback = FALLBACK_CRUISE_MPH[road_classes[i]] * .44704
                base = max(.01, limit - CRUISE_MARGIN_MPS) if limit is not None else min(estimate, fallback - CRUISE_MARGIN_MPS)
                # Absolute amplitude avoids a 70 mph road varying by 10 mph.
                amplitude = min(base * route.speed_variation, CRUISE_MARGIN_MPS * .85)
                target = base + amplitude * wave
            else:
                base = (route.segment_speeds_mps[i] * route.speed_mps / DEFAULT_SPEEDS[route.mode]
                        if route.segment_speeds_mps and not driving else route.speed_mps)
                target = base * (1 + route.speed_variation * wave)
            speeds.append(min(100., limit if limit is not None else 100., segment_caps[segment], max(.01, target)))
            traveled += meters
        accelerations = [1.6 if mode == 'driving' else .45 if mode == 'cycling' else .2 for mode in modes]
        brakings = [1.6 if mode == 'driving' else .6 if mode == 'cycling' else .25 for mode in modes]
        if any(smooth_segments):
            # Node caps look ahead to slower roads and stops, so deceleration
            # begins before the crossing instead of jumping to zero at it.
            node_speeds = [speeds[0]] + [min(a, b) for a, b in zip(speeds, speeds[1:])] + [speeds[-1]]
            node_speeds = [min(speed, cap) for speed, cap in zip(node_speeds, node_caps)]
            motion_stops = set(stops) if smooth_driving else set()
            motion_stops.update(i for i, seconds in dwell.items() if seconds > 0)
            for i in motion_stops:
                node_speeds[i] = 0.
            for i, meters in enumerate(lengths):
                if smooth_segments[i]:
                    node_speeds[i + 1] = min(node_speeds[i + 1], sqrt(node_speeds[i] ** 2 + 2 * accelerations[i] * meters))
            for i in reversed(range(len(lengths))):
                if smooth_segments[i]:
                    node_speeds[i] = min(node_speeds[i], sqrt(node_speeds[i + 1] ** 2 + 2 * brakings[i] * lengths[i]))
        def metadata(segment):
            return {'speed_limit_mps': route.segment_speed_limits_mps[segment] if route.segment_speed_limits_mps else None,
                    'road_class': road_classes[segment], 'grade': grades[segment], **segment_context[segment]}

        for i, a in enumerate(points):
            incoming_mode = modes[i - 1] if i else modes[0]
            incoming_segment = segments[i - 1] if i else segments[0]
            if dwell.get(i, 0) > 0:
                context = {**metadata(incoming_segment), **waypoint_context.get(i, {})}
                dwell_mode = 'walking' if context['parked_coordinate'] is not None else incoming_mode
                self.legs.append(Leg(a, a, 0, dwell[i], True, travel_mode=dwell_mode, **context))
            if i in transitions:
                self.legs.append(Leg(a, a, 0, CAR_TRANSITION_SECONDS, True,
                                     travel_mode='driving' if transitions[i] == 'exit_car' else 'walking',
                                     transition=transitions[i],
                                     **{**metadata(incoming_segment), **transition_context[i]}))
            if i + 1 == len(points):
                continue
            b = points[i + 1]
            meters = lengths[i]
            if smooth_segments[i] and meters > 0:
                acceleration, braking = accelerations[i], brakings[i]
                v0, v1 = node_speeds[i], node_speeds[i + 1]
                peak = min(speeds[i], sqrt((2 * acceleration * braking * meters + braking * v0 ** 2 + acceleration * v1 ** 2) / (acceleration + braking)))
                accelerating = max(0., (peak ** 2 - v0 ** 2) / (2 * acceleration))
                decelerating = max(0., (peak ** 2 - v1 ** 2) / (2 * braking))
                phases = [(accelerating, v0, peak), (max(0., meters - accelerating - decelerating), peak, peak), (decelerating, peak, v1)]
                covered = 0.
                for phase_meters, start_speed, end_speed in phases:
                    if phase_meters <= 1e-10:
                        continue
                    phase_start = interpolate(a, b, covered / meters)
                    covered += phase_meters
                    phase_end = interpolate(a, b, min(1., covered / meters))
                    self.legs.append(Leg(phase_start, phase_end, phase_meters, 2 * phase_meters / (start_speed + end_speed), False, start_speed, end_speed, modes[i], **metadata(segments[i])))
                continue
            seconds = b.time - a.time if route.timing == "recorded" else meters / speeds[i]
            if seconds > 0:
                self.legs.append(Leg(a, b, meters, seconds, False, travel_mode=modes[i], **metadata(segments[i])))
        self.ends = []
        self.distances = [0.0]
        total = 0.0
        for leg in self.legs:
            total += leg.duration
            self.ends.append(total)
            self.distances.append(self.distances[-1] + leg.distance)
        self.duration = total
        self.distance = self.distances[-1]
        self.elapsed = 0.0

    @property
    def total_duration(self):
        return None if self.route.loop else self.duration * self.route.repeat_count

    def sample(self, elapsed=None):
        elapsed = self.elapsed if elapsed is None else max(0, elapsed)
        completed = self.total_duration is not None and elapsed >= self.total_duration
        if completed:
            cycle = self.route.repeat_count - 1
            local = self.duration
        else:
            cycle, local = divmod(elapsed, self.duration)
        index = min(bisect_right(self.ends, local), len(self.legs) - 1)
        start = self.ends[index - 1] if index else 0
        leg = self.legs[index]
        seconds = min(leg.duration, max(0, local - start))
        fraction = leg.distance_fraction(seconds)
        coordinate = interpolate(leg.start, leg.end, fraction)
        traveled = cycle * self.distance + self.distances[index] + fraction * leg.distance
        phase, stop_index, parked = leg.phase, leg.stop_index, leg.parked_coordinate
        if completed and self.route.waypoints and self.route.waypoints[-1].point_index == len(self.route.points) - 1:
            waypoint = self.route.waypoints[-1]
            phase, stop_index = 'paused_at_stop', len(self.route.waypoints) - 1
            parked = waypoint.access.roadside_coordinate if waypoint.access else parked
        return {
            "coordinate": coordinate.model_dump(), "elapsed_seconds": elapsed,
            "distance_meters": traveled, "cycle": int(cycle) + 1,
            "total_distance_meters": None if self.route.loop else self.distance * self.route.repeat_count,
            "eta_seconds": None if self.total_duration is None else max(0, self.total_duration - elapsed),
            "progress": None if self.total_duration is None else min(1, elapsed / self.total_duration),
            "dwelling": leg.dwell, "complete": completed,
            "travel_mode": leg.travel_mode, "transition": None if completed else leg.transition,
            "phase": phase, "stop_index": stop_index,
            "parked_coordinate": parked.model_dump() if parked is not None else None,
            "pause_remaining_seconds": max(0., leg.duration - seconds) if phase == 'paused_at_stop' and leg.dwell and not completed else 0.,
            "speed_limit_mps": leg.speed_limit_mps, "road_class": leg.road_class, "grade": leg.grade,
            "speed_mps": 0. if completed or leg.dwell else leg.distance / leg.duration if leg.start_speed_mps is None else leg.start_speed_mps + (leg.end_speed_mps - leg.start_speed_mps) * seconds / leg.duration,
        }

    def set_speed(self, speed: float):
        if self.route.timing != "constant":
            raise ValueError("speed changes require constant timing; recorded timing uses original timestamps")
        cycle, local = divmod(self.elapsed, self.duration)
        index = min(bisect_right(self.ends, local), len(self.legs) - 1)
        start = self.ends[index - 1] if index else 0
        leg = self.legs[index]
        fraction = leg.distance_fraction(local - start)
        traveled = self.distances[index] + fraction * leg.distance
        dwell_order = sum(previous.dwell and abs(self.distances[j] - self.distances[index]) < 1e-6
                          for j, previous in enumerate(self.legs[:index])) if leg.dwell else 0
        replacement = Timeline(Route.model_validate({**self.route.model_dump(), "speed_mps": speed, "driving_speed_mode": "manual"}))
        # Acceleration phases can appear or disappear at a new pace. Preserve
        # distance (or elapsed dwell) instead of assuming the same leg indices.
        replacement_local = replacement.duration
        for j, new_leg in enumerate(replacement.legs):
            begin = replacement.ends[j - 1] if j else 0.
            if leg.dwell:
                if new_leg.dwell and abs(replacement.distances[j] - self.distances[index]) < 1e-6:
                    if dwell_order:
                        dwell_order -= 1
                        continue
                    replacement_local = begin + min(new_leg.duration, local - start)
                    break
            elif not new_leg.dwell and new_leg.travel_mode == leg.travel_mode and replacement.distances[j] <= traveled + 1e-8 <= replacement.distances[j + 1] + 1e-8:
                # At a leg's start, stay after any preceding dwell instead of
                # moving back to the end of the previous movement phase.
                if fraction < 1 - 1e-8 and abs(traveled - replacement.distances[j + 1]) < 1e-8:
                    continue
                replacement_local = begin + new_leg.seconds_at_distance(traveled - replacement.distances[j])
                break
        replacement.elapsed = cycle * replacement.duration + replacement_local
        self.__dict__.update(replacement.__dict__)


def reverse_track(route: Route) -> Route:
    points = list(reversed(route.points))
    if all(p.time is not None for p in points):
        first, last = route.points[0].time, route.points[-1].time
        points = [p.model_copy(update={"time": first + last - p.time}) for p in points]
    last_index = len(points) - 1

    def reverse_range(span):
        return None if span is None else {
            'start_index': last_index - span.end_index,
            'end_index': last_index - span.start_index,
        }

    waypoints = []
    for waypoint in reversed(route.waypoints):
        item = {**waypoint.model_dump(), 'point_index': last_index - waypoint.point_index}
        if waypoint.access is not None:
            access = waypoint.access
            item['access'] = {
                'roadside_coordinate': access.roadside_coordinate.model_dump(),
                'arrival_index': last_index - access.departure_index,
                'departure_index': last_index - access.arrival_index,
                'walking_to': reverse_range(access.walking_back),
                'walking_back': reverse_range(access.walking_to),
                'estimated_segments': sorted(last_index - 1 - i for i in access.estimated_segments),
            }
        waypoints.append(item)
    data = route.model_dump()
    data.update(points=[p.model_dump() for p in points], source="reversed_track",
                segment_speeds_mps=list(reversed(route.segment_speeds_mps)),
                segment_modes=list(reversed(route.segment_modes)),
                segment_road_classes=list(reversed(route.segment_road_classes)),
                segment_grades=[None if grade is None else -grade for grade in reversed(route.segment_grades)],
                # Road limits and controls may apply only in the original
                # direction. A reversed track needs recalculation for these.
                segment_speed_limits_mps=[] if route.mode == 'driving' else list(reversed(route.segment_speed_limits_mps)),
                traffic_controls_source="none",
                waypoints=waypoints,
                intersections=[{**i.model_dump(), "control": "unknown", "point_index": len(points) - 1 - i.point_index} for i in reversed(route.intersections)])
    return Route.model_validate(data)
