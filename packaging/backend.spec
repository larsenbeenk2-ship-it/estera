# PyInstaller one-directory helper. Keep _internal adjacent to the executable.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
# Do not import CLI/backup/app-access surfaces just to collect every upstream module.
# Userspace TCP and the OS-specific factory use dynamic imports: include these trees.
hidden = (collect_submodules("pymobiledevice3.osu") + collect_submodules("pmd_pytcp") + collect_submodules("pmd_net_addr") + collect_submodules("pmd_net_proto") +
          ["pymobiledevice3.remote.userspace_tunnel", "pymobiledevice3.remote.native_tunnel"])
data = collect_data_files("pymobiledevice3") + collect_data_files("certifi")
for package in ("pymobiledevice3", "openlocation-backend", "gpxpy", "httpx", "pydantic", "developer-disk-image", "pmd-pytcp", "truststore"):
    data += copy_metadata(package)
a = Analysis([str(root / "packaging/entrypoint.py")],
    pathex=[str(root / "backend/src")], binaries=[], datas=data, hiddenimports=hidden,
    hookspath=[], runtime_hooks=[], excludes=["pytest", "IPython", "tkinter", "matplotlib"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="openlocation-backend", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True, target_arch="arm64",
          codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OpenLocationBackend")
