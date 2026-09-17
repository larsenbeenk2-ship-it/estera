"""Focused deterministic contract tests. No physical device, network or app launches."""
import asyncio
import json
from contextlib import suppress

import pytest
from pydantic import ValidationError

from openlocation_backend import gpx
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Connect, Coordinate, Point, RemoteConfig, Route, Waypoint, distance
from openlocation_backend.protocol import Server, parse_json
from openlocation_backend.route import Timeline, reverse_track
from openlocation_backend.session import Session
from openlocation_backend.storage import Store


def route(points=None, **kwargs):
    return Route(points=points or [Point(latitude=0., longitude=0.), Point(latitude=0., longitude=.001)], source="drawn", **kwargs)


class Clock:
    now = 0.
    def __call__(self):
        return self.now


class Power:
    def acquire(self):
        pass
    def release(self):
        pass


class Device:
    transport = "usb"
    def __init__(self):
        self.writes = []
        self.clears = 0
        self.fail = False
    async def connect(self, options, progress):
        return {"transport": "usb"}
    async def set(self, coordinate):
        if self.fail:
            raise ConnectionError()
        self.writes.append(coordinate)
    async def clear(self):
        self.clears += 1
    async def close(self):
        pass
    async def wait_lost(self):
        await asyncio.Future()


async def parked_sleep(seconds):
    await asyncio.Future()


async def session():
    clock = Clock()
    device = Device()
    owner = Session(lambda e: None, adapter_factory=lambda: device, clock=clock, sleep=parked_sleep, power=Power())
    await owner.connect(Connect(device_id="test-device"))
    return owner, device, clock


def test_point_spacing_and_antimeridian():
    sparse = Timeline(route(speed_mps=10.))
    dense = Timeline(route(points=[Point(latitude=0., longitude=lon) for lon in (0., .0001, .0002, .0009, .001)], speed_mps=10.))
    for seconds in (0., 1., 3., 7.):
        assert sparse.sample(seconds)["coordinate"]["longitude"] == pytest.approx(dense.sample(seconds)["coordinate"]["longitude"], abs=1e-9)
    crossing = Timeline(route(points=[Point(latitude=0., longitude=179.9), Point(latitude=0., longitude=-179.9)]))
    assert abs(crossing.sample(crossing.duration / 2)["coordinate"]["longitude"]) == pytest.approx(180.)


def test_dwell_speed_recorded_and_closed_loops():
    track = route(speed_mps=10., waypoints=[Waypoint(name="Start", point_index=0, dwell_seconds=2.)])
    t = Timeline(track)
    assert t.sample(1.)["dwelling"]
    t.elapsed = 4.
    point = t.sample()["coordinate"]
    t.set_speed(20.)
    assert t.sample()["coordinate"] == pytest.approx(point)
    timed = route(points=[Point(latitude=0., longitude=0., time=10.), Point(latitude=0., longitude=.001, time=30.)], timing="recorded")
    assert Timeline(timed).duration == 20.
    assert [p.time for p in reverse_track(timed).points] == [10., 30.]
    with pytest.raises(ValueError):
        Timeline(timed).set_speed(2.)
    with pytest.raises(ValidationError):
        route(loop=True)
    loop = Timeline(route(points=[Point(latitude=0., longitude=0.), Point(latitude=0., longitude=.001), Point(latitude=0., longitude=0.)], repeat_count=2))
    assert not loop.sample(loop.duration)["complete"]
    assert loop.sample(loop.duration * 2)["complete"]


async def test_pause_speed_failure_stop_and_stale_session():
    owner, device, clock = await session()
    await owner.start_route(route(speed_mps=10.))
    clock.now = 3.
    await owner.step()
    assert owner.timeline.sample()["distance_meters"] == pytest.approx(30.)
    await owner.pause()
    progress = owner.timeline.elapsed
    clock.now = 100.
    await owner.speed(20.)
    assert owner.timeline.sample()["distance_meters"] == pytest.approx(30.)
    await owner.resume()
    clock.now = 101.
    await owner.step()
    assert owner.timeline.sample()["distance_meters"] == pytest.approx(50.)
    previous = owner.timeline.elapsed
    device.fail = True
    clock.now = 102.
    with pytest.raises(ConnectionError):
        await owner.step()
    assert owner.timeline.elapsed == previous
    await owner._lost(ConnectionError())
    assert owner.playback == "paused"
    assert owner.location_evidence == "unknown"
    with pytest.raises(BackendError, match="replaced"):
        owner.require("wrong-session")
    writes = len(device.writes)
    result = await owner.stop()
    assert not result["restoration_confirmed"]
    assert len(device.writes) == writes
    assert owner.runner is None and owner.recovery is None
    await owner.disconnect()


async def test_stop_preempts_slow_coordinate():
    owner, device, clock = await session()
    await owner.start_route(route())
    started = asyncio.Event()
    async def blocked(coordinate):
        started.set()
        await asyncio.Future()
    device.set = blocked
    owner.runner.cancel()
    with suppress(asyncio.CancelledError):
        await owner.runner
    clock.now = 1.
    owner.runner = asyncio.create_task(owner.step())
    await started.wait()
    result = await owner.stop()
    assert result["clear_sent"] and device.clears == 1
    assert owner.timeline.elapsed == 0.
    await owner.disconnect()


def test_gpx_segments_entities_limits_and_timestamps():
    xml = '<gpx version="1.1"><trk><trkseg><trkpt lat="0" lon="0"/><trkpt lat="0" lon="1"/></trkseg><trkseg><trkpt lat="1" lon="1"/><trkpt lat="2" lon="2"/></trkseg></trk></gpx>'
    assert len(gpx.segments(xml)) == 2
    assert len(gpx.import_segment(xml, 1).points) == 2
    with pytest.raises(ValueError):
        gpx.import_segment(xml, 2)
    with pytest.raises(ValidationError):
        gpx.import_segment(xml, 0, "recorded")
    with pytest.raises(Exception):
        gpx.segments('<!DOCTYPE gpx [<!ENTITY x SYSTEM "file:///etc/passwd">]><gpx>&x;</gpx>')
    with pytest.raises(ValueError):
        gpx.segments(" " * (5 * 1024 * 1024 + 1))
    assert len(gpx.import_segment(gpx.export(route()), 0).points) == 2


def test_store_atomic_revision_and_history(tmp_path):
    store = Store(tmp_path)
    for revision in range(52):
        store.execute("put", "location_history", name="Place", payload={"latitude": 1., "longitude": 2.}, revision=revision)
    result = store.execute("list", "location_history")
    assert result["total"] == 50
    assert result["revision"] == 52
    with pytest.raises(BackendError, match="changed"):
        store.execute("clear", "location_history", revision=0)
    item_id = result["items"][0]["id"]
    assert store.execute("get", "location_history", item_id=item_id)["item"]["payload"]["latitude"] == 1.
    assert tmp_path.joinpath("content.json").stat().st_mode & 0o077 == 0


class Writer:
    def __init__(self):
        self.frames = []
    def write(self, frame):
        self.frames.append(parse_json(frame))
    async def drain(self):
        pass


async def test_protocol_handshake_stale_stop_and_unknown_fields(tmp_path):
    writer = Writer()
    server = Server(asyncio.StreamReader(), writer, directory=tmp_path,
                    session_factory=lambda emit: Session(emit, adapter_factory=Device, power=Power(), sleep=parked_sleep))
    async def send(obj):
        await server.accept(json.dumps(obj).encode())
        await asyncio.gather(*list(server.tasks.values()))
        return server.responses.get_nowait()
    assert (await send({"id": "a", "op": "status"}))["error"]["code"] == "handshake_required"
    assert (await send({"id": "b", "op": "hello", "params": {"version": 2}}))["error"]["code"] == "protocol_mismatch"
    assert (await send({"id": "c", "op": "hello", "params": {"version": 1}}))["ok"]
    assert (await send({"id": "d", "op": "connect", "params": {"device_id": "test-device"}}))["ok"]
    assert (await send({"id": "e", "op": "stop", "session_id": "old"}))["error"]["code"] == "stale_session"
    assert server.session.adapter.clears == 0
    assert (await send({"id": "f", "op": "status", "params": {"shell": "bad"}}))["error"]["code"] == "invalid_request"
    assert (await send({"id": "f", "op": "status"}))["error"]["code"] == "duplicate_request"
    await server.session.disconnect()


def test_strict_coordinates_private_remote_and_tls_patch():
    for value in (float("nan"), float("inf"), True, "1"):
        with pytest.raises(ValidationError):
            Coordinate(latitude=value, longitude=0.)
    with pytest.raises(ValidationError):
        RemoteConfig(enabled=True, address="8.8.8.8", port=1234, pairing_identifier="id")
    with pytest.raises(ValueError):
        parse_json('{"x":1,"x":2}')
    with pytest.raises(ValueError):
        parse_json('{"x":NaN}')
    from pymobiledevice3.restore.tss import TSSRequest
    assert "openlocation_backend.preparation" in TSSRequest.send_receive.__code__.co_names


async def test_eof_cleanup_and_strict_handshake_after_lifecycle_changes(tmp_path):
    reader, writer = asyncio.StreamReader(), Writer()
    server = Server(reader, writer, directory=tmp_path,
                    session_factory=lambda emit: Session(emit, adapter_factory=Device, power=Power(), sleep=parked_sleep))
    await server.accept(b'{"id":"h","op":"hello","params":{"version":true}}\n')
    assert server.responses.get_nowait()["error"]["code"] == "protocol_mismatch"
    await server.session.connect(Connect(device_id="owned-test-device"))
    device = server.session.adapter
    await server.session.set_location(Coordinate(latitude=1., longitude=2.))
    stopped = []
    server.on_shutdown = lambda: stopped.append(True)
    reader.feed_eof()
    await server.run()
    assert stopped and device.clears == 1
    assert server.session.id is None
    assert server.session.runner is None


async def test_read_only_heartbeat_failure_cancels_tunnel_watcher():
    from openlocation_backend.adapter import DeviceAdapter
    adapter = DeviceAdapter()
    canceled = asyncio.Event()
    class Tunnel:
        async def wait_closed(self):
            try:
                await asyncio.Future()
            finally:
                canceled.set()
    adapter.tunnel_client = Tunnel()
    task = asyncio.create_task(adapter.wait_lost())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert canceled.is_set()


def test_extreme_times_speeds_and_tiny_segment_interpolation():
    from openlocation_backend.models import interpolate
    with pytest.raises(ValidationError):
        route(speed_mps=1e-300)
    with pytest.raises(ValidationError):
        Point(latitude=0., longitude=0., time=1e300)
    a = Coordinate(latitude=0., longitude=0.)
    b = Coordinate(latitude=0., longitude=0.0000001)
    assert interpolate(a, b, .5).longitude == pytest.approx(.00000005, abs=1e-12)
    assert interpolate(a, b, 1.) == b
