"""Finalize only our generated, ad-hoc-signed Mach-O artifacts for local use.

macOS can tag generated native files with quarantine in this host environment.
Do not modify system settings, source dependencies, or files outside this output.
"""
import subprocess
import sys
from pathlib import Path

if sys.platform == "darwin":
    root = Path(__file__).resolve().parent.parent / "dist/OpenLocationBackend"
    magic = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
             b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
    count = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            continue
        with path.open("rb") as file:
            native = file.read(4) in magic
        if not native:
            continue
        probe = subprocess.run(["/usr/bin/xattr", "-p", "com.apple.quarantine", str(path)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            subprocess.run(["/usr/bin/xattr", "-d", "com.apple.quarantine", str(path)], check=True)
            count += 1
    print(f"Finalized {count} generated native files for local development use")
