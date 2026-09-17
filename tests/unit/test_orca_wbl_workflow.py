"""Offline remote-orchestration tests for the ORCA WBL second stage."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shlex
from tempfile import TemporaryDirectory
from uuid import UUID

from moltage.app.local_project_index import LocalProjectIndexRepository
from moltage.app.orca_submission import (
    OrcaFrequencySubmissionRequest,
    OrcaOptimizationSubmissionRequest,
    OrcaSubmissionService,
)
import pytest

from moltage.app.orca_wbl import (
    OrcaWblRequest,
    OrcaWblService,
    OrcaWblServiceError,
    _verify_utilities,
)
from moltage.app.project_recovery import ProjectRecoveryService
from moltage.app.project_presentation import (
    StepIndicatorKind,
    project_presentation_record,
)
from moltage.domain.calculation_project import (
    ProjectStepKind,
    ProjectStepState,
    begin_orca_wbl_step,
)
from moltage.domain.structure import Atom, MolecularStructure
from moltage.orca.wbl import (
    OrcaWblContactSettings,
    OrcaWblSettings,
    WblContactSubspaceMode,
    WblLinkerKind,
    WblParameterStatus,
)
from moltage.orca.catalog import OrcaFrequencyMode
from moltage.orca.settings import OrcaFrequencySettings
from moltage.remote.executor import RemoteCommandResult, RemotePathNotFoundError
from moltage.remote.project_manifest import parse_project_manifest
from moltage.remote.project_repository import RemoteProjectRepository
from test_orca_submission_recovery import (
    FixedConnectionService,
    OrcaRemoteExecutor,
    configured_profile,
    settings as optimization_settings,
)


NOW = datetime(2030, 1, 3, 12, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _molecule() -> MolecularStructure:
    return MolecularStructure(
        (
            Atom(0, "S", -1.0, 0.0, 0.0),
            Atom(1, "C", 0.0, 0.0, 0.0),
            Atom(2, "S", 1.0, 0.0, 0.0),
        ),
        "synthetic S-C-S",
    )


def _wavefunction_json() -> bytes:
    atoms = (
        ("S", (-1.0, 0.0, 0.0)),
        ("C", (0.0, 0.0, 0.0)),
        ("S", (1.0, 0.0, 0.0)),
    )
    orbitals = (
        {"MOCoefficients": [0.6, 0.0, 0.6], "Occupancy": 2.0, "OrbitalEnergy": -0.15},
        {"MOCoefficients": [0.4, 0.2, -0.4], "Occupancy": 0.0, "OrbitalEnergy": 0.10},
    )
    return (
        json.dumps(
            {
                "Molecule": {
                    "Atoms": [
                        {
                            "Idx": index,
                            "ElementLabel": element,
                            "Coords": list(coords),
                            "Basis": [
                                {
                                    "Shell": "s",
                                    "Coefficients": [1.0],
                                    "Exponents": [1.0],
                                }
                            ],
                        }
                        for index, (element, coords) in enumerate(atoms)
                    ],
                    "CoordinateUnits": "Angs",
                    "Charge": 0,
                    "Multiplicity": 1,
                    "HFTyp": "RHF",
                    "MolecularOrbitals": {
                        "EnergyUnit": "Eh",
                        "MOs": list(orbitals),
                    },
                    "S-Matrix": [
                        [1.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0],
                        [0.0, 0.0, 1.0],
                    ],
                },
                "ORCA Header": {"Version": "Program Version 6.1.2"},
            }
        )
        + "\n"
    ).encode("ascii")


class WblRemoteExecutor(OrcaRemoteExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.directories.add("/tmp")
        self.wavefunction_json = _wavefunction_json()

    def rename(self, source: str, destination: str) -> None:
        if source in self.directories:
            self.operations.append(("rename", source, destination))
            if destination in self.directories or destination in self.files:
                raise RuntimeError("synthetic destination already exists")
            moved_directories = {
                candidate
                for candidate in self.directories
                if candidate == source or candidate.startswith(source + "/")
            }
            moved_files = {
                candidate: data
                for candidate, data in self.files.items()
                if candidate.startswith(source + "/")
            }
            for candidate in moved_directories:
                self.directories.remove(candidate)
                suffix = candidate[len(source) :]
                self.directories.add(destination + suffix)
            for candidate, data in moved_files.items():
                del self.files[candidate]
                self.files[destination + candidate[len(source) :]] = data
            return
        super().rename(source, destination)

    def execute(self, command: str) -> RemoteCommandResult:
        if "__MOLTAGE_ORCA_2MKL__" in command:
            self.operations.append(("execute", command))
            return RemoteCommandResult(
                0,
                b"__MOLTAGE_ORCA_2MKL__=UNAVAILABLE\n",
                b"",
            )
        if command.startswith("sha256sum -- "):
            self.operations.append(("execute", command))
            path = shlex.split(command)[-1]
            if path not in self.files:
                return RemoteCommandResult(1, b"", b"missing")
            digest = sha256(self.files[path]).hexdigest()
            return RemoteCommandResult(0, f"{digest}  {path}\n".encode(), b"")
        if "orca_wbl.gbw -json" in command:
            self.operations.append(("execute", command))
            temporary = next(
                item
                for item in self.directories
                if item.startswith("/tmp/moltage-orca-wbl-")
            )
            self.files[str(PurePosixPath(temporary) / "orca_wbl.json")] = (
                self.wavefunction_json
            )
            return RemoteCommandResult(0, b"", b"")
        if command.startswith("rm -rf -- "):
            self.operations.append(("execute", command))
            target = shlex.split(command)[-1]
            for path in tuple(self.files):
                if path.startswith(target + "/"):
                    del self.files[path]
            for path in tuple(self.directories):
                if path == target or path.startswith(target + "/"):
                    self.directories.remove(path)
            return RemoteCommandResult(0, b"", b"")
        return super().execute(command)


def _settings() -> OrcaWblSettings:
    return OrcaWblSettings(
        OrcaWblContactSettings(
            0,
            WblLinkerKind.SH,
            0.2,
            WblParameterStatus.HYPOTHESIS,
            WblContactSubspaceMode.MANUAL_AO,
            manual_ao_indices=(0,),
        ),
        OrcaWblContactSettings(
            2,
            WblLinkerKind.SH,
            0.3,
            WblParameterStatus.CALIBRATED,
            WblContactSubspaceMode.MANUAL_AO,
            manual_ao_indices=(2,),
        ),
        -5.0,
        -2.0,
        2.0,
        0.2,
    )


def _optimized_project(remote, index):
    submission = OrcaSubmissionService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: NOW,
        project_id_factory=lambda: PROJECT_ID,
        temporary_id_factory=lambda: "synthetic-submit",
    )
    submitted = submission.submit_optimization(
        OrcaOptimizationSubmissionRequest(
            configured_profile(),
            "SyntheticWbl",
            "synthetic.xyz",
            _molecule(),
            optimization_settings(),
            supplied_password="synthetic-password",
            project_id=PROJECT_ID,
        )
    ).project
    root = submitted.remote_project_path
    remote.files[f"{root}/orca_opt.out"] = (
        b"THE OPTIMIZATION HAS CONVERGED\nORCA TERMINATED NORMALLY\n"
    )
    remote.files[f"{root}/orca_opt.xyz"] = (
        b"3\nsynthetic optimized geometry\n"
        b"S -1.0 0.0 0.0\nC 0.0 0.0 0.0\nS 1.0 0.0 0.0\n"
    )
    remote.files[f"{root}/orca_opt.gbw"] = b"synthetic GBW evidence"
    remote.sacct_stdout = b"90001|COMPLETED|0:0\n"
    recovery = ProjectRecoveryService(
        FixedConnectionService(remote),
        index,
        now_factory=lambda: NOW,
        temporary_id_factory=lambda: "synthetic-refresh",
        covalent_radii_loader=lambda: {"S": 1.05, "C": 0.75},
    )
    optimized = recovery.refresh_project(
        configured_profile(),
        submitted.remote_project_path,
        supplied_password="synthetic-password",
    )
    return optimized, recovery


def test_wbl_conversion_persists_stage_and_refreshes_without_scheduler_or_orca_job():
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, recovery = _optimized_project(remote, index)
        root = optimized.project.remote_project_path
        scheduler_submits_before = remote.submit_count

        service = OrcaWblService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-wbl",
        )
        project_updates = []

        result = service.calculate(
            OrcaWblRequest(
                configured_profile(),
                optimized.project.remote_project_path,
                _settings(),
                "synthetic-password",
            ),
            project_updated=project_updates.append,
        )

        assert len(project_updates) == 2
        assert project_updates[0].steps[1].state is ProjectStepState.RUNNING
        assert project_updates[1] == result.project
        assert project_updates[1].steps[1].state is ProjectStepState.SUCCEEDED
        running_presentation = project_presentation_record(
            replace(
                optimized,
                project=project_updates[0],
                active_step_kind=ProjectStepKind.ORCA_WBL_TRANSMISSION,
            ),
            server_profile_id=configured_profile().profile_id,
        )
        assert running_presentation.indicators[1].kind is StepIndicatorKind.ACTIVE
        assert tuple(step.kind for step in result.project.steps) == (
            ProjectStepKind.ORCA_OPTIMIZATION,
            ProjectStepKind.ORCA_WBL_TRANSMISSION,
        )
        assert result.project.steps[1].state is ProjectStepState.SUCCEEDED
        assert remote.submit_count == scheduler_submits_before
        assert f"{root}/wbl/orca_wbl_result.json" in remote.files
        assert f"{root}/wbl/orca_wbl_transmission.csv" in remote.files

        refreshed = recovery.refresh_project(
            configured_profile(),
            root,
            supplied_password="synthetic-password",
        )

        assert refreshed.active_step_kind is ProjectStepKind.ORCA_WBL_TRANSMISSION
        assert refreshed.orca_wbl_presentation is not None
        assert refreshed.orca_wbl_presentation.model_classification == "HYPOTHESIS"
        assert refreshed.orca_wbl_presentation.transmission_total
        assert not refreshed.orca_wbl_presentation.transmission_alpha
        presentation = project_presentation_record(
            refreshed,
            server_profile_id=configured_profile().profile_id,
        )
        assert len(presentation.indicators) == 2
        assert presentation.indicators[1].kind is StepIndicatorKind.SUCCEEDED
        assert (
            presentation.indicators[1].step_kind
            is ProjectStepKind.ORCA_WBL_TRANSMISSION
        )


def test_frequency_activity_preserves_readable_wbl_and_reports_unreadable_artifacts():
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, recovery = _optimized_project(remote, index)
        root = optimized.project.remote_project_path
        wbl = OrcaWblService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-wbl-before-frequency",
        ).calculate(
            OrcaWblRequest(
                configured_profile(),
                root,
                _settings(),
                supplied_password="synthetic-password",
            )
        )
        source_settings = optimization_settings()
        frequency_settings = OrcaFrequencySettings(
            source_settings,
            source_settings.scientific_identity_sha256(),
            OrcaFrequencyMode.NUMFREQ,
        )
        submitted = OrcaSubmissionService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-frequency-after-wbl",
        ).submit_frequency(
            OrcaFrequencySubmissionRequest(
                configured_profile(),
                wbl.project,
                optimized.optimized_structure,
                frequency_settings,
                supplied_password="synthetic-password",
            )
        )
        remote.squeue_stdout = b"90002|RUNNING\n"

        readable = recovery.refresh_project(
            configured_profile(),
            submitted.project.remote_project_path,
            supplied_password="synthetic-password",
        )

        assert readable.active_step_kind is ProjectStepKind.ORCA_FREQUENCY
        assert readable.active_step.state is ProjectStepState.RUNNING
        assert readable.project.steps[1].state is ProjectStepState.SUCCEEDED
        assert readable.orca_wbl_presentation is not None
        assert readable.can_view_orca_wbl
        assert (
            project_presentation_record(
                readable,
                server_profile_id=configured_profile().profile_id,
            ).indicators[1].kind
            is StepIndicatorKind.SUCCEEDED
        )

        del remote.files[f"{root}/wbl/orca_wbl_result.json"]
        unreadable = recovery.refresh_project(
            configured_profile(),
            submitted.project.remote_project_path,
            supplied_password="synthetic-password",
        )

        assert unreadable.active_step_kind is ProjectStepKind.ORCA_FREQUENCY
        assert unreadable.active_step.state is ProjectStepState.RUNNING
        assert unreadable.project.steps[1].state is ProjectStepState.SUCCEEDED
        assert unreadable.orca_wbl_presentation is None
        assert not unreadable.can_view_orca_wbl
        assert "WBL presentation unavailable" in unreadable.status_message
        assert "Missing required ORCA WBL artifact" in unreadable.status_message
        assert (
            project_presentation_record(
                unreadable,
                server_profile_id=configured_profile().profile_id,
            ).indicators[1].kind
            is StepIndicatorKind.SUCCEEDED
        )


def test_wbl_failure_is_persisted_and_recovered_without_a_scheduler_query():
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, recovery = _optimized_project(remote, index)
        root = optimized.project.remote_project_path
        remote.wavefunction_json = b"{}\n"
        updates = []
        service = OrcaWblService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-wbl-failure",
        )

        with pytest.raises(OrcaWblServiceError):
            service.calculate(
                OrcaWblRequest(
                    configured_profile(), root, _settings(),
                    supplied_password="synthetic-password",
                ),
                project_updated=updates.append,
            )

        persisted = parse_project_manifest(remote.files[f"{root}/.moltage/project.json"])
        assert [project.steps[1].state for project in updates] == [
            ProjectStepState.RUNNING,
            ProjectStepState.FAILED,
        ]
        assert persisted.steps[0] == optimized.project.steps[0]
        assert persisted.steps[1].state is ProjectStepState.FAILED
        assert persisted.steps[1].last_error
        before_refresh = len(remote.operations)

        refreshed = recovery.refresh_project(
            configured_profile(), root, supplied_password="synthetic-password",
        )

        assert refreshed.active_step.state is ProjectStepState.FAILED
        assert refreshed.optimized_structure is not None
        assert refreshed.status_message == persisted.steps[1].last_error
        assert not any(
            item[0] == "execute" and ("squeue" in item[1] or "sacct" in item[1])
            for item in remote.operations[before_refresh:]
        )


def test_wbl_rejects_wavefunction_multiplicity_that_differs_from_submission():
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, _recovery = _optimized_project(remote, index)
        document = json.loads(remote.wavefunction_json)
        document["Molecule"]["Multiplicity"] = 3
        remote.wavefunction_json = json.dumps(document).encode("utf-8")
        service = OrcaWblService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-wbl-spin-mismatch",
        )

        with pytest.raises(
            OrcaWblServiceError,
            match="charge/multiplicity differs",
        ):
            service.calculate(
                OrcaWblRequest(
                    configured_profile(),
                    optimized.project.remote_project_path,
                    _settings(),
                    supplied_password="synthetic-password",
                )
            )


def test_running_wbl_refresh_and_duplicate_request_do_not_mutate_the_stage():
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, recovery = _optimized_project(remote, index)
        root = optimized.project.remote_project_path
        started = RemoteProjectRepository(remote).persist_update(
            begin_orca_wbl_step(optimized.project, settings=_settings(), started_at=NOW),
            updated_at=NOW,
        )
        before_refresh = len(remote.operations)

        refreshed = recovery.refresh_project(
            configured_profile(), root, supplied_password="synthetic-password",
        )

        assert refreshed.project == started
        assert refreshed.active_step.state is ProjectStepState.RUNNING
        assert refreshed.optimized_structure is not None
        assert not any(
            item[0] == "execute" and ("squeue" in item[1] or "sacct" in item[1])
            for item in remote.operations[before_refresh:]
        )
        service = OrcaWblService(FixedConnectionService(remote), index)

        with pytest.raises(OrcaWblServiceError, match="already contains"):
            service.calculate(
                OrcaWblRequest(
                    configured_profile(), root, _settings(),
                    supplied_password="synthetic-password",
                )
            )

        persisted = parse_project_manifest(remote.files[f"{root}/.moltage/project.json"])
        assert persisted == started


def test_missing_sibling_orca_2json_fails_without_a_bare_path_fallback():
    class MissingJsonExecutor(WblRemoteExecutor):
        def execute(self, command: str) -> RemoteCommandResult:
            if "__MOLTAGE_ORCA_2MKL__" in command:
                self.operations.append(("execute", command))
                return RemoteCommandResult(61, b"", b"")
            return super().execute(command)

    remote = MissingJsonExecutor()
    runtime = configured_profile().orca_runtime
    assert runtime is not None

    with pytest.raises(OrcaWblServiceError, match="does not provide executable orca_2json"):
        _verify_utilities(
            remote,
            runtime.environment,
            "/apps/orca/orca_2json",
            "/apps/orca/orca_2mkl",
        )

    commands = [value for operation, value in remote.operations if operation == "execute"]
    assert len(commands) == 1
    assert "command -v" not in commands[0]
