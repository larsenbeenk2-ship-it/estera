"""Bounded attributes for the exact directed Valhalla route, never nearby roads.

Valhalla's trace_attributes edge_walk accepts its own unsimplified route shape.
Only identical, ordered polyline6 coordinates may receive the returned edge
spans. A changed shape, ambiguous span or missing attribute stays unknown.
"""
import asyncio
import math
from urllib.parse import urljoin

from .errors import BackendError
from .models import MAX_POINTS


TRACE_BATCH_POINTS = 12_501
MAX_TRACE_REQUESTS = 4
TRACE_BUDGET_SECONDS = 8
ROAD_CLASSES = frozenset({'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
                          'unclassified', 'residential', 'service_other'})


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _coordinate_key(point):
    return round(point.latitude * 1e6), round(point.longitude * 1e6)


def trace_segments(value, points, decode_shape, *, terrain):
    """Return validated segment attributes, or None when geometry is untrusted."""
    if not isinstance(value, dict) or value.get('units', 'kilometers') != 'kilometers':
        return None
    try:
        shape = decode_shape(value.get('shape'))
    except ValueError:
        return None
    if len(shape) != len(points) or any(_coordinate_key(a) != _coordinate_key(b)
                                       for a, b in zip(shape, points)):
        return None
    edges = value.get('edges')
    if not isinstance(edges, list) or len(edges) > MAX_POINTS:
        return None
    limits, classes, grades = [None] * (len(points) - 1), ['unknown'] * (len(points) - 1), [None] * (len(points) - 1)
    previous_end = 0
    for edge in edges:
        if not isinstance(edge, dict):
            return None
        start, end = edge.get('begin_shape_index'), edge.get('end_shape_index')
        if (type(start) is not int or type(end) is not int or
                not previous_end <= start <= end < len(points)):
            # Overlapping, reversed, or out-of-range spans cannot identify a
            # unique directed edge. Do not resolve them by road proximity.
            return None
        previous_end = end
        limit = edge.get('speed_limit')
        # Zero/255/unlimited mean no finite mapped limit. edge.speed is a
        # routing estimate and must never be promoted to a posted limit.
        limit = limit / 3.6 if _finite(limit) and 0 < limit < 255 else None
        road_class = edge.get('road_class')
        road_class = ('ramp' if edge.get('use') == 'ramp' else
                      road_class if isinstance(road_class, str) and road_class in ROAD_CLASSES else 'unknown')
        grade = None
        if terrain:
            weighted, upward, downward = (edge.get(key) for key in
                                          ('weighted_grade', 'max_upward_grade', 'max_downward_grade'))
            # Valhalla emits weighted_grade even without elevation. Its maximum
            # slopes are 32768 when elevation is absent, so require both valid
            # availability markers before accepting even a zero weighted grade.
            # weighted_grade is an edge-level, quantized percentage estimate;
            # the maximum slopes are degrees and are not used as segment grades.
            if (_finite(weighted) and -100 <= weighted <= 100 and
                    _finite(upward) and 0 <= upward <= 90 and
                    _finite(downward) and -90 <= downward <= 0):
                grade = weighted / 100
        for i in range(start, end):
            # Identical adjacent points have no meaningful travel direction.
            if _coordinate_key(points[i]) != _coordinate_key(points[i + 1]):
                limits[i], classes[i], grades[i] = limit, road_class, grade
    return limits, classes, grades


async def enrich_route_metadata(provider, points, mode, existing_limits, decode_shape, *, is_disconnected=None):
    """At most four cached/rate-limited requests and eight seconds in total."""
    count = len(points) - 1
    if not 1 <= count < MAX_POINTS or len(existing_limits) != count:
        raise ValueError('Route metadata does not match its geometry')
    limits, classes, grades = list(existing_limits), ['unknown'] * count, [None] * count
    terrain = mode in {'walking', 'cycling'}
    attributes = ['shape', 'edge.begin_shape_index', 'edge.end_shape_index',
                  'edge.speed_limit', 'edge.road_class', 'edge.use']
    if terrain:
        attributes.extend(['edge.weighted_grade', 'edge.max_upward_grade', 'edge.max_downward_grade'])
    try:
        async with asyncio.timeout(TRACE_BUDGET_SECONDS):
            for request_number, start in enumerate(range(0, count, TRACE_BATCH_POINTS - 1)):
                if request_number == MAX_TRACE_REQUESTS:
                    break
                if is_disconnected is not None and await is_disconnected():
                    raise BackendError('cancelled', 'Route calculation canceled.')
                batch = points[start:start + TRACE_BATCH_POINTS]
                value = await provider.request('directions', urljoin(provider.route_url, 'trace_attributes'), {
                    'shape': [{'lat': point.latitude, 'lon': point.longitude} for point in batch],
                    'shape_match': 'edge_walk', 'shape_format': 'polyline6', 'units': 'kilometers',
                    'costing': {'walking': 'pedestrian', 'cycling': 'bicycle', 'driving': 'auto'}[mode],
                    'filters': {'action': 'include', 'attributes': attributes},
                })
                if is_disconnected is not None and await is_disconnected():
                    raise BackendError('cancelled', 'Route calculation canceled.')
                result = trace_segments(value, batch, decode_shape, terrain=terrain)
                if result is None:
                    break
                batch_limits, batch_classes, batch_grades = result
                for i, limit in enumerate(batch_limits, start):
                    if limit is not None:
                        limits[i] = min(limits[i], limit) if limits[i] is not None else limit
                classes[start:start + len(batch_classes)] = batch_classes
                grades[start:start + len(batch_grades)] = batch_grades
    except BackendError as exc:
        if exc.code == 'cancelled':
            raise
    except (TimeoutError, ValueError):
        pass
    if is_disconnected is not None and await is_disconnected():
        raise BackendError('cancelled', 'Route calculation canceled.')
    known = sum(grade is not None for grade in grades)
    terrain_source = ('provider' if known == count else 'partial' if known else 'unavailable') if terrain else 'none'
    return limits, classes, grades if terrain else [], terrain_source
