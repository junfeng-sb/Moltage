"""An imported ORCA optimization enters the existing WBL pipeline unchanged."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
import shlex
from tempfile import TemporaryDirectory
from uuid import UUID

import pytest

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_import import (
    OrcaImportRequest,
    OrcaImportValidationRequest,
    OrcaOptimizationImportService,
)
from moltage.app.orca_wbl import OrcaWblRequest, OrcaWblService
from moltage.app.project_geometry import (
    ProjectGeometryViewKind,
    ProjectGeometryViewRequest,
    ProjectGeometryViewService,
    project_geometry_view_kinds,
)
from moltage.app.project_presentation import (
    StepIndicatorKind,
    project_presentation_record,
)
from moltage.app.project_recovery import ProjectRecoveryService
from moltage.domain.calculation_project import ProjectStepKind, ProjectStepState
from moltage.remote.executor import RemoteCommandResult
from moltage.orca.wbl import detect_wbl_contacts
from test_orca_submission_recovery import (
    FixedConnectionService,
    configured_profile,
)
from test_orca_wbl_workflow import WblRemoteExecutor, _settings


NOW = datetime(2030, 6, 7, 8, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
SOURCE = "/home/scientist/external orca/scs run"
SOURCE_STEM = "scs"

SOURCE_INPUT = (
    b"! PBE0 DEF2-TZVP OPT\n"
    b"%pal\n  nprocs 8\nend\n"
    b"* xyz 0 1\n"
    b"  S -1.0 0.0 0.0\n"
    b"  C 0.0 0.0 0.0\n"
    b"  S 1.0 0.0 0.0\n"
    b"*\n"
)
SOURCE_OUTPUT = b"THE OPTIMIZATION HAS CONVERGED\nORCA TERMINATED NORMALLY\n"
SOURCE_GEOMETRY = (
    b"3\nsynthetic optimized geometry\n"
    b"S -1.0 0.0 0.0\nC 0.0 0.0 0.0\nS 1.0 0.0 0.0\n"
)
SOURCE_GBW = b"synthetic external GBW evidence"


class ImportingWblExecutor(WblRemoteExecutor):
    """The verified WBL fake plus a read-only external source directory."""

    def __init__(self) -> None:
        super().__init__()
        for directory in (
            "/home",
            "/home/scientist",
            "/home/scientist/external orca",
            SOURCE,
        ):
            self.directories.add(directory)
        base = PurePosixPath(SOURCE)
        self.files[str(base / f"{SOURCE_STEM}.inp")] = SOURCE_INPUT
        self.files[str(base / f"{SOURCE_STEM}.out")] = SOURCE_OUTPUT
        self.files[str(base / f"{SOURCE_STEM}.xyz")] = SOURCE_GEOMETRY
        self.files[str(base / f"{SOURCE_STEM}.gbw")] = SOURCE_GBW
        self.files[str(base / f"{SOURCE_STEM}_trj.xyz")] = b"trajectory\n"

    def snapshot_source(self) -> dict[str, bytes]:
        prefix = SOURCE + "/"
        return {
            path: data for path, data in self.files.items() if path.startswith(prefix)
        }

    def execute(self, command: str) -> RemoteCommandResult:
        if command.startswith("cp -- "):
            self.operations.append(("execute", command))
            _, _, source, destination = shlex.split(command)
            if source not in self.files:
                return RemoteCommandResult(1, b"", b"missing source\n")
            self.files[destination] = self.files[source]
            return RemoteCommandResult(0, b"", b"")
        if command.startswith("rm -f -- "):
            self.operations.append(("execute", command))
            for part in command.split("; "):
                tokens = shlex.split(part)
                if tokens[0] == "rm":
                    for target in tokens[2:]:
                        self.files.pop(target, None)
                elif tokens[0] == "rmdir":
                    self.directories.discard(tokens[2])
            return RemoteCommandResult(0, b"", b"")
        return super().execute(command)


@pytest.fixture()
def imported_environment():
    with TemporaryDirectory() as directory:
        remote = ImportingWblExecutor()
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        service = OrcaOptimizationImportService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            project_id_factory=lambda: PROJECT_ID,
            temporary_id_factory=lambda: "synthetic-import",
        )
        profile = configured_profile()
        validation = service.validate(
            OrcaImportValidationRequest(profile, SOURCE, None, "synthetic-password")
        )
        assert validation.importable, validation.blocking_reason
        result = service.import_optimization(
            OrcaImportRequest(
                profile,
                validation,
                "ImportedScs",
                "scs.xyz",
                "synthetic-password",
            )
        )
        yield remote, index, profile, result


def test_imported_project_shows_a_green_first_lamp_and_an_unstarted_second(
    imported_environment,
):
    _remote, _index, profile, result = imported_environment

    recovery = ProjectRecoveryService(
        FixedConnectionService(_remote),
        _index,
        now_factory=lambda: NOW,
        covalent_radii_loader=lambda: {"S": 1.05, "C": 0.75},
    )
    snapshot = recovery.refresh_project(
        profile, result.remote_project_path, supplied_password="synthetic-password"
    )
    record = project_presentation_record(
        snapshot, server_profile_id=profile.profile_id
    )

    assert [item.kind for item in record.indicators] == [
        StepIndicatorKind.SUCCEEDED,
        StepIndicatorKind.NOT_STARTED,
    ]
    assert record.indicators[1].step_kind is ProjectStepKind.ORCA_WBL_TRANSMISSION


def test_imported_optimized_geometry_opens_and_feeds_linker_detection(
    imported_environment,
):
    remote, index, profile, result = imported_environment

    kinds = project_geometry_view_kinds(
        result.project, ProjectStepKind.ORCA_OPTIMIZATION
    )
    assert ProjectGeometryViewKind.OUTPUT in kinds

    view = ProjectGeometryViewService(
        FixedConnectionService(remote),
        covalent_radii_loader=lambda: {"S": 1.05, "C": 0.75},
    ).load(
        ProjectGeometryViewRequest(
            profile,
            result.project,
            ProjectStepKind.ORCA_OPTIMIZATION,
            ProjectGeometryViewKind.OUTPUT,
            supplied_password="synthetic-password",
        )
    )

    assert view.source_filename == "orca_opt.xyz"
    assert [atom.element for atom in view.structure] == ["S", "C", "S"]
    # The existing WBL contact detector consumes the imported optimized
    # structure, not an FHI-aims artifact or the source directory.
    contacts = detect_wbl_contacts(view.structure, view.connectivity)
    assert isinstance(contacts, tuple)


def test_wbl_runs_on_the_imported_project_and_leaves_the_source_unchanged(
    imported_environment,
):
    remote, index, profile, result = imported_environment
    before = remote.snapshot_source()

    wbl = OrcaWblService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: NOW,
        temporary_id_factory=lambda: "syntheticwbl",
    )
    outcome = wbl.calculate(
        OrcaWblRequest(
            profile,
            result.remote_project_path,
            _settings(),
            "synthetic-password",
        )
    )

    wbl_step = next(
        step
        for step in outcome.project.steps
        if step.kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
    )
    assert wbl_step.state is ProjectStepState.SUCCEEDED
    assert outcome.remote_wbl_directory == f"{result.remote_project_path}/wbl"
    assert remote.snapshot_source() == before
    assert not [
        path for path in remote.files if path.startswith(SOURCE + "/wbl")
    ]


def test_wbl_reuses_the_verified_conversion_utility_and_runs_no_calculation(
    imported_environment,
):
    remote, index, profile, result = imported_environment

    OrcaWblService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: NOW,
        temporary_id_factory=lambda: "syntheticwbl",
    ).calculate(
        OrcaWblRequest(
            profile,
            result.remote_project_path,
            _settings(),
            "synthetic-password",
        )
    )

    commands = [item[1] for item in remote.operations if item[0] == "execute"]
    assert any("orca_wbl.gbw -json" in command for command in commands)
    assert not any("sbatch" in command for command in commands)
    assert not any("--parsable" in command for command in commands)
    # No remote command reaches back into the read-only source directory.
    assert not any(
        SOURCE in command
        for command in commands
        if not command.startswith(("sha256sum -- ", "cp -- "))
    )


def test_imported_project_reads_no_fhi_aims_artifact(imported_environment):
    remote, index, profile, result = imported_environment

    ProjectRecoveryService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: NOW,
        covalent_radii_loader=lambda: {"S": 1.05, "C": 0.75},
    ).refresh_project(
        profile, result.remote_project_path, supplied_password="synthetic-password"
    )

    reads = [item[1] for item in remote.operations if item[0] == "read"]
    assert reads, "refresh must actually read the managed ORCA artifacts"
    assert not [
        path
        for path in reads
        if PurePosixPath(path).name
        in {"control.in", "geometry.in", "geometry.in.next_step"}
    ]


def test_second_lamp_turns_green_after_wbl_on_an_imported_project(
    imported_environment,
):
    remote, index, profile, result = imported_environment

    OrcaWblService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: NOW,
        temporary_id_factory=lambda: "syntheticwbl",
    ).calculate(
        OrcaWblRequest(
            profile,
            result.remote_project_path,
            _settings(),
            "synthetic-password",
        )
    )
    snapshot = ProjectRecoveryService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: NOW,
        covalent_radii_loader=lambda: {"S": 1.05, "C": 0.75},
    ).refresh_project(
        profile, result.remote_project_path, supplied_password="synthetic-password"
    )
    record = project_presentation_record(
        snapshot, server_profile_id=profile.profile_id
    )

    assert [item.kind for item in record.indicators] == [
        StepIndicatorKind.SUCCEEDED,
        StepIndicatorKind.SUCCEEDED,
    ]
    assert snapshot.can_view_orca_wbl
