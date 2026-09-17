import unittest
from pathlib import PurePosixPath

from moltage.aims.optimization_settings import SpeciesAccuracy
from moltage.aims.species_library import SpeciesRequirement
from moltage.app.species_acquisition import (
    MAX_SPECIES_DEFAULT_BYTES,
    MissingSpeciesRootConfigurationError,
    SpeciesAcquisitionError,
    acquire_species_library,
)
from moltage.remote.executor import (
    RemoteExecutorError,
    RemotePathNotFoundError,
    RemotePathStat,
)
from species_test_support import TEST_SPECIES_ROOT, synthetic_species_text


class RecordingExecutor:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.stats = {}
        self.calls = []

    def stat(self, path):
        self.calls.append(("stat", path))
        if path in self.stats:
            value = self.stats[path]
            if isinstance(value, Exception):
                raise value
            return value
        try:
            data = self.files[path]
        except KeyError:
            raise RemotePathNotFoundError(path) from None
        return RemotePathStat(False, len(data))

    def read_bytes(self, path):
        self.calls.append(("read_bytes", path))
        value = self.files.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise RemotePathNotFoundError(path)
        return value

    def execute(self, command):
        self.calls.append(("execute", command))
        raise AssertionError("species acquisition must not execute commands")


def _path(element="C", accuracy=SpeciesAccuracy.TIGHT):
    number = {"H": "01", "C": "06", "Au": "79"}[element]
    return str(
        PurePosixPath(TEST_SPECIES_ROOT)
        / accuracy.value
        / f"{number}_{element}_default"
    )


class SpeciesAcquisitionTests(unittest.TestCase):
    def test_deduplicates_requirements_and_reads_each_exact_file_after_stat(self):
        carbon_path = _path("C")
        gold_path = _path("Au", SpeciesAccuracy.REALLY_TIGHT)
        executor = RecordingExecutor(
            {
                carbon_path: synthetic_species_text(
                    "C", SpeciesAccuracy.TIGHT
                ).encode(),
                gold_path: synthetic_species_text(
                    "Au", SpeciesAccuracy.REALLY_TIGHT
                ).encode(),
            }
        )

        library = acquire_species_library(
            executor,
            TEST_SPECIES_ROOT,
            (
                SpeciesRequirement("C", SpeciesAccuracy.TIGHT),
                SpeciesRequirement("C", SpeciesAccuracy.TIGHT),
                SpeciesRequirement("Au", SpeciesAccuracy.REALLY_TIGHT),
            ),
        )

        self.assertIn(
            "species C_alias",
            library.load(
                "C", SpeciesAccuracy.TIGHT, species_name="C_alias"
            ).text,
        )
        self.assertEqual(
            executor.calls,
            [
                ("stat", carbon_path),
                ("read_bytes", carbon_path),
                ("stat", gold_path),
                ("read_bytes", gold_path),
            ],
        )

    def test_missing_or_invalid_root_fails_before_remote_access(self):
        for root in (None, "relative/species", "/srv/../species", "/"):
            with self.subTest(root=root):
                executor = RecordingExecutor()
                with self.assertRaises(MissingSpeciesRootConfigurationError):
                    acquire_species_library(
                        executor,
                        root,
                        (SpeciesRequirement("C", SpeciesAccuracy.TIGHT),),
                    )
                self.assertEqual(executor.calls, [])

    def test_empty_requirements_fail_without_remote_access(self):
        executor = RecordingExecutor()
        with self.assertRaisesRegex(SpeciesAcquisitionError, "at least one"):
            acquire_species_library(executor, TEST_SPECIES_ROOT, ())
        self.assertEqual(executor.calls, [])

    def test_missing_file_reports_element_accuracy_and_exact_remote_path(self):
        executor = RecordingExecutor()
        with self.assertRaises(SpeciesAcquisitionError) as caught:
            acquire_species_library(
                executor,
                TEST_SPECIES_ROOT,
                (SpeciesRequirement("C", SpeciesAccuracy.TIGHT),),
            )
        message = str(caught.exception)
        self.assertIn("element=C", message)
        self.assertIn("accuracy=tight", message)
        self.assertIn(_path("C"), message)
        self.assertEqual(executor.calls, [("stat", _path("C"))])

    def test_stat_and_read_failures_are_contextual_and_do_not_fallback(self):
        cases = {
            "stat": RemoteExecutorError("permission denied"),
            "read": RemoteExecutorError("permission denied"),
        }
        for stage, error in cases.items():
            with self.subTest(stage=stage):
                path = _path("C")
                data = synthetic_species_text("C", SpeciesAccuracy.TIGHT).encode()
                executor = RecordingExecutor({path: data})
                if stage == "stat":
                    executor.stats[path] = error
                else:
                    executor.files[path] = error
                    executor.stats[path] = RemotePathStat(False, len(data))
                with self.assertRaises(SpeciesAcquisitionError) as caught:
                    acquire_species_library(
                        executor,
                        TEST_SPECIES_ROOT,
                        (SpeciesRequirement("C", SpeciesAccuracy.TIGHT),),
                    )
                self.assertIn(path, str(caught.exception))
                self.assertFalse(any(call[0] == "execute" for call in executor.calls))

    def test_rejects_directory_unknown_size_negative_size_and_oversized_file(self):
        path = _path("C")
        cases = {
            "directory": RemotePathStat(True),
            "unknown": RemotePathStat(False, None),
            "negative": RemotePathStat(False, -1),
            "oversized": RemotePathStat(False, MAX_SPECIES_DEFAULT_BYTES + 1),
        }
        for name, stat in cases.items():
            with self.subTest(name=name):
                executor = RecordingExecutor({path: b"species C\n"})
                executor.stats[path] = stat
                with self.assertRaises(SpeciesAcquisitionError):
                    acquire_species_library(
                        executor,
                        TEST_SPECIES_ROOT,
                        (SpeciesRequirement("C", SpeciesAccuracy.TIGHT),),
                    )
                self.assertEqual(executor.calls, [("stat", path)])

    def test_rejects_changed_size_invalid_utf8_wrong_element_and_duplicates(self):
        path = _path("C")
        cases = {
            "changed": (b"species C\n", RemotePathStat(False, 999)),
            "utf8": (b"\xff", RemotePathStat(False, 1)),
            "wrong": (b"species N\n", RemotePathStat(False, 10)),
            "duplicate": (b"species C\nspecies C\n", RemotePathStat(False, 20)),
        }
        for name, (data, stat) in cases.items():
            with self.subTest(name=name):
                executor = RecordingExecutor({path: data})
                executor.stats[path] = stat
                with self.assertRaises(SpeciesAcquisitionError) as caught:
                    acquire_species_library(
                        executor,
                        TEST_SPECIES_ROOT,
                        (SpeciesRequirement("C", SpeciesAccuracy.TIGHT),),
                    )
                self.assertIn(path, str(caught.exception))
                self.assertEqual(
                    executor.calls,
                    [("stat", path), ("read_bytes", path)],
                )


if __name__ == "__main__":
    unittest.main()
