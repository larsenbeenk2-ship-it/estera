"""Host policy and read-only Apple USB prerequisites. No device authorization."""
import asyncio
import os
import platform
import sys
from dataclasses import asdict, dataclass

from .errors import BackendError


@dataclass(frozen=True)
class HostPolicy:
    os: str
    architecture: str
    supported: bool = True
    os_version: str = ""

    @property
    def usb_only(self):
        return True

    @property
    def automatic_recovery(self):
        return self.os == "windows"

    @property
    def mux_options(self):
        # Never inherit a remote USBMUXD_SOCKET_ADDRESS on Windows.
        return {"usbmux_address": "127.0.0.1:27015"} if self.os == "windows" else {}

    def ensure_supported(self):
        if not self.supported:
            raise BackendError("unsupported_host", "Estera requires macOS or Windows 10 22H2 / Windows 11 on an Intel or AMD 64-bit PC. Windows ARM and 32-bit are not supported in this version.")

    def normalize_transport(self, transport):
        self.ensure_supported()
        if transport not in {"auto", "usb"}:
            raise BackendError("unsupported_transport", "Estera uses USB only. Connect your iPhone using a USB data cable and keep it connected.")
        # Accept older saved Auto choices without enabling a wireless fallback.
        return "usb"

    def validate_preparation(self, options):
        self.ensure_supported()
        if options.enable_wifi:
            raise BackendError("unsupported_transport", "Estera setup uses USB only and cannot enable Wi-Fi pairing.")

    def as_dict(self):
        return asdict(self)


def detect_host():
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    if sys.platform == "darwin":
        return HostPolicy("macos", arch, True, platform.mac_ver()[0])
    if sys.platform == "win32":
        native_arch = os.environ.get("PROCESSOR_ARCHITEW6432", os.environ.get("PROCESSOR_ARCHITECTURE", "")).lower()
        if native_arch == "arm64":
            arch = "arm64"
        version = sys.getwindowsversion()
        supported = arch == "x64" and sys.maxsize > 2**32 and version.major >= 10 and version.build >= 19045
        return HostPolicy("windows", arch, supported, f"{version.major}.{version.minor}.{version.build}")
    return HostPolicy("unsupported", arch, False, platform.release())


HOST_POLICY = detect_host()


def _apple_service_state():
    """Query SCM only; never start, install, stop, or modify a service."""
    import win32service
    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        try:
            service = win32service.OpenService(manager, "Apple Mobile Device Service", win32service.SERVICE_QUERY_STATUS)
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1060:
                return "missing"
            raise
        try:
            return "running" if win32service.QueryServiceStatus(service)[1] == win32service.SERVICE_RUNNING else "stopped"
        finally:
            win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(manager)


async def check_host(policy=None, *, service_probe=None, mux_probe=None):
    policy = policy or HOST_POLICY
    result = {"host": policy.as_dict(), "supported": policy.supported, "usb_service": "not_required", "message": "USB support is provided by macOS."}
    if not policy.supported:
        return {**result, "usb_service": "unavailable", "message": "Use Windows 10 22H2 or Windows 11 on an Intel/AMD 64-bit PC, or a supported Mac. Windows ARM and 32-bit are not supported yet."}
    if policy.os != "windows":
        return result
    try:
        async with asyncio.timeout(3):
            state = await asyncio.to_thread(service_probe or _apple_service_state)
    except Exception:
        state = "unavailable"
    try:
        if mux_probe is None:
            from pymobiledevice3.usbmux import list_devices
            mux_probe = list_devices
        async with asyncio.timeout(3):
            await mux_probe(**policy.mux_options)
        return {**result, "usb_service": "ready", "message": "Apple USB support is ready. Connect your iPhone with a data cable and unlock it."}
    except Exception:
        messages = {
            "missing": "Apple USB support is missing. Install Apple Devices using Apple's official Windows instructions, then reconnect your iPhone. An existing compatible iTunes installation can also provide USB support.",
            "stopped": "Apple Mobile Device Service is stopped. Open Apple Devices or follow Apple's service troubleshooting instructions, then check again.",
        }
        return {**result, "usb_service": state if state in messages else "unavailable",
                "message": messages.get(state, "Estera cannot reach Apple's local USB service. Check Apple Devices or your existing iTunes installation, then try again.")}
