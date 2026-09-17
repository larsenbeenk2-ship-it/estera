"""Private NDJSON protocol with bounded requests, output and mutation ownership."""
import asyncio
import contextlib
import json
from collections import deque
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from . import __version__
from . import adapter, gpx
from .errors import BackendError, public_error
from .models import Connect, Coordinate, DeviceTarget, Identifier, MAX_FRAME, Model, Name, Number, Prepare, Request, Route
from .route import Timeline, reverse_track
from .session import Session
from .storage import Store, data_directory
from .host import HOST_POLICY, check_host


class Empty(Model):
    pass


class DeviceList(Model):
    transport: Literal["auto", "usb", "wifi"] = "usb"


class Hello(Model):
    version: Annotated[int, Field(ge=1, le=1)]


class Fixed(Model):
    coordinate: Coordinate


class RouteInput(Model):
    route: Route


class Speed(Model):
    speed_mps: Annotated[Number, Field(ge=0.01, le=100)]


class Timer(Model):
    seconds: Annotated[Number, Field(gt=0, le=86400)] | None


class Cancel(Model):
    request_id: Identifier


class GPXInput(Model):
    xml: Annotated[str, Field(max_length=5 * 1024 * 1024)]


class GPXImport(GPXInput):
    segment_index: Annotated[int, Field(ge=0)]
    timing: Literal["constant", "recorded"] = "constant"


class Content(Model):
    bucket: Literal["locations", "routes", "location_history", "route_history"]
    item_id: Identifier | None = None
    name: Name | None = None
    payload: dict | None = None
    revision: Annotated[int, Field(ge=0)] | None = None
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 50


PARAMETERS = {
    "hello": Hello, "capabilities": Empty, "host.check": Empty, "status": Empty, "devices.list": DeviceList,
    "device.check": DeviceTarget, "device.prepare": Prepare, "connect": Connect, "disconnect": Empty, "retry": Empty,
    "location.set": Fixed, "route.start": RouteInput, "route.preview": RouteInput, "route.reverse": RouteInput,
    "pause": Empty, "resume": Empty, "speed": Speed, "stop": Empty, "timer": Timer,
    "cancel": Cancel, "shutdown": Empty, "gpx.inspect": GPXInput, "gpx.import": GPXImport, "gpx.export": RouteInput,
    **{f"store.{action}": Content for action in ("list", "get", "put", "delete", "clear")},
}
DEVICE_OPS = {"disconnect", "retry", "location.set", "route.start", "pause", "resume", "speed", "stop", "timer"}
MUTATIONS = DEVICE_OPS | {"connect", "device.check", "device.prepare"}
PREEMPT = {"stop", "disconnect", "shutdown"}
TIMEOUTS = {"host.check": 8, "devices.list": 20, "device.check": 15, "device.prepare": 300, "connect": 355, "retry": 355,
            "stop": 10, "disconnect": 10, "shutdown": 12}
CAPABILITIES = {
    "protocol_version": 1, "backend_version": __version__, "pymobiledevice3": "11.12.4",
    "connection_preparation": True,
    "initial_setup": {"version": 2, "transports": ["usb"], "reuses_developer_files": True},
    "transports": ["usb"], "experimental_transports": [], "bluetooth": "unavailable",
    "wifi": {"usb_bootstrap": False, "reconnect_without_usb": False,
             "discovery": "disabled", "attempt_diagnostics": False},
    "bluetooth_reason": "No direct Bluetooth developer-service provider exists in the pinned backend. Bluetooth PAN access to iPhone developer services requires hardware proof before support can be offered.",
    "location_fields": ["latitude", "longitude"], "phone_observation": False,
    "routing_provider": "frontend_supplied_geometry", "max_points": 50000, "max_gpx_bytes": 5242880,
    "max_frame_bytes": MAX_FRAME, "max_updates_per_second": 5, "operations": sorted(PARAMETERS),
}


def capabilities_for(policy):
    result = deepcopy(CAPABILITIES)
    result.update(host=policy.as_dict(), cable_required=policy.usb_only,
                  recovery={"automatic": policy.automatic_recovery,
                            "restore_location": policy.automatic_recovery,
                            "resume_playback": policy.automatic_recovery,
                            "retry_until_stopped": policy.automatic_recovery})
    return result


def parse_json(line):
    def reject_constant(value):
        raise ValueError("non-finite JSON number")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(line, parse_constant=reject_constant, object_pairs_hook=unique_keys)


class Server:
    def __init__(self, reader, writer, *, directory=None, session_factory=Session, policy=None):
        self.policy = policy or HOST_POLICY
        self.capabilities = capabilities_for(self.policy)
        self.reader, self.writer = reader, writer
        self.responses = asyncio.Queue(maxsize=32)
        self.events = {}  # coalesce by event name; bounded vocabulary, at most 16
        self.output_ready = asyncio.Event()
        self.sequence = 0
        self.session = (Session(self.emit, policy=self.policy) if session_factory is Session else session_factory(self.emit))
        self.session.on_timer = self.timer_stop
        self.store = Store(directory or data_directory())
        self.tasks = {}
        self.recent = deque(maxlen=256)
        self.handshake = False
        self.closing = False
        self.active_mutation = None
        self.mutation_lock = asyncio.Lock()

    def emit(self, event):
        self.sequence += 1
        self.events[event["event"]] = {**event, "sequence": self.sequence}
        self.output_ready.set()

    async def respond(self, value):
        async with asyncio.timeout(5):
            await self.responses.put(value)
        self.output_ready.set()

    async def output(self):
        while True:
            await self.output_ready.wait()
            while not self.responses.empty() or self.events:
                if not self.responses.empty():
                    value = self.responses.get_nowait()
                else:
                    key = min(self.events, key=lambda k: self.events[k]["sequence"])
                    value = self.events.pop(key)
                frame = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
                if len(frame) > MAX_FRAME:
                    frame = json.dumps({"id": value.get("id"), "ok": False, "error": {
                        "code": "response_too_large", "message": "Result exceeds 8 MiB; use smaller content.", "retryable": False}}).encode() + b"\n"
                self.writer.write(frame)
                async with asyncio.timeout(5):
                    await self.writer.drain()
            self.output_ready.clear()

    async def run(self):
        output = asyncio.create_task(self.output())
        try:
            while not self.closing:
                line_task = asyncio.create_task(self.reader.readline())
                done, _ = await asyncio.wait({line_task, output}, return_when=asyncio.FIRST_COMPLETED)
                if output in done:
                    line_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await line_task
                    break
                try:
                    line = line_task.result()
                except (ValueError, asyncio.LimitOverrunError):
                    break  # Oversized framing is fatal; do not interpret a truncated command.
                if not line:
                    break
                if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                    break
                await self.accept(line)
        finally:
            self.closing = True
            self.session.invalidate_recovery()
            if hasattr(self, "on_shutdown"):
                self.on_shutdown()
            for task in list(self.tasks.values()):
                task.cancel()
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
            with contextlib.suppress(Exception):
                async with asyncio.timeout(10):
                    await self.session.disconnect()
            with contextlib.suppress(Exception):
                async with asyncio.timeout(1):
                    while not self.responses.empty() or self.events:
                        if output.done():
                            break
                        await asyncio.sleep(0.01)
                    await self.writer.drain()
            output.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await output

    async def accept(self, line):
        identifier = None
        try:
            raw = parse_json(line)
            request = Request.model_validate(raw)
            identifier = request.id
            if identifier in self.recent or identifier in self.tasks:
                raise BackendError("duplicate_request", "Request IDs must be unique; do not replay device actions.")
            self.recent.append(identifier)
            if request.op not in PARAMETERS:
                raise BackendError("unsupported_operation", "Unknown or unsupported operation.")
            if not self.handshake and request.op != "hello":
                raise BackendError("handshake_required", "Send hello with protocol version 1 before any other operation.")
            try:
                params = PARAMETERS[request.op].model_validate(request.params)
            except ValidationError:
                if request.op == "hello":
                    raise BackendError("protocol_mismatch", "The app and helper must both support protocol version 1.") from None
                raise
            if request.op == "hello":
                self.handshake = True
                await self.respond({"id": identifier, "ok": True, "result": self.capabilities})
                return
            if len(self.tasks) >= 16 and request.op not in PREEMPT | {"cancel"}:
                raise BackendError("busy", "Too many in-flight requests.", True)
            # Validate before preemption: a stale stop must never cancel a new phone's work.
            if request.op in DEVICE_OPS:
                self.session.require(request.session_id, connected=self.connection_required(request.op))
            if request.op in MUTATIONS and request.op not in PREEMPT and self.active_mutation and not self.active_mutation.done():
                raise BackendError("busy", "A device operation is in progress; cancel it or stop first.", True)
            if request.op in PREEMPT:
                self.session.invalidate_recovery()
                await self.preempt()
            task = asyncio.create_task(self.execute(request, params))
            self.tasks[identifier] = task
            if request.op in MUTATIONS | {"shutdown"}:
                self.active_mutation = task
            task.add_done_callback(lambda t, key=identifier: self.tasks.pop(key, None))
        except Exception as exc:
            await self.respond({"id": identifier, "ok": False, "error": self.error(exc)})

    @staticmethod
    def error(exc):
        if isinstance(exc, (ValidationError, ValueError, TypeError, RecursionError, UnicodeError)):
            return BackendError("invalid_request", "Invalid input. Check parameter types, bounds and the protocol schema.").as_dict()
        return public_error(exc).as_dict()

    def connection_required(self, op):
        return op not in {"disconnect", "retry", "stop", "timer"} and not (op == "pause" and self.session.can_pause_offline)

    async def preempt(self):
        task = self.active_mutation
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def timer_stop(self):
        self.session.invalidate_recovery()
        await self.preempt()
        async with self.mutation_lock:
            await self.session.stop()

    async def execute(self, request, params):
        try:
            async with asyncio.timeout(TIMEOUTS.get(request.op, 15)):
                if request.op in MUTATIONS | {"shutdown"}:
                    async with self.mutation_lock:
                        if request.op in DEVICE_OPS:
                            self.session.require(request.session_id, connected=self.connection_required(request.op))
                        result = await self.dispatch(request, params)
                else:
                    result = await self.dispatch(request, params)
            await self.respond({"id": request.id, "ok": True, "result": result})
        except asyncio.CancelledError:
            await self.respond({"id": request.id, "ok": False, "error": BackendError(
                "cancelled", "Operation canceled; an in-flight device write may have reached the phone. Stop/reset to request restoration.").as_dict()})
        except Exception as exc:
            await self.respond({"id": request.id, "ok": False, "error": self.error(exc)})
        finally:
            if request.op == "shutdown":
                self.closing = True
                self.reader.feed_eof()

    async def dispatch(self, request, p):
        op = request.op
        if op == "status":
            # A snapshot carries the event checkpoint it includes. Browser/native
            # clients can reject queued older events after an HTTP/IPC response.
            return {**self.session.status(), "sequence": self.sequence}
        if op == "capabilities":
            return self.capabilities
        if op == "host.check":
            return await check_host(self.policy)
        if op == "devices.list":
            return await adapter.discover(p.transport, policy=self.policy)
        if op in {"device.check", "device.prepare"}:
            if self.session.id is not None:
                self.session.require(request.session_id, connected=False)
                if p.device_id != self.session.device_id:
                    raise BackendError("wrong_device", "Setup must target the iPhone selected for this session.")
                if op == "device.prepare" and (self.session.transport != "failed" or self.session.ever_connected or
                        self.session.requested is not None or self.session.adapter is not None):
                    raise BackendError("session_exists", "Stop and disconnect the existing connection before preparing this iPhone.")
            if op == "device.check":
                return await adapter.check_setup(p.device_id, policy=self.policy)
            return await adapter.prepare(p, self.store.directory, lambda stage, data: self.emit({
                "event": "preparation", "session_id": self.session.id, "route_revision": self.session.route_revision,
                "data": {"request_id": request.id, "stage": stage, **data}}), policy=self.policy)
        if op in {"connect", "retry"}:
            async def prepare_device(device_id):
                return await adapter.prepare(Prepare(device_id=device_id, mount_image=True, allow_download=True),
                    self.store.directory, lambda stage, data: self.emit({
                        "event": "preparation", "session_id": self.session.id, "route_revision": self.session.route_revision,
                        "data": {"request_id": request.id, "stage": stage, **data}}), policy=self.policy)
            if op == "connect":
                result = await self.session.connect(p, prepare_device=prepare_device)
            else:
                result = await self.session.retry(prepare_device=prepare_device)
            return {**result, "status": {**self.session.status(), "sequence": self.sequence}}
        if op in {"disconnect", "pause", "resume", "stop"}:
            return await getattr(self.session, op)()
        if op == "location.set":
            return await self.session.set_location(p.coordinate)
        if op == "route.start":
            return await self.session.start_route(p.route)
        if op == "speed":
            return await self.session.speed(p.speed_mps)
        if op == "timer":
            return await self.session.set_timer(p.seconds)
        if op == "route.preview":
            timeline = Timeline(p.route)
            return {"distance_meters": timeline.distance, "lap_seconds": timeline.duration,
                    "duration_seconds": timeline.total_duration, "route": p.route.model_dump(), "device_affected": False}
        if op == "route.reverse":
            return {"route": reverse_track(p.route).model_dump(), "notice": "Raw track reversed. Road directions were not recalculated; one-way streets may be traversed backward."}
        if op == "gpx.inspect":
            return {"segments": await asyncio.to_thread(gpx.segments, p.xml)}
        if op == "gpx.import":
            route = await asyncio.to_thread(gpx.import_segment, p.xml, p.segment_index, p.timing)
            return {"route": route.model_dump()}
        if op == "gpx.export":
            return {"xml": await asyncio.to_thread(gpx.export, p.route), "notice": "GPX carries geometry and timestamps; save route JSON to preserve all playback settings."}
        if op.startswith("store."):
            action = op.split(".")[1]
            if action == "put" and (p.name is None or p.payload is None):
                raise ValueError("put requires name and payload")
            if action in {"get", "delete"} and p.item_id is None:
                raise ValueError("item_id is required")
            if action in {"put", "delete", "clear"} and p.revision is None:
                raise ValueError("write requires revision")
            return await asyncio.to_thread(self.store.execute, action, **p.model_dump())
        if op == "cancel":
            target = self.tasks.get(p.request_id)
            if target is asyncio.current_task():
                raise ValueError("cannot cancel self")
            if target and not target.done():
                target.cancel()
                await target
                return {"cancelled": True}
            return {"cancelled": False}
        if op == "shutdown":
            return await self.session.disconnect()
        raise BackendError("unsupported_operation", "Unsupported operation.")
