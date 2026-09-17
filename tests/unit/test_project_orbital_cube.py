from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from moltage.aims.orbital_cube import FrontierOrbital
from moltage.aims.recovery import recover_optimized_structure
from moltage.app.project_orbital_cube import (
    ProjectOrbitalCubeError,
    ProjectOrbitalCubeLoadRequest,
    ProjectOrbitalCubeService,
    discover_step1_orbital_cubes,
)
from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.project_recovery import ProjectRecoveryService
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
    remote_step_directory,
)

from test_project_recovery import (
    FixedConnectionService,
    RecoveryRemoteExecutor,
    TEST_PROFILE,
    _bundle,
    _project,
)


BOHR_TO_ANGSTROM = 0.529177210903


class OrbitalRemoteExecutor(RecoveryRemoteExecutor):
    def download_file(self, path, destination, progress=None):
        try:
            data = self.files[path]
        except KeyError:
            raise AssertionError(f"unexpected missing download: {path}") from None
        midpoint = len(data) // 2
        if progress is not None:
            progress(midpoint, len(data))
        Path(destination).write_bytes(data)
        if progress is not None:
            progress(len(data), len(data))


def _fhi_cube(structure) -> bytes:
    rows = [
        "CUBE FILE written by FHI-AIMS",
        "Single orbital scalar dataset",
        f"{len(structure):5d}  -1.000000  -1.000000  -1.000000",
        "    2    0.500000    0.000000    0.000000",
        "    2    0.000000    0.500000    0.000000",
        "    2    0.000000    0.000000    0.500000",
    ]
    atomic_numbers = {"C": 6, "N": 7}
    for atom in structure:
        rows.append(
            f"{atomic_numbers[atom.element]:5d} 0.000000 "
            f"{atom.x / BOHR_TO_ANGSTROM:.8f} "
            f"{atom.y / BOHR_TO_ANGSTROM:.8f} "
            f"{atom.z / BOHR_TO_ANGSTROM:.8f}"
        )
    rows.extend(
        (
            "-0.04 -0.02 0.00 0.02",
            "0.04 0.06 -0.06 0.01",
        )
    )
    return ("\n".join(rows) + "\n").encode("utf-8")


class ProjectOrbitalCubeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.remote = OrbitalRemoteExecutor()
        self.bundle = _bundle()
        self.project = _project(ProjectStepState.SUCCEEDED, revision=3)
        self.remote.add_project(self.project, self.bundle)
        self.directory = remote_step_directory(
            self.project,
            ProjectStepKind.MOLECULE_OPT,
        )
        self.expected_structure = recover_optimized_structure(
            control_text=self.bundle.control_text,
            original_geometry_text=self.bundle.geometry_text,
            next_geometry_text=self.remote.files[
                self.directory + "/geometry.in.next_step"
            ],
        )

    def test_one_directory_listing_returns_only_requested_nonempty_files(self):
        self.remote.files[self.directory + "/orbital_HOMO.cube"] = _fhi_cube(
            self.expected_structure
        )
        self.remote.files[self.directory + "/orbital_LUMO.cube"] = b""
        self.remote.files[self.directory + "/unrelated.cube"] = b"other"

        catalog = discover_step1_orbital_cubes(
            self.remote,
            self.directory,
            self.bundle.control_text.encode("utf-8"),
        )

        self.assertEqual(
            tuple(item.state for item in catalog.available),
            (FrontierOrbital.HOMO,),
        )
        self.assertIn("orbital_LUMO.cube", catalog.missing_filenames)
        self.assertNotIn("unrelated.cube", catalog.missing_filenames)
        self.assertEqual(
            [operation for operation in self.remote.operations if operation[0] == "list"],
            [("list", self.directory)],
        )

    def test_selected_cube_download_is_bohr_aligned_and_presentation_only(self):
        cube = _fhi_cube(self.expected_structure)
        filename = "orbital_HOMO.cube"
        self.remote.files[self.directory + "/" + filename] = cube
        artifact = discover_step1_orbital_cubes(
            self.remote,
            self.directory,
            self.bundle.control_text.encode("utf-8"),
        ).available[0]
        progress = []
        service = ProjectOrbitalCubeService(FixedConnectionService(self.remote))

        result = service.load(
            ProjectOrbitalCubeLoadRequest(
                TEST_PROFILE,
                self.project,
                artifact,
                self.expected_structure,
                supplied_password="memory-only",
            ),
            progress=progress.append,
        )

        self.assertEqual(result.artifact.state, FrontierOrbital.HOMO)
        self.assertEqual(result.scalar_field.dimensions, (2, 2, 2))
        self.assertEqual(result.scalar_field.source_coordinate_unit.value, "bohr")
        self.assertTrue(any("Downloading HOMO Cube" in item for item in progress))
        self.assertTrue(self.remote.closed)

    def test_confirmed_cross_machine_profile_binding_can_load_cube(self):
        historical_project = replace(
            self.project,
            server_profile_id=UUID("66666666-6666-4666-8666-666666666666"),
        )
        self.remote.add_project(historical_project, self.bundle)
        filename = "orbital_HOMO.cube"
        self.remote.files[self.directory + "/" + filename] = _fhi_cube(
            self.expected_structure
        )
        artifact = discover_step1_orbital_cubes(
            self.remote,
            self.directory,
            self.bundle.control_text.encode("utf-8"),
        ).available[0]
        service = ProjectOrbitalCubeService(FixedConnectionService(self.remote))

        with self.assertRaisesRegex(ProjectOrbitalCubeError, "not bound"):
            service.load(
                ProjectOrbitalCubeLoadRequest(
                    TEST_PROFILE,
                    historical_project,
                    artifact,
                    self.expected_structure,
                )
            )

        result = service.load(
            ProjectOrbitalCubeLoadRequest(
                TEST_PROFILE,
                historical_project,
                artifact,
                self.expected_structure,
                profile_rebind_confirmed=True,
            )
        )

        self.assertEqual(result.artifact.state, FrontierOrbital.HOMO)

    def test_successful_step1_recovery_carries_the_available_cube_catalog(self):
        filename = "orbital_HOMO_minus_2.cube"
        self.remote.files[self.directory + "/" + filename] = _fhi_cube(
            self.expected_structure
        )
        with tempfile.TemporaryDirectory() as temporary:
            service = ProjectRecoveryService(
                FixedConnectionService(self.remote),
                LocalProjectIndexRepository(Path(temporary) / "projects.json"),
            )

            snapshot = service.refresh_project(
                TEST_PROFILE,
                self.project.remote_project_path,
            )

        self.assertEqual(len(snapshot.orbital_cubes), 1)
        self.assertEqual(
            snapshot.orbital_cubes[0].state,
            FrontierOrbital.HOMO_MINUS_2,
        )
        self.assertIn("orbital_HOMO.cube", snapshot.orbital_cube_diagnostic)

    def test_cube_with_different_atom_coordinates_is_rejected(self):
        moved = type(self.expected_structure)(
            tuple(
                type(atom)(
                    atom.index,
                    atom.element,
                    atom.x + (1.0 if atom.index == 0 else 0.0),
                    atom.y,
                    atom.z,
                )
                for atom in self.expected_structure
            )
        )
        filename = "orbital_HOMO.cube"
        self.remote.files[self.directory + "/" + filename] = _fhi_cube(moved)
        artifact = discover_step1_orbital_cubes(
            self.remote,
            self.directory,
            self.bundle.control_text.encode("utf-8"),
        ).available[0]
        service = ProjectOrbitalCubeService(FixedConnectionService(self.remote))

        with self.assertRaisesRegex(ProjectOrbitalCubeError, "do not match"):
            service.load(
                ProjectOrbitalCubeLoadRequest(
                    TEST_PROFILE,
                    self.project,
                    artifact,
                    self.expected_structure,
                    supplied_password="memory-only",
                )
            )


if __name__ == "__main__":
    unittest.main()
