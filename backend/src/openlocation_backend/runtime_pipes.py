"""Bounded, binary stdio bridge for Windows anonymous pipes.

Only two daemon threads perform blocking I/O. Asyncio's default executor is never
used: an abandoned stdin read must not keep asyncio.run() alive at shutdown.
"""
import asyncio
import contextlib
import ctypes
import os
import sys
import threading
from collections import deque

# Keep this module stdlib-only: output is isolated before loading dependencies.
MAX_FRAME = 8 * 1024 * 1024


class ThreadedPipeReader:
    def __init__(self, fd, *, max_frame=MAX_FRAME, capacity=2, on_end=None):
        if max_frame < 1 or capacity < 1:
            raise ValueError("Pipe bounds must be positive")
        self.fd, self.max_frame, self.capacity = fd, max_frame, capacity
        self.loop = asyncio.get_running_loop()
        self.ready = asyncio.Event()
        self.condition = threading.Condition()
        self.frames = deque()
        self.ended = False
        self.error = None
        self.on_end = on_end
        self.thread = threading.Thread(target=self._read, name="private-stdin", daemon=True)
        self.thread.start()

    def _wake(self):
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.ready.set)

    def _put(self, frame):
        with self.condition:
            self.condition.wait_for(lambda: self.ended or len(self.frames) < self.capacity)
            if self.ended:
                return False
            wake = not self.frames
            self.frames.append(frame)
        if wake:
            self._wake()
        return True

    def _read(self):
        pending = bytearray()
        try:
            while not self.ended:
                chunk = os.read(self.fd, min(65536, self.max_frame + 1 - len(pending)))
                if not chunk:
                    if pending:
                        self._put(bytes(pending))
                    break
                pending.extend(chunk)
                while (end := pending.find(b"\n")) >= 0:
                    if end + 1 > self.max_frame:
                        raise ValueError("Input exceeds 8 MiB frame limit")
                    frame = bytes(pending[:end + 1])
                    del pending[:end + 1]
                    if not self._put(frame):
                        return
                if len(pending) >= self.max_frame:
                    raise ValueError("Input exceeds 8 MiB frame limit")
        except Exception as exc:
            with self.condition:
                if not self.ended:
                    self.error = exc
        finally:
            with self.condition:
                self.ended = True
                self.condition.notify_all()
            if self.on_end:
                self.on_end()
            self._wake()
            with contextlib.suppress(OSError):
                os.close(self.fd)

    async def readline(self):
        while True:
            with self.condition:
                if self.frames:
                    frame = self.frames.popleft()
                    self.condition.notify_all()
                    return frame
                if self.error:
                    raise self.error
                if self.ended:
                    return b""
                self.ready.clear()
            await self.ready.wait()

    def feed_eof(self):
        with self.condition:
            self.ended = True
            self.frames.clear()
            self.condition.notify_all()
        self._wake()

    def pause_reading(self):
        self.feed_eof()

    def close(self):
        self.feed_eof()


class ThreadedPipeWriter:
    def __init__(self, fd, *, max_frame=MAX_FRAME, capacity=2):
        if max_frame < 1 or capacity < 1:
            raise ValueError("Pipe bounds must be positive")
        self.fd, self.max_frame, self.capacity = fd, max_frame, capacity
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        self.condition = threading.Condition()
        self.frames = deque()
        self.pending = 0  # Includes the frame currently being written.
        self.closed = False
        self.error = None
        self.thread = threading.Thread(target=self._write, name="private-stdout", daemon=True)
        self.thread.start()

    def _wake(self):
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.changed.set)

    def write(self, frame):
        if len(frame) > self.max_frame:
            raise ValueError("Output exceeds 8 MiB frame limit")
        with self.condition:
            if self.error:
                raise self.error
            if self.closed:
                raise ConnectionError("Private output is closed")
            if self.pending >= self.capacity:
                raise BufferError("Drain private output before writing another frame")
            self.frames.append(bytes(frame))
            self.pending += 1
            self.condition.notify()

    def _write(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.frames or self.closed)
                    if self.closed:
                        return
                    frame = self.frames.popleft()
                view = memoryview(frame)
                while view:
                    count = os.write(self.fd, view)
                    if count <= 0:
                        raise BrokenPipeError("Private output stopped accepting data")
                    view = view[count:]
                with self.condition:
                    self.pending -= 1
                self._wake()
        except Exception as exc:
            with self.condition:
                self.error = exc
                self.closed = True
            self._wake()
        finally:
            with contextlib.suppress(OSError):
                os.close(self.fd)

    async def drain(self):
        while True:
            with self.condition:
                if self.error:
                    raise self.error
                if self.closed:
                    raise ConnectionError("Private output is closed")
                if not self.pending:
                    return
                self.changed.clear()
            await self.changed.wait()

    def close(self):
        with self.condition:
            self.closed = True
            self.frames.clear()
            self.condition.notify_all()
        self._wake()


def private_stdout():
    """Keep the protocol descriptor, discard Python and native incidental output."""
    pipe_fd = os.dup(sys.stdout.fileno())
    os.set_inheritable(pipe_fd, False)
    diagnostics = os.fdopen(os.dup(2), "w", buffering=1, encoding="utf-8", errors="replace")
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        os.dup2(null, 2)
    finally:
        os.close(null)
    if sys.platform == "win32":
        import msvcrt
        from ctypes import wintypes
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(pipe_fd, os.O_BINARY)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        kernel.SetStdHandle.restype = wintypes.BOOL
        # dup2 redirects CRT descriptors; SetStdHandle also covers native WriteFile.
        for standard, descriptor in ((-11, 1), (-12, 2)):
            if not kernel.SetStdHandle(standard & 0xffffffff, msvcrt.get_osfhandle(descriptor)):
                raise ctypes.WinError(ctypes.get_last_error())
    return pipe_fd, diagnostics


class ParentProcess:
    """Retain the Windows process object, avoiding PID reuse and getppid polling."""
    def __init__(self):
        self.pid = os.getppid()
        self.handle = None
        if sys.platform == "win32":
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x00100000, False, self.pid)  # SYNCHRONIZE only
            if not self.handle:
                raise OSError("Could not retain the private helper's parent process")

    def alive(self):
        if self.handle:
            return self.kernel.WaitForSingleObject(self.handle, 0) == 0x102  # WAIT_TIMEOUT
        return os.getppid() == self.pid

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
