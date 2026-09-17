"""Synthetic Bonjour identities; no local pairing records or device connections."""

from types import SimpleNamespace

import pytest

from openlocation_backend.wireless_identity import matches_paired_service


HOST_ID = "00112233-4455-6677-8899-AABBCCDDEEFF"
IDENTIFIER = "unit-test-bonjour-identifier"
# Fixed vector computed independently with RFC 5869 extract/expand using
# stdlib HMAC-SHA512, then HMAC-SHA256 truncated to eight bytes.
AUTH_TAG = "xdSd/bWDo4g="


def service(properties=None, mac="02:00:00:00:00:02"):
    return SimpleNamespace(instance=f"{mac}@unit-test._apple-mobdev2._tcp.local.",
                           properties=properties if properties is not None else {})


def test_modern_known_vector_matches_despite_private_mac():
    record = {"HostID": HOST_ID, "WiFiMACAddress": "00:11:22:33:44:55"}
    answer = service({"identifier": IDENTIFIER, "authTag": AUTH_TAG})
    assert matches_paired_service(answer, record)


def test_modern_matches_a_later_tag_and_trims_ascii_whitespace():
    answer = service({"identifier": IDENTIFIER, "authTag": "invalid",
                      "authTag#1": f" \t{AUTH_TAG}\r\n"})
    assert matches_paired_service(answer, {"HostID": HOST_ID})


def test_txt_keys_are_case_insensitive_but_values_are_not():
    answer = service({"IDENTIFIER": IDENTIFIER, "AUTHTAG#1": AUTH_TAG})
    assert matches_paired_service(answer, {"HostID": HOST_ID})
    answer.properties["IDENTIFIER"] = IDENTIFIER.upper()
    assert not matches_paired_service(answer, {"HostID": HOST_ID})


def test_modern_rejects_different_selected_host():
    answer = service({"identifier": IDENTIFIER, "authTag": AUTH_TAG})
    assert not matches_paired_service(answer, {"HostID": "different-test-host"})


@pytest.mark.parametrize("auth_tag", [
    "!!!", "", None, 123, "xdSd/bWDo4g=garbage", "xdSd/bWDo4g=!",
    "xdSd/bWDo4gA",  # Nine decoded bytes, including an otherwise matching prefix.
    "xdSd/bWDow==",  # Seven decoded bytes.
    "x" * 256, "\ud800", "☃",
])
def test_modern_rejects_malformed_tags(auth_tag):
    answer = service({"identifier": IDENTIFIER, "authTag": auth_tag})
    assert not matches_paired_service(answer, {"HostID": HOST_ID})


@pytest.mark.parametrize("identifier", [None, 123, "", "x" * 256, "\ud800", "é" * 128])
def test_modern_rejects_invalid_or_oversized_identifier(identifier):
    assert not matches_paired_service(service({"identifier": identifier, "authTag": AUTH_TAG}),
                                      {"HostID": HOST_ID})


@pytest.mark.parametrize("record", [None, [], {}, {"HostID": 123}, {"HostID": ""},
                                  {"HostID": "x" * 129}, {"HostID": "\ud800"}])
def test_modern_rejects_invalid_pair_record(record):
    assert not matches_paired_service(service({"identifier": IDENTIFIER, "authTag": AUTH_TAG}), record)


def test_modern_rejects_too_many_txt_fields():
    properties = {"identifier": IDENTIFIER, "authTag": AUTH_TAG}
    properties.update({f"extra{index}": "value" for index in range(63)})
    assert not matches_paired_service(service(properties), {"HostID": HOST_ID})


def test_legacy_matches_mac_case_insensitively():
    assert matches_paired_service(service(mac="aA:bB:cC:dD:eE:fF"),
                                 {"WiFiMACAddress": "AA:BB:CC:DD:EE:FF"})


@pytest.mark.parametrize("mac", ["00:11:22:33:44:56", "not-a-mac", "1:2:3:4:5:6", "" ])
def test_legacy_rejects_wrong_or_invalid_mac(mac):
    assert not matches_paired_service(service(mac=mac), {"WiFiMACAddress": "00:11:22:33:44:55"})


def test_legacy_requires_instance_separator():
    answer = SimpleNamespace(instance="00:11:22:33:44:55", properties={})
    assert not matches_paired_service(answer, {"WiFiMACAddress": "00:11:22:33:44:55"})
