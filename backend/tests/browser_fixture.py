"""Disposable browser fixture; never starts HelperClient or touches an iPhone.

Run from the repository root:
  backend/.venv/bin/python backend/tests/browser_fixture.py --port 3019

Open /__fixture/setup/windows, /__fixture/setup/remembered, or
/__fixture/setup/macos to reset the in-memory scenario and browser preference.
POST /__fixture/control with a same-origin Origin header and JSON action
service (state=missing|ready), lose, or restore. GET /__fixture/observations
returns the commands issued by the UI. Stop with Ctrl-C. No content is saved.

Only this process changes the production app's host allowlist. Map assets use
an empty local style and external connections are forbidden by fixture CSP.
"""
import argparse
import asyncio
import copy
import json
import socket

import httpx
import uvicorn
from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from openlocation_backend import web
from openlocation_backend.providers import Providers


PHONE = "browser-test-iphone"
SESSION = "browser-test-session"
POINT = {"latitude": 37.7749, "longitude": -122.4194}


class FakeHelper:
    def __init__(self):
        self.listeners = set()
        self.calls = []
        self.sequence = 0
        self.reset("windows")

    def reset(self, scenario):
        self.platform = "macos" if scenario == "macos" else "windows"
        self.service = "missing" if scenario == "windows" else "ready"
        self.calls.clear()
        self.status = {"session_id": None, "device_id": None, "transport_state": "disconnected",
            "transport": None, "requested_transport": None, "ever_connected": False,
            "setup_complete": False, "connection_error": None, "playback_state": "idle",
            "route_revision": 0, "last_contact": None, "requested_coordinate": None,
            "last_transport_write": None, "location_evidence": "unknown", "route": None,
            "recovery": {"state": "idle", "action": "none"}}

    def capabilities(self):
        windows = self.platform == "windows"
        return {"host": {"os": self.platform, "architecture": "x64", "supported": True,
                "os_version": "10.0.22631" if windows else "15.0"},
            "connection_preparation": True,
            "transports": ["usb"] if windows else ["auto", "usb", "wifi"],
            "experimental_transports": [] if windows else ["remote"],
            "initial_setup": {"version": 2, "transports": ["usb"] if windows else ["usb", "wifi"], "reuses_developer_files": True},
            "wifi": {"usb_bootstrap": not windows}, "cable_required": windows,
            "recovery": {"automatic": windows, "restore_location": windows,
                "resume_playback": windows, "retry_until_stopped": windows}}

    async def start(self): pass
    async def close(self): pass

    def emit(self, name="state", data=None):
        self.sequence += 1
        self.status["sequence"] = self.sequence
        event = {"event": name, "sequence": self.sequence, "session_id": self.status["session_id"],
            "data": copy.deepcopy(self.status if data is None else data)}
        for queue in self.listeners:
            if queue.full(): queue.get_nowait()
            queue.put_nowait(event)

    async def call(self, op, params=None, session_id=None, request_id=None):
        params = params or {}
        if op not in {"status", "capabilities"}:
            self.calls.append({"op": op, "params": params, "session_id": session_id})
        if op == "status": return copy.deepcopy(self.status)
        if op == "capabilities": return self.capabilities()
        if op == "host.check": return {"host": self.capabilities()["host"], "supported": True,
            "usb_service": self.service, "message": "Apple USB service is ready." if self.service == "ready" else "Apple Mobile Device Service was not found. Follow Apple’s repair instructions."}
        if op == "devices.list": return {"devices": [{"device_id": PHONE, "name": "Fixture iPhone",
            "os_version": "18.5", "transports": ["usb"], "state": "discovered"}], "warnings": []}
        if op == "device.check": return {"usb_connected": True, "paired": True, "developer_mode": True,
            "image_mounted": True, "next_step": "connect", "message": "Ready."}
        if op == "device.prepare": return {"paired": True, "developer_mode": True, "image": "already_mounted", "setup_complete": True}
        if op in {"connect", "retry"}:
            self.status.update(session_id=SESSION, device_id=PHONE, transport_state="connected",
                transport=params.get("transport", "usb"), requested_transport=params.get("transport", "usb"),
                ever_connected=True, setup_complete=True, last_contact="2026-09-15T12:00:00+00:00")
            self.emit()
            return {"session_id": SESSION, "status": copy.deepcopy(self.status)}
        if op == "pause":
            self.status["playback_state"] = "paused"
            self.status["recovery"]["action"] = "keep_paused"
        elif op == "stop":
            self.status.update(playback_state="uncertain", recovery={"state": "idle", "action": "none"})
        elif op == "disconnect":
            scenario = self.platform
            calls = self.calls[:]
            self.reset(scenario)
            self.calls = calls
        elif op not in {"timer", "cancel"}:
            raise ValueError(f"Unimplemented fixture command: {op}")
        self.emit()
        if op in {"stop", "disconnect"}:
            return {"clear_sent": False, "message": "Fixture: retries stopped; no phone command was sent."}
        return copy.deepcopy(self.status)


def create_fixture(port, helper=None, providers=None):
    web.HOSTS = {f"127.0.0.1:{port}", f"localhost:{port}"}
    web.ORIGINS = {f"http://{host}" for host in web.HOSTS}
    if helper is None:
        helper = FakeHelper()
    if providers is None:
        providers = Providers(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))))
    app = web.create_app(helper=helper, providers=providers)

    @app.middleware("http")
    async def fixture_assets(request, call_next):
        response = await call_next(request)
        if request.url.path.endswith(".js") and request.url.path.startswith("/assets/MapView-"):
            body = b"".join([chunk async for chunk in response.body_iterator])
            body = body.replace(b"https://tiles.openfreemap.org/styles/liberty", b"/__fixture/map-style")
            response = Response(body, media_type="application/javascript")
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'"
        return response

    @app.get("/__fixture/map-style")
    async def map_style():
        return {"version": 8, "sources": {}, "layers": [{"id": "background", "type": "background", "paint": {"background-color": "#090e11"}}]}

    @app.get("/__fixture/setup/{scenario}")
    async def setup(scenario: str):
        if scenario not in {"windows", "remembered", "macos"}: raise ValueError("Unknown fixture scenario")
        helper.reset(scenario)
        saved = {"version": 1, "device_id": PHONE, "name": "Fixture iPhone", "transport": "wifi"} if scenario == "remembered" else None
        return HTMLResponse("<title>Estera test fixture</title><script>localStorage.clear();" +
            ("localStorage.setItem('openlocation.initial-setup.v1'," + json.dumps(json.dumps(saved)) + ");" if saved else "") +
            "location.replace('/');</script>")

    @app.get("/__fixture/observations")
    async def observations(): return {"calls": helper.calls, "status": helper.status}

    @app.post("/__fixture/control")
    async def control(request: Request):
        data = await request.json()
        if data["action"] == "service":
            if data["state"] not in {"missing", "ready"}: raise ValueError("Unknown service state")
            helper.service = data["state"]
        elif data["action"] == "lose":
            helper.status.update(transport_state="reconnecting", transport=None, playback_state="paused",
                requested_coordinate=POINT, last_transport_write={"coordinate": POINT, "at": "2026-09-15T12:00:00+00:00"},
                location_evidence="unknown", recovery={"state": "waiting_usb", "action": "resume_route"},
                route={"coordinate": POINT, "elapsed_seconds": 20, "distance_meters": 100, "total_distance_meters": 1000,
                    "eta_seconds": 180, "progress": .1, "cycle": 1, "dwelling": False, "complete": False})
            helper.emit("connection_lost", {"message": "Fixture: USB cable disconnected. Phone location is unknown."})
            helper.emit()
        elif data["action"] == "restore":
            resumed = helper.status["recovery"]["action"] == "resume_route"
            helper.status.update(transport_state="connected", transport="usb", playback_state="playing" if resumed else "paused",
                location_evidence="set_result_received", recovery={"state": "idle", "action": "none"})
            helper.emit("reconnected", {"resume_required": False, "restored": True, "resumed": resumed, "phone_location": "unknown"})
            helper.emit()
        else: raise ValueError("Unknown fixture action")
        return {"ok": True}
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3019)
    args = parser.parse_args()
    # Bind ourselves so an occupied port fails without affecting another process.
    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port))
    sock.listen()
    app = create_fixture(sock.getsockname()[1])
    print(f"TEST-ONLY FIXTURE http://127.0.0.1:{sock.getsockname()[1]}/__fixture/setup/windows", flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])
