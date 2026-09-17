#!/usr/bin/env python3
"""Apply the sole source patch to the EXACT locked upstream module; fail on drift."""
import hashlib
import importlib.util
import os
import tempfile
from pathlib import Path

path = Path(importlib.util.find_spec("pymobiledevice3.restore.tss").origin)
source = path.read_text()
marker = "# OpenLocation verified HTTPS TSS patch v1"
if marker not in source:
    expected = Path(__file__).with_name("tss-upstream.sha256").read_text().strip()
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit("TSS source differs from reviewed 11.12.4; refusing to patch")
    begin = source.index("    async def send_receive(self) -> TSSResponse:")
    end = source.index("\n\n@dataclass(frozen=True)", begin)
    replacement = '''    async def send_receive(self) -> TSSResponse:
        # OpenLocation verified HTTPS TSS patch v1
        from openlocation_backend.preparation import send_tss
        return TSSResponse(await send_tss(self._request))
'''
    patched = source[:begin] + replacement + source[end:]
    # The upstream HTTP constant is also replaced so no insecure fallback remains.
    patched = patched.replace('"http://gs.apple.com/TSS/controller?action=2"', '"https://gs.apple.com/TSS/controller?action=2"')
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".py")
    try:
        with os.fdopen(fd, "w") as output:
            output.write(patched)
        os.replace(temporary, path)  # never modify a possible uv cache hardlink
    finally:
        Path(temporary).unlink(missing_ok=True)
print("Reviewed TSS HTTPS patch present")
