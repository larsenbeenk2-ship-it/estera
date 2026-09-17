"""Match Valhalla control flags to the directed road used by a route.

The OSRM route serializer does not expose stop signs. Valhalla's verbose
``locate`` response does, on the incoming directed edge. Its edge geometry
must agree with the route before a nearby sign can affect the simulation.
"""
import asyncio
from urllib.parse import urljoin

from .errors import BackendError
from .models import Coordinate, distance, interpolate


MAX_CONTROL_LOCATIONS = 120
CONTROL_BATCH_SIZE = 10
CONTROL_BUDGET_SECONDS = 12


def _backward_sample(points, end, meters):
    """Return a point this far before an endpoint, and the distance available."""
    remaining = meters
    for i in range(end, 0, -1):
        length = distance(points[i - 1], points[i])
        if length >= remaining and length > 0:
            return interpolate(points[i], points[i - 1], remaining / length), meters
        remaining -= length
    return points[0], meters - remaining


def _same_approach(points, index, shape):
    # Both endpoints and the preceding geometry must agree. Looking only at a
    # junction coordinate would incorrectly include side roads or reverse travel.
    if len(shape) < 2 or distance(shape[-1], points[index]) > 1:
        return False
    far, available = _backward_sample(shape, len(shape) - 1, 20)
    route_far, route_available = _backward_sample(points, index, available)
    if available < 3 or route_available < available - .5 or distance(far, route_far) > 1.5:
        return False
    near, _ = _backward_sample(shape, len(shape) - 1, min(5, available / 2))
    route_near, _ = _backward_sample(points, index, min(5, available / 2))
    return distance(near, route_near) <= 1.5


def _graph_id(value):
    if isinstance(value, dict) and type(value.get('value')) is int and value['value'] >= 0:
        return value['value']
    return None


def control_at_location(value, points, index, decode_shape):
    """Return unknown if metadata is absent, ambiguous, or belongs to another road."""
    if not isinstance(value, dict):
        return 'unknown'
    try:
        requested = Coordinate(latitude=value.get('input_lat'), longitude=value.get('input_lon'))
    except ValueError:
        return 'unknown'
    if distance(requested, points[index]) > 1:
        return 'unknown'
    edges, nodes = value.get('edges'), value.get('nodes')
    if not isinstance(edges, list) or len(edges) > 128 or not isinstance(nodes, list) or len(nodes) > 128:
        return 'unknown'
    node_signals = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = _graph_id(node.get('node_id'))
        signal = node.get('traffic_signal')
        if node_id is not None and type(signal) is bool:
            node_signals[node_id] = signal
    matches = []
    for candidate in edges:
        if not isinstance(candidate, dict):
            continue
        edge, info = candidate.get('edge'), candidate.get('edge_info')
        if not isinstance(edge, dict) or not isinstance(info, dict) or type(edge.get('forward')) is not bool:
            continue
        access = edge.get('access')
        if not isinstance(access, dict) or access.get('car') is not True:
            continue
        try:
            shape = decode_shape(info.get('shape'))
        except ValueError:
            continue
        if not edge['forward']:
            shape.reverse()
        if not _same_approach(points, index, shape):
            continue
        signal = edge.get('traffic_signal')
        stop = edge.get('stop_sign')
        node_signal = node_signals.get(_graph_id(edge.get('end_node')))
        if signal is True or node_signal is True:
            matches.append('traffic_signal')
        elif stop is True:
            matches.append('stop_sign')
        elif signal is False and stop is False and node_signal is False:
            # This means no control was mapped for this approach. OSM may still
            # be incomplete, so the UI must not promise every real-world sign.
            matches.append('uncontrolled')
        else:
            matches.append('unknown')
    return matches[0] if matches and len(set(matches)) == 1 else 'unknown'


async def driving_controls(provider, points, candidates, decode_shape, *, is_disconnected=None):
    """One bounded, cached lookup per ten route nodes; missing data never invents stops."""
    indices = sorted(i for i in candidates if 0 < i < len(points) - 1)
    if not indices:
        return {}, 'none'
    controls = {}
    try:
        async with asyncio.timeout(CONTROL_BUDGET_SECONDS):
            for start in range(0, min(len(indices), MAX_CONTROL_LOCATIONS), CONTROL_BATCH_SIZE):
                if is_disconnected is not None and await is_disconnected():
                    raise BackendError('cancelled', 'Route calculation canceled.')
                batch = indices[start:min(start + CONTROL_BATCH_SIZE, MAX_CONTROL_LOCATIONS)]
                values = await provider.request('directions', urljoin(provider.route_url, 'locate'), {
                    'locations': [{'lat': points[i].latitude, 'lon': points[i].longitude,
                                   'radius': 5, 'search_cutoff': 10} for i in batch],
                    'costing': 'auto', 'verbose': True, 'units': 'kilometers',
                })
                if not isinstance(values, list) or len(values) != len(batch):
                    break
                for index, value in zip(batch, values):
                    control = control_at_location(value, points, index, decode_shape)
                    if control != 'unknown':
                        controls[index] = control
    except BackendError as exc:
        if exc.code == 'cancelled':
            raise
    except (TimeoutError, ValueError):
        pass
    if is_disconnected is not None and await is_disconnected():
        raise BackendError('cancelled', 'Route calculation canceled.')
    source = 'provider' if len(controls) == len(indices) else 'partial' if controls else 'unavailable'
    return controls, source
