"""Real local pipe/storage checks; native Windows assertions explicitly skip elsewhere."""
import asyncio
import contextlib
import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from openlocation_backend.errors import BackendError
from openlocation_backend.models import MAX_FRAME
from openlocation_backend.power import PowerAssertion
from openlocation_backend.private_files import ensure_private_directory, exclusive_file_lock
from openlocation_backend.runtime_pipes import ParentProcess, ThreadedPipeReader, ThreadedPipeWriter
from openlocation_backend.storage import Store, data_directory


async def test_threaded_binary_roundtrip_and_eof():
    read_fd, write_fd = os.pipe()
    ended = threading.Event()
    reader = ThreadedPipeReader(read_fd, on_end=ended.set)
    writer = ThreadedPipeWriter(write_fd)
    frames = [b'{"name":"Caf\xc3\xa9 \xe6\x9d\xb1\xe4\xba\xac"}\n', b'{"literal":"\\r\\n"}\n']
    try:
        for frame in frames:
            writer.write(frame)
            await asyncio.wait_for(writer.drain(), 1)
            assert await asyncio.wait_for(reader.readline(), 1) == frame
        writer.close()
        assert await asyncio.wait_for(reader.readline(), 1) == b""
        assert ended.is_set()
    finally:
        reader.close()
        writer.close()


async def test_threaded_frame_exact_limit_and_oversize():
    read_fd, write_fd = os.pipe()
    reader = ThreadedPipeReader(read_fd, max_frame=16)
    try:
        os.write(write_fd, b"x" * 15 + b"\n")
        assert await asyncio.wait_for(reader.readline(), 1) == b"x" * 15 + b"\n"
        os.write(write_fd, b"x" * 17)
        with pytest.raises(ValueError, match="frame limit"):
            await asyncio.wait_for(reader.readline(), 1)
        assert not reader.frames
    finally:
        reader.close()
        os.close(write_fd)


async def test_threaded_unterminated_eof_is_preserved_for_protocol_rejection():
    read_fd, write_fd = os.pipe()
    reader = ThreadedPipeReader(read_fd)
    os.write(write_fd, b'{"op":"shutdown"}')
    os.close(write_fd)
    assert await asyncio.wait_for(reader.readline(), 1) == b'{"op":"shutdown"}'
    assert await reader.readline() == b""


async def test_threaded_reader_queue_is_bounded_and_feed_eof_wakes():
    read_fd, write_fd = os.pipe()
    reader = ThreadedPipeReader(read_fd, capacity=2)
    try:
        os.write(write_fd, b"{}\n" * 100)
        await asyncio.wait_for(reader.ready.wait(), 1)
        with reader.condition:
            assert 1 <= len(reader.frames) <= 2
        reader.feed_eof()
        assert await asyncio.wait_for(reader.readline(), 1) == b""
    finally:
        reader.close()
        os.close(write_fd)


async def test_threaded_writer_backpressure_is_bounded_and_cancellable():
    read_fd, write_fd = os.pipe()
    writer = ThreadedPipeWriter(write_fd, capacity=2)
    try:
        frame = b"x" * (MAX_FRAME - 1) + b"\n"
        writer.write(frame)
        writer.write(frame)
        with pytest.raises(BufferError):
            writer.write(b"{}\n")
        with pytest.raises(ValueError):
            writer.write(frame + b"x")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(writer.drain(), 0.03)
        assert writer.pending == 2
        assert writer.thread.daemon
    finally:
        writer.close()
        os.close(read_fd)


async def test_threaded_broken_output_fails_drain():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    writer = ThreadedPipeWriter(write_fd)
    try:
        writer.write(b"{}\n")
        with pytest.raises((BrokenPipeError, OSError)):
            await asyncio.wait_for(writer.drain(), 1)
    finally:
        writer.close()


async def test_server_uses_threaded_bridge_without_devices(tmp_path):
    from openlocation_backend.protocol import Server

    class Session:
        def __init__(self, emit):
            pass
        def invalidate_recovery(self):
            pass
        async def disconnect(self):
            return {"disconnected": True}

    request_read, request_write = os.pipe()
    response_read, response_write = os.pipe()
    reader = ThreadedPipeReader(request_read)
    writer = ThreadedPipeWriter(response_write)
    responses = ThreadedPipeReader(response_read)
    server = Server(reader, writer, directory=tmp_path, session_factory=Session)
    task = asyncio.create_task(server.run())
    try:
        os.write(request_write, b'{"id":"hello","op":"hello","params":{"version":1}}\n')
        value = json.loads(await asyncio.wait_for(responses.readline(), 2))
        assert value["id"] == "hello" and value["ok"]
        os.write(request_write, b'{"id":"bye","op":"shutdown","params":{}}\n')
        value = json.loads(await asyncio.wait_for(responses.readline(), 2))
        assert value["id"] == "bye" and value["ok"]
        await asyncio.wait_for(task, 2)
    finally:
        reader.close()
        writer.close()
        responses.close()
        os.close(request_write)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_stdout_isolated_from_python_and_native_fd_output():
    code = '''
import os
from openlocation_backend.runtime_pipes import private_stdout
fd, diagnostics = private_stdout()
print("incidental Python output", flush=True)
os.write(1, b"incidental native fd output\\n")
os.write(2, b"incidental error output\\n")
os.write(fd, b'{"ok":true}\\n')
os.close(fd)
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'{"ok":true}\n'
    assert result.stderr == b""


def test_guardian_outlives_asyncio_executor_shutdown():
    code = '''
import asyncio, contextlib, sys, time, types
from openlocation_backend import __main__ as entry
entry.SHUTDOWN_SECONDS = 0.05
class Server:
    def __init__(self, reader, writer):
        pass
    async def run(self):
        work = asyncio.create_task(asyncio.to_thread(time.sleep, 20))
        await asyncio.sleep(0.02)
        self.on_shutdown()
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await work
protocol = types.ModuleType("openlocation_backend.protocol")
protocol.Server = Server
sys.modules[protocol.__name__] = protocol
entry.main()
'''
    result = subprocess.run([sys.executable, "-c", code], input=b"", capture_output=True, timeout=3)
    assert result.returncode == 2, result.stderr
    assert result.stdout == b"" and result.stderr == b""


def test_utf8_store_and_failed_replace_preserve_existing_data(tmp_path, monkeypatch):
    from openlocation_backend import storage
    store = Store(tmp_path)
    result = store.execute("put", "locations", name="Café 東京", payload={"latitude": 2, "longitude": 3}, revision=0)
    original = store.path.read_bytes()
    assert "Café 東京" in original.decode("utf-8")
    assert store.execute("get", "locations", item_id=result["id"])["item"]["name"] == "Café 東京"

    def rejected_replace(*_):
        raise PermissionError("Injected replacement failure")
    monkeypatch.setattr(storage.os, "replace", rejected_replace)
    with pytest.raises(PermissionError):
        store.execute("clear", "locations", revision=1)
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".content-*"))
    assert store.execute("list", "locations")["revision"] == 1


def test_private_file_lock_excludes_same_process_and_releases_on_error(tmp_path):
    path = tmp_path / "content.lock"
    with pytest.raises(ValueError):
        with exclusive_file_lock(path):
            with pytest.raises(BackendError) as failure:
                with exclusive_file_lock(path):
                    pytest.fail("Duplicate lock succeeded")
            assert failure.value.code == "store_busy"
            raise ValueError("Injected action failure")
    with exclusive_file_lock(path):
        pass


def test_private_file_lock_excludes_another_process(tmp_path):
    path = tmp_path / "content.lock"
    code = '''
import sys
from pathlib import Path
from openlocation_backend.private_files import exclusive_file_lock
from openlocation_backend.errors import BackendError
try:
    with exclusive_file_lock(Path(sys.argv[1])):
        raise SystemExit(2)
except BackendError as error:
    raise SystemExit(0 if error.code == "store_busy" else 3)
'''
    with exclusive_file_lock(path):
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_data_directory_keeps_mac_path_and_uses_localappdata(monkeypatch, tmp_path):
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "darwin")
        assert data_directory() == Path.home() / "Library" / "Application Support" / "OpenLocation"
        patch.setattr(sys, "platform", "win32")
        patch.setenv("LOCALAPPDATA", str(tmp_path))
        assert data_directory() == tmp_path / "OpenLocation"
        patch.delenv("LOCALAPPDATA")
        with pytest.raises(OSError, match="LOCALAPPDATA"):
            data_directory()


def test_private_directory_rejects_symlink(tmp_path):
    target = tmp_path / "actual"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows Developer Mode or privilege")
    with pytest.raises(OSError, match="link"):
        ensure_private_directory(link)


async def test_suspend_callback_sets_fence_before_loop_delivery_and_drops_stale_events():
    power = PowerAssertion()
    calls = []
    power._loop = asyncio.get_running_loop()
    power._callback = lambda suspended: calls.append((suspended, threading.get_ident()))
    loop_thread = threading.get_ident()
    thread = threading.Thread(target=lambda: (power._power_event(4), power._power_event(18)))
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert not power.suspended and power.suspend_epoch == 1
    assert calls == []
    await asyncio.sleep(0)
    assert calls == [(True, loop_thread), (False, loop_thread)]
    power._power_event(4)
    assert power.suspended and power.suspend_epoch == 2
    power.unwatch()
    await asyncio.sleep(0)
    assert len(calls) == 2


async def test_helper_shutdown_bounds_blocked_writer_and_reaps(monkeypatch):
    from openlocation_backend import web_client

    class Input:
        closed = False
        def write(self, frame):
            pass
        async def drain(self):
            await asyncio.Future()
        def close(self):
            self.closed = True
    class Process:
        stdin = Input()
        returncode = None
        killed = False
        async def wait(self):
            if self.returncode is None:
                await asyncio.Future()
            return self.returncode
        def kill(self):
            self.killed = True
            self.returncode = -9

    monkeypatch.setattr(web_client, "CLOSE_GRACE_SECONDS", 0.03)
    monkeypatch.setattr(web_client, "CLOSE_REAP_SECONDS", 0.03)
    client = web_client.HelperClient()
    process = client.process = Process()
    client.reader_task = asyncio.create_task(asyncio.Event().wait())
    await asyncio.wait_for(client.close(), 0.5)
    assert process.killed and process.stdin.closed
    assert client.process is None and not client.pending
    assert client.reader_task.cancelled()
    await client.close()


async def test_helper_shutdown_cancellation_still_kills_child():
    from openlocation_backend.web_client import HelperClient

    class Process:
        stdin = None
        returncode = None
        async def wait(self):
            if self.returncode is None:
                await asyncio.Future()
            return self.returncode
        def kill(self):
            self.returncode = -9

    client = HelperClient()
    process = client.process = Process()
    started = asyncio.Event()
    async def blocked_shutdown(*_args):
        started.set()
        await asyncio.Future()
    client.call = blocked_shutdown
    task = asyncio.create_task(client.close())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.5)
    assert process.returncode == -9 and client.process is None


def test_windows_execution_state_flags_same_thread_and_failure(monkeypatch):
    class Call:
        def __init__(self):
            self.calls = []
            self.result = 0x80000000
        def __call__(self, flags):
            self.calls.append((flags, threading.get_ident()))
            return self.result
    class Kernel:
        SetThreadExecutionState = Call()
    kernel = Kernel()
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        patch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False)
        power = PowerAssertion()
        power.acquire()
        power.acquire()
        errors = []
        def release_from_wrong_thread():
            try:
                power.release()
            except RuntimeError as error:
                errors.append(str(error))
        worker = threading.Thread(target=release_from_wrong_thread)
        worker.start()
        worker.join(timeout=1)
        assert errors and power.owner_thread == threading.get_ident()
        power.release()
        assert [flags for flags, _ in kernel.SetThreadExecutionState.calls] == [0x80000001, 0x80000000]
        assert power.owner_thread is None
        kernel.SetThreadExecutionState.result = 0
        with pytest.raises(OSError):
            power.acquire()
        assert power.owner_thread is None


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows DACL and byte locking APIs")
def test_native_windows_private_directory_acl_and_store(tmp_path):
    import win32api
    import win32security
    directory = ensure_private_directory(tmp_path / "private")
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    descriptor = win32security.GetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION)
    assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    acl = descriptor.GetSecurityDescriptorDacl()
    assert acl.GetAceCount() == 1
    assert acl.GetAce(0)[2] == user
    Store(directory).execute("put", "locations", name="東京", payload={"latitude": 2, "longitude": 3}, revision=0)
    file_acl = win32security.GetFileSecurity(str(directory / "content.json"), win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    assert file_acl.GetAceCount() == 1 and file_acl.GetAce(0)[2] == user


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows parent process and power notification APIs")
async def test_native_windows_parent_and_power_registration():
    parent = ParentProcess()
    try:
        assert parent.handle and parent.alive()
    finally:
        parent.close()
    power = PowerAssertion()
    try:
        power.watch(lambda _suspended: None)
        assert power._registration
        power.acquire()
    finally:
        power.release()
        power.unwatch()
    assert power._registration is None


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows GetStdHandle and WriteFile output redirection")
def test_native_windows_standard_handles_are_redirected():
    code = '''
import ctypes, os
from ctypes import wintypes
from openlocation_backend.runtime_pipes import private_stdout
fd, diagnostics = private_stdout()
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetStdHandle.argtypes = [wintypes.DWORD]
kernel.GetStdHandle.restype = wintypes.HANDLE
kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel.WriteFile.restype = wintypes.BOOL
written = wintypes.DWORD()
for stream in (-11, -12):
    assert kernel.WriteFile(kernel.GetStdHandle(stream & 0xffffffff), b"native noise", 12, ctypes.byref(written), None)
os.write(fd, b'{"ok":true}\\n')
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'{"ok":true}\n' and result.stderr == b""


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows helper startup, signals and anonymous stdio pipes")
def test_native_windows_helper_hello_shutdown_and_eof():
    frames = b'{"id":"hello","op":"hello","params":{"version":1}}\n{"id":"shutdown","op":"shutdown","params":{}}\n'
    result = subprocess.run([sys.executable, "-m", "openlocation_backend"], input=frames,
        capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    values = [json.loads(line) for line in result.stdout.splitlines()]
    replies = {value["id"]: value for value in values if "id" in value}
    assert replies["hello"]["ok"]
    # EOF may cancel shutdown before its reply; the bounded clean exit is authoritative.
    assert result.stderr == b""
