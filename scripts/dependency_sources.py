#!/usr/bin/env python3
"""Bundle locked dependency sources without executing downloaded package code.

Run with backend/.venv/bin/python scripts/dependency_sources.py. Downloads are
cached below build/dependency-sources and verified against pinned SHA-256 hashes.
This complements the application's source archive; see MANIFEST.json for scope.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import importlib.metadata as metadata
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tomllib
import urllib.parse
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parent.parent
GEOS = {
    "name": "geos",
    "version": "3.13.1",
    "url": "https://download.osgeo.org/geos/geos-3.13.1.tar.bz2",
    "sha256": "df2c50503295f325e7c8d7b783aca8ba4773919cde984193850cf9e361dfd28c",
    "hash_source": "https://lists.buildroot.org/pipermail/buildroot/2025-April/776878.html",
    "upstream": "https://libgeos.org/usage/download/",
    "license": "LGPL-2.1",
    "reason": "Shapely 2.1.2 macOS wheel bundles GEOS 3.13.1",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def screen_archive(path):
    """Read member names only; never extract or execute upstream content."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
    prohibited = [name for name in names if PurePosixPath(name).suffix.lower()
                  in {".dmg", ".ipsw", ".img4", ".aea", ".mobileprovision"}]
    if prohibited:
        raise ValueError(f"Refusing image/provisioning payload in {path.name}: {prohibited}")


def download(record, stage):
    parsed = urllib.parse.urlparse(record["url"])
    if parsed.scheme != "https" or parsed.hostname not in {"files.pythonhosted.org", "download.osgeo.org"}:
        raise ValueError(f"Unexpected source host: {record['url']}")
    name = Path(urllib.parse.unquote(parsed.path)).name
    target = stage / record["category"] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or digest(target) != record["sha256"]:
        temporary = target.with_name(target.name + ".part")
        request = urllib.request.Request(record["url"], headers={"User-Agent": "Estera-source-bundler/0.1.0"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if digest(temporary) != record["sha256"]:
                raise ValueError(f"SHA-256 mismatch for {name}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    screen_archive(target)
    return {**record, "path": target.relative_to(stage).as_posix(), "size": target.stat().st_size}


def installed_inventory(stage):
    records = []
    for distribution in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        licenses = []
        for item in distribution.files or []:
            parts = PurePosixPath(str(item)).parts
            if not any(part.endswith(".dist-info") for part in parts):
                continue
            if not any(word in item.name.lower() for word in ("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            index = next(i for i, part in enumerate(parts) if part.endswith(".dist-info"))
            relative = Path("licenses", name, *parts[index + 1:])
            if ".." in relative.parts:
                raise ValueError(f"Unexpected license path: {item}")
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            licenses.append({"path": relative.as_posix(), "sha256": digest(destination)})
        records.append({
            "name": name,
            "version": distribution.version,
            "declared_license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
            "license_classifiers": [value for value in distribution.metadata.get_all("Classifier", []) if value.startswith("License ::")],
            "license_files": licenses,
        })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    stage = ROOT / "build/dependency-sources"
    stage.mkdir(parents=True, exist_ok=True)
    lock_path = ROOT / "backend/uv.lock"
    lock = tomllib.loads(lock_path.read_text())
    sources = []
    missing = []
    for package in lock["package"]:
        sdist = package.get("sdist")
        if sdist:
            algorithm, sha256 = sdist["hash"].split(":", 1)
            if algorithm != "sha256":
                raise ValueError(f"Unexpected lock hash: {algorithm}")
            sources.append({"name": package["name"], "version": package["version"],
                            "url": sdist["url"], "sha256": sha256, "category": "pypi",
                            "hash_source": "build-inputs/backend/uv.lock"})
        else:
            missing.append({"name": package["name"], "version": package["version"],
                            "reason": "Local application is supplied in the separate application source archive."
                            if package["source"].get("editable") else
                            "No sdist is published in the lock; Windows-only pywin32 is not installed in this macOS build."})

    # Inspect installed binary filenames, without importing or loading Shapely.
    shapely = metadata.distribution("shapely")
    versions = {match.group(1) for item in shapely.files or []
                if (match := re.fullmatch(r"libgeos\.(\d+\.\d+\.\d+)\.dylib", item.name))}
    if versions != {GEOS["version"]}:
        raise ValueError(f"Installed GEOS version changed; update the source pin: {versions}")
    sources.append({**GEOS, "category": "native"})
    downloaded = []
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.jobs, 8))) as pool:
        pending = {pool.submit(download, record, stage): record for record in sources}
        for future in as_completed(pending):
            try:
                downloaded.append(future.result())
            except Exception as error:
                failures.append({"name": pending[future]["name"], "error": str(error)})
            if (len(downloaded) + len(failures)) % 10 == 0:
                print(f"Processed {len(downloaded) + len(failures)}/{len(sources)} source archives", flush=True)
    if failures:
        raise SystemExit(json.dumps({"download_failures": failures}, indent=2))

    inputs = []
    for relative in ("backend/uv.lock", "backend/requirements.lock.txt", "backend/pyproject.toml",
                     "scripts/patch_tss.py", "scripts/tss-upstream.sha256", "scripts/dependency_sources.py"):
        source = ROOT / relative
        destination = stage / "build-inputs" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        inputs.append({"path": destination.relative_to(stage).as_posix(), "sha256": digest(destination)})
    inventory = installed_inventory(stage)
    frozen = ROOT / "dist/OpenLocationBackend/_internal"
    native_files = sorted({path.relative_to(frozen).as_posix() for path in frozen.rglob("*")
                           if path.is_file() and path.suffix in {".so", ".dylib"}}) if frozen.exists() else []
    manifest = {
        "format": 1,
        "application": "Estera 0.1.0",
        "scope": "Dependency source companion; not a complete self-contained toolchain or all-source archive.",
        "lock_sha256": digest(lock_path),
        "sources": sorted(downloaded, key=lambda record: record["name"]),
        "build_inputs": inputs,
        "installed_build_environment": inventory,
        "frozen_native_inventory_at_packaging": native_files,
        "omitted_locked_packages": missing,
        "coverage_notes": [
            "All published PyPI sdists recorded in uv.lock are included, including build/dev and non-macOS packages. This is a superset of the frozen runtime.",
            "GEOS 3.13.1 is included separately because the Shapely sdist does not contain the GEOS library bundled in its wheel.",
            "pylzss (LGPL-3.0), PyInstaller bootloader, and GPL Python dependencies are included via their exact locked sdists.",
            "Exact TSS patch script and upstream hash are included; its openlocation_backend.preparation implementation is in the companion application source archive.",
            "Upstream archives were SHA-256 verified and inspected for prohibited image/provisioning filenames; downloaded code was not executed.",
            "Installed distribution license texts are included without selecting only the packages frozen by PyInstaller.",
        ],
        "limitations": [
            "This bundle does not include CPython, the Swift/Xcode/macOS SDK toolchain, or every permissively licensed native library used by dependency wheels (such as Pillow image libraries, OpenSSL, BoringSSL, and zstd).",
            "This bundle does not vendor all transitive Rust/Cargo, build-backend, or compiler dependencies needed for a fully offline source rebuild.",
            "GEOS is upstream 3.13.1 source; the exact third-party wheel build environment and any unreported downstream wheel modifications are not reconstructed.",
            "The frozen native inventory records whichever existing helper build was present while packaging and is not proof of the later final app's exact contents.",
            "No claim is made that this archive alone is a complete Corresponding Source distribution for every possible platform or binary; distribute with the matching application source archive and notices.",
            "Apple developer images, device backups, provisioning profiles, pairing records, credentials, and user state are excluded.",
        ],
    }
    (stage / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (stage / "README.txt").write_text(
        "Estera 0.1.0 dependency source companion\n\n"
        "Read MANIFEST.json for exact URLs, hashes, installed licenses, and limitations.\n"
        "pypi/ contains the unmodified source archives pinned by backend/uv.lock.\n"
        "native/ contains GEOS 3.13.1 source (LGPL-2.1).\n"
        "build-inputs/ preserves the lock and exact TSS patch inputs.\n"
        "Use this with the matching Estera application source archive. The latter\n"
        "contains the application code and instructions for rebuilding/replacing\n"
        "the helper and dynamically linked dependencies. This is not a complete\n"
        "offline toolchain or a claim that every bundled library's source is here.\n"
    )
    # Archive only current manifest-selected inputs, never stale cache downloads.
    included = {"MANIFEST.json", "README.txt"}
    included.update(record["path"] for record in downloaded + inputs)
    included.update(item["path"] for record in inventory for item in record["license_files"])
    output = ROOT / "dist/Estera-0.1.0-dependency-sources.tar.gz"
    output.parent.mkdir(exist_ok=True)
    with output.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|") as archive:
            for relative in sorted(included):
                content = (stage / relative).read_bytes()
                info = tarfile.TarInfo("Estera-0.1.0-dependency-sources/" + relative)
                info.size = len(content)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(content))
    print(json.dumps({"archive": str(output), "bytes": output.stat().st_size,
                      "sha256": digest(output), "sources": len(downloaded)}, indent=2))


if __name__ == "__main__":
    main()
