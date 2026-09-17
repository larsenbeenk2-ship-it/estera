"""Archive only the desktop build and explicitly tracked corresponding source."""
import hashlib
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
dist = root / 'dist'
app = dist / 'Estera.app'
archive = dist / 'Estera-0.1.0-macos-arm64.zip'
archive.unlink(missing_ok=True)
subprocess.run(['/usr/bin/ditto', '-c', '-k', '--keepParent', str(app), str(archive)], check=True)
digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n')
print(f'Desktop download: {archive}')
