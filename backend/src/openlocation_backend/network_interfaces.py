"""Identify configured LAN interfaces without accepting a phone's USB IP link.

SystemConfiguration owns the interface metadata. An ``en`` prefix or a Bonjour
advertisement alone cannot distinguish Wi-Fi/Ethernet from a cable-only link.
This is an allowlist snapshot, not a reachability or routing check.
"""

import ctypes
import sys


_LAN_TYPES = frozenset({"IEEE80211", "Ethernet", "Bridge", "VLAN", "Bond"})
_PHONE_NAMES = ("iphone", "ipad", "ipod", "tether")
_MAX_INTERFACES = 128
_MAX_LAYERS = 8
_UTF8 = 0x08000100


class _NativeInterfaces:
    def __init__(self):
        self.sc = ctypes.CDLL(
            "/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration"
        )
        self.cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        pointer = ctypes.c_void_p
        signatures = {
            "SCNetworkInterfaceCopyAll": ([], pointer),
            "SCNetworkInterfaceGetBSDName": ([pointer], pointer),
            "SCNetworkInterfaceGetInterfaceType": ([pointer], pointer),
            "SCNetworkInterfaceGetLocalizedDisplayName": ([pointer], pointer),
            "SCNetworkInterfaceGetInterface": ([pointer], pointer),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.sc, name)
            function.argtypes, function.restype = arguments, result
        signatures = {
            "CFArrayGetCount": ([pointer], ctypes.c_long),
            "CFArrayGetValueAtIndex": ([pointer, ctypes.c_long], pointer),
            "CFStringGetLength": ([pointer], ctypes.c_long),
            "CFStringGetCString": (
                [pointer, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32], ctypes.c_ubyte
            ),
            "CFRelease": ([pointer], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.cf, name)
            function.argtypes, function.restype = arguments, result


def _string(cf, value):
    if not value:
        return None
    length = cf.CFStringGetLength(value)
    if not 0 < length <= 1024:
        return None
    # CF length counts UTF-16 code units; four UTF-8 bytes per unit is sufficient.
    buffer = ctypes.create_string_buffer(length * 4 + 1)
    if not cf.CFStringGetCString(value, buffer, len(buffer), _UTF8):
        return None
    return buffer.value.decode("utf-8")


def _is_lan(native, interface):
    seen = set()
    for _ in range(_MAX_LAYERS):
        if not interface or interface in seen:
            return False
        seen.add(interface)
        kind = _string(native.cf, native.sc.SCNetworkInterfaceGetInterfaceType(interface))
        display = _string(native.cf, native.sc.SCNetworkInterfaceGetLocalizedDisplayName(interface))
        if kind not in _LAN_TYPES or not display:
            return False
        if any(name in display.casefold() for name in _PHONE_NAMES):
            return False
        interface = native.sc.SCNetworkInterfaceGetInterface(interface)
        if not interface:
            return True
    return False


def local_network_interfaces() -> set[str] | None:
    """Return configured macOS LAN names; unknown metadata is never permitted.

    ``None`` means no platform restriction outside macOS. An empty set on macOS
    means no eligible interface was found or the metadata could not be read.
    Wi-Fi, Ethernet and their supported logical layers are allowed, excluding
    phone/tethering interfaces even when they report the Ethernet type. The
    localized display label is the public API's phone-interface discriminator;
    absent labels are conservatively rejected. No network or device is contacted.
    """
    if sys.platform != "darwin":
        return None
    try:
        native = _NativeInterfaces()
        interfaces = native.sc.SCNetworkInterfaceCopyAll()
        if not interfaces:
            return set()
        try:
            count = native.cf.CFArrayGetCount(interfaces)
            if not 0 <= count <= _MAX_INTERFACES:
                return set()
            allowed, rejected = set(), set()
            for index in range(count):
                interface = native.cf.CFArrayGetValueAtIndex(interfaces, index)
                if not interface:
                    continue
                name = _string(native.cf, native.sc.SCNetworkInterfaceGetBSDName(interface))
                if not name:
                    continue
                (allowed if _is_lan(native, interface) else rejected).add(name)
            return allowed - rejected
        finally:
            # CopyAll owns one array reference. All Get results are borrowed.
            native.cf.CFRelease(interfaces)
    except (OSError, AttributeError, ValueError, TypeError):
        return set()
