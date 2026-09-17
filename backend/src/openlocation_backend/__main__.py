"""Entrypoint; stdout fd is private JSON, upstream output is discarded/redacted."""
import asyncio
import contextlib
import logging
import os
import signal
import sys
import threading
import time

from . import __version__

_diagnostics = None
SHUTDOWN_SECONDS = 12


async def serve(*, exiting=None):
    from .runtime_pipes import MAX_FRAME, ParentProcess, ThreadedPipeReader, ThreadedPipeWriter, private_stdout

    # Protect stdout before imports can initialize native dependencies.
    global _diagnostics
    pipe_fd, _diagnostics = private_stdout()
    parent = ParentProcess()
    loop = asyncio.get_running_loop()
    owns_exit_event = exiting is None
    exiting = exiting if exiting is not None else threading.Event()
    shutdown_started = threading.Event()
    shutdown_at = []
    shutdown_lock = threading.Lock()
    input_transport = reader = writer = None
    previous_signals = {}

    def begin_shutdown():
        with shutdown_lock:
            if not shutdown_at:
                shutdown_at.append(time.monotonic())
        shutdown_started.set()

    def shutdown_signal():
        begin_shutdown()
        if input_transport is not None:
            input_transport.pause_reading()
        if reader is not None:
            reader.feed_eof()

    def guardian():
        # Independent of the asyncio loop: a blocked native dependency cannot orphan
        # the helper after its parent dies or after EOF has begun shutdown.
        try:
            while not exiting.wait(0.25):
                parent_alive = parent.alive()
                if not parent_alive or shutdown_started.is_set():
                    begin_shutdown()
                    if not parent_alive:
                        with contextlib.suppress(RuntimeError):
                            loop.call_soon_threadsafe(shutdown_signal)
                    remaining = max(0, SHUTDOWN_SECONDS - (time.monotonic() - shutdown_at[0]))
                    if not exiting.wait(remaining):
                        os._exit(2)
                    return
        finally:
            parent.close()
    threading.Thread(target=guardian, daemon=True, name="parent-guardian").start()
    try:
        if sys.platform == "win32":
            input_fd = os.dup(sys.stdin.fileno())
            os.set_inheritable(input_fd, False)
            reader = input_transport = ThreadedPipeReader(input_fd, on_end=begin_shutdown)
            writer = ThreadedPipeWriter(pipe_fd)
        else:
            reader = asyncio.StreamReader(limit=MAX_FRAME)
            input_transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
            output_pipe = os.fdopen(pipe_fd, "wb", buffering=0)
            protocol = asyncio.streams.FlowControlMixin(loop=loop)
            output_transport, _ = await loop.connect_write_pipe(lambda: protocol, output_pipe)
            writer = asyncio.StreamWriter(output_transport, protocol, None, loop)
        loop.set_exception_handler(lambda _loop, _context: _diagnostics.write("Estera background operation failed; reconnect or stop/reset.\n"))
        signals = (signal.SIGTERM, signal.SIGINT)
        if sys.platform == "win32":
            signals += (signal.SIGBREAK,)
        for sig in signals:
            if sys.platform == "win32":
                def handle_signal(*_):
                    begin_shutdown()
                    loop.call_soon_threadsafe(shutdown_signal)
                previous_signals[sig] = signal.signal(sig, handle_signal)
            else:
                loop.add_signal_handler(sig, shutdown_signal)
                previous_signals[sig] = None
        from .protocol import Server
        server = Server(reader, writer)
        server.on_shutdown = shutdown_signal
        await server.run()
    finally:
        if owns_exit_event:
            exiting.set()
        if input_transport is not None:
            input_transport.close()
        if writer is not None:
            writer.close()
        else:
            with contextlib.suppress(OSError):
                os.close(pipe_fd)
        for sig in previous_signals:
            if sys.platform == "win32":
                signal.signal(sig, previous_signals[sig])
            else:
                loop.remove_signal_handler(sig)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--version":
        print(f"Estera backend {__version__}; protocol 1; pymobiledevice3 11.12.4")
        return
    if len(sys.argv) != 1:
        print("Usage: openlocation-backend [--version]. Otherwise communicate using private NDJSON pipes.", file=sys.stderr)
        raise SystemExit(2)
    os.umask(0o077)
    logging.disable(logging.CRITICAL)  # upstream diagnostics may contain pair records or coordinates
    exiting = threading.Event()
    try:
        asyncio.run(serve(exiting=exiting))
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception:
        print("Estera helper failed; check dependency packaging and private pipe ownership.", file=_diagnostics or sys.stderr)
        raise SystemExit(1) from None
    finally:
        # Keep the watchdog alive through asyncio.run's executor shutdown. A
        # cancelled to_thread operation can leave a blocked native worker behind.
        exiting.set()


if __name__ == "__main__":
    main()
