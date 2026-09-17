"""Developer-image failure paths use fake services and HTTP; never contact an iPhone or Apple."""
import asyncio
import hashlib
import plistlib
import ssl
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import truststore

from openlocation_backend import preparation
from openlocation_backend.errors import BackendError


def http_mock(monkeypatch, handler):
    client = httpx.AsyncClient

    def create(**options):
        assert isinstance(options['verify'], truststore.SSLContext)
        assert options['verify'].verify_mode == ssl.CERT_REQUIRED
        assert options['verify'].check_hostname is True
        assert options['trust_env'] is False
        assert options['follow_redirects'] is False
        return client(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(preparation.httpx, 'AsyncClient', create)


@pytest.fixture
def mounter(monkeypatch):
    from pymobiledevice3.restore.tss import TSSRequest
    from pymobiledevice3.services import mobile_image_mounter

    state = SimpleNamespace(events=[], active=0, mounted=[False, False], failure=None, blocked=None)
    paths = tuple(Path(name) for name in ('Image.dmg', 'BuildManifest.plist', 'Image.dmg.trustcache'))

    async def patched_send(self):
        from openlocation_backend.preparation import send_tss
        return await send_tss({})

    class Mounter:
        def __init__(self, phone):
            assert phone is state

        async def __aenter__(self):
            assert state.active == 0
            state.active += 1
            state.events.append('open')
            return self

        async def __aexit__(self, *_):
            state.active -= 1
            state.events.append('close')

        async def is_image_mounted(self, image_type):
            assert image_type == 'Personalized'
            state.events.append('check')
            return state.mounted.pop(0)

        async def mount(self, image, build_manifest, trust_cache):
            assert (image, build_manifest, trust_cache) == paths
            assert all(isinstance(path, Path) for path in (image, build_manifest, trust_cache))
            state.events.append('mount')
            if state.blocked:
                state.blocked.set()
                await asyncio.Future()
            if state.failure:
                raise state.failure

    async def files(*_):
        assert state.active == 0, 'No phone service may remain idle during file downloads'
        state.events.append('files')
        return paths

    monkeypatch.setattr(TSSRequest, 'send_receive', patched_send)
    monkeypatch.setattr(mobile_image_mounter, 'PersonalizedImageMounter', Mounter)
    monkeypatch.setattr(preparation, 'image_files', files)
    return state


@pytest.mark.parametrize('mounted,expected,events', [
    ([False, False], 'mounted', ['open', 'check', 'close', 'files', 'open', 'check', 'mount', 'close']),
    ([True], 'already_mounted', ['open', 'check', 'close']),
    ([False, True], 'already_mounted', ['open', 'check', 'close', 'files', 'open', 'check', 'close']),
])
async def test_fresh_service_after_files_and_already_mounted_fast_paths(mounter, tmp_path, mounted, expected, events):
    mounter.mounted = mounted
    progress = []
    result = await preparation.mount(mounter, tmp_path, True, lambda stage, _: progress.append(stage))
    assert result == {'image': expected}
    assert mounter.events == events and not mounter.active
    assert progress[0] == 'checking_developer_image'
    assert ('personalizing_and_mounting' in progress) is ('files' in events)


@pytest.mark.parametrize('kind,code', [
    ('PyMobileDevice3Exception', 'developer_image_mount_failed'),
    ('NoSuchBuildIdentityError', 'developer_image_unsupported'),
    ('DeveloperModeIsNotEnabledError', 'developer_mode_required'),
    ('PasswordRequiredError', 'unlock_required'),
    ('InvalidServiceError', 'preparation_required'),
])
async def test_mount_failures_keep_actionable_errors_without_device_details(mounter, tmp_path, kind, code):
    from pymobiledevice3 import exceptions
    error_type = getattr(exceptions, kind)
    args = ('private-device-id/private-apple-reply',)
    if issubclass(error_type, exceptions.LockdownError):
        args += ('private-device-id', '26.6.2')
    mounter.failure = error_type(*args)
    with pytest.raises(BackendError) as caught:
        await preparation.mount(mounter, tmp_path, True, lambda *_: None)
    assert caught.value.code == code and 'private-' not in caught.value.message
    assert not mounter.active and mounter.events[-1] == 'close'
    if code == 'developer_image_mount_failed':
        assert kind in caught.value.message and 'confirmed on' in caught.value.message


@pytest.mark.parametrize('prefix,error,expected', [
    ('command ReceiveBytes failed with: ', 'DeviceLocked', 'unlock_required'),
    ('command ReceiveBytes failed to send bytes with: ', 'DeviceLocked', 'unlock_required'),
    ('command MountImage failed with: ', 'DeviceLocked', 'unlock_required'),
    ('command ReceiveBytes failed with: ', 'ImageCorrupt', 'developer_image_mount_failed'),
    ('unrecognized failure: ', 'DeviceLocked', 'developer_image_mount_failed'),
])
async def test_wrapped_mount_device_locked_guidance_is_sanitized(mounter, tmp_path, prefix, error, expected):
    from pymobiledevice3.exceptions import PyMobileDevice3Exception

    response = {'Error': error, 'UnrelatedDeviceDetails': 'private-device-id DeviceLocked'}
    mounter.failure = PyMobileDevice3Exception(prefix + repr(response))
    with pytest.raises(BackendError) as caught:
        await preparation.mount(mounter, tmp_path, False, lambda *_: None)
    assert caught.value.code == expected
    assert 'private-' not in caught.value.message and 'DeviceLocked' not in caught.value.message
    assert not mounter.active and mounter.events[-1] == 'close'
    if expected == 'unlock_required':
        assert 'Home Screen' in caught.value.message


async def test_cancel_mount_closes_service_and_propagates_cancellation(mounter, tmp_path):
    mounter.blocked = asyncio.Event()
    task = asyncio.create_task(preparation.mount(mounter, tmp_path, True, lambda *_: None))
    await mounter.blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not mounter.active and mounter.events[-1] == 'close'


def developer_payloads():
    from pymobiledevice3.services.mobile_image_mounter import LATEST_DDI_BUILD_ID
    image, trust = b'fake developer image', b'fake trust cache'
    identities = [
        {'Manifest': {
            'PersonalizedDMG': {'Digest': hashlib.new(algorithm, image).digest()},
            'LoadableTrustCache': {'Digest': hashlib.new(algorithm, trust).digest()},
        }} for algorithm in ('sha1', 'sha384')
    ]
    identities.append({'Manifest': {
        'PersonalizedDMG': {},
        'LoadableTrustCache': {'Digest': 'not a byte digest'},
    }})
    return {
        'Image.dmg': image,
        'BuildManifest.plist': plistlib.dumps({'ProductBuildVersion': LATEST_DDI_BUILD_ID, 'BuildIdentities': identities}),
        'Image.dmg.trustcache': trust,
    }


@pytest.mark.parametrize('failure', [None, 'Image.dmg', 'Image.dmg.trustcache', 'sha384_mismatch', 'no_usable_digest'])
async def test_manifest_digest_variants_preserve_strict_validation(tmp_path, failure):
    from pymobiledevice3.services.mobile_image_mounter import LATEST_DDI_BUILD_ID

    payloads = developer_payloads()
    if failure in {'Image.dmg', 'Image.dmg.trustcache'}:
        payloads[failure] = b'corrupted payload'
    elif failure in {'sha384_mismatch', 'no_usable_digest'}:
        manifest = plistlib.loads(payloads['BuildManifest.plist'])
        if failure == 'sha384_mismatch':
            # The matching SHA1 must not override a published SHA384 mismatch.
            manifest['BuildIdentities'][1]['Manifest']['PersonalizedDMG']['Digest'] = b'\0' * 48
        else:
            for identity in manifest['BuildIdentities']:
                identity['Manifest']['PersonalizedDMG']['Digest'] = ['not', 'bytes']
        payloads['BuildManifest.plist'] = plistlib.dumps(manifest)
    files = []
    for name, contents in payloads.items():
        path = tmp_path / name
        path.write_bytes(contents)
        files.append(path)

    if failure is None:
        await preparation._validate_image_files(files, LATEST_DDI_BUILD_ID)
    else:
        with pytest.raises(BackendError) as caught:
            await preparation._validate_image_files(files, LATEST_DDI_BUILD_ID)
        assert caught.value.code == 'developer_image_cache_invalid'


async def test_download_exact_assets_validate_cache_and_repair_only_when_authorized(monkeypatch, tmp_path):
    payloads, requests = developer_payloads(), []

    def handler(request):
        name = request.url.path.rsplit('/', 1)[1]
        assert request.method == 'GET' and str(request.url) == f'{preparation.DDI_ROOT}/{name}'
        requests.append(name)
        return httpx.Response(200, content=payloads[name])

    http_mock(monkeypatch, handler)
    paths = await preparation.image_files(tmp_path, True, lambda *_: None)
    assert [p.name for p in paths] == list(payloads) == requests
    assert [p.read_bytes() for p in paths] == list(payloads.values())
    assert await preparation.image_files(tmp_path, False, lambda *_: None) == paths
    assert len(requests) == 3
    paths[0].write_bytes(b'damaged')
    with pytest.raises(BackendError, match='saved developer files') as caught:
        await preparation.image_files(tmp_path, False, lambda *_: None)
    assert caught.value.code == 'developer_image_cache_invalid' and len(requests) == 3
    await preparation.image_files(tmp_path, True, lambda *_: None)
    assert paths[0].read_bytes() == payloads['Image.dmg'] and len(requests) == 6


@pytest.mark.parametrize('failure,expected', [
    ('http', 'developer_image_download_failed'),
    ('network', 'developer_image_download_failed'),
    ('cache_xml', 'developer_image_cache_invalid'),
    ('build', 'developer_image_build_mismatch'),
])
async def test_download_and_manifest_failures_are_sanitized(monkeypatch, tmp_path, failure, expected):
    payloads = developer_payloads()
    if failure == 'cache_xml':
        payloads['BuildManifest.plist'] = b'<?xml version="1.0"?><plist>private-device-id'
    if failure == 'build':
        manifest = plistlib.loads(payloads['BuildManifest.plist'])
        manifest['ProductBuildVersion'] = 'private-device-id'
        payloads['BuildManifest.plist'] = plistlib.dumps(manifest)

    def handler(request):
        if failure == 'http':
            return httpx.Response(404, content=b'private-device-id')
        if failure == 'network':
            raise httpx.ConnectError('private-device-id', request=request)
        return httpx.Response(200, content=payloads[request.url.path.rsplit('/', 1)[1]])

    http_mock(monkeypatch, handler)
    with pytest.raises(BackendError) as caught:
        await preparation.image_files(tmp_path, True, lambda *_: None)
    assert caught.value.code == expected and 'private-' not in caught.value.message
    assert not list(tmp_path.rglob('*.partial'))


async def test_cancel_download_removes_partial_file(monkeypatch, tmp_path):
    blocked = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'x' * (256 * 1024)
            blocked.set()
            await asyncio.Future()

    http_mock(monkeypatch, lambda _: httpx.Response(200, stream=Stream()))
    task = asyncio.create_task(preparation.image_files(tmp_path, True, lambda *_: None))
    await blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(tmp_path.rglob('*.partial')) and not list(tmp_path.rglob('Image.dmg'))


async def test_tss_success_uses_exact_endpoint_and_plist_payload(monkeypatch):
    expected = {'@ApImg4Ticket': True, 'ApECID': 123, 'ApNonce': b'fake nonce'}

    def handler(request):
        assert request.method == 'POST' and str(request.url) == preparation.TSS_URL
        assert plistlib.loads(request.content) == expected
        assert request.headers['content-type'] == 'text/xml; charset=utf-8'
        return httpx.Response(200, content=b'STATUS=0&MESSAGE=SUCCESS&REQUEST_STRING=' + plistlib.dumps({'ApImg4Ticket': b'fake ticket'}))

    http_mock(monkeypatch, handler)
    assert await preparation.send_tss(expected) == {'ApImg4Ticket': b'fake ticket'}


@pytest.mark.parametrize('failure,expected', [
    ('http', 'apple_personalization_http_failed'),
    ('network', 'apple_personalization_network_failed'),
    ('reject', 'apple_personalization_rejected'),
    ('invalid_status', 'apple_personalization_rejected'),
    ('malformed_xml', 'apple_personalization_invalid_response'),
    ('malformed_header', 'apple_personalization_invalid_response'),
])
async def test_tss_errors_do_not_expose_request_or_apple_body(monkeypatch, failure, expected):
    def handler(request):
        if failure == 'http':
            return httpx.Response(503, content=b'private-device-id')
        if failure == 'network':
            raise httpx.ConnectError('private-device-id', request=request)
        bodies = {
            'reject': b'STATUS=94&MESSAGE=private-device-id',
            'invalid_status': b'STATUS=private-device-id&MESSAGE=private-device-id',
            'malformed_xml': b'STATUS=0&MESSAGE=SUCCESS&REQUEST_STRING=<?xml version="1.0"?><plist>private-device-id',
            'malformed_header': b'private-device-id',
        }
        return httpx.Response(200, content=bodies[failure])

    http_mock(monkeypatch, handler)
    with pytest.raises(BackendError) as caught:
        await preparation.send_tss({'@ApImg4Ticket': True, 'secret': 'private-device-id'})
    assert caught.value.code == expected and 'private-' not in caught.value.message
    if failure == 'reject':
        assert 'status 94' in caught.value.message
