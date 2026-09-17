"""Rebuild coverage: uv run --project backend --with pyshp==3.1.6 python scripts/us_boundary.py."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import httpx
import shapefile
from shapely.geometry import shape
from shapely import union_all, to_wkb

URL = "https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip"
root = Path(__file__).resolve().parents[1]
response = httpx.get(URL, timeout=90, follow_redirects=False)
response.raise_for_status()
if len(response.content) > 50 * 1024 * 1024:
    raise ValueError("Unexpected Census download size")
with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
    def member(suffix):
        name = next(n for n in archive.namelist() if n.endswith(suffix))
        if archive.getinfo(name).file_size > 100 * 1024 * 1024:
            raise ValueError("Unexpected Census member size")
        return io.BytesIO(archive.read(name))
    reader = shapefile.Reader(shp=member('.shp'), shx=member('.shx'), dbf=member('.dbf'))
    states = [record for record in reader.iterShapeRecords() if int(record.record['STATEFP']) < 60]
    assert len(states) == 51, "Expected 50 states and DC"
    geometry = union_all([shape(record.shape.__geo_interface__) for record in states])
    assert geometry.is_valid
directory = root / 'backend/src/openlocation_backend/data'
directory.mkdir(exist_ok=True)
data = to_wkb(geometry)
(directory / 'us.wkb').write_bytes(data)
(directory / 'us-source.json').write_text(json.dumps({
    'source': URL, 'year': 2025, 'coverage': '50 states and DC; territories excluded',
    'license': 'US Census Bureau public domain geographic data',
    'source_sha256': hashlib.sha256(response.content).hexdigest(),
    'wkb_sha256': hashlib.sha256(data).hexdigest(),
    'states': sorted(record.record['STUSPS'] for record in states),
}, indent=2) + '\n')
print(f'Wrote {len(data):,} bytes of US boundary data')
