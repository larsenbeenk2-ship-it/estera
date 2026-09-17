"""Deterministic Windows recovery contracts; no device, sockets, or OS calls."""
import asyncio
from contextlib import suppress

import pytest

from openlocation_backend.errors import BackendError
from openlocation_backend.host import HostPolicy
from openlocation_backend.models import Connect, Coordinate, Point, Route, Waypoint
from openlocation_backend.session import Session

WINDOWS = HostPolicy("windows", "x64")
MAC = HostPolicy("macos", "arm64")
A = Coordinate(latitude=1., longitude=2.)
B = Coordinate(latitude=3., longitude=4.)


class Clock:
    now = 0.

    def __call__(self):
        return self.now


class Power:
    suspended = False

    def __init__(self):
        self.active = False
        self.callback = None

    def acquire(self):
        self.active = True

    def release(self):
        self.active = False

    def watch(self, callback):
        self.callback = callback

    def unwatch(self):
        self.callback = None

    def change(self, suspended):
        self.suspended = suspended
        self.callback(suspended)


class Sleeper:
    def __init__(self):
        self.pending = asyncio.Queue()
        self.delays = []

    async def __call__(self, seconds):
        if seconds == .2:
            await asyncio.Future()
        future = asyncio.get_running_loop().create_future()
        self.delays.append(seconds)
        self.pending.put_nowait((seconds, future))
        await future

    async def advance(self):
        async with asyncio.timeout(1):
            while True:
                seconds, future = await self.pending.get()
                if not future.done():
                    future.set_result(None)
                    await spin()
                    return seconds


async def spin():
    for _ in range(12):
        await asyncio.sleep(0)


class Device:
    transport = "usb"

    def __init__(self, *, connect_error=None):
        self.connect_error = connect_error
        self.writes = []
        self.clears = 0
        self.closed = 0
        self.options = []
        self.lost = asyncio.Event()
        self.set_hook = self.connect_hook = None

    async def connect(self, options, progress):
        self.options.append(options)
        if self.connect_hook:
            await self.connect_hook()
        if self.connect_error:
            raise self.connect_error
        return {"transport": "usb", "device_id": options.device_id}

    async def set(self, coordinate):
        if self.set_hook:
            await self.set_hook(coordinate)
        self.writes.append(coordinate)

    async def clear(self):
        self.clears += 1

    async def close(self):
        self.closed += 1

    async def wait_lost(self):
        await self.lost.wait()


def route(**kwargs):
    return Route(points=[Point(latitude=0., longitude=0.), Point(latitude=0., longitude=.01)],
                 source="drawn", speed_mps=10., **kwargs)


class Harness:
    def __init__(self, *, policy=WINDOWS, devices=None):
        self.clock, self.sleep, self.power = Clock(), Sleeper(), Power()
        self.devices = devices or [Device(), Device()]
        self.opened = []
        self.events = []
        self.present = True
        self.probes = []
        self.owner = Session(self.events.append, policy=policy, adapter_factory=self.factory,
                             clock=self.clock, sleep=self.sleep, power=self.power, presence_probe=self.probe)

    def factory(self):
        device = self.devices[len(self.opened)] if len(self.opened) < len(self.devices) else Device()
        self.opened.append(device)
        return device

    async def probe(self, device_id):
        self.probes.append(device_id)
        return self.present

    async def connect(self):
        await self.owner.connect(Connect(device_id="same-phone"))
        return self.owner

    async def lose(self):
        await self.owner._lost(ConnectionError())
        await spin()

    async def recover(self):
        for _ in range(40):
            if self.owner.transport == "connected":
                return
            await self.sleep.advance()
        pytest.fail("Recovery did not connect within the scripted attempts")

    async def close(self):
        await self.owner.disconnect()
        await spin()


@pytest.mark.parametrize("intent", ["fixed", "playing", "paused", "completed", "empty"])
async def test_restore_last_success_and_playback_intent(intent):
    h = Harness()
    owner = await h.connect()
    try:
        if intent == "fixed":
            await owner.set_location(A)
        elif intent != "empty":
            await owner.start_route(route())
            h.clock.now = 2.
            await owner.step()
            if intent == "paused":
                await owner.pause()
            if intent == "completed":
                h.clock.now = 1000.
                await owner.step()
        coordinate = owner.last_write["coordinate"] if owner.last_write else None
        elapsed = owner.timeline.elapsed if owner.timeline else None
        sid = owner.id
        await h.lose()
        h.clock.now = 2000.
        await h.recover()
        assert owner.id == sid and owner.device_id == "same-phone"
        assert all(probe == "same-phone" for probe in h.probes)
        assert all(d.options[0].transport == "usb" for d in h.opened)
        assert [c.model_dump() for c in h.opened[-1].writes] == ([coordinate] if coordinate else [])
        assert owner.playback == {"playing": "playing", "paused": "paused", "completed": "holding",
                                  "fixed": "holding", "empty": "uncertain"}[intent]
        if elapsed is not None:
            assert owner.timeline.elapsed == elapsed
        assert (owner.runner is not None and not owner.runner.done()) is (intent == "playing")
        event = next(e["data"] for e in reversed(h.events) if e["event"] == "reconnected")
        assert event["restored"] is (intent != "empty")
        assert event["resumed"] is (intent == "playing")
        assert not event["resume_required"]
        if intent == "playing":
            runner = owner.runner
            await spin()
            assert owner.runner is runner
            h.clock.now = 2001.
            await owner.step()
            assert owner.timeline.elapsed == elapsed + 1.
    finally:
        await h.close()


async def test_missing_usb_polling_and_more_than_six_failed_connections():
    devices = [Device()] + [Device(connect_error=BackendError("unlock_required", "Unlock phone.")) for _ in range(8)] + [Device()]
    h = Harness(devices=devices)
    owner = await h.connect()
    try:
        await owner.set_location(A)
        h.present = False
        await h.lose()
        assert [await h.sleep.advance() for _ in range(5)] == [1, 2, 5, 5, 5]
        assert len(h.opened) == 1
        h.present = True
        await h.recover()
        assert len(h.opened) == 10
        assert max(h.sleep.delays) == 30
        assert h.opened[-1].writes == [A]
        assert all(d.options[0].device_id == "same-phone" for d in h.opened)
    finally:
        await h.close()


@pytest.mark.parametrize("old_route,new_route", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_unsuccessful_replacement_preserves_committed_intent(old_route, new_route, cancelled):
    h = Harness()
    owner = await h.connect()
    try:
        if old_route:
            await owner.start_route(route())
            h.clock.now = 2.
            await owner.step()
        else:
            await owner.set_location(A)
        previous = owner.last_write["coordinate"].copy()
        timeline = owner.timeline
        revision = owner.route_revision
        entered = asyncio.Event()
        async def reject(_):
            entered.set()
            if cancelled:
                await asyncio.Future()
            raise ConnectionError()
        h.opened[0].set_hook = reject
        operation = asyncio.create_task(owner.start_route(route(name="Replacement")) if new_route else owner.set_location(B))
        await entered.wait()
        if cancelled:
            operation.cancel()
        with pytest.raises(asyncio.CancelledError if cancelled else ConnectionError):
            await operation
        assert owner.timeline is timeline and owner.route_revision == revision
        await h.recover()
        assert h.opened[-1].writes[0].model_dump() == previous
        assert owner.playback == ("playing" if old_route else "holding")
    finally:
        await h.close()


async def test_failed_first_write_does_not_restore_uncommitted_request():
    h = Harness()
    owner = await h.connect()
    try:
        async def reject(_):
            raise ConnectionError()
        h.opened[0].set_hook = reject
        with pytest.raises(ConnectionError):
            await owner.start_route(route())
        assert owner.timeline is None and owner.committed is None
        await h.recover()
        assert not h.opened[-1].writes and owner.runner is None
    finally:
        await h.close()


async def test_interrupted_tick_is_not_committed_even_if_device_swallows_cancellation():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.start_route(route(waypoints=[Waypoint(name="Wait", point_index=0, dwell_seconds=30.)]))
        h.clock.now = 2.
        await owner.step()
        before = owner.timeline.elapsed
        entered = asyncio.Event()
        async def swallow(_):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return
        h.opened[0].set_hook = swallow
        await owner._halt()
        h.clock.now = 8.
        owner.runner = asyncio.create_task(owner.step())
        await entered.wait()
        await h.lose()
        assert owner.timeline.elapsed == before
        h.clock.now = 4000.
        await h.recover()
        assert owner.timeline.elapsed == before and owner.playback == "playing"
        assert owner.timeline.sample()["dwelling"]
    finally:
        await h.close()


async def test_offline_pause_changes_pending_auto_resume_intent():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.start_route(route())
        await h.lose()
        assert owner.can_pause_offline
        await owner.pause()
        assert owner.status()["recovery"]["action"] == "keep_paused"
        assert not owner.can_pause_offline
        await h.recover()
        assert owner.playback == "paused" and owner.runner is None
    finally:
        await h.close()


@pytest.mark.parametrize("terminal", ["stop", "disconnect"])
async def test_terminal_action_blocks_late_connect_even_if_cancellation_is_swallowed(terminal):
    h = Harness()
    owner = await h.connect()
    try:
        await owner.start_route(route())
        entered = asyncio.Event()
        async def swallow():
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return
        h.devices[1].connect_hook = swallow
        await h.lose()
        await h.sleep.advance()
        await entered.wait()
        await getattr(owner, terminal)()
        await spin()
        assert h.devices[1].closed and not h.devices[1].writes
        assert owner.runner is None and owner.recovery is None
        assert owner.transport != "connected"
        assert owner.committed is None and owner.stop_latched
    finally:
        if owner.id:
            await h.close()


async def test_stop_blocks_late_restore_commit_and_runner():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.start_route(route())
        entered = asyncio.Event()
        async def swallow(_):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return
        h.devices[1].set_hook = swallow
        await h.lose()
        await h.sleep.advance()
        await entered.wait()
        await owner.stop()
        assert owner.runner is None and owner.recovery is None
        assert owner.committed is None and owner.stop_latched
        assert owner.location_evidence == "unknown"
        assert not any(e["event"] == "reconnected" for e in h.events)
    finally:
        await h.close()


async def test_explicit_retry_restores_but_retry_after_stop_never_restores():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.set_location(A)
        await h.lose()
        restored = await owner.retry()
        assert restored["restored"] and h.opened[-1].writes == [A]
        await h.lose()
        await owner.stop()
        restored = await owner.retry()
        assert not restored["restored"] and not h.opened[-1].writes
        assert owner.runner is None and owner.stop_latched
    finally:
        await h.close()


async def test_original_stop_deadline_wins_before_usb_restore():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.start_route(route())
        await owner.set_timer(10.)
        await h.lose()
        deadline = owner.timer_deadline
        h.clock.now = 11.
        # Trigger the recovery deadline guard without delivering the timer's
        # sleep first: this models the first scheduled task after waking.
        with pytest.raises(asyncio.CancelledError):
            owner._check_owned(owner.generation)
        await spin()
        assert deadline == 10. and owner.stop_latched
        assert owner.committed is None and owner.runner is None
        assert len(h.opened) == 1
    finally:
        await h.close()


async def test_suspend_and_wake_freeze_route_and_only_resume_once():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.start_route(route())
        h.clock.now = 2.
        await owner.step()
        h.power.change(True)
        await spin()
        elapsed = owner.timeline.elapsed
        assert owner.transport == "reconnecting"
        assert owner.runner is None and len(h.opened) == 1
        h.clock.now = 3000.
        h.power.change(False)
        await h.recover()
        assert owner.timeline.elapsed == elapsed
        assert owner.playback == "playing" and owner.runner is not None
        runner = owner.runner
        h.power.change(False)
        await spin()
        assert owner.runner is runner
        h.clock.now = 3001.
        await owner.step()
        assert owner.timeline.elapsed == elapsed + 1.
    finally:
        await h.close()


async def test_suspend_during_pending_reconnect_restarts_wait_on_wake():
    h = Harness(devices=[Device(), Device(), Device()])
    owner = await h.connect()
    try:
        await owner.set_location(A)
        entered = asyncio.Event()
        async def blocked():
            entered.set()
            await asyncio.Future()
        h.devices[1].connect_hook = blocked
        await h.lose()
        await h.sleep.advance()
        await entered.wait()
        h.power.change(True)
        await spin()
        assert h.devices[1].closed and not h.devices[1].writes
        h.power.change(False)
        await h.recover()
        assert h.opened[-1].writes == [A]
    finally:
        await h.close()


async def test_mac_recovery_remains_bounded_and_requires_manual_resume():
    h = Harness(policy=MAC)
    owner = await h.connect()
    try:
        await owner.start_route(route())
        await h.lose()
        await h.recover()
        assert owner.playback == "paused" and owner.runner is None
        assert not h.opened[-1].writes and not h.probes
        event = next(e["data"] for e in reversed(h.events) if e["event"] == "reconnected")
        assert event["resume_required"] and not event["restored"]
    finally:
        await h.close()
    h = Harness(policy=MAC, devices=[Device()] + [Device(connect_error=ConnectionError()) for _ in range(6)])
    owner = await h.connect()
    try:
        await h.lose()
        assert [await h.sleep.advance() for _ in range(6)] == [1, 2, 4, 8, 16, 30]
        assert owner.transport == "failed" and len(h.opened) == 7
    finally:
        await h.close()


async def test_new_explicit_route_after_stop_rearms_connection_monitor():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.set_location(A)
        await owner.stop()
        await owner.start_route(route())
        h.opened[0].lost.set()
        await spin()
        assert owner.transport == "reconnecting"
        await h.recover()
        assert owner.playback == "playing" and owner.runner is not None
    finally:
        await h.close()


async def test_captured_adapter_close_cannot_detach_or_close_new_connection():
    h = Harness()
    owner = await h.connect()
    try:
        old = owner.adapter
        entered, release = asyncio.Event(), asyncio.Event()
        await owner._cancel(owner.monitor)
        async def delayed_monitor():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                entered.set()
                await release.wait()
        owner.monitor = asyncio.create_task(delayed_monitor())
        await spin()
        closing = asyncio.create_task(owner._close_adapter())
        await entered.wait()
        replacement = Device()
        owner.adapter = replacement
        new_monitor = asyncio.create_task(replacement.wait_lost())
        owner.monitor = new_monitor
        release.set()
        await closing
        assert old.closed == 1 and replacement.closed == 0
        assert owner.adapter is replacement and owner.monitor is new_monitor
    finally:
        await h.close()


async def test_complete_suspend_epoch_before_callbacks_blocks_tick_commit():
    h = Harness()
    owner = await h.connect()
    try:
        await owner.start_route(route())
        h.clock.now = 2.
        await owner.step()
        elapsed = owner.timeline.elapsed
        h.power.suspend_epoch = 1
        h.power.suspended = False
        h.clock.now = 3000.
        with pytest.raises(asyncio.CancelledError):
            await owner.step()
        assert owner.timeline.elapsed == elapsed
        await h.recover()
        assert owner.timeline.elapsed == elapsed and owner.playback == "playing"
    finally:
        await h.close()


async def test_failed_established_retry_returns_to_automatic_waiting():
    h = Harness(devices=[Device(), Device(connect_error=ConnectionError()), Device()])
    owner = await h.connect()
    try:
        await owner.set_location(A)
        await h.lose()
        with pytest.raises(ConnectionError):
            await owner.retry()
        assert owner.transport == "reconnecting" and owner.recovery is not None
        await h.recover()
        assert h.opened[-1].writes == [A]
    finally:
        await h.close()


async def test_initial_connection_cannot_revive_session_after_synchronous_invalidation():
    h = Harness()
    owner = h.owner
    entered, release = asyncio.Event(), asyncio.Event()
    async def swallow():
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            return
    h.devices[0].connect_hook = swallow
    connecting = asyncio.create_task(h.connect())
    await entered.wait()
    owner.invalidate_recovery()
    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connecting
    await owner.stop()
    assert owner.stop_latched and not owner.ever_connected
    assert owner.transport == "failed"
    assert owner.adapter is None and h.devices[0].closed
    assert not h.devices[0].writes
    await h.close()
