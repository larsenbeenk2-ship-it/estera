"""Generate exact installed dependency inventory and preserve distribution licenses."""
import importlib.metadata as metadata
import json
import hashlib
import platform
import shutil
import sysconfig
from pathlib import Path

root = Path(__file__).resolve().parent.parent
output = root / "dist/OpenLocationBackend"
licenses = output / "licenses"
licenses.mkdir(exist_ok=True)
records = []
for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
    name = dist.metadata["Name"]
    record = {"name": name, "version": dist.version, "license": dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See distribution license files",
              "source": "local GPL source" if name == "openlocation-backend" else "https://pypi.org/project/" + name + "/" + dist.version + "/"}
    # Include the full locked build environment inventory; not every distribution is frozen.
    records.append(record)
    for file in dist.files or []:
        if any(token in file.name.lower() for token in ("license", "copying", "notice")) and ".dist-info" in str(file):
            source = Path(dist.locate_file(file))
            if source.is_file():
                destination = licenses / name / str(file).split(".dist-info/", 1)[-1]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
(output / "DEPENDENCIES.json").write_text(json.dumps(records, indent=2))
(output / "BUILD-INFO.json").write_text(json.dumps({"version": "0.1.0", "architecture": platform.machine(), "python": platform.python_version(),
    "macos": platform.mac_ver()[0], "pymobiledevice3": "11.12.4", "security_patch": "TSS HTTPS v1", "signing": "ad-hoc local development", "hardware_tested": False, "lock_sha256": hashlib.sha256((root / "backend/uv.lock").read_bytes()).hexdigest()}, indent=2))
for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
    shutil.copy2(root / name, output / name)
shutil.copytree(root / "docs", output / "docs", dirs_exist_ok=True)

python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
if python_license.is_file():
    shutil.copy2(python_license, licenses / "PYTHON-LICENSE.txt")
