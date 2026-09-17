"""Packaged desktop entrypoint and ownership of its private loopback server."""
import argparse
import contextlib
import multiprocessing
import os
import re
import sys
import threading

from . import __version__
from .runtime_pipes import ParentProcess

PARENT_SHUTDOWN_SECONDS = 18


@contextlib.contextmanager
def watch_parent(parent_pid, request_exit):
    """Observe only our direct parent; never search for or kill other processes."""
    if parent_pid is None:
        yield
        return
    parent = ParentProcess()
    if parent_pid <= 1 or parent.pid != parent_pid or not parent.alive():
        parent.close()
        raise RuntimeError('The desktop launcher is no longer this server\'s parent.')
    finished = threading.Event()

    def guardian():
        try:
            while not finished.wait(0.25):
                if not parent.alive():
                    request_exit()
                    # A blocked native dependency must not keep the server alive
                    # after the app has exited. The helper has its own guardian.
                    if not finished.wait(PARENT_SHUTDOWN_SECONDS):
                        os._exit(2)
                    return
        finally:
            parent.close()

    thread = threading.Thread(target=guardian, daemon=True, name='desktop-parent-guardian')
    thread.start()
    try:
        yield
    finally:
        finished.set()
        thread.join(timeout=1)


def main(argv=None):
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description='Estera private desktop runtime')
    parser.add_argument('--version', action='version', version=f'Estera desktop runtime {__version__}')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--web', action='store_true')
    mode.add_argument('--helper', action='store_true')
    parser.add_argument('--desktop-token')
    parser.add_argument('--parent-pid', type=int)
    args = parser.parse_args(argv)
    if args.helper:
        if args.desktop_token is not None or args.parent_pid is not None:
            parser.error('Desktop launch options require --web.')
        from .__main__ import main as helper_main
        # The established helper accepts no command-line device commands.
        sys.argv = [sys.argv[0]]
        helper_main()
        return
    if not args.desktop_token or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', args.desktop_token):
        parser.error('--web requires a 32–128 character random --desktop-token.')
    if args.parent_pid is None or args.parent_pid <= 1 or args.parent_pid != os.getppid():
        parser.error('--web requires --parent-pid matching the desktop launcher.')
    os.umask(0o077)
    from .web import main as web_main
    web_main(desktop_instance=args.desktop_token, parent_pid=args.parent_pid)


if __name__ == '__main__':
    main()
