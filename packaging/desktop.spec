# One-directory runtime copied intact into Estera.app/Contents/Resources.
from pathlib import Path
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
web_dist = root / 'web' / 'dist'
if not (web_dist / 'index.html').is_file():
    raise SystemExit('Build the application frontend before packaging the desktop runtime.')

# Keep the upstream dynamic transport imports used by the existing helper build.
hidden = (
    collect_submodules('pymobiledevice3.osu')
    + collect_submodules('pmd_pytcp')
    + collect_submodules('pmd_net_addr')
    + collect_submodules('pmd_net_proto')
    + collect_submodules('uvicorn')
    + ['pymobiledevice3.remote.userspace_tunnel', 'pymobiledevice3.remote.native_tunnel']
)
data = collect_data_files('pymobiledevice3') + collect_data_files('certifi')
# Recursive metadata preserves distribution versions and their supplied licenses
# for all runtime dependencies, including the web server's dynamic imports.
data += copy_metadata('openlocation-backend', recursive=True)
data += [
    (str(web_dist), 'web/dist'),
    (str(root / 'backend/src/openlocation_backend/data'), 'openlocation_backend/data'),
    (str(root / 'LICENSE'), '.'),
    (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
]
if (root / 'web/licenses').is_dir():
    data.append((str(root / 'web/licenses'), 'licenses/web'))
python_license = Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'
if python_license.is_file():
    data.append((str(python_license), 'licenses/python'))

a = Analysis(
    [str(root / 'packaging/desktop_entrypoint.py')],
    pathex=[str(root / 'backend/src')], binaries=[], datas=data, hiddenimports=hidden,
    hookspath=[], runtime_hooks=[], excludes=['pytest', 'IPython', 'tkinter', 'matplotlib'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='estera-desktop', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True,
    target_arch='arm64', codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='EsteraBackend')
