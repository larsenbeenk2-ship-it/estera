"""Private per-checkout web instance ownership and authenticated local shutdown."""
import contextlib
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from .errors import BackendError
from .private_files import ensure_private_directory, exclusive_file_lock
from .storage import data_directory

ORIGIN = "http://localhost:3000"


class WebInstance:
    def __init__(self, root, *, directory=None):
        key = hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode("utf-8")).hexdigest()[:32]
        self.directory = Path(directory) if directory is not None else data_directory() / "runtime"
        self.path = self.directory / f"{key}.json"
        self.lock_path = self.directory / f"{key}.lock"
        self.id = uuid.uuid4().hex

    @contextlib.contextmanager
    def claim(self, token):
        ensure_private_directory(self.directory)
        with exclusive_file_lock(self.lock_path):
            value = {"version": 1, "instance_id": self.id, "token": token, "pid": os.getpid()}
            fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".instance-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                    json.dump(value, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            try:
                yield
            finally:
                with contextlib.suppress(OSError, ValueError, KeyError):
                    if self.read()["instance_id"] == self.id:
                        self.path.unlink(missing_ok=True)

    def read(self):
        if self.path.stat().st_size > 4096:
            raise ValueError("Invalid local instance record")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != 1 or
                not isinstance(value.get("instance_id"), str) or len(value["instance_id"]) != 32 or
                any(c not in "0123456789abcdef" for c in value["instance_id"]) or
                not isinstance(value.get("token"), str) or not 32 <= len(value["token"]) <= 128):
            raise ValueError("Invalid local instance record")
        return value


async def stop_instance(root, *, directory=None, client=None):
    """No port-owner killing, process-name matching or externally supplied URLs."""
    import httpx
    instance = WebInstance(root, directory=directory)
    try:
        value = instance.read()
    except FileNotFoundError:
        return "No running Estera instance is recorded for this checkout."
    except (OSError, ValueError):
        raise BackendError("instance_unavailable", "The local Estera instance record is unreadable. Stop the original terminal process; no other process was stopped.") from None
    owned = client is None
    client = client or httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False)
    try:
        response = await client.post(ORIGIN + "/api/shutdown",
            headers={"Origin": ORIGIN, "X-OpenLocation-Token": value["token"]},
            json={"instance_id": value["instance_id"]})
        if response.status_code != 200 or response.json().get("instance_id") != value["instance_id"]:
            raise ValueError("Instance identity rejected")
        return "Stopping Estera and attempting bounded iPhone cleanup."
    except (httpx.HTTPError, ValueError, AttributeError):
        raise BackendError("instance_unavailable", "This Estera instance did not accept shutdown. Stop its original terminal process; no other process was stopped. Phone location is unknown.") from None
    finally:
        if owned:
            await client.aclose()


def main():
    import asyncio
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m openlocation_backend.instance CHECKOUT_PATH")
    try:
        print(asyncio.run(stop_instance(sys.argv[1])))
    except BackendError as exc:
        raise SystemExit(exc.message) from None


if __name__ == "__main__":
    main()
