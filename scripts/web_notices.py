"""Inventory the locked frontend packages and preserve their supplied licenses."""
import json
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
web = root / 'web'
lock = json.loads((web / 'package-lock.json').read_text())
inventory = []
for path, entry in sorted(lock['packages'].items()):
    if not path:
        continue
    package_dir = web / path
    metadata_path = package_dir / 'package.json'
    if not metadata_path.exists():
        continue  # optional packages for a different OS/architecture are not installed
    metadata = json.loads(metadata_path.read_text())
    name = metadata['name']
    inventory.append({'name': name, 'version': metadata['version'],
        'license': metadata.get('license'), 'source': entry.get('resolved'),
        'integrity': entry.get('integrity'), 'development': entry.get('dev', False)})
    for file in package_dir.iterdir():
        if file.is_file() and file.name.lower().startswith(('license', 'licence', 'copying', 'notice')):
            destination = web / 'licenses' / name.replace('/', '__') / file.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, destination)
(web / 'DEPENDENCIES.json').write_text(json.dumps(inventory, indent=2) + '\n')
print(f'Preserved frontend notices for {len(inventory)} installed packages')
