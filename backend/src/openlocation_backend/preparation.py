"""Cancelable TLS-verified preparation. Never calls upstream synchronous downloads."""
import asyncio
import ast
import hashlib
import os
import plistlib
import re
import ssl
from pathlib import Path
from xml.parsers.expat import ExpatError

import httpx
import truststore
from .errors import BackendError, public_error

DDI_REVISION = "5423e4e955fbb3a9eef3e1212acfbfc6e7a26236"
DDI_ROOT = f"https://raw.githubusercontent.com/doronz88/DeveloperDiskImage/{DDI_REVISION}/PersonalizedImages/Xcode_iOS_DDI_Personalized"
TSS_URL = "https://gs.apple.com/TSS/controller?action=2"


async def reveal_developer_mode(lockdown, progress):
    """Reveal the Settings toggle only. Never enable, reboot or confirm for a user."""
    from pymobiledevice3.services.amfi import AmfiService

    progress("developer_mode_setting", {"notice": "Making the Developer Mode setting visible. Enable it and confirm the restart on your iPhone."})
    # The upstream reveal method leaves service cleanup to its caller; own the
    # short-lived service explicitly while using the pinned action constant.
    async with await lockdown.start_lockdown_service(AmfiService.SERVICE_NAME) as service:
        response = await service.send_recv_plist({"action": AmfiService.DEVELOPER_MODE_REVEAL})
    if not response.get("success"):
        raise BackendError("developer_mode_reveal_failed", "The iPhone could not show the Developer Mode setting. Unlock it and try again. If this is a managed phone, ask its administrator whether development is allowed.", True)
    return True


async def send_tss(request: dict) -> dict:
    """Verified HTTPS personalization with errors that never expose the request or reply."""
    try:
        payload = plistlib.dumps(request)
    except (TypeError, ValueError, OverflowError):
        raise BackendError("apple_personalization_failed", "The app could not prepare its developer-image request for Apple. Update or reinstall the helper.") from None
    try:
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), trust_env=False, timeout=20, follow_redirects=False) as client:
                async with client.stream("POST", TSS_URL, content=payload, headers={
                    "Content-Type": "text/xml; charset=utf-8", "Cache-Control": "no-cache", "User-Agent": "InetURL/1.0",
                }) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2 * 1024 * 1024:
                            raise BackendError("apple_personalization_invalid_response", "Apple returned an oversized developer-image response. Try preparation again later.", True)
    except httpx.HTTPStatusError as exc:
        raise BackendError("apple_personalization_http_failed", f"Apple's developer-image service returned HTTP {exc.response.status_code}. Try preparation again later.", True) from None
    except (httpx.RequestError, TimeoutError):
        raise BackendError("apple_personalization_network_failed", "The app could not reach Apple's developer-image service securely. Check your computer's internet connection, then try preparation again.", True) from None

    header, separator, body = bytes(data).partition(b"&REQUEST_STRING=")
    fields = dict(field.split(b"=", 1) for field in header.split(b"&") if b"=" in field)
    status = fields.get(b"STATUS", b"")
    if b"MESSAGE" not in fields and b"STATUS" not in fields:
        raise BackendError("apple_personalization_invalid_response", "Apple returned an unreadable developer-image response. Try preparation again later.", True)
    if fields.get(b"MESSAGE") != b"SUCCESS" or status not in {b"", b"0"}:
        # Apple may echo device information in MESSAGE; only a bounded numeric status is public.
        suffix = f" (status {status.decode('ascii')})" if re.fullmatch(rb"[0-9]{1,5}", status) else ""
        raise BackendError("apple_personalization_rejected", f"Apple declined developer-image personalization{suffix}. Developer Mode is already enabled. Try preparation again; if this repeats, the helper may need an updated developer image.", True)
    try:
        result = plistlib.loads(body) if separator else None
        if not isinstance(result, dict) or (request.get("@ApImg4Ticket") and not isinstance(result.get("ApImg4Ticket"), bytes)):
            raise ValueError
        return result
    except (ValueError, TypeError, plistlib.InvalidFileException, ExpatError):
        raise BackendError("apple_personalization_invalid_response", "Apple returned an unreadable developer-image response. Try preparation again later.", True) from None


def _invalid_cache():
    return BackendError("developer_image_cache_invalid", "The saved developer files are incomplete or damaged. Choose Prepare developer services to download them again.", True)


async def _validate_image_files(files, expected_build):
    """Check the pinned manifest and its strongest published payload digests, cancelably."""
    try:
        if not 0 < files[1].stat().st_size <= 16 * 1024 * 1024:
            raise _invalid_cache()
        manifest = plistlib.loads(files[1].read_bytes())
        if not isinstance(manifest, dict):
            raise _invalid_cache()
        if manifest.get("ProductBuildVersion") != expected_build:
            raise BackendError("developer_image_build_mismatch", "The developer files do not match this helper's required build. Update or reinstall the helper before preparing this iPhone.")
        identities = manifest["BuildIdentities"]
        if not isinstance(identities, list):
            raise _invalid_cache()
        for path, component, limit in ((files[0], "PersonalizedDMG", 512 * 1024 * 1024),
                                       (files[2], "LoadableTrustCache", 16 * 1024 * 1024)):
            if not 0 < path.stat().st_size <= limit:
                raise _invalid_cache()
            digests = set()
            for identity in identities:
                components = identity.get("Manifest") if isinstance(identity, dict) else None
                entry = components.get(component) if isinstance(components, dict) else None
                value = entry.get("Digest") if isinstance(entry, dict) else None
                # Some build variants omit a payload digest. Only published byte
                # digests for supported algorithms can validate this shared file.
                if isinstance(value, bytes) and len(value) in {20, 48}:
                    digests.add(value)
            length = 48 if any(len(digest) == 48 for digest in digests) else 20
            expected = {digest for digest in digests if len(digest) == length}
            if not expected:
                raise _invalid_cache()
            digest = hashlib.new("sha384" if length == 48 else "sha1")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    await asyncio.sleep(0)
            if digest.digest() not in expected:
                raise _invalid_cache()
    except BackendError:
        raise
    except (OSError, ValueError, TypeError, KeyError, plistlib.InvalidFileException, ExpatError):
        raise _invalid_cache() from None


async def image_files(cache: Path, allow_download: bool, progress):
    from pymobiledevice3.services.mobile_image_mounter import LATEST_DDI_BUILD_ID
    from .private_files import ensure_private_directory
    directory = cache / "developer-images" / DDI_REVISION
    try:
        ensure_private_directory(cache)
        ensure_private_directory(directory)
    except OSError:
        raise BackendError("developer_image_cache_unwritable", "The app could not save its developer files. Check free disk space and access to the app's local data, then try again.", True) from None
    names = ("Image.dmg", "BuildManifest.plist", "Image.dmg.trustcache")
    files = [directory / n for n in names]
    if all(f.is_file() for f in files):
        try:
            await _validate_image_files(files, LATEST_DDI_BUILD_ID)
            return files
        except BackendError as exc:
            if not allow_download or exc.code != "developer_image_cache_invalid":
                raise
    elif not allow_download:
        raise BackendError("developer_image_download_required", "Developer files are missing. Choose Prepare developer services to allow their download.", True)
    try:
        async with httpx.AsyncClient(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), trust_env=False, timeout=30, follow_redirects=False) as client:
            for name, destination in zip(names, files):
                limit = 512 * 1024 * 1024 if name.endswith("dmg") else 16 * 1024 * 1024
                temporary = destination.with_suffix(destination.suffix + ".partial")
                try:
                    async with client.stream("GET", f"{DDI_ROOT}/{name}") as response:
                        response.raise_for_status()
                        received = 0
                        with temporary.open("wb") as output:
                            async for chunk in response.aiter_bytes(256 * 1024):
                                received += len(chunk)
                                if received > limit:
                                    raise BackendError("developer_image_download_failed", "The developer-file download exceeded its expected size. Update the helper or try preparation again later.", True)
                                output.write(chunk)
                                total = response.headers.get("content-length", "")
                                progress("download", {"file": name, "received_bytes": received,
                                          "total_bytes": int(total) if total.isascii() and total.isdigit() else None})
                            output.flush()
                            os.fsync(output.fileno())
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
    except httpx.HTTPStatusError as exc:
        raise BackendError("developer_image_download_failed", f"The developer-file download returned HTTP {exc.response.status_code}. Check your computer's internet connection or try preparation again later.", True) from None
    except httpx.RequestError:
        raise BackendError("developer_image_download_failed", "The app could not download the developer files securely. Check your computer's internet connection, then try preparation again.", True) from None
    except OSError:
        raise BackendError("developer_image_cache_unwritable", "The app could not save its developer files. Check free disk space and access to the app's local data, then try again.", True) from None
    await _validate_image_files(files, LATEST_DDI_BUILD_ID)
    return files


def _mount_response_is_locked(exc):
    if type(exc).__module__ != "pymobiledevice3.exceptions" or type(exc).__name__ != "PyMobileDevice3Exception":
        return False
    message = exc.args[0] if len(exc.args) == 1 else None
    if not isinstance(message, str) or len(message) > 4096:
        return False
    # The pinned mounter wraps the device's plist response in these exact strings.
    # Parse only bounded literals; neither the response nor its details are public.
    for prefix in ("command ReceiveBytes failed with: ",
                   "command ReceiveBytes failed to send bytes with: ",
                   "command MountImage failed with: "):
        if message.startswith(prefix):
            try:
                response = ast.literal_eval(message[len(prefix):])
            except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
                return False
            return isinstance(response, dict) and response.get("Error") == "DeviceLocked"
    return False


def _mount_error(exc):
    if _mount_response_is_locked(exc):
        return BackendError("unlock_required", "Unlock the selected iPhone, leave it on the Home Screen, then try Prepare developer services again. Keep the iPhone unlocked until preparation finishes.", True)
    error = public_error(exc)
    if not isinstance(exc, BackendError) and error.code == "preparation_required":
        return BackendError("preparation_required", "Developer Mode was confirmed on, but the iPhone's developer service is unavailable. Unlock it, reconnect USB, then try preparation again.", True)
    if isinstance(exc, BackendError) or error.code in {
        "setup_required", "unlock_required", "developer_mode_required", "preparation_required",
        "dependency_missing", "permission_denied",
    }:
        return error
    if type(exc).__name__ == "NoSuchBuildIdentityError":
        return BackendError("developer_image_unsupported", "The bundled developer image does not support this iPhone. Update the helper before trying preparation again.")
    kind = type(exc)
    reference = f" Reference: {kind.__name__}." if kind.__module__ in {"builtins", "pymobiledevice3.exceptions"} and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", kind.__name__) else ""
    return BackendError("developer_image_mount_failed", "Developer Mode was confirmed on, but the iPhone could not prepare its developer services. Unlock it, unplug and reconnect USB, then try preparation again." + reference, True)


async def mount(lockdown, cache: Path, allow_download: bool, progress):
    from pymobiledevice3.services.mobile_image_mounter import PersonalizedImageMounter
    from pymobiledevice3.restore.tss import TSSRequest
    # Fail closed even when someone bypasses bootstrap and uses an unpatched environment.
    if "openlocation_backend.preparation" not in TSSRequest.send_receive.__code__.co_names:
        raise BackendError("preparation_unavailable", "Secure TSS patch missing; run scripts/bootstrap and rebuild the helper.")
    progress("checking_developer_image", {"notice": "Developer Mode is on. Checking the iPhone's developer services."})
    try:
        async with PersonalizedImageMounter(lockdown) as mounter:
            if await mounter.is_image_mounted("Personalized"):
                return {"image": "already_mounted"}
    except Exception as exc:
        raise _mount_error(exc) from None
    # Downloads and cache validation can take time. Do not leave a device service idle
    # across them; open a fresh connection and recheck in case another app prepared it.
    files = await image_files(cache, allow_download, progress)
    progress("personalizing_and_mounting", {"notice": "Developer Mode is on. Preparing developer services; Apple may receive device-specific preparation data."})
    try:
        async with PersonalizedImageMounter(lockdown) as mounter:
            if await mounter.is_image_mounted("Personalized"):
                return {"image": "already_mounted"}
            await mounter.mount(*files)
    except Exception as exc:
        if type(exc).__name__ == "AlreadyMountedError":
            return {"image": "already_mounted"}
        raise _mount_error(exc) from None
    return {"image": "mounted"}
