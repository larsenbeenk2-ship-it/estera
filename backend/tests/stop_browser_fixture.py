"""Disposable map preview fixture with no device client or persistent store.

Run from the repository root after building the frontend:
  backend/.venv/bin/python backend/tests/stop_browser_fixture.py --port 3021

Open /__fixture/setup/macos, then Library > Routes >
"Park and walk preview fixture". Use the map's Preview route controls.
The synthetic itinerary drives, parks, walks, waits six seconds, returns to
the car, drives again, then parks and walks to the final destination.

The connected session exists only in memory to expose the actual app's map.
Only status, capabilities, store.list and store.get are implemented. All phone
commands and store mutations are rejected. The shared browser fixture supplies
an empty local map style and blocks external browser/provider connections.
No HelperClient is instantiated, no phone is contacted, and no data is saved.
"""

import argparse
import copy
import socket

import uvicorn

from browser_fixture import FakeHelper, create_fixture
from openlocation_backend.errors import BackendError
from openlocation_backend.models import Route
from test_stop_phases import parked_itinerary


ROUTE_ID = "stop-preview-route"
ROUTE_NAME = "Park and walk preview fixture"


def preview_route():
    """Reuse the timeline fixture, starting at its initial roadside point."""
    payload = parked_itinerary().model_dump()
    payload["name"] = ROUTE_NAME
    payload["points"] = payload["points"][1:]
    payload["segment_modes"] = payload["segment_modes"][1:]
    payload["waypoints"][0] = {
        "name": "Roadside start",
        "point_index": 0,
        "requested_coordinate": {
            key: payload["points"][0][key] for key in ("latitude", "longitude")
        },
        "dwell_seconds": 0.0,
        "walk_to_stop": False,
    }
    for waypoint in payload["waypoints"][1:]:
        waypoint["point_index"] -= 1
        access = waypoint["access"]
        access["arrival_index"] -= 1
        access["departure_index"] -= 1
        for key in ("walking_to", "walking_back"):
            if access[key] is not None:
                access[key]["start_index"] -= 1
                access[key]["end_index"] -= 1
        access["estimated_segments"] = [index - 1 for index in access["estimated_segments"]]
    payload["waypoints"][1]["dwell_seconds"] = 6.0
    return Route.model_validate(payload)


class StopPreviewHelper(FakeHelper):
    def __init__(self):
        self.item = {
            "id": ROUTE_ID,
            "name": ROUTE_NAME,
            "created_at": "2026-09-15T12:00:00+00:00",
            "updated_at": "2026-09-15T12:00:00+00:00",
            "payload": preview_route().model_dump(),
        }
        super().__init__()

    def reset(self, scenario="macos"):
        super().reset("macos")
        self.status.update(
            session_id="stop-preview-session-only",
            device_id="stop-preview-no-device",
            transport_state="connected",
            transport="usb",
            requested_transport="usb",
            ever_connected=True,
            setup_complete=True,
            last_contact="2026-09-15T12:00:00+00:00",
        )

    async def call(self, op, params=None, session_id=None, request_id=None):
        params = params or {}
        if op in {"status", "capabilities"}:
            return await super().call(op, params, session_id, request_id)
        self.calls.append({"op": op, "params": copy.deepcopy(params), "session_id": session_id})
        if op == "store.list":
            items = [self.item] if params["bucket"] == "routes" else []
            offset = params.get("offset", 0)
            limit = params.get("limit", 20)
            return {
                "revision": 1,
                "total": len(items),
                "items": [
                    {key: value for key, value in item.items() if key != "payload"}
                    for item in items[offset:offset + limit]
                ],
            }
        if op == "store.get":
            if params["bucket"] != "routes" or params["item_id"] != ROUTE_ID:
                raise BackendError("not_found", "This fixture contains one synthetic saved route.")
            return {"revision": 1, "item": copy.deepcopy(self.item)}
        raise BackendError(
            "fixture_read_only",
            "Map preview fixture only. Phone commands and saved-data changes are disabled.",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3021)
    args = parser.parse_args()
    # Binding our own socket prevents replacing or stopping an existing server.
    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port))
    sock.listen()
    port = sock.getsockname()[1]
    app = create_fixture(port, helper=StopPreviewHelper())

    class FixtureServer(uvicorn.Server):
        async def shutdown(self, sockets=None):
            app.state.stopping.set()
            await super().shutdown(sockets)

    print(f"MAP-ONLY FIXTURE http://127.0.0.1:{port}/__fixture/setup/macos", flush=True)
    print(f"Library > Routes > {ROUTE_NAME}", flush=True)
    FixtureServer(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2)).run(sockets=[sock])
