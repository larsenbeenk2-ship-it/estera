"""Interface classification with fake public APIs; no hardware or discovery."""

import ctypes
from types import SimpleNamespace

import pytest

from openlocation_backend import network_interfaces as network


class FakeNative:
    def __init__(self, entries):
        self.entries = entries
        self.released = []
        self.strings = {}
        self.sc = SimpleNamespace(
            SCNetworkInterfaceCopyAll=lambda: 1000,
            SCNetworkInterfaceGetBSDName=lambda ref: self.string(entries[ref].get("name")),
            SCNetworkInterfaceGetInterfaceType=lambda ref: self.string(entries[ref].get("kind")),
            SCNetworkInterfaceGetLocalizedDisplayName=lambda ref: self.string(entries[ref].get("display")),
            SCNetworkInterfaceGetInterface=lambda ref: entries[ref].get("child", 0),
        )
        self.cf = SimpleNamespace(
            CFArrayGetCount=lambda _: len(entries),
            CFArrayGetValueAtIndex=lambda _, index: list(entries)[index],
            CFStringGetLength=lambda ref: len(self.strings[ref].encode("utf-16-le")) // 2,
            CFStringGetCString=self.copy_string,
            CFRelease=self.released.append,
        )

    def string(self, value):
        if value is None:
            return 0
        ref = len(self.strings) + 2000
        self.strings[ref] = value
        return ref

    def copy_string(self, ref, buffer, size, encoding):
        assert encoding == network._UTF8
        encoded = self.strings[ref].encode("utf-8") + b"\0"
        if len(encoded) > size:
            return False
        ctypes.memmove(buffer, encoded, len(encoded))
        return True


@pytest.fixture
def native(monkeypatch):
    def install(entries):
        fake = FakeNative(entries)
        monkeypatch.setattr(network.sys, "platform", "darwin")
        monkeypatch.setattr(network, "_NativeInterfaces", lambda: fake)
        return fake
    return install


def test_only_known_lan_interfaces_are_allowed(native):
    fake = native({
        1: {"name": "en0", "kind": "IEEE80211", "display": "Wi-Fi"},
        2: {"name": "en1", "kind": "Ethernet", "display": "USB 10/100/1000 LAN"},
        3: {"name": "bridge0", "kind": "Bridge", "display": "Thunderbolt Bridge"},
        4: {"name": "en8", "kind": "Ethernet", "display": "iPhone USB"},
        5: {"name": "en9", "kind": "Ethernet", "display": "iPad USB"},
        6: {"name": "pdp_ip0", "kind": "WWAN", "display": "Mobile data"},
        7: {"name": "en10", "kind": "Ethernet"},
        8: {"name": "en12", "kind": "FutureNetwork", "display": "Unknown"},
    })
    assert network.local_network_interfaces() == {"en0", "en1", "bridge0"}
    assert fake.released == [1000]
    # Temporary phone IP interfaces absent from SystemConfiguration stay absent.
    assert "en11" not in network.local_network_interfaces()


def test_layered_interfaces_reject_phone_or_unknown_underlying_links(native):
    native({
        1: {"name": "vlan0", "kind": "VLAN", "display": "Office", "child": 2},
        2: {"name": "en1", "kind": "Ethernet", "display": "Ethernet"},
        3: {"name": "vlan1", "kind": "VLAN", "display": "Phone VLAN", "child": 4},
        4: {"name": "en8", "kind": "Ethernet", "display": "iPHONE USB"},
        5: {"name": "bond0", "kind": "Bond", "display": "Bond", "child": 6},
        6: {"name": "en9", "kind": None, "display": "Unknown"},
    })
    assert network.local_network_interfaces() == {"vlan0", "en1"}


def test_cycles_and_excessive_depth_fail_closed(native):
    entries = {
        index: {"name": f"vlan{index}", "kind": "VLAN", "display": "LAN", "child": index + 1}
        for index in range(1, 11)
    }
    entries[10]["child"] = 10
    native(entries)
    assert network.local_network_interfaces() == set()


def test_conflicting_metadata_for_same_bsd_name_is_rejected(native):
    native({
        1: {"name": "en0", "kind": "IEEE80211", "display": "Wi-Fi"},
        2: {"name": "en0", "kind": "Ethernet", "display": "Phone tethering"},
    })
    assert network.local_network_interfaces() == set()


def test_bad_strings_are_rejected_and_owned_array_is_released(native):
    fake = native({1: {"name": "en0", "kind": "IEEE80211", "display": "Wi-Fi"}})
    fake.cf.CFStringGetCString = lambda *_: False
    assert network.local_network_interfaces() == set()
    assert fake.released == [1000]


def test_exception_discards_partial_result_and_releases_array(native):
    fake = native({1: {"name": "en0", "kind": "IEEE80211", "display": "Wi-Fi"}})

    def fail(_):
        raise OSError("unavailable metadata")

    fake.sc.SCNetworkInterfaceGetInterface = fail
    assert network.local_network_interfaces() == set()
    assert fake.released == [1000]


def test_interface_limit_releases_array_without_reading_entries(native):
    fake = native({})
    fake.cf.CFArrayGetCount = lambda _: network._MAX_INTERFACES + 1
    assert network.local_network_interfaces() == set()
    assert fake.released == [1000]


def test_unicode_display_labels_convert_without_truncation(native):
    native({1: {"name": "en0", "kind": "IEEE80211", "display": "无线局域网 📶"}})
    assert network.local_network_interfaces() == {"en0"}


def test_framework_unavailable_is_fail_closed(monkeypatch):
    monkeypatch.setattr(network.sys, "platform", "darwin")

    def unavailable():
        raise OSError("framework unavailable")

    monkeypatch.setattr(network, "_NativeInterfaces", unavailable)
    assert network.local_network_interfaces() == set()


def test_other_platforms_do_not_load_macos_frameworks(monkeypatch):
    monkeypatch.setattr(network.sys, "platform", "win32")
    monkeypatch.setattr(network, "_NativeInterfaces", lambda: pytest.fail("unexpected native call"))
    assert network.local_network_interfaces() is None
