import hashlib
import platform
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
archive = root / "dist" / f"OpenLocationBackend-0.1.0-macos-{platform.machine()}.tar.gz"
with tarfile.open(archive, "w:gz", dereference=False) as tar:
    tar.add(root / "dist/OpenLocationBackend", arcname="OpenLocationBackend")
# Corresponding source excludes caches, personal data, environments and reference downloads.
source_archive = root / "dist/OpenLocationBackend-0.1.0-source.tar.gz"
with tarfile.open(source_archive, "w:gz") as tar:
    for name in ("backend", "web", "scripts", "packaging", "docs", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore"):
        def allowed(info):
            return None if any(p in {".venv", "node_modules", "dist", "__pycache__", ".pytest_cache"} or p.endswith(".egg-info") for p in Path(info.name).parts) else info
        tar.add(root / name, arcname="OpenLocationBackend-source/" + name, filter=allowed)
for file in (archive, source_archive):
    digest = hashlib.file_digest(file.open("rb"), "sha256").hexdigest()
    file.with_suffix(file.suffix + ".sha256").write_text(f"{digest}  {file.name}\n")
    print(file)
