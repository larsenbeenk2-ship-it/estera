"""One private helper child for the loopback web application."""
import asyncio
import contextlib
import json
import sys
import uuid

from .errors import BackendError
from .models import MAX_FRAME
from .protocol import TIMEOUTS

CLOSE_GRACE_SECONDS = 10
CLOSE_REAP_SECONDS = 2


class HelperClient:
    def __init__(self):
        self.process = None
        self.pending = {}
        self.listeners = set()
        self.writer_lock = asyncio.Lock()
        self.close_lock = asyncio.Lock()
        self.reader_task = None

    async def start(self):
        arguments = ['--helper'] if getattr(sys, 'frozen', False) else ['-m', 'openlocation_backend']
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, *arguments,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_FRAME + 1)
        self.reader_task = asyncio.create_task(self.read())
        await self.call('hello', {'version': 1})

    def publish(self, event):
        for queue in tuple(self.listeners):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                if len(line) > MAX_FRAME or not line.endswith(b'\n'):
                    raise ValueError('Helper emitted an invalid frame')
                value = json.loads(line)
                if 'event' in value:
                    self.publish(value)
                elif (future := self.pending.get(value.get('id'))) is not None and not future.done():
                    future.set_result(value)
        finally:
            error = BackendError('helper_unavailable', 'The device helper stopped. Quit and reopen Estera, or restart the local server. Phone location is unknown.', True)
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(error)
            self.publish({'event': 'helper_unavailable', 'data': error.as_dict()})

    async def call(self, op, params=None, session_id=None, request_id=None):
        if not self.process or self.process.returncode is not None or (self.reader_task and self.reader_task.done()):
            raise BackendError('helper_unavailable', 'The device helper is unavailable. Restart the local server.', True)
        if len(self.pending) >= 12 and op not in {'stop', 'disconnect', 'shutdown', 'cancel'}:
            raise BackendError('busy', 'Another operation is in progress. Try again shortly.', True)
        identifier = request_id or uuid.uuid4().hex
        if identifier in self.pending:
            raise BackendError('duplicate_request', 'This request is already running.')
        message = {'id': identifier, 'op': op, 'params': params or {}}
        if session_id:
            message['session_id'] = session_id
        frame = json.dumps(message, allow_nan=False, separators=(',', ':')).encode() + b'\n'
        if len(frame) > MAX_FRAME:
            raise ValueError('Request exceeds 8 MiB')
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            async with self.writer_lock:
                self.process.stdin.write(frame)
                async with asyncio.timeout(5):
                    await self.process.stdin.drain()
            # Include the helper's bounded preparation/open/cleanup operation,
            # then allow time for its response to cross the local pipe.
            async with asyncio.timeout(max(60, TIMEOUTS.get(op, 15) + 10)):
                value = await future
            if not value.get('ok'):
                error = value['error']
                raise BackendError(error['code'], error['message'], error.get('retryable', False))
            return value['result']
        finally:
            self.pending.pop(identifier, None)

    async def close(self):
        async with self.close_lock:
            if not self.process:
                return
            process = self.process
            cancelled = False
            try:
                # One graceful budget covers the request AND process exit.
                async with asyncio.timeout(CLOSE_GRACE_SECONDS):
                    with contextlib.suppress(Exception):
                        await self.call('shutdown')
                    if process.stdin:
                        process.stdin.close()
                    await process.wait()
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                cancelled = True
            finally:
                if process.stdin:
                    process.stdin.close()
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                if self.reader_task:
                    self.reader_task.cancel()
                # A stopped pipe or unresponsive child cannot extend shutdown.
                waits = [asyncio.create_task(process.wait())]
                if self.reader_task:
                    waits.append(self.reader_task)
                done, pending = await asyncio.wait(waits, timeout=CLOSE_REAP_SECONDS)
                for task in pending:
                    task.cancel()
                for task in done:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        task.result()
                self.process = None
            if cancelled:
                raise asyncio.CancelledError
