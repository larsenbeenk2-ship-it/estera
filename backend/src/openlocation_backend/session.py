"""One transport owner and one coordinate writer. Nothing resumes at startup."""
import asyncio
import contextlib
import time
import uuid
from datetime import datetime, timezone

from . import adapter as device_adapter
from .adapter import DeviceAdapter
from .errors import BackendError, public_error
from .host import HOST_POLICY
from .models import Coordinate
from .power import PowerAssertion
from .route import Timeline


class Session:
    def __init__(self, emit, *, adapter_factory=None, clock=time.monotonic, sleep=asyncio.sleep,
                 power=None, policy=None, presence_probe=None):
        self.emit = emit
        self.policy = policy if policy is not None else HOST_POLICY
        self.adapter_factory = adapter_factory or (lambda: DeviceAdapter(policy=self.policy))
        self.presence_probe = presence_probe
        self.clock, self.sleep = clock, sleep
        self.power = power if power is not None else PowerAssertion()
        self.id = None
        self.device_id = None
        self.options = None
        self.adapter = None
        self.transport = "disconnected"
        self.playback = "idle"
        self.route_revision = 0
        self.timeline = None
        self.last_contact = None
        self.last_write = None
        self.requested = None
        self.location_evidence = "unknown"
        self.runner = self.monitor = self.recovery = self.timer = None
        self.write_lock = asyncio.Lock()
        self.last_tick = self.clock()
        self.connection_error = None
        self.last_connection_loss = None
        self.ever_connected = False
        self.setup_complete = False
        self.connection_attempts = []
        self.generation = 0
        self.stop_latched = False
        # Only a successful write replaces this snapshot. Pending location/route
        # requests cannot erase the last acknowledged location or its route intent.
        self.committed = None
        self.recovery_state = "idle"
        self.timer_deadline = None
        self._timer_generation = 0
        self._power_epoch = getattr(self.power, "suspend_epoch", 0)
        self._suspended = False
        self._awake = asyncio.Event()
        self._awake.set()

    def event(self, event="state", data=None):
        self.emit({"event": event, "session_id": self.id, "route_revision": self.route_revision,
                   "data": self.status() if data is None else data})

    @property
    def recovery_action(self):
        if not self.policy.automatic_recovery or self.stop_latched or not self.committed:
            return "none"
        if self.committed["playback"] == "playing":
            return "resume_route"
        if self.committed["playback"] == "paused":
            return "keep_paused"
        return "restore_location"

    @property
    def can_pause_offline(self):
        return (self.policy.automatic_recovery and self.recovery_action == "resume_route"
                and self.transport in {"reconnecting", "connecting", "failed"})

    def status(self):
        return {"session_id": self.id, "device_id": self.device_id, "transport_state": self.transport,
                "transport": self.adapter.transport if self.adapter else None, "playback_state": self.playback,
                "route_revision": self.route_revision, "last_contact": self.last_contact,
                "requested_coordinate": self.requested, "last_transport_write": self.last_write,
                "location_evidence": self.location_evidence, "phone_observation": "not_observed",
                "requested_transport": self.options.transport if self.options else None,
                "connection_error": self.connection_error, "ever_connected": self.ever_connected,
                "last_connection_loss": self.last_connection_loss,
                "setup_complete": self.setup_complete,
                "connection_path": getattr(self.adapter, "connection_path", None),
                "connection_attempts": list(getattr(self.adapter, "connection_attempts", self.connection_attempts)),
                "route": self.timeline.sample() if self.timeline else None,
                "recovery": {"state": self.recovery_state, "action": self.recovery_action}}

    def require(self, session_id, connected=True):
        if self.id is None or session_id != self.id:
            raise BackendError("stale_session", "This action belongs to a replaced or missing device session.")
        if connected and (self.adapter is None or self.transport != "connected"):
            raise BackendError("not_connected", "Reconnect the selected iPhone before this action.", True)

    def invalidate_recovery(self):
        """Synchronous barrier: call before awaiting cancellation for a terminal action."""
        self.generation += 1
        self.stop_latched = True
        self.committed = None
        self.recovery_state = "idle"

    def _owned(self, generation, session_id=None):
        return (generation == self.generation and self.id is not None
                and (session_id is None or session_id == self.id))

    def _check_owned(self, generation, *, writing=False):
        task = asyncio.current_task()
        if not self._owned(generation) or (task is not None and task.cancelling()):
            raise asyncio.CancelledError()
        if writing or self.policy.automatic_recovery:
            if self.timer_deadline is not None and self.clock() >= self.timer_deadline and not self.stop_latched:
                self.invalidate_recovery()
                # Invalidate immediately even if the timer task has not been
                # scheduled yet after wake. It will own normal Stop cleanup.
                self._timer_generation += 1
                if self.timer is not None:
                    self.timer.cancel()
                self.timer = asyncio.create_task(self._expire_timer())
                raise asyncio.CancelledError()
        if writing and self.stop_latched:
            raise asyncio.CancelledError()
        if self.policy.automatic_recovery and getattr(self.power, "suspend_epoch", 0) != self._power_epoch:
            self._power_changed(True)
            if not getattr(self.power, "suspended", False):
                self._power_changed(False)
            raise asyncio.CancelledError()
        if self.policy.automatic_recovery and (self._suspended or getattr(self.power, "suspended", False)):
            if not self._suspended:
                self._power_changed(True)
            raise asyncio.CancelledError()

    async def connect(self, options, *, prepare_device=None):
        if self.id is not None:
            raise BackendError("session_exists", "Disconnect the existing session before selecting another device.")
        self.policy.ensure_supported()
        transport = self.policy.normalize_transport(options.transport)
        # Older Auto requests resolve to USB and never authorize wireless setup.
        if transport != options.transport:
            options = options.model_copy(update={"transport": transport})
        self.generation += 1
        generation = self.generation
        self.stop_latched = False
        self.id, self.device_id = uuid.uuid4().hex, options.device_id
        self.options = options
        self.connection_attempts = []
        self.connection_error = None
        self.last_connection_loss = None
        self.ever_connected = False
        self.setup_complete = False
        self.transport, self.playback = "connecting", "idle"
        self.event()
        try:
            if self.policy.automatic_recovery and hasattr(self.power, "watch"):
                self.power.watch(self._power_changed)
            result = await self._connect_adapter(prepare_device, generation)
            self._check_owned(generation)
            self._connected(generation)
            self.event()
            return {**result, "session_id": self.id}
        except BaseException as exc:
            await self._connection_failed(exc, generation)
            raise

    async def _connect_adapter(self, prepare_device, generation):
        if self.options.prepare and not self.ever_connected and not self.setup_complete:
            if prepare_device is None:
                raise BackendError("preparation_required", "Prepare developer services before connecting this iPhone.")
            async with asyncio.timeout(300):
                prepared = await prepare_device(self.device_id)
            self._check_owned(generation)
            self.setup_complete = prepared.get("setup_complete") is True
            self.event()
        self._check_owned(generation)
        adapter = self.adapter_factory()
        self.adapter = adapter
        try:
            async with asyncio.timeout(45):
                result = await adapter.connect(self.options, self._progress)
            self._check_owned(generation)
            return result
        except BaseException:
            await self._close_adapter(adapter)
            raise

    async def _connection_failed(self, exc, generation):
        if not self._owned(generation):
            return
        await self._close_adapter()
        if not self._owned(generation):
            return
        self.transport = "failed"
        self.recovery_state = "needs_attention" if self.policy.automatic_recovery else "idle"
        self.connection_error = (public_error(exc).as_dict() if not isinstance(exc, asyncio.CancelledError) else
                                 BackendError("cancelled", "Connection attempt canceled.", True).as_dict())
        self.event()

    def _connected(self, generation):
        self.transport = "connected"
        self.connection_error = None
        self.ever_connected = True
        self.contact()
        self.monitor = asyncio.create_task(self._watch(self.id, self.adapter, generation))

    def _progress(self, stage, data):
        self.event("progress", {"stage": stage, **data})

    def contact(self):
        self.last_contact = datetime.now(timezone.utc).isoformat()

    async def _cancel(self, task):
        if task and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _halt(self):
        runner, self.runner = self.runner, None
        await self._cancel(runner)

    async def _write(self, coordinate, generation=None):
        generation = self.generation if generation is None else generation
        self._check_owned(generation, writing=True)
        adapter = self.adapter
        requested = coordinate.model_dump()
        self.requested = requested
        try:
            async with self.write_lock:
                self._check_owned(generation, writing=True)
                if adapter is None or adapter is not self.adapter or self.transport not in {"connected", "reconnecting", "connecting"}:
                    raise ConnectionError()
                async with asyncio.timeout(5):
                    await adapter.set(coordinate)
                # Some upstream coroutines swallow cancellation. Their late
                # result must not commit a tick, replace a snapshot, or resume.
                self._check_owned(generation, writing=True)
                if adapter is not self.adapter:
                    raise asyncio.CancelledError()
        except BaseException:
            if self._owned(generation):
                self.location_evidence = "unknown"
            raise
        self.contact()
        self.last_write = {"coordinate": requested.copy(), "at": self.last_contact}
        self.location_evidence = "set_result_received"

    def _commit(self, coordinate, timeline, playback):
        self.timeline = timeline
        self.playback = playback
        self.committed = {"coordinate": coordinate.model_dump(), "timeline": timeline,
                          "playback": playback, "revision": self.route_revision}

    def _new_intent(self):
        # A fresh user location/route after Stop is a new explicit intent. Retry
        # and automatic reconnect never call this method.
        if self.stop_latched and self.adapter is not None and self.transport == "connected":
            if self.monitor is not None:
                self.monitor.cancel()
            self.monitor = asyncio.create_task(self._watch(self.id, self.adapter, self.generation))
        self.stop_latched = False
        return self.generation

    async def set_location(self, coordinate):
        generation = self._new_intent()
        await self._halt()
        self._check_owned(generation, writing=True)
        self.playback = "applying"
        self.power.acquire()
        self.event()
        try:
            await self._write(coordinate, generation)
        except BaseException as exc:
            if self._owned(generation):
                await self._lost(exc)
            raise
        self.route_revision += 1
        self._commit(coordinate, None, "holding")
        self.event()
        return {"evidence": "set_result_received", "phone_observation": "not_observed"}

    async def start_route(self, route):
        timeline = Timeline(route)
        generation = self._new_intent()
        await self._halt()
        self._check_owned(generation, writing=True)
        self.power.acquire()
        self.playback = "applying"
        self.event()
        coordinate = Coordinate(**timeline.sample()["coordinate"])
        try:
            await self._write(coordinate, generation)
        except BaseException as exc:
            if self._owned(generation):
                await self._lost(exc)
            raise
        self.route_revision += 1
        self._commit(coordinate, timeline, "playing")
        self.last_tick = self.clock()
        self._start_runner(generation)
        self.event()
        return self.status()

    def _start_runner(self, generation):
        if self.runner is None or self.runner.done():
            self.runner = asyncio.create_task(self._run(self.id, self.route_revision, generation))

    async def _run(self, session_id, revision, generation=None):
        generation = self.generation if generation is None else generation
        try:
            while self.playback == "playing" and self._owned(generation, session_id) and self.route_revision == revision:
                await self.sleep(0.2)
                self._check_owned(generation, writing=True)
                if self.playback != "playing" or self.route_revision != revision:
                    return
                await self.step()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._owned(generation, session_id):
                await self._lost(exc)

    async def step(self):
        """Advance only after a successful write; failed writes never commit progress."""
        generation = self.generation
        self._check_owned(generation, writing=True)
        if self.timeline is None or self.playback != "playing" or self.transport != "connected":
            return
        timeline = self.timeline
        now = self.clock()
        elapsed = timeline.elapsed + max(0, now - self.last_tick)
        if timeline.total_duration is not None:
            elapsed = min(elapsed, timeline.total_duration)
        sample = timeline.sample(elapsed)
        coordinate = Coordinate(**sample["coordinate"])
        await self._write(coordinate, generation)
        timeline.elapsed = elapsed
        self.last_tick = now
        self._commit(coordinate, timeline, "holding" if sample["complete"] else "playing")
        if sample["complete"] and timeline.route.completion == "clear":
            await self.stop()
        self.event("playback")

    async def pause(self):
        if self.can_pause_offline:
            self.committed["playback"] = "paused"
            self.playback = "paused"
            self.event()
            return self.status()
        if self.playback != "playing":
            raise BackendError("invalid_state", "Only a playing route can be paused.")
        generation = self.generation
        await self._halt()
        self._check_owned(generation, writing=True)
        self.playback = "paused"
        if self.committed:
            self.committed["playback"] = "paused"
        self.event()
        return self.status()

    async def resume(self):
        if self.playback != "paused" or self.timeline is None:
            raise BackendError("invalid_state", "Only a paused route can resume.")
        generation = self.generation
        self._check_owned(generation, writing=True)
        self.power.acquire()
        coordinate = Coordinate(**self.timeline.sample()["coordinate"])
        try:
            await self._write(coordinate, generation)
        except BaseException as exc:
            if self._owned(generation):
                await self._lost(exc)
            raise
        self.last_tick = self.clock()
        self._commit(coordinate, self.timeline, "playing")
        self._start_runner(generation)
        self.event()
        return self.status()

    async def speed(self, speed):
        if not self.timeline or self.playback not in {"playing", "paused"}:
            raise BackendError("invalid_state", "Speed changes require a playing or paused route.")
        playing = self.playback == "playing"
        if self.timeline.route.timing != "constant":
            raise BackendError("invalid_state", "Recorded timing uses original timestamps; select constant timing to change speed.")
        generation = self.generation
        await self._halt()
        self._check_owned(generation, writing=True)
        self.timeline.set_speed(speed)
        self.route_revision += 1
        if self.committed:
            self.committed["revision"] = self.route_revision
        self.last_tick = self.clock()
        if playing:
            self._start_runner(generation)
        self.event()
        return self.status()

    async def stop(self):
        self.invalidate_recovery()
        self._timer_generation += 1
        self.timer_deadline = None
        initial = not self.ever_connected and self.requested is None
        self.playback = "stopping"
        await self._halt()
        recovery, self.recovery = self.recovery, None
        await self._cancel(recovery)
        timer, self.timer = self.timer, None
        await self._cancel(timer)
        if initial:
            if self.transport in {"connecting", "reconnecting"}:
                self.transport = "failed"
            self.playback = "idle"
            self.power.release()
            self.event()
            return {"clear_sent": False, "restoration_confirmed": False,
                    "message": "Connection attempt ended. No location command was sent."}
        self.event()
        cleared = False
        try:
            adapter = self.adapter
            if adapter is not None and self.transport == "connected":
                async with self.write_lock:
                    async with asyncio.timeout(5):
                        await adapter.clear()
                self.contact()
                cleared = True
                self.location_evidence = "clear_sent"
            else:
                self.location_evidence = "unknown"
        except Exception:
            self.location_evidence = "unknown"
            self.transport = "failed"
        finally:
            if not cleared:
                self.location_evidence = "unknown"
            if self.transport in {"reconnecting", "connecting"}:
                self.transport = "failed"
            self.power.release()
            self.playback = "stopped" if cleared else "uncertain"
            self.event()
        return {"clear_sent": cleared, "restoration_confirmed": False,
                "message": "Clear sent; physical location reacquisition is not independently confirmed." if cleared else
                    "Restoration could not be confirmed. Reconnect and stop/reset; if needed restart the iPhone."}

    async def _watch(self, session_id, adapter=None, generation=None):
        adapter = self.adapter if adapter is None else adapter
        generation = self.generation if generation is None else generation
        try:
            await adapter.wait_lost()
            if self._owned(generation, session_id) and self.adapter is adapter:
                await self._lost(ConnectionError())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._owned(generation, session_id) and self.adapter is adapter:
                await self._lost(exc)

    def _begin_loss(self, exc):
        if self.transport in {"reconnecting", "disconnected", "failed"}:
            return None
        should_recover = not self.stop_latched and self.playback not in {"stopping", "stopped"}
        self.generation += 1  # Freeze in-flight ticks before the first await.
        self.transport = "reconnecting" if should_recover else "failed"
        if self.policy.automatic_recovery and self.committed and should_recover:
            self.timeline = self.committed["timeline"]
            self.route_revision = self.committed["revision"]
            self.playback = "paused" if self.timeline else "uncertain"
        else:
            self.playback = "paused" if self.timeline and should_recover else "uncertain"
        self.recovery_state = "waiting_usb" if self.policy.automatic_recovery and should_recover else "idle"
        self.location_evidence = "unknown"
        self.connection_error = public_error(exc).as_dict()
        # Recovery may fail for a different reason. Preserve the original loss,
        # with class names and public errors only: no upstream text or addresses.
        self.last_connection_loss = {
            "at": datetime.now(timezone.utc).isoformat(),
            **(getattr(self.adapter, "loss_diagnostics", None) or {"source": "operation"}),
            "exception_type": type(exc).__name__,
            "error": self.connection_error,
        }
        self.power.release()
        self.event("connection_lost", {"message": "Connection lost — phone location unknown.", "last_contact": self.last_contact,
                                      "error": self.connection_error, "diagnostics": self.last_connection_loss})
        self.event()
        return self.generation if should_recover else None

    async def _lost(self, exc):
        generation = self._begin_loss(exc)
        await self._halt()
        if generation is not None and self._owned(generation) and not self.stop_latched:
            if not self.recovery or self.recovery.done():
                self.recovery = asyncio.create_task(self._recover(self.id, generation))

    def _power_changed(self, suspended):
        if not self.policy.automatic_recovery:
            return
        self._power_epoch = getattr(self.power, "suspend_epoch", 0)
        self._suspended = bool(suspended)
        if suspended:
            self._awake.clear()
            if self.id is not None and self.ever_connected and not self.stop_latched:
                generation = self._begin_loss(ConnectionError())
                if generation is None and self.transport == "reconnecting":
                    self.generation += 1
                    generation = self.generation
                if generation is not None:
                    previous = self.recovery
                    self.recovery = asyncio.create_task(self._recover_after_cancel(previous, self.id, generation))
        else:
            self.last_tick = self.clock()
            self._awake.set()

    async def _close_adapter(self, adapter=None):
        adapter = self.adapter if adapter is None else adapter
        monitor = None
        # Detach both resources before awaiting the monitor: cancellation may
        # allow another operation to install a newer connection in the meantime.
        if adapter is self.adapter:
            if adapter is not None:
                self.connection_attempts = list(getattr(adapter, "connection_attempts", []))
            self.adapter = None
            monitor, self.monitor = self.monitor, None
        await self._cancel(monitor)
        if adapter:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(5):
                    await adapter.close()

    async def _usb_present(self):
        if self.presence_probe is not None:
            return await self.presence_probe(self.device_id)
        # Injected devices may supply the read-only probe. No fake adapter ever
        # needs to reach real discovery unless the caller explicitly uses it.
        probe = getattr(self, "_recovery_probe", None)
        if probe is not None:
            return await probe(self.device_id)
        return await device_adapter.usb_present(self.device_id, policy=self.policy)

    async def _restore(self, generation):
        self._check_owned(generation)
        restored = resumed = False
        if self.policy.automatic_recovery and self.committed and not self.stop_latched:
            self.recovery_state = "restoring"
            self.event()
            snapshot = self.committed
            self.power.acquire()
            await self._write(Coordinate(**snapshot["coordinate"]), generation)
            # Offline Pause is permitted while this write is pending.
            self.timeline = snapshot["timeline"]
            self.route_revision = snapshot["revision"]
            self.playback = snapshot["playback"]
            self.last_tick = self.clock()
            restored = True
            resumed = self.playback == "playing"
            if resumed:
                self._start_runner(generation)
        self.recovery_state = "idle"
        return {"resume_required": not self.policy.automatic_recovery,
                "restored": restored, "resumed": resumed, "phone_location": "unknown"}

    async def _recover_after_cancel(self, previous, session_id, generation):
        await self._cancel(previous)
        if self._owned(generation, session_id) and not self.stop_latched:
            await self._recover(session_id, generation)

    async def _recover(self, session_id, generation=None):
        generation = self.generation if generation is None else generation
        if not self._owned(generation, session_id) or self.stop_latched:
            return
        original = self.adapter
        if original is not None and hasattr(original, "usb_present"):
            self._recovery_probe = original.usb_present
        await self._halt()
        if not self._owned(generation, session_id) or self.stop_latched:
            return
        await self._close_adapter(original)
        missing, failures = 0, 0
        try:
            while self._owned(generation, session_id) and not self.stop_latched:
                if self.policy.automatic_recovery:
                    await self._awake.wait()
                    self._check_owned(generation)
                    if self.recovery_state != "waiting_usb":
                        self.recovery_state = "waiting_usb"
                        self.event()
                    delay = (1, 2, 5)[min(missing, 2)]
                else:
                    if failures >= 6:
                        break
                    delay = (1, 2, 4, 8, 16, 30)[failures]
                await self.sleep(delay)
                self._check_owned(generation)
                if self.policy.automatic_recovery:
                    try:
                        present = await self._usb_present()
                        self._check_owned(generation)
                        if not present:
                            missing += 1
                            continue
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        error = public_error(exc).as_dict()
                        if self.connection_error != error:
                            self.connection_error = error
                            self.event()
                        missing += 1
                        continue
                    missing = 0
                    if failures:
                        self.recovery_state = "needs_attention"
                        self.event()
                        await self.sleep(min(30, 2 ** min(failures, 5)))
                        self._check_owned(generation)
                    self.recovery_state = "connecting"
                    self.event()
                adapter = self.adapter_factory()
                self.adapter = adapter
                try:
                    async with asyncio.timeout(45):
                        await adapter.connect(self.options, self._progress)
                    self._check_owned(generation)
                    result = await self._restore(generation)
                    self._check_owned(generation)
                    self._connected(generation)
                    self.event("reconnected", result)
                    self.event()
                    return
                except asyncio.CancelledError:
                    await self._close_adapter(adapter)
                    raise
                except Exception as exc:
                    self.connection_error = public_error(exc).as_dict()
                    self.location_evidence = "unknown"
                    self.power.release()
                    await self._close_adapter(adapter)
                    if not self._owned(generation, session_id):
                        return
                    failures += 1
                    if self.policy.automatic_recovery:
                        self.recovery_state = "needs_attention"
                        self.event()
                    elif not self.connection_error["retryable"]:
                        break
            if self._owned(generation, session_id) and not self.stop_latched:
                self.transport = "failed"
                self.event()
        except asyncio.CancelledError:
            raise

    async def retry(self, *, prepare_device=None):
        if self.transport not in {"failed", "reconnecting"}:
            raise BackendError("invalid_state", "Retry is available after a connection failure.")
        self.generation += 1
        generation = self.generation
        recovery, self.recovery = self.recovery, None
        await self._cancel(recovery)
        self._check_owned(generation)
        await self._close_adapter()
        self._check_owned(generation)
        self.transport = "connecting"
        self.recovery_state = "connecting" if self.policy.automatic_recovery else "idle"
        self.connection_error = None
        self.connection_attempts = []
        self.event()
        try:
            result = await self._connect_adapter(prepare_device, generation)
            restored = await self._restore(generation)
            self._check_owned(generation)
            self._connected(generation)
            self.event("reconnected", restored)
            self.event()
            return {**result, **restored}
        except BaseException as exc:
            await self._connection_failed(exc, generation)
            if (self.policy.automatic_recovery and self.ever_connected
                    and self._owned(generation) and not self.stop_latched):
                self.transport = "reconnecting"
                self.recovery = asyncio.create_task(self._recover(self.id, generation))
                self.event()
            raise

    async def _expire_timer(self):
        self.invalidate_recovery()
        if hasattr(self, "on_timer"):
            await self.on_timer()
        else:
            await self.stop()

    async def set_timer(self, seconds):
        self._timer_generation += 1
        timer_generation = self._timer_generation
        timer, self.timer = self.timer, None
        await self._cancel(timer)
        self.timer_deadline = None if seconds is None else self.clock() + seconds
        if seconds is not None:
            session_id = self.id
            async def expire():
                await self.sleep(seconds)
                if self.id == session_id and self._timer_generation == timer_generation:
                    await self._expire_timer()
            self.timer = asyncio.create_task(expire())
        return {"stop_after_seconds": seconds}

    async def disconnect(self):
        self.invalidate_recovery()
        result = await self.stop()
        await self._close_adapter()
        if hasattr(self.power, "unwatch"):
            self.power.unwatch()
        self.transport = "disconnected"
        self.id = self.device_id = self.options = None
        self.timeline = None
        self.last_write = self.requested = self.last_contact = None
        self.connection_error = None
        self.ever_connected = False
        self.last_connection_loss = None
        self.setup_complete = False
        self.connection_attempts = []
        self._recovery_probe = None
        self.event()
        return result
