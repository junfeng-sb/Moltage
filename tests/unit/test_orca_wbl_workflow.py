"""Offline remote-orchestration tests for the ORCA WBL second stage."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
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
    calculate_orca_wbl,
)
from moltage.structure.connectivity import DEFAULT_CONNECTIVITY_MULTIPLIER
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
    def __init__(self, *, molden: bytes | None = None) -> None:
        super().__init__()
        self.directories.add("/tmp")
        self.wavefunction_json = _wavefunction_json()
        self.molden = molden

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
        if command.startswith("cp -- "):
            self.operations.append(("execute", command))
            _, _, source, destination = shlex.split(command)
            if source not in self.files:
                return RemoteCommandResult(1, b"", b"missing source")
            self.files[destination] = self.files[source]
            return RemoteCommandResult(0, b"", b"")
        if command.startswith(("test -d ", "test -f ")):
            self.operations.append(("execute", command))
            for check in command.split(" && "):
                args = shlex.split(check)
                if args[1] == "-d" and args[2] not in self.directories:
                    return RemoteCommandResult(1, b"", b"missing directory")
                if args[1] == "-f" and args[2] not in self.files:
                    return RemoteCommandResult(1, b"", b"missing file")
                if args[0] == "rmdir":
                    if any(path.startswith(args[2] + "/") for path in (*self.files, *self.directories)):
                        return RemoteCommandResult(1, b"", b"not empty")
                    self.directories.remove(args[2])
                # This fake has only regular files/directories, no symlinks.
            return RemoteCommandResult(0, b"", b"")
        if command.startswith("rm -f -- "):
            self.operations.append(("execute", command))
            parts = command.split(" && ")
            remove = parts[0]
            for path in shlex.split(remove)[3:]:
                self.files.pop(path, None)
            if len(parts) == 1:
                return RemoteCommandResult(0, b"", b"")
            directory = shlex.split(parts[1])[2]
            if any(path.startswith(directory + "/") for path in self.files):
                return RemoteCommandResult(1, b"", b"not empty")
            self.directories.remove(directory)
            return RemoteCommandResult(0, b"", b"")
        if "__MOLTAGE_ORCA_2MKL__" in command:
            self.operations.append(("execute", command))
            availability = b"AVAILABLE" if self.molden is not None else b"UNAVAILABLE"
            return RemoteCommandResult(
                0,
                b"__MOLTAGE_ORCA_2MKL__=" + availability + b"\n",
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
            if self.molden is not None:
                self.files[str(PurePosixPath(temporary) / "orca_wbl.molden.input")] = self.molden
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
        DEFAULT_CONNECTIVITY_MULTIPLIER,
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


@pytest.mark.parametrize("molden", [None, b"[Molden Format]\nsynthetic provenance only\n"])
def test_wbl_conversion_persists_stage_and_refreshes_without_scheduler_or_orca_job(molden):
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor(molden=molden)
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

        with patch("moltage.app.orca_wbl.sha256", wraps=sha256) as local_hash:
            result = service.calculate(
                OrcaWblRequest(
                    configured_profile(),
                    optimized.project.remote_project_path,
                    _settings(),
                    "synthetic-password",
                ),
                project_updated=project_updates.append,
            )

        # Only JSON and config need local service hashing. Conversion artifacts
        # are copied and verified on the server, never uploaded again.
        assert local_hash.call_count == 2
        conversion_root = "/tmp/moltage-orca-wbl-synthetic-wbl/"
        assert sum(
            op[0] == "read" and op[1] == conversion_root + "orca_wbl.json"
            for op in remote.operations
        ) == 1
        assert not any(
            (op[0] == "read" and op[1].endswith(".molden.input"))
            or (op[0] == "write" and "orca_wavefunction." in op[1])
            for op in remote.operations
        )
        assert result.molden_generated is (molden is not None)
        for name, digest in result.artifact_hashes:
            assert sha256(remote.files[f"{root}/wbl/{name}"]).hexdigest() == digest
        if molden is not None:
            assert remote.files[f"{root}/wbl/orca_wavefunction.molden.input"] == molden

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


@pytest.mark.parametrize("invalid_artifact", ["json", "molden"])
def test_wbl_failure_is_persisted_and_recovered_without_a_scheduler_query(invalid_artifact):
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, recovery = _optimized_project(remote, index)
        root = optimized.project.remote_project_path
        if invalid_artifact == "json":
            remote.wavefunction_json = b"{}\n"
        else:
            remote.molden = b""
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


def test_wbl_interprets_bonds_with_the_reviewed_threshold_factor():
    """The factor the user reviewed decides the bonds the projection sees."""

    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, _recovery = _optimized_project(remote, index)
        service = OrcaWblService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-wbl",
        )
        # The synthetic S-C-S geometry bonds both sulfurs to each other at the
        # 1.10 inference default and keeps them apart at 0.80.
        reviewed = replace(_settings(), connectivity_multiplier=0.80)
        observed = []
        real_calculation = calculate_orca_wbl

        def capture(wavefunction, structure, connectivity, settings, *, basis):
            observed.append(connectivity)
            return real_calculation(
                wavefunction, structure, connectivity, settings, basis=basis
            )

        with patch("moltage.app.orca_wbl.calculate_orca_wbl", side_effect=capture):
            result = service.calculate(
                OrcaWblRequest(
                    configured_profile(),
                    optimized.project.remote_project_path,
                    reviewed,
                    "synthetic-password",
                )
            )

        assert len(observed) == 1
        bonds = {
            (bond.first_index, bond.second_index) for bond in observed[0]
        }
        assert bonds == {(0, 1), (1, 2)}
        persisted = parse_project_manifest(
            remote.files[
                f"{optimized.project.remote_project_path}/.moltage/project.json"
            ]
        )
        assert persisted.steps[1].orca_wbl_settings.connectivity_multiplier == 0.80
        assert result.project.steps[1].state is ProjectStepState.SUCCEEDED


def test_a_failed_wbl_stage_can_retry_but_finished_result_needs_explicit_replacement():
    with TemporaryDirectory() as directory:
        index = LocalProjectIndexRepository(Path(directory) / "known_projects.json")
        remote = WblRemoteExecutor()
        optimized, _recovery = _optimized_project(remote, index)
        root = optimized.project.remote_project_path
        service = OrcaWblService(
            FixedConnectionService(remote),
            index,
            now_factory=lambda: NOW,
            temporary_id_factory=lambda: "synthetic-wbl",
        )
        request = OrcaWblRequest(
            configured_profile(), root, _settings(), "synthetic-password"
        )
        usable_json = remote.wavefunction_json
        remote.wavefunction_json = b'{"Molecule": {}}\n'

        with pytest.raises(OrcaWblServiceError, match="no atom/basis evidence"):
            service.calculate(request)
        failed = parse_project_manifest(remote.files[f"{root}/.moltage/project.json"])
        assert failed.steps[1].state is ProjectStepState.FAILED

        remote.wavefunction_json = usable_json
        result = service.calculate(request)

        kinds = [step.kind for step in result.project.steps]
        assert kinds.count(ProjectStepKind.ORCA_WBL_TRANSMISSION) == 1
        assert result.project.steps[1].state is ProjectStepState.SUCCEEDED
        assert result.project.steps[1].last_error is None
        with pytest.raises(OrcaWblServiceError, match="Confirm replacement"):
            service.calculate(request)


@pytest.fixture
def completed_wbl_run(tmp_path):
    index = LocalProjectIndexRepository(tmp_path / "known_projects.json")
    remote = WblRemoteExecutor()
    optimized, recovery = _optimized_project(remote, index)
    service = OrcaWblService(
        FixedConnectionService(remote), index,
        now_factory=lambda: NOW,
        temporary_id_factory=lambda: "synthetic-replacement",
    )
    first_request = OrcaWblRequest(
        configured_profile(), optimized.project.remote_project_path,
        _settings(), "synthetic-password",
    )
    first = service.calculate(first_request)
    request = replace(
        first_request,
        settings=replace(_settings(), energy_step_ev=0.1, fermi_energy_ev=-4.8),
        replace_existing_result=first.project.steps[1].orca_wbl_result,
    )
    return remote, service, recovery, first, request


def test_successful_wbl_can_replace_only_step_2_with_new_parameters(completed_wbl_run):
    remote, service, recovery, first, request = completed_wbl_run
    root = request.remote_project_path
    optimization_files = {
        path: data for path, data in remote.files.items()
        if path.startswith(root + "/orca_opt.")
    }
    old_csv = remote.files[f"{root}/wbl/orca_wbl_transmission.csv"]
    previous_submits = remote.submit_count
    remote.operations.clear()
    updates = []

    result = service.calculate(request, project_updated=updates.append)

    assert [item.steps[1].state for item in updates] == [
        ProjectStepState.RUNNING, ProjectStepState.SUCCEEDED,
    ]
    assert len(result.project.steps) == 2
    assert result.project.steps[0] == first.project.steps[0]
    assert result.project.steps[1].orca_wbl_settings == request.settings
    assert result.project.steps[1].orca_wbl_result != request.replace_existing_result
    assert remote.files[f"{root}/wbl/orca_wbl_transmission.csv"] != old_csv
    assert all(remote.files[path] == data for path, data in optimization_files.items())
    assert remote.submit_count == previous_submits
    assert not any(".wbl.previous-" in path for path in remote.directories)
    assert not any(".wbl.previous-" in path for path in remote.files)
    assert not any("wbl-previous-" in path for path in remote.files)
    assert not any(
        op[0] == "read" and (
            op[1].endswith(".gbw") or "/wbl/" in op[1] or "/.wbl." in op[1]
        ) for op in remote.operations
    ), "Result verification must hash on the server, not download large artifacts"
    refreshed = recovery.refresh_project(
        request.profile, root, supplied_password="synthetic-password",
    )
    assert refreshed.project.steps[1] == result.project.steps[1]
    assert refreshed.can_view_orca_wbl
    assert len(refreshed.orca_wbl_presentation.energy_relative_ev) == 41
    assert refreshed.orca_wbl_presentation.report.fermi_energy_ev == -4.8


@pytest.mark.parametrize("authorization", ["missing", "stale"])
def test_replacement_needs_the_current_successful_result(completed_wbl_run, authorization):
    remote, service, _, _, request = completed_wbl_run
    if authorization == "missing":
        request = replace(request, replace_existing_result=None)
    else:
        service.calculate(request)  # Makes the first result's authorization stale.
    before_files, before_directories = dict(remote.files), set(remote.directories)
    with pytest.raises(OrcaWblServiceError, match="Confirm replacement"):
        service.calculate(request)
    assert remote.files == before_files
    assert remote.directories == before_directories


@pytest.mark.parametrize("failure", [
    "conversion", "copy", "copy-ack", "upload", "backup-rename", "publish-rename", "publish-ack", "manifest",
])
def test_failed_replacement_restores_previous_manifest_and_results(
    completed_wbl_run, monkeypatch, failure,
):
    remote, service, recovery, first, request = completed_wbl_run
    root = request.remote_project_path
    old_files = {p: data for p, data in remote.files.items() if p.startswith(root + "/wbl/")}
    original_rename = remote.rename
    original_write = remote.write_bytes
    original_execute = remote.execute
    original_persist = RemoteProjectRepository.persist_update
    failed = False

    def rename(source, destination):
        nonlocal failed
        target = (
            failure == "backup-rename" and destination == root + "/.wbl.previous-synthetic-replacement"
        ) or (failure in {"publish-rename", "publish-ack"} and destination == root + "/wbl")
        if target and not failed:
            failed = True
            if failure == "publish-ack":
                original_rename(source, destination)
            raise RuntimeError("synthetic publication interruption")
        return original_rename(source, destination)

    def write(path, data):
        if failure == "upload" and "/.wbl.tmp-" in path:
            raise RuntimeError("synthetic upload interruption")
        return original_write(path, data)

    def execute(command):
        if failure in {"copy", "copy-ack"} and command.startswith("cp -- "):
            if failure == "copy-ack":
                original_execute(command)
            raise RuntimeError("synthetic copy interruption")
        return original_execute(command)

    def persist(repository, project, **kwargs):
        nonlocal failed
        if (
            failure == "manifest" and not failed
            and project.steps[1].state is ProjectStepState.SUCCEEDED
        ):
            failed = True
            raise RuntimeError("synthetic manifest interruption")
        return original_persist(repository, project, **kwargs)

    monkeypatch.setattr(remote, "rename", rename)
    monkeypatch.setattr(remote, "write_bytes", write)
    monkeypatch.setattr(remote, "execute", execute)
    monkeypatch.setattr(RemoteProjectRepository, "persist_update", persist)
    if failure == "conversion":
        remote.wavefunction_json = b"{}"
    with pytest.raises(OrcaWblServiceError, match="previous WBL parameters and result were restored"):
        service.calculate(request)
    persisted = RemoteProjectRepository(remote).load(root)
    assert persisted.steps == first.project.steps
    assert {p: data for p, data in remote.files.items() if p.startswith(root + "/wbl/")} == old_files
    assert not any(".wbl.previous-" in path for path in remote.directories)
    assert not any(".wbl.tmp-" in path for path in remote.directories)
    assert not any(".wbl.previous-" in path for path in remote.files)
    assert not any("wbl-previous-" in path for path in remote.files)
    refreshed = recovery.refresh_project(request.profile, root, supplied_password="synthetic-password")
    assert refreshed.can_view_orca_wbl
    assert refreshed.project.steps[1].orca_wbl_settings == first.project.steps[1].orca_wbl_settings


def test_lost_completion_ack_is_verified_without_undoing_success(completed_wbl_run, monkeypatch):
    remote, service, _, _, request = completed_wbl_run
    original_persist = RemoteProjectRepository.persist_update

    def persist(repository, project, **kwargs):
        result = original_persist(repository, project, **kwargs)
        if project.steps[1].state is ProjectStepState.SUCCEEDED:
            raise RuntimeError("synthetic lost acknowledgement after commit")
        return result

    monkeypatch.setattr(RemoteProjectRepository, "persist_update", persist)
    messages = []
    result = service.calculate(request, progress=messages.append)
    assert result.project == RemoteProjectRepository(remote).load(request.remote_project_path)
    assert result.project.steps[1].orca_wbl_settings == request.settings
    assert any("committed result was verified" in message for message in messages)


def test_lost_start_ack_requires_matching_running_record(completed_wbl_run, monkeypatch):
    remote, service, _, _, request = completed_wbl_run
    original_persist = RemoteProjectRepository.persist_update

    def persist(repository, project, **kwargs):
        result = original_persist(repository, project, **kwargs)
        if project.steps[1].state is ProjectStepState.RUNNING:
            raise RuntimeError("synthetic lost acknowledgement after start")
        return result

    monkeypatch.setattr(RemoteProjectRepository, "persist_update", persist)
    result = service.calculate(request)
    assert result.project.steps[1].state is ProjectStepState.SUCCEEDED
    assert result.project == RemoteProjectRepository(remote).load(request.remote_project_path)


def test_existing_conversion_directory_is_not_deleted_on_mkdir_failure(completed_wbl_run):
    remote, service, _, first, request = completed_wbl_run
    unrelated = "/tmp/moltage-orca-wbl-synthetic-replacement"
    remote.directories.add(unrelated)
    remote.files[unrelated + "/unrelated-data.txt"] = b"not owned by this run"
    with pytest.raises(OrcaWblServiceError, match="previous WBL parameters and result were restored"):
        service.calculate(request)
    assert remote.files[unrelated + "/unrelated-data.txt"] == b"not owned by this run"
    assert RemoteProjectRepository(remote).load(request.remote_project_path).steps == first.project.steps


def test_failed_backup_cleanup_keeps_new_result_and_reports_old_location(completed_wbl_run, monkeypatch):
    remote, service, _, _, request = completed_wbl_run
    original_execute = remote.execute

    def execute(command):
        if command.startswith("rm -f -- "):
            return RemoteCommandResult(1, b"", b"synthetic permission failure")
        return original_execute(command)

    monkeypatch.setattr(remote, "execute", execute)
    result = service.calculate(request)
    assert result.project.steps[1].state is ProjectStepState.SUCCEEDED
    assert result.project.steps[1].orca_wbl_settings == request.settings
    assert ".wbl.previous-" in result.cleanup_warning
    assert any(".wbl.previous-" in path for path in remote.files)


def test_unverified_previous_artifacts_are_never_overwritten(completed_wbl_run):
    remote, service, _, _, request = completed_wbl_run
    remote.files[request.remote_project_path + "/wbl/user-notes.txt"] = b"user-owned note"
    before = dict(remote.files)
    with pytest.raises(OrcaWblServiceError, match="differs from recorded artifacts"):
        service.calculate(request)
    assert remote.files == before


def test_unknown_manifest_outcome_preserves_both_result_sets(completed_wbl_run, monkeypatch):
    remote, service, _, first, request = completed_wbl_run
    original_persist = RemoteProjectRepository.persist_update
    original_load = RemoteProjectRepository.load
    disconnected = False

    def persist(repository, project, **kwargs):
        nonlocal disconnected
        if project.steps[1].state is ProjectStepState.SUCCEEDED:
            disconnected = True
            raise RuntimeError("synthetic connection loss")
        return original_persist(repository, project, **kwargs)

    def load(repository, path, **kwargs):
        if disconnected:
            raise RuntimeError("synthetic connection unavailable")
        return original_load(repository, path, **kwargs)

    monkeypatch.setattr(RemoteProjectRepository, "persist_update", persist)
    monkeypatch.setattr(RemoteProjectRepository, "load", load)
    with pytest.raises(OrcaWblServiceError, match="outcome/rollback could not be verified"):
        service.calculate(request)
    assert request.remote_project_path + "/wbl/orca_wbl_result.json" in remote.files
    backups = {PurePosixPath(p).name: sha256(data).hexdigest() for p, data in remote.files.items() if "/.wbl.previous-synthetic-replacement/" in p}
    assert backups == dict(first.project.steps[1].orca_wbl_result.artifact_hashes)
    snapshot = remote.files[request.remote_project_path + "/wbl-previous-synthetic-replacement.project.json"]
    assert parse_project_manifest(snapshot) == first.project


def test_connection_loss_during_conversion_retains_prior_project_and_hashes(completed_wbl_run, monkeypatch):
    remote, service, _, first, request = completed_wbl_run
    original_execute = remote.execute
    original_load = RemoteProjectRepository.load
    disconnected = False

    def execute(command):
        nonlocal disconnected
        if "orca_wbl.gbw -json" in command:
            disconnected = True
            raise RuntimeError("synthetic lost connection during conversion")
        return original_execute(command)

    def load(repository, path, **kwargs):
        if disconnected:
            raise RuntimeError("synthetic disconnected")
        return original_load(repository, path, **kwargs)

    monkeypatch.setattr(remote, "execute", execute)
    monkeypatch.setattr(RemoteProjectRepository, "load", load)
    with pytest.raises(OrcaWblServiceError, match="previous project settings and checksums are retained") as failure:
        service.calculate(request)
    snapshot_path = request.remote_project_path + "/wbl-previous-synthetic-replacement.project.json"
    assert snapshot_path in str(failure.value)
    previous = parse_project_manifest(remote.files[snapshot_path])
    assert previous == first.project
    for name, digest in previous.steps[1].orca_wbl_result.artifact_hashes:
        assert sha256(remote.files[request.remote_project_path + "/wbl/" + name]).hexdigest() == digest
    running = parse_project_manifest(remote.files[request.remote_project_path + "/.moltage/project.json"])
    assert running.steps[1].state is ProjectStepState.RUNNING


def test_committed_replacement_survives_local_index_failure(completed_wbl_run, monkeypatch):
    remote, service, _, _, request = completed_wbl_run
    original_mark = service._local_index_repository.mark_seen

    def mark(project, **kwargs):
        if project.steps[1].state is ProjectStepState.SUCCEEDED:
            raise OSError("synthetic local disk full")
        return original_mark(project, **kwargs)

    monkeypatch.setattr(service._local_index_repository, "mark_seen", mark)
    result = service.calculate(request)
    assert "local project index update failed" in result.cleanup_warning
    assert result.project == RemoteProjectRepository(remote).load(request.remote_project_path)
    assert result.project.steps[1].state is ProjectStepState.SUCCEEDED


@pytest.mark.parametrize("transfer", ["upload", "copy"])
def test_partial_transfer_with_unverified_bytes_is_retained_and_reported(completed_wbl_run, monkeypatch, transfer):
    remote, service, _, first, request = completed_wbl_run
    original_write = remote.write_bytes
    original_execute = remote.execute

    def write(path, data):
        if transfer == "upload" and "/.wbl.tmp-" in path:
            original_write(path, b"partial synthetic upload")
            raise OSError("synthetic interrupted upload")
        return original_write(path, data)

    def execute(command):
        if transfer == "copy" and command.startswith("cp -- "):
            # Simulate source changes after download, before the server copy.
            source = shlex.split(command)[2]
            remote.files[source] = b"changed synthetic source"
        return original_execute(command)

    monkeypatch.setattr(remote, "write_bytes", write)
    monkeypatch.setattr(remote, "execute", execute)
    with pytest.raises(OrcaWblServiceError, match="temporary result cleanup needs attention") as failure:
        service.calculate(request)
    if transfer == "copy":
        assert "SHA256 verification failed for orca_wavefunction.json" in str(failure.value)
    assert "/.wbl.tmp-synthetic-replacement" in str(failure.value)
    assert RemoteProjectRepository(remote).load(request.remote_project_path).steps == first.project.steps
    retained = b"partial synthetic upload" if transfer == "upload" else b"changed synthetic source"
    assert any(data == retained for path, data in remote.files.items() if "/.wbl.tmp-" in path)


def test_failed_recovery_snapshot_upload_does_not_start_rerun(completed_wbl_run, monkeypatch):
    remote, service, _, first, request = completed_wbl_run
    original_write = remote.write_bytes

    def write(path, data):
        if "wbl-previous-" in path:
            original_write(path, b"partial synthetic manifest")
            raise OSError("synthetic interrupted snapshot upload")
        return original_write(path, data)

    monkeypatch.setattr(remote, "write_bytes", write)
    with pytest.raises(OrcaWblServiceError, match="run was not started") as failure:
        service.calculate(request)
    assert "wbl-previous-synthetic-replacement.project.json.tmp-synthetic-replacement" in str(failure.value)
    assert RemoteProjectRepository(remote).load(request.remote_project_path) == first.project


@pytest.mark.parametrize("failure", ["lost-ack", "unexpected-file"])
def test_uncertain_staging_creation_reports_retained_directory(completed_wbl_run, monkeypatch, failure):
    remote, service, _, first, request = completed_wbl_run
    original_mkdir = remote.mkdir

    def mkdir(path):
        original_mkdir(path)
        if "/.wbl.tmp-" in path:
            if failure == "lost-ack":
                raise OSError("synthetic lost mkdir acknowledgement")
            remote.files[path + "/orca_wavefunction.json"] = b"unverified existing data"

    monkeypatch.setattr(remote, "mkdir", mkdir)
    message = "staging creation could not be confirmed" if failure == "lost-ack" else "staging is not empty"
    with pytest.raises(OrcaWblServiceError, match=message) as error:
        service.calculate(request)
    assert "/.wbl.tmp-synthetic-replacement" in str(error.value)
    assert request.remote_project_path + "/.wbl.tmp-synthetic-replacement" in remote.directories
    assert RemoteProjectRepository(remote).load(request.remote_project_path).steps == first.project.steps
    if failure == "unexpected-file":
        assert remote.files[request.remote_project_path + "/.wbl.tmp-synthetic-replacement/orca_wavefunction.json"] == b"unverified existing data"
