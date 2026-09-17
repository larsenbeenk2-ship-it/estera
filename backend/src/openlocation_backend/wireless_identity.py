"""Match paired iPhone Bonjour advertisements without revealing pairing data.

iOS 26.4+ uses TXT identifiers and authentication tags instead of exposing the
hardware Wi-Fi MAC. The published matching algorithm is documented by idevice:
https://github.com/jkcoxson/idevice/blob/master/idevice/src/mdns.rs

This selects candidates only. A HostID can be shared across paired devices;
the connection must still authenticate and check the selected device's UDID.
"""

import base64
import binascii
import hashlib
import hmac
import re

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_MAC = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
_ASCII_SPACE = b" \t\n\r\v\f"


def _utf8(value, limit):
    if not isinstance(value, str) or not 0 < len(value) <= limit:
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return None
    return encoded if len(encoded) <= limit else None


def _matches_tags(properties, host_id):
    if not isinstance(properties, dict) or len(properties) > 64:
        return False
    # DNS-SD TXT keys are case-insensitive. Do not normalize their values.
    fields = {key.lower(): value for key, value in properties.items()
              if isinstance(key, str) and len(key) <= 32}
    host = _utf8(host_id, 128)
    identifier = _utf8(fields.get("identifier"), 255)
    if host is None or identifier is None:
        return False
    key = HKDF(algorithm=hashes.SHA512(), length=32, salt=None, info=b"").derive(host)
    expected = hmac.new(key, identifier, hashlib.sha256).digest()[:8]
    for name, value in fields.items():
        if name != "authtag" and not name.startswith("authtag#"):
            continue
        encoded = _utf8(value, 255)
        if encoded is None:
            continue
        try:
            tag = base64.b64decode(encoded.strip(_ASCII_SPACE), validate=True)
        except (ValueError, binascii.Error):
            continue
        if len(tag) == 8 and hmac.compare_digest(tag, expected):
            return True
    return False


def _matches_mac(instance, wifi_mac):
    if not isinstance(instance, str) or len(instance) > 1024 or "@" not in instance:
        return False
    if not isinstance(wifi_mac, str) or len(wifi_mac) != 17:
        return False
    advertised = instance.split("@", 1)[0]
    return bool(_MAC.fullmatch(advertised) and _MAC.fullmatch(wifi_mac)
                and advertised.casefold() == wifi_mac.casefold())


def matches_paired_service(answer, pair_record):
    """Accept a modern paired-host tag or a legacy matching Wi-Fi MAC.

    Input sizes and decoding are bounded. No connection, record mutation,
    secret output, or pairing operation is performed.
    """
    if not isinstance(pair_record, dict):
        return False
    return _matches_tags(getattr(answer, "properties", None), pair_record.get("HostID")) or _matches_mac(
        getattr(answer, "instance", None), pair_record.get("WiFiMACAddress")
    )
