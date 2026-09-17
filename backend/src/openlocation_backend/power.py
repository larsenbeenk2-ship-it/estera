"""Scoped idle-sleep assertions and Windows suspend/resume notifications."""
import asyncio
import contextlib
import ctypes
import sys
import threading
from typing import Callable


class PowerAssertion:
    def __init__(self):
        self.identifier = ctypes.c_uint32(0)
        self.iokit = None
        self.kernel = None
        self.owner_thread = None
        self.suspended = False
        self.suspend_epoch = 0
        self._watch_generation = 0
        self._registration = None
        self._native_callback = None
        self._parameters = None
        self._loop = None
        self._callback = None

    def acquire(self):
        if sys.platform == "win32":
            if self.owner_thread is not None:
                self._require_owner_thread()
                return
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.SetThreadExecutionState.argtypes = [wintypes.DWORD]
            self.kernel.SetThreadExecutionState.restype = wintypes.DWORD
            if not self.kernel.SetThreadExecutionState(0x80000001):  # CONTINUOUS | SYSTEM_REQUIRED
                raise OSError("Could not acquire the Windows idle-sleep assertion")
            self.owner_thread = threading.get_ident()
            return
        if sys.platform != "darwin" or self.identifier.value:
            return
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        create = self.iokit.IOPMAssertionCreateWithName
        create.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        create.restype = ctypes.c_int
        kind = cf.CFStringCreateWithCString(None, b"PreventUserIdleSystemSleep", 0x08000100)
        name = cf.CFStringCreateWithCString(None, b"Estera location session", 0x08000100)
        try:
            if create(kind, 255, name, ctypes.byref(self.identifier)) != 0:
                raise RuntimeError("Could not acquire idle-sleep assertion")
        finally:
            cf.CFRelease(kind)
            cf.CFRelease(name)

    def release(self):
        if self.owner_thread is not None:
            self._require_owner_thread()
            if not self.kernel.SetThreadExecutionState(0x80000000):  # CONTINUOUS clears SYSTEM_REQUIRED
                raise OSError("Could not release the Windows idle-sleep assertion")
            self.owner_thread = None
            return
        if self.identifier.value and self.iokit:
            self.iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
            self.iokit.IOPMAssertionRelease(self.identifier)
            self.identifier = ctypes.c_uint32(0)

    def _require_owner_thread(self):
        if self.owner_thread != threading.get_ident():
            raise RuntimeError("Windows idle-sleep assertion must be released on its acquiring thread")

    def watch(self, callback: Callable[[bool], None]):
        """Deliver True on suspend and False on resume to the registering loop."""
        if sys.platform != "win32":
            return
        self.unwatch()
        self.suspended = False
        self._loop = asyncio.get_running_loop()
        self._callback = callback
        from ctypes import wintypes
        callback_type = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)

        class Parameters(ctypes.Structure):
            _fields_ = [("Callback", callback_type), ("Context", ctypes.c_void_p)]

        self._native_callback = callback_type(lambda _context, kind, _setting: self._power_event(kind))
        self._parameters = Parameters(self._native_callback, None)
        self._power_api = ctypes.WinDLL("powrprof", use_last_error=True)
        register = self._power_api.PowerRegisterSuspendResumeNotification
        register.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
        register.restype = wintypes.DWORD
        self._power_api.PowerUnregisterSuspendResumeNotification.argtypes = [wintypes.HANDLE]
        self._power_api.PowerUnregisterSuspendResumeNotification.restype = wintypes.DWORD
        registration = wintypes.HANDLE()
        error = register(2, ctypes.byref(self._parameters), ctypes.byref(registration))  # DEVICE_NOTIFY_CALLBACK
        if error:
            self._native_callback = self._parameters = self._callback = self._loop = None
            raise OSError(error, "Could not watch Windows suspend/resume")
        self._registration = registration

    def _power_event(self, kind):
        if kind not in {4, 7, 18}:  # PBT_APMSUSPEND, RESUMESUSPEND, RESUMEAUTOMATIC
            return 0
        suspended = kind == 4
        if suspended == self.suspended:
            return 0
        # This happens on the OS callback thread before the loop can issue a write.
        self.suspended = suspended
        if suspended:
            self.suspend_epoch += 1
        generation = self._watch_generation
        loop = self._loop
        if loop is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._deliver_power_event, generation, suspended)
        return 0

    def _deliver_power_event(self, generation, suspended):
        if generation == self._watch_generation and self._callback is not None:
            self._callback(suspended)

    def unwatch(self):
        self._watch_generation += 1
        if self._registration is not None:
            error = self._power_api.PowerUnregisterSuspendResumeNotification(self._registration)
            if error:
                # Keep the native function and its context alive if unregistration fails.
                raise OSError(error, "Could not stop watching Windows suspend/resume")
            self._registration = None
        self._native_callback = self._parameters = self._callback = self._loop = None
