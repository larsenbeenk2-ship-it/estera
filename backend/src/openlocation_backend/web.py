"""Single-user loopback web bridge. No network-facing device-control service."""
import asyncio
import contextlib
import hmac
import json
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import Field, ValidationError

from . import gpx
from .coverage import require_coordinate, require_route
from .errors import BackendError
from .models import Coordinate, Identifier, MAX_FRAME, Model, Route
from .protocol import PARAMETERS, parse_json
from .providers import Directions, Providers
from .route import Timeline
from .web_client import HelperClient

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[3]
WEB_DIST = Path(getattr(sys, '_MEIPASS', ROOT)) / 'web' / 'dist'
HOSTS = {'localhost:3000', '127.0.0.1:3000'}
ORIGINS = {f'http://{host}' for host in HOSTS}


class WebShutdown(Model):
    instance_id: Identifier


class WebCommand(Model):
    op: str
    params: dict = {}
    session_id: str | None = None
    authorized: bool = False
    request_id: Identifier | None = None


async def read_body(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FRAME:
            raise ValueError('Request exceeds 8 MiB')
    return parse_json(body)


def create_app(helper=None, providers=None, *, instance=None, desktop_instance=None):
    helper = helper or HelperClient()
    providers = providers or Providers()
    token = secrets.token_urlsafe(32)
    authorized_session = None

    @asynccontextmanager
    async def lifespan(app):
        with instance.claim(token) if instance is not None else contextlib.nullcontext():
            try:
                await helper.start()
                yield
            finally:
                await helper.close()
                await providers.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.helper = helper
    app.state.stopping = asyncio.Event()
    app.state.request_exit = None

    @app.middleware('http')
    async def local_only(request, call_next):
        host = request.headers.get('host', '')
        origin = request.headers.get('origin')
        if host not in HOSTS or (origin is not None and origin not in ORIGINS):
            return JSONResponse({'error': {'code': 'forbidden', 'message': 'Use http://localhost:3000 on this computer.'}}, status_code=403)
        if request.url.path.startswith('/api/') and request.url.path != '/api/bootstrap':
            if not hmac.compare_digest(request.headers.get('x-openlocation-token', ''), token):
                return JSONResponse({'error': {'code': 'forbidden', 'message': 'Reload Estera to reconnect securely.'}}, status_code=403)
        if request.method not in {'GET', 'HEAD'} and origin not in ORIGINS:
            return JSONResponse({'error': {'code': 'forbidden', 'message': 'Same-origin requests are required.'}}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://tiles.openfreemap.org https://assets.openfreemap.com; "
            "connect-src 'self' https://tiles.openfreemap.org https://assets.openfreemap.com; "
            "worker-src 'self' blob:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        return response

    @app.exception_handler(BackendError)
    async def backend_error(request, exc):
        return JSONResponse({'error': exc.as_dict()}, status_code=409 if exc.code in {'busy', 'revision_conflict', 'provider_busy'} else 400)

    @app.exception_handler(ValueError)
    async def input_error(request, exc):
        message = 'Invalid input. Check coordinates, route settings and required fields.' if isinstance(exc, ValidationError) else str(exc)
        return JSONResponse({'error': {'code': 'invalid_input', 'message': message, 'retryable': False}}, status_code=400)

    @app.get('/api/bootstrap')
    async def bootstrap():
        result = {'token': token, 'version': '0.1.0', 'coverage': 'Worldwide',
                  'status': await helper.call('status'), 'capabilities': await helper.call('capabilities')}
        if desktop_instance is not None:
            # This launch identity lets the native window reject an unrelated
            # process on port 3000. It never substitutes for the API token.
            result['desktop_instance'] = desktop_instance
        return result

    @app.post('/api/shutdown')
    async def shutdown_instance(request: Request):
        data = WebShutdown.model_validate(await read_body(request))
        if instance is None or not hmac.compare_digest(data.instance_id, instance.id) or app.state.request_exit is None:
            raise BackendError('instance_mismatch', 'This process does not match the requested Estera instance.')
        async def finish():
            app.state.stopping.set()
            app.state.request_exit()
        return JSONResponse({'instance_id': instance.id, 'stopping': True}, background=BackgroundTask(finish))

    @app.get('/api/status')
    async def status():
        return await helper.call('status')

    @app.get('/api/events')
    async def events():
        if len(helper.listeners) >= 8:
            raise BackendError('busy', 'Too many Estera tabs are open. Close an unused tab.', True)
        queue = asyncio.Queue(maxsize=8)
        helper.listeners.add(queue)
        async def stream():
            stopped = asyncio.create_task(app.state.stopping.wait())
            next_event = None
            try:
                yield 'data: ' + json.dumps({'event': 'state', 'data': await helper.call('status')}) + '\n\n'
                while not app.state.stopping.is_set():
                    next_event = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait({next_event, stopped}, timeout=15, return_when=asyncio.FIRST_COMPLETED)
                    if stopped in done:
                        break
                    if next_event in done:
                        event = next_event.result()
                        yield 'data: ' + json.dumps(event) + '\n\n'
                    else:
                        next_event.cancel()
                        await asyncio.gather(next_event, return_exceptions=True)
                        yield ': heartbeat\n\n'
            finally:
                helper.listeners.discard(queue)
                stopped.cancel()
                if next_event:
                    next_event.cancel()
                await asyncio.gather(stopped, *([next_event] if next_event else []), return_exceptions=True)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.post('/api/coordinate')
    async def coordinate(request: Request):
        point = Coordinate.model_validate(await read_body(request))
        require_coordinate(point)
        return point.model_dump()

    @app.post('/api/search')
    async def search(request: Request):
        data = await read_body(request)
        if not isinstance(data, dict) or not isinstance(data.get('query'), str):
            raise ValueError('Enter a place, address, or postal code')
        return await providers.search(data['query'])

    @app.post('/api/suggest')
    async def suggest(request: Request):
        data = await read_body(request)
        if not isinstance(data, dict) or not isinstance(data.get('query'), str):
            raise ValueError('Enter at least 3 characters to find place suggestions')
        return await providers.suggest(data['query'])

    @app.post('/api/reverse')
    async def reverse(request: Request):
        return await providers.reverse(Coordinate.model_validate(await read_body(request)))

    @app.post('/api/directions')
    async def directions(request: Request):
        return await providers.directions(Directions.model_validate(await read_body(request)), is_disconnected=request.is_disconnected)

    @app.post('/api/preview')
    async def preview(request: Request):
        route = Route.model_validate(await read_body(request))
        require_route(route)
        timeline = Timeline(route)
        return {'distance_meters': timeline.distance, 'lap_seconds': timeline.duration,
                'duration_seconds': timeline.total_duration,
                'legs': [{'start': {'latitude': leg.start.latitude, 'longitude': leg.start.longitude},
                          'end': {'latitude': leg.end.latitude, 'longitude': leg.end.longitude},
                          'end_seconds': end, 'duration': leg.duration, 'distance': leg.distance, 'dwell': leg.dwell,
                          'start_speed_mps': leg.start_speed_mps, 'end_speed_mps': leg.end_speed_mps,
                          'travel_mode': leg.travel_mode, 'transition': leg.transition,
                          'phase': leg.phase, 'stop_index': leg.stop_index,
                          'parked_coordinate': leg.parked_coordinate.model_dump() if leg.parked_coordinate else None,
                          'speed_limit_mps': leg.speed_limit_mps, 'road_class': leg.road_class, 'grade': leg.grade}
                         for leg, end in zip(timeline.legs, timeline.ends)]}

    @app.post('/api/command')
    async def command(request: Request):
        nonlocal authorized_session
        data = WebCommand.model_validate(await read_body(request))
        if data.op not in PARAMETERS or data.op in {'hello', 'shutdown'}:
            raise ValueError('Unsupported web operation')
        # Use the same strict boundary schema as the private helper.
        params = PARAMETERS[data.op].model_validate(data.params)
        if data.op in {'connect', 'device.prepare'} and not data.authorized:
            raise BackendError('authorization_required', 'Select your iPhone and explicitly authorize this action first.')
        if data.op == 'device.prepare' and data.session_id and data.session_id != authorized_session:
            raise BackendError('authorization_required', 'Return to device selection and authorize this iPhone before setup.')
        if data.op in {'retry', 'location.set', 'route.start', 'pause', 'resume', 'speed', 'timer'} and (not data.session_id or data.session_id != authorized_session):
            raise BackendError('authorization_required', 'Connect and authorize the selected iPhone before applying changes.')
        if data.op == 'location.set':
            require_coordinate(params.coordinate)
        if data.op in {'route.start', 'route.preview', 'route.reverse', 'gpx.export'}:
            require_route(params.route)
        if data.op == 'store.put':
            if 'route' in params.bucket:
                require_route(Route.model_validate(params.payload))
            else:
                require_coordinate(Coordinate.model_validate(params.payload))
        try:
            # Preserve an omitted transport on connect: the default Auto value
            # is not explicit authorization to prepare wireless access.
            result = await helper.call(data.op, params.model_dump(exclude_unset=data.op == 'connect'), data.session_id, data.request_id)
        except BackendError:
            if data.op == 'connect':
                # An authorized connect can create a selected session and then fail
                # setup. Keep its exact identity authorized for an explicit retry;
                # do not authorize whichever unrelated session happens to exist.
                with contextlib.suppress(Exception):
                    failed = await helper.call('status')
                    if failed.get('device_id') == params.device_id and failed.get('session_id'):
                        authorized_session = failed['session_id']
            raise
        if data.op == 'connect':
            authorized_session = result['session_id']
        if data.op == 'disconnect':
            authorized_session = None
        if data.op == 'gpx.inspect':
            for segment in result['segments']:
                for point in segment['points']:
                    require_coordinate(Coordinate(latitude=point['latitude'], longitude=point['longitude']))
        if data.op == 'gpx.import':
            require_route(Route.model_validate(result['route']))
        if data.op == 'store.get':
            payload = result['item']['payload']
            if 'route' in params.bucket:
                require_route(Route.model_validate(payload))
            else:
                require_coordinate(Coordinate.model_validate(payload))
        return result

    if WEB_DIST.is_dir():
        app.mount('/assets', StaticFiles(directory=WEB_DIST / 'assets'), name='assets')

    @app.get('/')
    async def index():
        if not WEB_DIST.joinpath('index.html').is_file():
            return JSONResponse({'error': 'Frontend is not built. Run npm run build first.'}, status_code=503)
        return FileResponse(WEB_DIST / 'index.html')

    @app.get('/favicon.svg')
    async def favicon():
        return FileResponse(WEB_DIST / 'favicon.svg', media_type='image/svg+xml')

    return app


def main(*, desktop_instance=None, parent_pid=None):
    import uvicorn
    from .desktop import watch_parent
    from .instance import WebInstance
    app = create_app(instance=WebInstance(ROOT), desktop_instance=desktop_instance)
    class LocalServer(uvicorn.Server):
        async def shutdown(self, sockets=None):
            # Finish SSE streams before uvicorn waits for open HTTP requests.
            # Otherwise an ordinary Ctrl-C waits for its timeout and logs errors.
            app.state.stopping.set()
            await super().shutdown(sockets)
    server = LocalServer(uvicorn.Config(app, host='127.0.0.1', port=3000, access_log=False,
        log_level='warning', timeout_graceful_shutdown=2))
    app.state.request_exit = lambda: setattr(server, 'should_exit', True)
    with watch_parent(parent_pid, app.state.request_exit):
        server.run()


if __name__ == '__main__':
    main()
