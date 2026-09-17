"""Bounded iPhone service discovery through macOS's DNS-SD daemon.

The daemon owns multicast sockets and interface selection. Each PTR is resolved
to SRV/TXT, then to A/AAAA on its originating interface. No phone is contacted.
DNSServiceRefSockFD is watched by asyncio; every reader and reference is owned
by the browse call, including when its caller cancels it.
"""

import asyncio
import ctypes
import ipaddress
import math
import socket
import sys
from dataclasses import dataclass, field

from pymobiledevice3.bonjour import Address, ServiceInstance


_MAX_SERVICES = 32
_MAX_ADDRESSES = 16
_ADD = 0x2
_FORCE_MULTICAST = 0x400
_NO_SUCH_RECORD = -65554
_DAEMON_ERRORS = {-65563, -65569, -65570, -65571}
_REMOTEPAIRING = b"_remotepairing._tcp"
_MOBDEV2 = b"_apple-mobdev2._tcp"

_REF = ctypes.c_void_p
_U32 = ctypes.c_uint32
_I32 = ctypes.c_int32
_U16 = ctypes.c_uint16
_STR = ctypes.c_char_p
_BROWSE_REPLY = ctypes.CFUNCTYPE(None, _REF, _U32, _U32, _I32, _STR, _STR, _STR, _REF)
_RESOLVE_REPLY = ctypes.CFUNCTYPE(None, _REF, _U32, _U32, _I32, _STR, _STR, _U16, _U16, _REF, _REF)
_ADDRESS_REPLY = ctypes.CFUNCTYPE(None, _REF, _U32, _U32, _I32, _STR, _REF, _U32, _REF)


class BonjourError(RuntimeError):
    """DNS-SD failed; the message deliberately contains no network identifiers."""

    def __init__(self, code):
        self.code = code
        super().__init__(f"Bonjour discovery failed (DNS-SD error {code}).")


class _NativeDNS:
    def __init__(self):
        # libSystem exports the public dns_sd.h API on supported macOS versions.
        self.library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        signatures = {
            "DNSServiceBrowse": ([ctypes.POINTER(_REF), _U32, _U32, _STR, _STR, _BROWSE_REPLY, _REF], _I32),
            "DNSServiceResolve": ([ctypes.POINTER(_REF), _U32, _U32, _STR, _STR, _STR, _RESOLVE_REPLY, _REF], _I32),
            "DNSServiceGetAddrInfo": ([ctypes.POINTER(_REF), _U32, _U32, _U32, _STR, _ADDRESS_REPLY, _REF], _I32),
            "DNSServiceRefSockFD": ([_REF], ctypes.c_int),
            "DNSServiceProcessResult": ([_REF], _I32),
            "DNSServiceRefDeallocate": ([_REF], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)


class _Operation:
    """Keep the C callback alive until its DNSServiceRef has been deallocated."""

    def __init__(self, owner, callback_type, handler, function, *arguments):
        self.owner, self.handler = owner, handler
        self.ref, self.fd = _REF(), -1
        self.processing = self.closing = False
        self.callback = callback_type(self._deliver)
        error = function(ctypes.byref(self.ref), *arguments, self.callback, None)
        if error:
            # An unsuccessful start leaves the output reference uninitialized.
            self.ref = _REF()
            raise BonjourError(error)
        owner.operations.add(self)
        try:
            self.fd = owner.native.DNSServiceRefSockFD(self.ref)
            if self.fd < 0:
                raise RuntimeError("Bonjour discovery could not open its daemon connection.")
            owner.loop.add_reader(self.fd, self._readable)
        except BaseException:
            self.close()
            raise

    def _deliver(self, *arguments):
        if self.closing or self.owner.closed:
            return
        try:
            self.handler(self, *arguments)
        except Exception:
            # Exceptions must never escape a ctypes callback (which prints them).
            self.owner.fail(RuntimeError("Bonjour discovery could not process a daemon reply."))
            self.close()

    def _readable(self):
        if self.closing or not self.ref.value or self.owner.closed:
            return
        self.processing = True
        try:
            error = self.owner.native.DNSServiceProcessResult(self.ref)
            if error:
                self.owner.fail(BonjourError(error))
                self.closing = True
        except Exception:
            self.owner.fail(RuntimeError("Bonjour discovery lost its daemon connection."))
            self.closing = True
        finally:
            self.processing = False
            if self.closing:
                self.close()

    def close(self):
        self.closing = True
        # A resolve callback can retire its own reference. Wait until the C call
        # returns before freeing that reference or its callback's storage.
        if self.processing or not self.ref.value:
            return
        ref, self.ref = self.ref, _REF()
        try:
            if self.fd >= 0:
                self.owner.loop.remove_reader(self.fd)
        finally:
            self.owner.native.DNSServiceRefDeallocate(ref)
            self.owner.operations.discard(self)
            self.callback = self.handler = None


class _ScopedAddress(Address):
    __slots__ = ()

    @property
    def full_ip(self):
        # Upstream only scopes strings beginning with fe80:, although IPv6's
        # link-local range is fe80::/10. Preserve every link-local address.
        if self.iface and ":" in self.ip and ipaddress.IPv6Address(self.ip).is_link_local:
            return f"{self.ip}%{self.iface}"
        return self.ip


def _address(pointer, interface):
    if not pointer:
        return None
    # Darwin sockaddr starts with one-byte length and family fields.
    length, family = ctypes.string_at(pointer, 2)
    scope = interface
    if family == socket.AF_INET and length >= 16:
        raw = ctypes.string_at(pointer, 16)
        ip = socket.inet_ntop(socket.AF_INET, raw[4:8])
    elif family == socket.AF_INET6 and length >= 28:
        raw = ctypes.string_at(pointer, 28)
        ip = socket.inet_ntop(socket.AF_INET6, raw[8:24])
        scope = int.from_bytes(raw[24:28], sys.byteorder) or interface
    else:
        return None
    # Reserved DNS-SD interface indices are not valid IPv6 zone identifiers.
    scope = scope if 0 < scope < 0xFFFFFFFC else 0
    iface = None
    if scope:
        try:
            iface = socket.if_indextoname(scope)
        except OSError:
            iface = str(scope)  # Numeric IPv6 zones also preserve the interface.
    if family == socket.AF_INET6 and ipaddress.IPv6Address(ip).is_link_local and not iface:
        return None
    return _ScopedAddress(ip=ip, iface=iface)


def _properties(data):
    result = {}
    position = 0
    while position < len(data) and len(result) < 64:
        length = data[position]
        position += 1
        if position + length > len(data):
            break
        item = data[position:position + length]
        position += length
        key, _, value = item.partition(b"=")
        if key:
            result.setdefault(key.decode("utf-8", "replace"), value.decode("utf-8", "replace"))
    return result


@dataclass
class _Service:
    interface: int
    result: ServiceInstance | None = None
    resolve: _Operation | None = None
    lookup: _Operation | None = None
    addresses: dict = field(default_factory=dict)

    def close(self):
        if self.resolve:
            self.resolve.close()
        if self.lookup:
            self.lookup.close()


class _Browser:
    def __init__(self, service_type=_REMOTEPAIRING):
        self.loop = asyncio.get_running_loop()
        self.native = _NativeDNS()
        self.service_type = service_type
        self.error = self.loop.create_future()
        self.operations = set()
        self.services = {}
        self.closed = False

    def fail(self, error):
        if not self.error.done():
            self.error.set_exception(error)

    def start(self):
        _Operation(self, _BROWSE_REPLY, self._browse, self.native.DNSServiceBrowse,
                   0, 0, self.service_type, b"local.")

    def _browse(self, operation, ref, flags, interface, error, name, regtype, domain, context):
        if error:
            self.fail(BonjourError(error))
            operation.close()
            return
        if not name or not regtype or not domain:
            return
        key = (name, regtype.lower(), domain.lower(), interface)
        if not flags & _ADD:
            service = self.services.pop(key, None)
            if service:
                service.close()
            return
        if key in self.services or len(self.services) >= _MAX_SERVICES:
            return
        service = _Service(interface)
        self.services[key] = service
        try:
            service.resolve = _Operation(
                self, _RESOLVE_REPLY, lambda *args: self._resolve(service, *args),
                self.native.DNSServiceResolve, _FORCE_MULTICAST, interface, name, regtype, domain,
            )
        except BonjourError as exc:
            if exc.code in _DAEMON_ERRORS:
                self.fail(exc)

    def _resolve(self, service, operation, ref, flags, interface, error,
                 fullname, hostname, port, txt_length, txt_pointer, context):
        operation.close()
        if error:
            if error in _DAEMON_ERRORS:
                self.fail(BonjourError(error))
            return
        if not fullname or not hostname or not port:
            return
        properties = _properties(ctypes.string_at(txt_pointer, txt_length)) if txt_pointer and txt_length <= 4096 else {}
        service.result = ServiceInstance(
            instance=fullname.decode("utf-8", "replace"),
            host=hostname.decode("utf-8", "replace").rstrip("."),
            port=socket.ntohs(port), properties=properties,
        )
        # Resolve can return the concrete interface for a special browse index.
        service.interface = interface or service.interface
        try:
            service.lookup = _Operation(
                self, _ADDRESS_REPLY, lambda *args: self._addresses(service, *args),
                self.native.DNSServiceGetAddrInfo, _FORCE_MULTICAST, service.interface,
                0x1 | 0x2, hostname,  # Explicitly ask for IPv4 and IPv6.
            )
        except BonjourError as exc:
            if exc.code in _DAEMON_ERRORS:
                self.fail(exc)

    def _addresses(self, service, operation, ref, flags, interface, error, hostname, pointer, ttl, context):
        if error:
            if error in _DAEMON_ERRORS:
                self.fail(BonjourError(error))
            # A missing AAAA record must not discard a valid IPv4 response.
            if error != _NO_SUCH_RECORD:
                operation.close()
            return
        address = _address(pointer, interface or service.interface)
        if address is None:
            return
        key = (address.ip, address.iface)
        if not flags & _ADD or ttl == 0:
            service.addresses.pop(key, None)
        elif len(service.addresses) < _MAX_ADDRESSES:
            service.addresses[key] = address

    def results(self):
        merged = {}
        for service in self.services.values():
            item = service.result
            if item is None or not service.addresses:
                continue
            key = (item.instance.casefold(), item.host.casefold(), item.port)
            result = merged.setdefault(key, item)
            existing = {(address.ip, address.iface) for address in result.addresses}
            for address in service.addresses.values():
                if (address.ip, address.iface) not in existing and len(result.addresses) < _MAX_ADDRESSES:
                    result.addresses.append(address)
                    existing.add((address.ip, address.iface))
        return list(merged.values())

    def close(self):
        self.closed = True
        for operation in tuple(self.operations):
            operation.close()
        self.services.clear()
        if not self.error.done():
            self.error.cancel()


async def browse_remotepairing(timeout=3) -> list[ServiceInstance]:
    """Discover remote-pairing endpoints without contacting or trusting a phone."""
    return await _browse(_REMOTEPAIRING, timeout)


async def browse_mobdev2(timeout=3) -> list[ServiceInstance]:
    """Discover wireless lockdown endpoints without contacting or trusting a phone."""
    return await _browse(_MOBDEV2, timeout)


async def _browse(service_type, timeout) -> list[ServiceInstance]:
    """Return resolved advertisements collected within one overall timeout.

    At most 32 interface/service entries and 16 addresses per returned service
    are retained. Timeout returns the partial resolved results; cancellation and
    DNS-SD daemon/policy failures propagate after cleanup. Non-macOS uses the
    pinned upstream browser. Advertisements do not authenticate a phone.
    """
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Bonjour timeout must be a finite nonnegative number.")
    if timeout == 0:
        return []
    if sys.platform != "darwin":
        from pymobiledevice3 import bonjour as upstream

        upstream_browse = upstream.browse_mobdev2 if service_type == _MOBDEV2 else upstream.browse_remotepairing
        return await upstream_browse(timeout=timeout)
    browser = _Browser(service_type)
    try:
        async with asyncio.timeout(timeout):
            browser.start()
            await browser.error
    except TimeoutError:
        return browser.results()
    finally:
        browser.close()
