"""Download and verify the corresponding-source assets for one release."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("third_party_sources.toml")
DEFAULT_OUTPUT = PROJECT_ROOT / "dist" / "release-sources"
BUFFER_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(BUFFER_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    temporary = destination.with_name(f"{destination.name}.part")
    temporary.unlink(missing_ok=True)
    windows_curl = shutil.which("curl.exe") if os.name == "nt" else None
    if windows_curl is not None:
        try:
            subprocess.run(
                [
                    windows_curl,
                    "--fail",
                    "--location",
                    "--retry",
                    "3",
                    "--output",
                    str(temporary),
                    url,
                ],
                check=True,
            )
            temporary.replace(destination)
            return
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Moltage-release-source-preparer/0.2.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            expected_length = response.headers.get("Content-Length")
            total = int(expected_length) if expected_length else None
            received = 0
            last_report = time.monotonic()
            with temporary.open("wb") as stream:
                while chunk := response.read(BUFFER_SIZE):
                    stream.write(chunk)
                    received += len(chunk)
                    if time.monotonic() - last_report >= 5:
                        suffix = f"/{total}" if total is not None else ""
                        print(
                            f"  received {received}{suffix} bytes",
                            flush=True,
                        )
                        last_report = time.monotonic()
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def prepare_sources(
    manifest_path: Path,
    output_directory: Path,
) -> tuple[dict[str, object], tuple[Path, ...]]:
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("source")
    if not isinstance(entries, list) or not entries:
        raise ValueError("source manifest contains no [[source]] entries")
    output_directory.mkdir(parents=True, exist_ok=True)
    prepared: list[Path] = []
    for entry in entries:
        filename = str(entry["filename"])
        if Path(filename).name != filename:
            raise ValueError(f"unsafe source filename: {filename!r}")
        expected_hash = str(entry["sha256"]).lower()
        if len(expected_hash) != 64:
            raise ValueError(f"invalid SHA-256 for {filename}")
        destination = output_directory / filename
        if destination.exists() and sha256_file(destination) != expected_hash:
            raise ValueError(
                f"existing source archive has wrong SHA-256: {destination}"
            )
        if not destination.exists():
            print(f"Downloading {entry['name']} {entry['version']}...")
            _download(str(entry["url"]), destination)
        actual_hash = sha256_file(destination)
        if actual_hash != expected_hash:
            destination.unlink(missing_ok=True)
            raise ValueError(
                f"SHA-256 mismatch for {filename}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        print(f"Verified {filename}")
        prepared.append(destination)

    manifest_copy = output_directory / "third_party_sources.toml"
    shutil.copyfile(manifest_path, manifest_copy)
    checksum_lines = [
        f"{sha256_file(path)}  {path.name}" for path in prepared
    ]
    checksum_lines.append(
        f"{sha256_file(manifest_copy)}  {manifest_copy.name}"
    )
    (output_directory / "SHA256SUMS.txt").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return manifest, tuple(prepared)


def build_bundle(
    manifest: dict[str, object],
    prepared: tuple[Path, ...],
    output_directory: Path,
) -> Path:
    release = manifest.get("release")
    if not isinstance(release, dict):
        raise ValueError("source manifest has no [release] table")
    bundle_name = str(release["bundle_name"])
    if Path(bundle_name).name != bundle_name:
        raise ValueError(f"unsafe bundle filename: {bundle_name!r}")
    bundle = output_directory / bundle_name
    temporary = bundle.with_name(f"{bundle.name}.part")
    temporary.unlink(missing_ok=True)
    bundle.unlink(missing_ok=True)
    supporting = (
        output_directory / "third_party_sources.toml",
        output_directory / "SHA256SUMS.txt",
    )
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            for path in (*prepared, *supporting):
                archive.write(path, arcname=path.name)
        temporary.replace(bundle)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f"Created {bundle}")
    print(f"Bundle SHA-256: {sha256_file(bundle)}")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bundle", action="store_true")
    arguments = parser.parse_args(argv)
    manifest, prepared = prepare_sources(
        arguments.manifest.resolve(),
        arguments.output_dir.resolve(),
    )
    if arguments.bundle:
        build_bundle(manifest, prepared, arguments.output_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
