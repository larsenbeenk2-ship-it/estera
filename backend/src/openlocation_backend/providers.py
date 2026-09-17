"""Bounded public map providers for a single-user local beta."""
import asyncio
import copy
import json
import math
import os
import time
from collections import OrderedDict
from typing import Annotated, Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import Field

from .coverage import require_coordinate, require_route
from .errors import BackendError
from .models import DEFAULT_SPEEDS, MAX_POINTS, Coordinate, Intersection, Model, Name, Number, Point, Route, StopAccess, IndexRange, Waypoint, distance
from .road_metadata import driving_controls
from .route_metadata import enrich_route_metadata


class Stop(Coordinate):
    name: Name
    dwell_seconds: Annotated[Number, Field(ge=0, le=86400)] = 0.0
    walk_to_stop: bool = True


class Directions(Model):
    stops: Annotated[list[Stop], Field(min_length=2, max_length=25)]
    mode: Literal['walking', 'cycling', 'driving']


WALKING_BUDGET_SECONDS = 45


def _access_error(stop, detail='Walking directions are temporarily unavailable'):
    return BackendError('walking_access_unavailable',
        f'{stop.name}: {detail}. Recalculate or turn off Walk to this stop to stay by the road.', True)


def _access_leg(decoded, start, end):
    """Reach the exact pin, labeling gaps outside mapped pedestrian geometry."""
    if len(decoded) < 2:
        raise ValueError('The pedestrian route has no usable geometry')
    points, estimated = [Point(latitude=start.latitude, longitude=start.longitude)], []
    if distance(points[-1], decoded[0]) > .000001:
        estimated.append(0)
        points.append(decoded[0])
    points.extend(decoded[1:])
    if distance(points[-1], end) > .000001:
        estimated.append(len(points) - 1)
        points.append(Point(latitude=end.latitude, longitude=end.longitude))
    return points, estimated


async def _walking_access(provider, route_data, stops, *, is_disconnected=None):
    """Start in the car; walk from each destination's road anchor to its pin."""
    paths, point_count = {}, len(route_data['points'])
    current_stop = stops[0]
    try:
        async with asyncio.timeout(WALKING_BUDGET_SECONDS):
            for ordinal, (waypoint, stop) in enumerate(zip(route_data['waypoints'], stops)):
                current_stop = stop
                roadside = route_data['points'][waypoint.point_index]
                if ordinal == 0 or not stop.walk_to_stop or distance(roadside, stop) <= .5:
                    continue
                if is_disconnected is not None and await is_disconnected():
                    raise BackendError('cancelled', 'Route calculation canceled.')
                locations = [roadside, stop] if ordinal == len(stops) - 1 else [roadside, stop, roadside]
                try:
                    try:
                        legs, _ = await provider._route_legs(locations, 'walking')
                    except BackendError as error:
                        if error.code != 'no_route':
                            raise
                        # The car stays at the drivable road anchor even if a
                        # driveway or the final approach is absent from the map.
                        routed = [([Point(latitude=point.latitude, longitude=point.longitude)
                                    for point in (start, end)], [0])
                                  for start, end in zip(locations, locations[1:])]
                    else:
                        routed = []
                        for (leg, osrm), start, end in zip(legs, locations, locations[1:]):
                            decoded, _, _, _ = _decode_leg(leg, osrm, 'walking')
                            routed.append(_access_leg(decoded, start, end))
                    point_count += sum(len(path[0]) - 1 for path in routed)
                    if point_count > MAX_POINTS:
                        raise ValueError('The walking itinerary exceeds 50,000 points')
                    paths[ordinal] = routed
                except BackendError as error:
                    if error.code == 'cancelled':
                        raise
                    raise _access_error(stop) from None
                except ValueError as error:
                    raise _access_error(stop, str(error)) from None
                if is_disconnected is not None and await is_disconnected():
                    raise BackendError('cancelled', 'Route calculation canceled.')
    except TimeoutError:
        raise _access_error(current_stop, 'Walking directions exceeded the 45-second time limit') from None
    return _assemble_walking_access(route_data, stops, paths)


def _assemble_walking_access(route_data, stops, paths):
    """Assemble ordered visits before applying complete-route validation."""
    visits = {}
    for ordinal, waypoint in enumerate(route_data['waypoints']):
        visits.setdefault(waypoint.point_index, []).append((ordinal, waypoint, stops[ordinal]))
    points, modes, speeds, limits, classes, grades, waypoints, road_indices = [], [], [], [], [], [], [], {}

    def append(point, mode='walking', speed=DEFAULT_SPEEDS['walking'], limit=None, road_class='unknown', grade=None):
        if len(points) >= MAX_POINTS:
            raise ValueError('Route exceeds 50,000 points; choose a shorter trip')
        if points:
            modes.append(mode)
            speeds.append(speed)
            limits.append(limit)
            classes.append(road_class)
            grades.append(grade)
        points.append(point)

    def walk(path):
        decoded, estimated = path
        start = len(points) - 1
        for point in decoded[1:]:
            append(point)
        return IndexRange(start_index=start, end_index=len(points) - 1), [start + index for index in estimated]

    for i, road_point in enumerate(route_data['points']):
        current_visits = visits.get(i, [])
        if i == 0:
            append(road_point)
        else:
            append(road_point, 'driving', route_data['segment_speeds_mps'][i - 1],
                   route_data['segment_speed_limits_mps'][i - 1],
                   route_data['segment_road_classes'][i - 1] if route_data['segment_road_classes'] else 'unknown',
                   route_data['segment_grades'][i - 1] if route_data['segment_grades'] else None)
        road_indices[i] = len(points) - 1
        for ordinal, waypoint, stop in current_visits:
            arrival = len(points) - 1
            waypoint_index = arrival
            access = None
            if ordinal > 0 and ordinal in paths:
                walking_to, estimated = walk(paths[ordinal][0])
                waypoint_index = len(points) - 1
                walking_back, departure = None, arrival
                if ordinal < len(stops) - 1:
                    walking_back, returning_estimated = walk(paths[ordinal][1])
                    estimated.extend(returning_estimated)
                    departure = len(points) - 1
                access = StopAccess(roadside_coordinate=Coordinate(latitude=road_point.latitude, longitude=road_point.longitude),
                    arrival_index=arrival, departure_index=departure, walking_to=walking_to,
                    walking_back=walking_back, estimated_segments=estimated)
            waypoints.append(waypoint.model_copy(update={
                'point_index': waypoint_index, 'walk_to_stop': stop.walk_to_stop,
                'requested_coordinate': Coordinate(latitude=stop.latitude, longitude=stop.longitude), 'access': access,
            }))
    if sum(distance(a, b) for a, b in zip(points, points[1:])) > 500_000:
        raise ValueError('The route including walking exceeds the 500 km online limit. Split the itinerary.')
    return {**route_data, 'points': points, 'segment_modes': modes, 'walking_access_version': 1,
        'segment_speeds_mps': speeds, 'segment_speed_limits_mps': limits, 'waypoints': waypoints,
        'segment_road_classes': classes if route_data['segment_road_classes'] else [],
        'segment_grades': grades if route_data['segment_grades'] else [],
        'terrain_source': 'partial' if route_data['terrain_source'] == 'provider' and None in grades else route_data['terrain_source'],
        'intersections': [crossing.model_copy(update={'point_index': road_indices[crossing.point_index]})
                          for crossing in route_data['intersections']]}


def decode_shape(encoded):
    if not isinstance(encoded, str) or len(encoded) > 2_000_000:
        raise ValueError('Invalid routing geometry')
    points, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            result, shift = 0, 0
            while True:
                if index >= len(encoded) or shift > 30:
                    raise ValueError('Invalid routing geometry')
                byte = ord(encoded[index]) - 63
                index += 1
                if not 0 <= byte <= 63:
                    raise ValueError('Invalid routing geometry')
                result |= (byte & 31) << shift
                shift += 5
                if byte < 32:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        points.append(Point(latitude=lat / 1e6, longitude=lon / 1e6))
        if len(points) > 50000:
            raise ValueError('Route exceeds 50,000 points; choose a shorter trip')
    return points


def _append_shape(points, decoded):
    if len(decoded) < 2:
        raise ValueError('A route leg has no usable geometry')
    offset = max(0, len(points) - 1)
    if points and distance(points[-1], decoded[0]) > 1:
        raise ValueError('Routing provider returned disconnected geometry')
    if len(points) + len(decoded) - bool(points) > MAX_POINTS:
        raise ValueError('Route exceeds 50,000 points; choose a shorter trip')
    points.extend(decoded[1:] if points else decoded)
    return offset


def _street_name(value):
    return (value.strip()[:200] or None) if isinstance(value, str) else None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _segment_paces(points, spans, leg, mode, osrm):
    """Use shape timing first, maneuver timing second, then a labeled fallback."""
    lengths = [distance(a, b) for a, b in zip(points, points[1:])]
    speeds, measured, limits = [DEFAULT_SPEEDS[mode]] * len(lengths), [False] * len(lengths), [None] * len(lengths)
    for start, end, seconds in spans:
        if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0):
            raise ValueError('Routing provider returned an invalid maneuver duration')
        if not _number(seconds):
            continue
        meters = sum(lengths[start:end])
        if meters > 0:
            for i in range(start, end):
                speeds[i], measured[i] = meters / seconds, True
    attributes = leg.get('annotation' if osrm else 'shape_attributes') or {}
    if not isinstance(attributes, dict):
        raise ValueError('Routing provider returned invalid speed annotations')
    for key in ('duration' if osrm else 'time', 'speed', 'maxspeed' if osrm else 'speed_limit'):
        values = attributes.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or len(values) != len(lengths):
            raise ValueError('Routing provider speed annotations do not match its geometry')
        for i, value in enumerate(values):
            if key in {'maxspeed', 'speed_limit'}:
                if osrm:
                    if not isinstance(value, dict):
                        raise ValueError('Routing provider returned an invalid speed limit')
                    number, unit = value.get('speed'), value.get('unit')
                    limit = number * (0.44704 if unit == 'mph' else 1 / 3.6) if _number(number) and unit in {'mph', 'km/h'} else None
                else:
                    limit = value / 3.6 if _number(value) and value != 255 else None
                limits[i] = min(100., limit) if limit is not None else None
            else:
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError('Routing provider returned an invalid segment speed or duration')
                if value > 0 and lengths[i] > 0:
                    speeds[i], measured[i] = ((value if osrm else value / 3.6) if key == 'speed' else lengths[i] / value), True
    cap = {'walking': 2.2, 'cycling': 9., 'driving': 38.9}[mode]
    speeds = [max(.01, min(cap, speed, limit if limit is not None else cap)) for speed, limit in zip(speeds, limits)]
    return speeds, limits, measured


def _native_leg(leg, mode):
    points = decode_shape(leg.get('shape'))
    intersections, spans = {}, []
    maneuvers = leg.get('maneuvers', [])
    if not isinstance(maneuvers, list):
        raise ValueError('Routing provider returned invalid maneuvers')
    for maneuver in maneuvers:
        if not isinstance(maneuver, dict):
            raise ValueError('Routing provider returned invalid maneuvers')
        start, end = maneuver.get('begin_shape_index'), maneuver.get('end_shape_index')
        if type(start) is not int or type(end) is not int or not 0 <= start <= end < len(points):
            raise ValueError('Routing provider returned an invalid maneuver span')
        spans.append((start, end, maneuver.get('time')))
        if mode == 'driving':
            maneuver_type = maneuver.get('type')
            if type(maneuver_type) is not int:
                raise ValueError('Routing provider returned an invalid maneuver type')
            # Native directions lack cross-way topology. Only explicit turns and
            # roundabout entry are candidates; never infer stops from a bend.
            if maneuver_type not in {9, 10, 11, 12, 13, 14, 15, 16, 26}:
                continue
            index = maneuver.get('begin_shape_index')
            if type(index) is not int or not 0 <= index < len(points):
                raise ValueError('Routing provider returned an invalid maneuver index')
            names = maneuver.get('street_names', [])
            intersections[index] = _street_name(names[0]) if isinstance(names, list) and names else None
    return points, intersections, 'maneuvers' if mode == 'driving' and maneuvers else 'none', _segment_paces(points, spans, leg, mode, False)


def _osrm_leg(leg, mode, control_candidates=None):
    """Valhalla OSRM steps carry full geometry and leg-relative node indices."""
    steps = leg.get('steps')
    if not isinstance(steps, list) or not steps or len(steps) > MAX_POINTS:
        raise ValueError('Routing provider returned no usable route steps')
    points, spans = [], []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('maneuver'), dict):
            raise ValueError('Routing provider returned an invalid route step')
        if not isinstance(step['maneuver'].get('type'), str):
            raise ValueError('Routing provider returned an invalid maneuver type')
        decoded = decode_shape(step.get('geometry'))
        if step['maneuver'].get('type') == 'arrive':
            # Valhalla adds a two-identical-point arrival step; it is not travel.
            if not points or not decoded or any(distance(points[-1], p) > 1 for p in decoded):
                raise ValueError('Routing provider returned a disconnected arrival')
            continue
        offset = _append_shape(points, decoded)
        spans.append((step, offset, len(points) - 1))
    intersections, topology_complete = {}, True
    for step, start, end in spans:
        nodes = step.get('intersections')
        if nodes is None or nodes == []:
            topology_complete = False
            if step['maneuver'].get('type') in {'turn', 'end of road', 'roundabout', 'rotary', 'roundabout turn'}:
                intersections[start] = _street_name(step.get('name'))
            continue
        if not isinstance(nodes, list) or len(nodes) > MAX_POINTS:
            raise ValueError('Routing provider returned invalid intersections')
        coordinate_indices = None
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError('Routing provider returned an invalid intersection')
            classes = node.get('classes', [])
            if not isinstance(classes, list):
                raise ValueError('Routing provider returned invalid road classes')
            if 'motorway' in classes:
                continue
            bearings = node.get('bearings')
            if not isinstance(bearings, list) or any(type(b) is not int or not 0 <= b < 360 for b in bearings):
                raise ValueError('Routing provider returned invalid intersection bearings')
            crossing = len(set(bearings)) >= 3
            if not crossing and not (mode == 'driving' and control_candidates is not None):
                continue
            if 'in' not in node or 'out' not in node:
                continue
            if any(type(node[k]) is not int or not 0 <= node[k] < len(bearings) for k in ('in', 'out')):
                raise ValueError('Routing provider returned invalid intersection edges')
            location = node.get('location')
            if not isinstance(location, list) or len(location) != 2:
                raise ValueError('Routing provider returned an invalid intersection location')
            coordinate = Coordinate(longitude=location[0], latitude=location[1])
            index = node.get('geometry_index')
            if index is None:
                # Older compatible serializers omit geometry_index. Match only
                # inside this step, so a return visit cannot select an earlier leg.
                if coordinate_indices is None:
                    coordinate_indices = {}
                    for i in range(start, end + 1):
                        key = (round(points[i].longitude, 6), round(points[i].latitude, 6))
                        coordinate_indices.setdefault(key, []).append(i)
                matches = coordinate_indices.get((round(coordinate.longitude, 6), round(coordinate.latitude, 6)), [])
                # Ambiguous revisits need a provider index; do not guess a stop.
                if len(matches) != 1:
                    raise ValueError('Routing provider returned an ambiguous intersection location')
                index = matches[0]
            if type(index) is not int or not start <= index <= end or distance(points[index], coordinate) > 1:
                raise ValueError('Routing provider returned an intersection outside its geometry')
            name = _street_name(step.get('name'))
            if mode == 'driving' and control_candidates is not None:
                # Two-bearing nodes can still carry a mapped stop or signal.
                # Keep them for metadata lookup, not as presumed intersections.
                control_candidates[index] = name
            # Incoming/outgoing edges alone describe a curve, not a crossing.
            if crossing:
                intersections[index] = name
    return points, intersections, 'topology' if topology_complete else 'maneuvers', _segment_paces(
        points, [(start, end, step.get('duration')) for step, start, end in spans], leg, mode, True)


def _decode_leg(leg, osrm, mode, control_candidates=None):
    if not isinstance(leg, dict):
        raise ValueError('Routing provider returned an invalid route leg')
    # The provider can legitimately report only an arrival when two distinct
    # pins snap to the same road anchor. Require explicit zero length AND time,
    # plus real, coincident provider geometry; missing/broken legs stay errors.
    summary = leg if osrm else leg.get('summary', {})
    length_key, time_key = ('distance', 'duration') if osrm else ('length', 'time')
    stationary = (isinstance(summary, dict) and
        all(type(summary.get(key)) in (int, float) and summary[key] == 0 for key in (length_key, time_key)))
    if stationary:
        if osrm:
            steps = leg.get('steps')
            if not isinstance(steps, list) or not steps or len(steps) > MAX_POINTS:
                raise ValueError('Routing provider returned no usable route steps')
            shapes = []
            for step in steps:
                if not isinstance(step, dict) or not isinstance(step.get('maneuver'), dict) or not isinstance(step['maneuver'].get('type'), str):
                    raise ValueError('Routing provider returned an invalid route step')
                shapes.extend(decode_shape(step.get('geometry')))
                if len(shapes) > MAX_POINTS:
                    raise ValueError('Route exceeds 50,000 points; choose a shorter trip')
        else:
            shapes = decode_shape(leg.get('shape'))
        if shapes and all(distance(shapes[0], point) < .01 for point in shapes):
            return [shapes[0], shapes[0]], {}, 'none', ([DEFAULT_SPEEDS[mode]], [None], [False])
    return _osrm_leg(leg, mode, control_candidates) if osrm else _native_leg(leg, mode)


class Providers:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=30, verify=True, trust_env=False, follow_redirects=False,
            headers={'User-Agent': 'OpenLocation-Local-Beta/0.1 (single-user local route planner)',
                     'X-Client-Id': 'openlocation-local-beta'})
        self.locks = {'search': asyncio.Lock(), 'suggest': asyncio.Lock(), 'directions': asyncio.Lock()}
        self.last = {'search': 0., 'suggest': 0., 'directions': 0.}
        self._suggest_task = None
        self.cache = OrderedDict()
        self.search_url = os.environ.get('OPENLOCATION_SEARCH_URL', 'https://nominatim.openstreetmap.org/search')
        self.reverse_url = os.environ.get('OPENLOCATION_REVERSE_URL', urljoin(self.search_url, 'reverse'))
        self.suggest_url = os.environ.get('OPENLOCATION_AUTOCOMPLETE_URL', 'https://photon.komoot.io/api/')
        self.route_url = os.environ.get('OPENLOCATION_ROUTE_URL', 'https://valhalla1.openstreetmap.de/route')
        for url in (self.search_url, self.reverse_url, self.suggest_url, self.route_url):
            parsed = urlparse(url)
            if (not parsed.hostname or parsed.username or parsed.password or
                    (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost'}))):
                raise ValueError('Map providers must use HTTPS or a self-hosted loopback endpoint')

    async def close(self):
        if self._suggest_task is not None:
            self._suggest_task.cancel()
            await asyncio.gather(self._suggest_task, return_exceptions=True)
        await self.client.aclose()

    async def request(self, kind, url, params):
        if kind != 'suggest':
            return await self._request(kind, url, params)
        # Each caller owns and awaits its worker. Canceling an obsolete Photon
        # stream frees the provider for the latest query, including while typing.
        if self._suggest_task is not None:
            self._suggest_task.cancel('superseded')
        task = asyncio.create_task(self._request(kind, url, params))
        self._suggest_task = task
        try:
            return await task
        except asyncio.CancelledError as error:
            if asyncio.current_task().cancelling() or error.args != ('superseded',):
                raise
            raise BackendError('suggestion_superseded', 'A newer place search is running.', True) from None
        finally:
            if self._suggest_task is task:
                self._suggest_task = None

    async def _request(self, kind, url, params):
        key = (kind, url, json.dumps(params, sort_keys=True))
        cacheable = kind != 'suggest'
        if cacheable and key in self.cache:
            self.cache.move_to_end(key)
            return copy.deepcopy(self.cache[key])
        if kind != 'suggest' and self.locks[kind].locked():
            raise BackendError('provider_busy', 'A map request is already running. Try again in a moment.', True)
        async with self.locks[kind]:
            spacing = .25 if kind == 'suggest' else 1.05
            await asyncio.sleep(max(0, spacing - (time.monotonic() - self.last[kind])))
            self.last[kind] = time.monotonic()
            try:
                async with asyncio.timeout(5 if kind == 'suggest' else 35):
                    arguments = {'json': params} if kind == 'directions' else {'params': params}
                    async with self.client.stream('POST' if kind == 'directions' else 'GET', url, **arguments) as response:
                        if response.status_code == 429:
                            raise BackendError('provider_busy', 'The map provider is busy. Wait a moment and try again.', True)
                        if response.status_code >= 400:
                            code = 'no_route' if kind == 'directions' else 'suggestions_unavailable' if kind == 'suggest' else 'search_unavailable'
                            message = ('No route is available for these stops and travel mode. Try closer stops.' if kind == 'directions' else
                                'Place suggestions are unavailable. Continue typing or submit the search.' if kind == 'suggest' else
                                'Place search is unavailable. Try again or choose a point on the map.')
                            raise BackendError(code, message, True)
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > (256 * 1024 if kind == 'suggest' else 8 * 1024 * 1024):
                                raise ValueError('Place suggestions response is too large' if kind == 'suggest' else
                                                 'Map response is too large; choose a shorter route')
                        result = json.loads(data)
            except (httpx.HTTPError, TimeoutError):
                raise BackendError('map_unavailable', 'Could not reach the map provider. Check your internet connection; saved paths remain available.', True) from None
            if cacheable:
                self.cache[key] = result
                while len(self.cache) > 64:
                    self.cache.popitem(last=False)
            return copy.deepcopy(result)

    async def search(self, query):
        query = query.strip()
        if not 2 <= len(query) <= 200:
            raise ValueError('Enter a place, address, or postal code with 2–200 characters')
        values = await self.request('search', self.search_url,
            {'q': query, 'format': 'jsonv2', 'limit': 7, 'addressdetails': 1})
        if not isinstance(values, list) or len(values) > 40:
            raise ValueError('Place search returned an invalid response')
        result = [self.place(value) for value in values]
        return {'places': result, 'attribution': '© OpenStreetMap contributors; Nominatim'}

    async def suggest(self, query):
        query = query.strip()
        if not 3 <= len(query) <= 200:
            raise ValueError('Enter at least 3 characters to find place suggestions')
        value = await self.request('suggest', self.suggest_url, {'q': query, 'limit': 5})
        features = value.get('features') if isinstance(value, dict) and value.get('type') == 'FeatureCollection' else None
        if not isinstance(features, list) or len(features) > 40:
            raise ValueError('Place suggestions returned an invalid response')
        places = []
        for feature in features[:5]:
            try:
                places.append(self.suggestion(feature))
            except ValueError:
                continue
        return {'places': places,
                'attribution': '© OpenStreetMap contributors; Photon'}

    @staticmethod
    def suggestion(feature):
        if not isinstance(feature, dict) or not isinstance(feature.get('properties'), dict):
            raise ValueError('Place suggestions returned an invalid result')
        geometry, properties = feature.get('geometry'), feature['properties']
        coordinates = geometry.get('coordinates') if isinstance(geometry, dict) and geometry.get('type') == 'Point' else None
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            raise ValueError('Place suggestions returned invalid coordinates')
        if any(type(value) not in (int, float) for value in coordinates[:2]):
            raise ValueError('Place suggestions returned invalid coordinates') from None
        longitude, latitude = coordinates[0], coordinates[1]
        if not all(math.isfinite(value) for value in (latitude, longitude)) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError('Place suggestions returned invalid coordinates')
        name = properties.get('name')
        if not isinstance(name, str) or not (name := name.strip()):
            raise ValueError('Place suggestions returned an invalid address')
        def text(key):
            value = properties.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else None
        street, number = text('street'), text('housenumber')
        parts = [f'{number} {street}' if number and street else street or number]
        parts.extend(text(key) for key in ('locality', 'district', 'city', 'county', 'state', 'postcode', 'country'))
        label_parts, seen = [], set()
        for part in parts:
            if part and part.casefold() not in seen:
                label_parts.append(part)
                seen.add(part.casefold())
        kind = text('type') or text('osm_value')
        zooms = {'country': 4, 'state': 6, 'county': 9, 'city': 11, 'town': 13, 'village': 14, 'locality': 14, 'district': 14, 'street': 16, 'house': 18}
        result = {'name': name[:200], 'label': (', '.join(label_parts) or name)[:500],
                  'latitude': latitude, 'longitude': longitude}
        if kind in zooms:
            result['zoom'] = zooms[kind]
        extent = properties.get('extent')
        if isinstance(extent, list) and len(extent) == 4 and all(type(value) in (int, float) for value in extent):
            west, north, east, south = extent
            if all(math.isfinite(value) for value in (west, south, east, north)) and -180 <= west < east <= 180 and -90 <= south < north <= 90:
                result['bounds'] = [west, south, east, north]
        return result

    @staticmethod
    def place(value, coordinate=None):
        if not isinstance(value, dict) or not isinstance(value.get('display_name'), str) or not value['display_name'].strip():
            raise ValueError('Place search returned an invalid address')
        search_result = coordinate is None
        if coordinate is None:
            try:
                coordinate = Coordinate(latitude=float(value['lat']), longitude=float(value['lon']))
            except (KeyError, TypeError, ValueError):
                raise ValueError('Place search returned invalid coordinates') from None
        name = value.get('name')
        address = value.get('address', {})
        if isinstance(address, dict) and address.get('house_number') and address.get('road'):
            name = f"{address['house_number']} {address['road']}"
        zooms = {'country': 4, 'state': 6, 'province': 6, 'county': 9, 'city': 11, 'town': 13, 'village': 14, 'postcode': 13, 'suburb': 14, 'neighbourhood': 15, 'road': 16, 'house': 18, 'building': 18}
        zoom = zooms.get(value.get('addresstype'))
        result = {'name': (name if isinstance(name, str) and name.strip() else value['display_name'].split(',')[0])[:200],
                  'label': value['display_name'][:500], **coordinate.model_dump(), **({'zoom': zoom} if zoom is not None else {})}
        envelope = value.get('boundingbox')
        if search_result and isinstance(envelope, list) and len(envelope) == 4 and all(type(v) in (str, int, float) for v in envelope):
            try:
                south, north, west, east = map(float, envelope)
            except (ValueError, OverflowError):
                pass
            else:
                if all(math.isfinite(v) for v in (south, north, west, east)) and -90 <= south < north <= 90 and -180 <= west < east <= 180:
                    result['bounds'] = [west, south, east, north]
        return result

    async def reverse(self, coordinate: Coordinate):
        require_coordinate(coordinate)
        # Share the search rate limiter: both operations use the same geocoder.
        value = await self.request('search', self.reverse_url,
            {'lat': coordinate.latitude, 'lon': coordinate.longitude, 'format': 'jsonv2', 'zoom': 18, 'addressdetails': 1})
        if isinstance(value, dict) and value.get('error'):
            raise BackendError('address_not_found', 'No nearby address was found. You can still use this map location.')
        # Reverse geocoding returns the nearest indexed object, never a reason
        # to move a user-selected point to that object's center or entrance.
        return self.place(value, coordinate)

    async def _route_legs(self, locations, mode):
        request = {'locations': [{'lat': s.latitude, 'lon': s.longitude, 'type': 'break'} for s in locations],
                   'costing': {'walking': 'pedestrian', 'cycling': 'bicycle', 'driving': 'auto'}[mode],
                   'units': 'kilometers', 'language': 'en-US', 'shape_format': 'polyline6',
                   'format': 'osrm', 'directions_type': 'maneuvers',
                   'filters': {'action': 'include', 'attributes': ['shape_attributes.time', 'shape_attributes.speed', 'shape_attributes.speed_limit']},
                   'costing_options': {
                       'driving': {'auto': {'shortest': False, 'use_distance': 0}},
                       'walking': {'pedestrian': {'walking_speed': DEFAULT_SPEEDS['walking'] * 3.6}},
                       'cycling': {'bicycle': {'bicycle_type': 'city', 'cycling_speed': DEFAULT_SPEEDS['cycling'] * 3.6}},
                   }[mode]}
        data = await self.request('directions', self.route_url, request)
        if not isinstance(data, dict):
            raise ValueError('Routing provider returned an invalid response')
        osrm = 'routes' in data or 'code' in data
        if osrm:
            routes = data.get('routes', [])
            trip = routes[0] if isinstance(routes, list) and routes else {}
            ok = data.get('code') == 'Ok'
            seconds = trip.get('duration', 0) if isinstance(trip, dict) else 0
        else:
            trip = data.get('trip', {})
            ok = isinstance(trip, dict) and type(trip.get('status')) is int and trip['status'] == 0
            summary = trip.get('summary', {}) if isinstance(trip, dict) else {}
            seconds = summary.get('time', 0) if isinstance(summary, dict) else 0
        batch = trip.get('legs', []) if isinstance(trip, dict) else []
        if not ok or not isinstance(batch, list) or len(batch) != len(locations) - 1:
            raise BackendError('no_route', 'No road or path route was found for these stops. Try another mode or move a stop.')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Routing provider returned an invalid duration')
        return [(leg, osrm) for leg in batch], seconds

    async def directions(self, options: Directions, *, is_disconnected=None):
        for stop in options.stops:
            require_coordinate(stop)
        # Keep public demo use modest; no hidden fallback or huge interstate request.
        if sum(distance(a, b) for a, b in zip(options.stops, options.stops[1:])) > 500_000:
            raise ValueError('Online routing is limited to 500 km per itinerary in this beta. Split the trip or import a GPX track.')
        legs, road_seconds = [], 0.0
        # The public FOSSGIS instance accepts at most 10 locations. Split at a
        # shared stop, rate-limit every request, then require continuous geometry.
        # POST also avoids large, percent-encoded query strings being truncated.
        for start in range(0, len(options.stops) - 1, 9):
            if is_disconnected is not None and await is_disconnected():
                raise BackendError('cancelled', 'Route calculation canceled.')
            subset = options.stops[start:start + 10]
            batch, seconds = await self._route_legs(subset, options.mode)
            if is_disconnected is not None and await is_disconnected():
                raise BackendError('cancelled', 'Route calculation canceled.')
            legs.extend(batch)
            road_seconds += seconds
        points, indices, intersections, sources, speeds, speed_limits, measured = [], [], {}, [], [], [], []
        control_candidates = {}
        for leg, osrm in legs:
            if not isinstance(leg, dict):
                raise ValueError('Routing provider returned an invalid route leg')
            leg_candidates = {}
            decoded, crossings, source, pacing = _decode_leg(leg, osrm, options.mode, leg_candidates)
            offset = _append_shape(points, decoded)
            speeds.extend(pacing[0])
            speed_limits.extend(pacing[1])
            measured.extend(pacing[2])
            indices.append(offset)
            if options.mode == 'driving':
                intersections.update({offset + index: name for index, name in crossings.items()})
                control_candidates.update({offset + index: name for index, name in {**crossings, **leg_candidates}.items()})
                sources.append(source)
        indices.append(len(points) - 1)
        if sum(distance(a, b) for a, b in zip(points, points[1:])) > 500_000:
            raise ValueError('The resolved road route is longer than the 500 km online limit. Split the itinerary.')
        speed_limits, road_classes, grades, terrain_source = await enrich_route_metadata(
            self, points, options.mode, speed_limits, decode_shape, is_disconnected=is_disconnected)
        speeds = [min(speed, limit) if limit is not None else speed for speed, limit in zip(speeds, speed_limits)]
        controls, controls_source = ({}, 'none')
        if options.mode == 'driving':
            controls, controls_source = await driving_controls(self, points, control_candidates, decode_shape,
                is_disconnected=is_disconnected)
            for index, control in controls.items():
                if control in {'stop_sign', 'traffic_signal'}:
                    intersections.setdefault(index, control_candidates[index])
            if controls_source == 'provider' and any(source != 'topology' for source in sources):
                controls_source = 'partial'
        route_data = dict(name=f'{options.stops[0].name} to {options.stops[-1].name}'[:200], points=points,
            source='road', mode=options.mode, speed_mps=DEFAULT_SPEEDS[options.mode],
            segment_speeds_mps=speeds, segment_speed_limits_mps=speed_limits,
            segment_road_classes=road_classes, segment_grades=grades, terrain_source=terrain_source,
            speed_source='provider' if all(measured) else 'mixed' if any(measured) else 'fallback',
            realistic_motion=options.mode == 'driving',
            intersections=[Intersection(point_index=i, name=name, control=controls.get(i, 'unknown'))
                           for i, name in sorted(intersections.items()) if 0 < i < len(points) - 1],
            traffic_controls_source=controls_source,
            intersection_source=('topology' if all(s == 'topology' for s in sources) else 'maneuvers' if any(s != 'none' for s in sources) else 'none') if sources else 'none',
            intersection_pause_seconds=3.0 if options.mode == 'driving' else 0.0,
            speed_variation=0.06 if options.mode == 'driving' else 0.0,
            waypoints=[Waypoint(name=s.name, point_index=i, dwell_seconds=s.dwell_seconds,
                                walk_to_stop=s.walk_to_stop,
                                requested_coordinate=Coordinate(latitude=s.latitude, longitude=s.longitude))
                       for s, i in zip(options.stops, indices)])
        if options.mode == 'driving':
            route_data = await _walking_access(self, route_data, options.stops, is_disconnected=is_disconnected)
        route = Route.model_validate(route_data)
        require_route(route)
        return {'route': route.model_dump(), 'provider': 'Valhalla / OpenStreetMap',
                'road_seconds': road_seconds, 'geometry_reused': False}
