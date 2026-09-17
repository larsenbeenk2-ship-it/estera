"""Strict boundary models; no coercion of strings or booleans into coordinates."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_POINTS = 50_000
MAX_GPX_BYTES = 5 * 1024 * 1024
MAX_FRAME = 8 * 1024 * 1024
DEFAULT_SPEEDS = {"walking": 1.4, "cycling": 4.2, "driving": 13.4}
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
RoadClass = Literal["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service_other", "ramp", "unknown"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Coordinate(Model):
    latitude: Annotated[Number, Field(ge=-90, le=90)]
    longitude: Annotated[Number, Field(ge=-180, le=180)]


class Point(Coordinate):
    time: Annotated[Number, Field(ge=-62135596800, le=253402300799)] | None = None  # UTC Unix seconds, GPX timing only


class IndexRange(Model):
    start_index: Annotated[int, Field(ge=0, lt=MAX_POINTS)]
    end_index: Annotated[int, Field(ge=0, lt=MAX_POINTS)]

    @model_validator(mode="after")
    def ordered(self):
        if self.end_index <= self.start_index:
            raise ValueError("walking range must contain at least one geometry pair")
        return self


class StopAccess(Model):
    roadside_coordinate: Coordinate
    arrival_index: Annotated[int, Field(ge=0, lt=MAX_POINTS)]
    departure_index: Annotated[int, Field(ge=0, lt=MAX_POINTS)]
    walking_to: IndexRange | None = None
    walking_back: IndexRange | None = None
    estimated_segments: Annotated[list[Annotated[int, Field(ge=0, lt=MAX_POINTS - 1)]], Field(max_length=MAX_POINTS - 1)] = []


class Waypoint(Model):
    name: Name
    point_index: Annotated[int, Field(ge=0, lt=MAX_POINTS)]
    dwell_seconds: Annotated[Number, Field(ge=0, le=86400)] = 0.0
    walk_to_stop: bool = True
    requested_coordinate: Coordinate | None = None
    access: StopAccess | None = None


class Intersection(Model):
    point_index: Annotated[int, Field(gt=0, lt=MAX_POINTS - 1)]
    name: Name | None = None
    control: Literal["stop_sign", "traffic_signal", "uncontrolled", "unknown"] = "unknown"


class Route(Model):
    name: Name = "Untitled route"
    points: Annotated[list[Point], Field(min_length=2, max_length=MAX_POINTS)]
    waypoints: Annotated[list[Waypoint], Field(max_length=1000)] = []
    intersections: Annotated[list[Intersection], Field(max_length=MAX_POINTS - 2)] = []
    intersection_source: Literal["topology", "maneuvers", "none"] = "none"
    traffic_controls_source: Literal["provider", "partial", "unavailable", "none"] = "none"
    stop_policy: Literal["mapped", "all"] = "mapped"
    intersection_pause_seconds: Annotated[Number, Field(ge=0, le=60)] = 0.0
    driving_speed_mode: Literal["adaptive", "manual"] = "adaptive"
    speed_variation: Annotated[Number, Field(ge=0, le=0.5)] = 0.0
    segment_speeds_mps: Annotated[list[Annotated[Number, Field(ge=0.01, le=100)]], Field(max_length=MAX_POINTS - 1)] = []
    segment_speed_limits_mps: Annotated[list[Annotated[Number, Field(ge=0.01, le=100)] | None], Field(max_length=MAX_POINTS - 1)] = []
    segment_modes: Annotated[list[Literal["walking", "cycling", "driving"]], Field(max_length=MAX_POINTS - 1)] = []
    segment_road_classes: Annotated[list[RoadClass], Field(max_length=MAX_POINTS - 1)] = []
    segment_grades: Annotated[list[Annotated[Number, Field(ge=-1, le=1)] | None], Field(max_length=MAX_POINTS - 1)] = []
    terrain_source: Literal["none", "provider", "partial", "unavailable"] = "none"
    speed_source: Literal["none", "provider", "mixed", "fallback"] = "none"
    realistic_motion: bool = False
    mode: Literal["walking", "cycling", "driving"] = "walking"
    source: Literal["mapkit", "road", "gpx", "drawn", "reversed_track"]
    speed_mps: Annotated[Number, Field(ge=0.01, le=100)] = 1.4
    timing: Literal["constant", "recorded"] = "constant"
    repeat_count: Annotated[int, Field(ge=1, le=10000)] = 1
    loop: bool = False
    completion: Literal["hold", "clear"] = "hold"
    walking_access_version: Annotated[int, Field(ge=0, le=1)] = 0

    @model_validator(mode="after")
    def valid_path(self):
        if any(values and len(values) != len(self.points) - 1 for values in (
                self.segment_speeds_mps, self.segment_speed_limits_mps, self.segment_modes,
                self.segment_road_classes, self.segment_grades)):
            raise ValueError("segment metadata must match every consecutive geometry pair")
        if self.segment_modes and (self.mode != 'driving' or self.timing != 'constant' or 'cycling' in self.segment_modes):
            raise ValueError("mixed travel modes require constant-timing driving with driving or walking segments")
        if any(w.point_index >= len(self.points) for w in self.waypoints):
            raise ValueError("waypoint index is outside geometry")
        if len({w.point_index for w in self.waypoints}) != len(self.waypoints):
            raise ValueError("use one waypoint/dwell per geometry index")
        for waypoint in self.waypoints:
            access = waypoint.access
            if access is None:
                continue
            if self.mode != 'driving' or not waypoint.walk_to_stop or not self.segment_modes:
                raise ValueError("stop access requires mixed driving/walking geometry and an enabled stop")
            if not 0 <= access.arrival_index <= access.departure_index < len(self.points):
                raise ValueError("roadside indices are outside or out of order in geometry")
            if any(distance(self.points[index], access.roadside_coordinate) > .5
                   for index in (access.arrival_index, access.departure_index)):
                raise ValueError("roadside indices must identify the parked car coordinate")
            ranges = [span for span in (access.walking_to, access.walking_back) if span is not None]
            if not ranges:
                raise ValueError("stop access requires a walking range")
            if waypoint.requested_coordinate is not None and distance(
                    self.points[waypoint.point_index], waypoint.requested_coordinate) > .01:
                raise ValueError("walking stop must reach the exact requested pin")
            if access.walking_to is not None and (access.walking_to.start_index != access.arrival_index
                    or access.walking_to.end_index != waypoint.point_index):
                raise ValueError("walking-to range must connect roadside arrival to the stop")
            if access.walking_back is not None and (access.walking_back.start_index != waypoint.point_index
                    or access.walking_back.end_index != access.departure_index):
                raise ValueError("walking-back range must connect the stop to roadside departure")
            walking_segments = set()
            for span in ranges:
                if span.end_index >= len(self.points):
                    raise ValueError("walking range is outside geometry")
                indices = range(span.start_index, span.end_index)
                if any(self.segment_modes[index] != 'walking' for index in indices):
                    raise ValueError("walking ranges must contain only walking segments")
                walking_segments.update(indices)
            if len(set(access.estimated_segments)) != len(access.estimated_segments) or any(
                    index not in walking_segments
                    for index in access.estimated_segments):
                raise ValueError("estimated access segments must be within the stop's walking ranges")
        if any(i.point_index >= len(self.points) - 1 for i in self.intersections):
            raise ValueError("intersection index must be inside the route, excluding endpoints")
        if len({i.point_index for i in self.intersections}) != len(self.intersections):
            raise ValueError("use one intersection per geometry index")
        if sum(distance(a, b) for a, b in zip(self.points, self.points[1:])) < 0.01:
            raise ValueError("route needs at least 1 cm of usable travel")
        if self.timing == "recorded":
            if self.intersection_pause_seconds or self.speed_variation or self.realistic_motion or self.segment_speeds_mps or self.segment_speed_limits_mps:
                raise ValueError("recorded timing requires simulated motion, road speeds, and intersection pauses to be disabled")
            times = [p.time for p in self.points]
            if any(t is None for t in times) or any(b <= a for a, b in zip(times, times[1:])):
                raise ValueError("recorded timing requires strictly increasing timestamps on every point")
            if any(distance(a, b) / (b.time - a.time) > 100 for a, b in zip(self.points, self.points[1:])):
                raise ValueError("recorded timing exceeds 100 m/s")
        if (self.loop or self.repeat_count > 1) and distance(self.points[0], self.points[-1]) > 0.5:
            raise ValueError("repeating requires a closed track; preview and supply a return path first")
        return self


class RemoteConfig(Model):
    enabled: bool
    address: Annotated[str, Field(min_length=1, max_length=100)]
    port: Annotated[int, Field(ge=1, le=65535)]
    pairing_identifier: Identifier

    @model_validator(mode="after")
    def private_address(self):
        import ipaddress
        if not self.enabled:
            raise ValueError("remote mode requires explicit opt-in")
        ip = ipaddress.ip_address(self.address)
        allowed = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
        if not any(ip in ipaddress.ip_network(n) for n in allowed):
            raise ValueError("remote requires an explicit private VPN IP literal")
        return self


class Connect(Model):
    device_id: Identifier
    transport: Literal["auto", "usb", "wifi", "remote"] = "usb"
    remote: RemoteConfig | None = None
    prepare: bool = False

    @model_validator(mode="after")
    def gated_remote(self):
        if (self.transport == "remote") != (self.remote is not None):
            raise ValueError("remote configuration is required only for remote transport")
        return self


class DeviceTarget(Model):
    device_id: Identifier


class Prepare(DeviceTarget):
    pair: bool = False
    reveal_developer_mode: bool = False
    enable_wifi: bool = False
    mount_image: bool = False
    allow_download: bool = False


class Request(Model):
    id: Identifier
    op: Annotated[str, Field(min_length=1, max_length=40)]
    params: dict = {}
    session_id: Identifier | None = None


def distance(a: Coordinate, b: Coordinate) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.atan2(math.sqrt(min(1, h)), math.sqrt(max(0, 1 - h)))


def interpolate(a: Coordinate, b: Coordinate, fraction: float) -> Coordinate:
    """Great-circle interpolation including the antimeridian and polar paths."""
    def vector(p):
        lat, lon = math.radians(p.latitude), math.radians(p.longitude)
        return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
    av, bv = vector(a), vector(b)
    angle = math.acos(max(-1, min(1, sum(x * y for x, y in zip(av, bv)))))
    if fraction <= 0:
        return Coordinate(latitude=a.latitude, longitude=a.longitude)
    if fraction >= 1:
        return Coordinate(latitude=b.latitude, longitude=b.longitude)
    if angle < 1e-8:
        delta_lon = (b.longitude - a.longitude + 180) % 360 - 180
        return Coordinate(latitude=a.latitude + (b.latitude - a.latitude) * fraction,
                          longitude=(a.longitude + delta_lon * fraction + 180) % 360 - 180)
    if math.pi - angle < 1e-8:
        # An antipodal pair has no unique great circle; require an intermediate point.
        raise ValueError("antipodal endpoints need an intermediate point")
    w1, w2 = math.sin((1 - fraction) * angle) / math.sin(angle), math.sin(fraction * angle) / math.sin(angle)
    x, y, z = (w1 * x + w2 * y for x, y in zip(av, bv))
    return Coordinate(latitude=math.degrees(math.atan2(z, math.hypot(x, y))), longitude=math.degrees(math.atan2(y, x)))
