"""Versioned local JSON content store. No live session or credentials are persisted."""
import copy
import json
import os
import tempfile
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .errors import BackendError
from .models import Coordinate, Route
from .private_files import ensure_private_directory, exclusive_file_lock

BUCKETS = {"locations": 200, "routes": 200, "location_history": 50, "route_history": 20}
MAX_STORE = 64 * 1024 * 1024


def data_directory():
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local or not Path(local).is_absolute():
            raise OSError("LOCALAPPDATA must identify the current user's local data directory")
        return Path(local) / "OpenLocation"
    return Path.home() / "Library" / "Application Support" / "OpenLocation"


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "content.json"

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "revision": 0, **{key: [] for key in BUCKETS}}
        if self.path.stat().st_size > MAX_STORE:
            raise BackendError("store_too_large", "Local content exceeds the 64 MiB store limit.")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data["version"] != 1:
                raise BackendError("store_version", "Local content was created by a different version; it has not been modified.")
            if type(data["revision"]) is not int or any(not isinstance(data[k], list) for k in BUCKETS):
                raise ValueError()
            return data
        except (ValueError, KeyError, TypeError):
            raise BackendError("store_corrupt", "Local content is unreadable; preserve content.json and restore a backup.") from None

    def execute(self, action, bucket, *, item_id=None, name=None, payload=None, revision=None, offset=0, limit=50):
        if bucket not in BUCKETS:
            raise ValueError("unknown content bucket")
        ensure_private_directory(self.directory)
        with exclusive_file_lock(self.directory / "content.lock"):
            data = self._read()
            items = data[bucket]
            if action == "list":
                return {"revision": data["revision"], "total": len(items), "items": [
                    {k: v for k, v in item.items() if k != "payload"} for item in items[offset:offset + limit]]}
            if action == "get":
                for item in items:
                    if item["id"] == item_id:
                        return {"revision": data["revision"], "item": copy.deepcopy(item), "geometry_reused": "route" in bucket}
                raise BackendError("not_found", "Saved item not found.")
            if revision != data["revision"]:
                raise BackendError("revision_conflict", "Content changed; read the latest revision before writing.", True)
            if action == "put":
                model = Route if "route" in bucket else Coordinate
                validated = model.model_validate(payload).model_dump()
                identifier = item_id or uuid.uuid4().hex
                existing = next((i for i in items if i["id"] == identifier), None)
                if item_id is not None and existing is None:
                    raise BackendError("not_found", "Saved item not found.")
                now = datetime.now(timezone.utc).isoformat()
                item = {"id": identifier, "name": name, "created_at": existing["created_at"] if existing else now,
                        "updated_at": now, "payload": validated}
                data[bucket] = [item] + [i for i in items if i["id"] != identifier]
                if "history" in bucket:
                    data[bucket] = data[bucket][:BUCKETS[bucket]]
                elif len(data[bucket]) > BUCKETS[bucket]:
                    raise BackendError("content_limit", "Bookmark limit is 200 per kind; delete an item first.")
                result = {"id": identifier}
            elif action == "delete":
                data[bucket] = [i for i in items if i["id"] != item_id]
                result = {"deleted": len(items) != len(data[bucket])}
            elif action == "clear":
                data[bucket] = []
                result = {"deleted": len(items)}
            else:
                raise ValueError("unknown store action")
            data["revision"] += 1
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_STORE:
                raise BackendError("store_too_large", "Content exceeds 64 MiB; remove unused routes first.")
            fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".content-")
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
                if sys.platform != "win32":
                    directory_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return {**result, "revision": data["revision"]}
