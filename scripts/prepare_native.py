"""Ad-hoc sign a private copy of cryptography's extension for macOS build analysis.

uv may hardlink cache files: never codesign an installed inode in place before
replacing it with a private copy. No global cache or system security changes.
"""
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.platform == "darwin":
    dist = importlib.metadata.distribution("cryptography")
    for item in dist.files:
        if str(item).startswith("cryptography/") and str(item).endswith(".so"):
            target = Path(dist.locate_file(item))
            fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".so")
            os.close(fd)
            try:
                # A fresh locally-built copy must not inherit downloaded-file quarantine
                # metadata. Preserve only bytes/mode; do not alter system Gatekeeper policy.
                shutil.copyfile(target, temporary)
                os.chmod(temporary, target.stat().st_mode & 0o777)
                subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", temporary], check=True)
                os.replace(temporary, target)
                # macOS can attach quarantine to a newly generated native file too.
                # Remove only that attribute from this reviewed project-local build copy.
                probe = subprocess.run(["/usr/bin/xattr", "-p", "com.apple.quarantine", str(target)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if probe.returncode == 0:
                    subprocess.run(["/usr/bin/xattr", "-d", "com.apple.quarantine", str(target)], check=True)

            finally:
                Path(temporary).unlink(missing_ok=True)
