import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "packaging" / "prepare_release_sources.py"
SPEC = importlib.util.spec_from_file_location("prepare_release_sources", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load release source helper: {MODULE_PATH}")
release_sources = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_sources)


class ReleaseSourcePreparationTests(unittest.TestCase):
    def test_existing_archives_are_verified_and_bundled_without_network(self) -> None:
        payload = b"synthetic corresponding source\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "output"
            output.mkdir()
            archive = output / "synthetic-source.tar.gz"
            archive.write_bytes(payload)
            manifest = root / "sources.toml"
            manifest.write_text(
                "\n".join(
                    (
                        "[release]",
                        'version = "test"',
                        'bundle_name = "synthetic-bundle.zip"',
                        "",
                        "[[source]]",
                        'name = "Synthetic"',
                        'version = "1"',
                        'filename = "synthetic-source.tar.gz"',
                        'url = "https://example.invalid/source.tar.gz"',
                        f'sha256 = "{digest}"',
                        'license = "MIT"',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            parsed, prepared = release_sources.prepare_sources(manifest, output)
            bundle = release_sources.build_bundle(parsed, prepared, output)

            self.assertEqual(prepared, (archive,))
            self.assertTrue(bundle.is_file())
            with zipfile.ZipFile(bundle) as zipped:
                self.assertEqual(
                    set(zipped.namelist()),
                    {
                        "synthetic-source.tar.gz",
                        "third_party_sources.toml",
                        "SHA256SUMS.txt",
                    },
                )
                self.assertEqual(
                    zipped.read("synthetic-source.tar.gz"),
                    payload,
                )

    def test_wrong_existing_archive_is_rejected_without_replacement(self) -> None:
        zero_hash = "0" * 64
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "output"
            output.mkdir()
            archive = output / "synthetic-source.tar.gz"
            archive.write_bytes(b"wrong")
            manifest = root / "sources.toml"
            manifest.write_text(
                "\n".join(
                    (
                        "[release]",
                        'version = "test"',
                        'bundle_name = "synthetic-bundle.zip"',
                        "",
                        "[[source]]",
                        'name = "Synthetic"',
                        'version = "1"',
                        'filename = "synthetic-source.tar.gz"',
                        'url = "https://example.invalid/source.tar.gz"',
                        f'sha256 = "{zero_hash}"',
                        'license = "MIT"',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "wrong SHA-256"):
                release_sources.prepare_sources(manifest, output)
            self.assertEqual(archive.read_bytes(), b"wrong")


if __name__ == "__main__":
    unittest.main()
