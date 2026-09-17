from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import unittest
from uuid import UUID

from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QSpinBox

from moltage.aims.transport_evidence import (
    TransportCompletionEvidence,
    TransportSpinMode,
)
from moltage.app.project_planning import create_initial_project
from moltage.app.project_recovery import ProjectRecoverySnapshot
from moltage.domain.calculation_project import ProjectStepKind, ProjectStepState
from moltage.domain.connectivity import Connectivity
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import LsfResourceRequirementMode
from moltage.gui.aitranss_dialog import AitranssStep4Dialog
from moltage.gui.projects_dialog import Step3RetryDialog
from moltage.junction.electrode_surface import propose_electrode_surfaces
from phase2b1_test_support import profile
from test_electrode_surface import accepted_shape


NOW = datetime(2030, 8, 29, 12, 0, tzinfo=timezone.utc)
PROFILE = profile(profile_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"))


def _snapshot(state=ProjectStepState.SUCCEEDED, *, pyramid_layers=6):
    structure, provenance = accepted_shape(pyramid_layers)
    digest = hashlib.sha256(b"geometry").hexdigest()
    base = create_initial_project(
        base_name="Dialog",
        remote_directory_name="Dialog.20300829",
        source_molecule_name="Dialog.xyz",
        server_profile_id=PROFILE.profile_id,
        remote_project_root=PROFILE.remote_project_root,
        starting_step=ProjectStepKind.MOLECULE_AU_OPT,
        now=NOW,
        project_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
    )
    step2 = replace(base.steps[1], state=ProjectStepState.SUCCEEDED)
    step3 = replace(
        base.steps[2],
        state=state,
        job_id="41001",
        submitted_at=NOW,
        finished_at=NOW,
        input_hashes=(("geometry.in", digest),),
        last_error=("运行时间到达设定上限" if state is ProjectStepState.FAILED else None),
        scheduler_state=("TIMEOUT" if state is ProjectStepState.FAILED else "COMPLETED"),
        submit_script_filename="submit.sh",
        slurm_output_filename="aims.dft.out",
    )
    project = replace(
        base,
        steps=(base.steps[0], step2, step3, base.steps[3]),
        electrode_provenance=provenance,
    )
    if state is ProjectStepState.FAILED:
        return ProjectRecoverySnapshot(
            project,
            ProjectStepKind.TRANSPORT_CONVERGENCE,
            "failed",
        )
    evidence = TransportCompletionEvidence(
        len(structure), 512, TransportSpinMode.NONE, digest
    )
    return ProjectRecoverySnapshot(
        project,
        ProjectStepKind.TRANSPORT_CONVERGENCE,
        "succeeded",
        optimized_structure=structure,
        connectivity=Connectivity(len(structure), ()),
        transport_evidence=evidence,
        surface_proposal=propose_electrode_surfaces(structure, provenance),
    )


class AitranssDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_unavailable_executable_keeps_preview_but_disables_submit(self):
        dialog = AitranssStep4Dialog(
            _snapshot(),
            PROFILE,
            verified_aitranss_path=None,
            executable_preflight_error="AITRANSS executable is unavailable",
        )
        self.assertIn("$natoms  128", dialog._preview.toPlainText())
        self.assertIn("$lsurc   73", dialog._preview.toPlainText())
        self.assertFalse(dialog._submit.isEnabled())
        self.assertIn("unavailable", dialog._validation.text())
        dialog.reject()

    def test_nlayers_source_and_missing_manual_completion_are_visible(self):
        for layers, value, source_text in (
            (4, 2, "AIMS recommended"),
            (5, 3, "AIMS recommended"),
            (6, 4, "User specified"),
        ):
            with self.subTest(layers=layers):
                dialog = AitranssStep4Dialog(
                    _snapshot(pyramid_layers=layers),
                    PROFILE,
                    verified_aitranss_path="/opt/aitranss/bin/aitranss.synthetic.x",
                )
                self.assertEqual(dialog._integer_fields["nlayers"].value(), value)
                self.assertIn(source_text, dialog._nlayers_source_label.text())
                dialog._integer_fields["nlayers"].setValue(value + 1)
                self.application.processEvents()
                self.assertIn("User specified", dialog._nlayers_source_label.text())
                dialog.reject()

        dialog = AitranssStep4Dialog(
            _snapshot(pyramid_layers=7),
            PROFILE,
            verified_aitranss_path="/opt/aitranss/bin/aitranss.synthetic.x",
        )
        self.assertEqual(dialog._integer_fields["nlayers"].value(), 0)
        self.assertFalse(dialog._submit.isEnabled())
        self.assertIn("No reviewed", dialog._nlayers_source_label.text())
        dialog._integer_fields["nlayers"].setValue(4)
        self.application.processEvents()
        self.assertTrue(dialog._submit.isEnabled())
        self.assertIn("User specified", dialog._nlayers_source_label.text())
        dialog.reject()

    def test_step4_editor_uses_two_compact_columns(self):
        dialog = AitranssStep4Dialog(
            _snapshot(),
            PROFILE,
            verified_aitranss_path="/opt/aitranss/bin/aitranss.synthetic.x",
        )
        columns = dialog.layout().itemAt(1).layout()

        self.assertIsNotNone(columns)
        self.assertEqual(columns.count(), 2)
        self.assertIsNotNone(
            dialog.findChild(QGroupBox, "step4SystemSurfaceGroup")
        )
        self.assertIsNotNone(
            dialog.findChild(QGroupBox, "step4TransportOutputGroup")
        )
        self.assertLess(dialog.sizeHint().height(), 900)
        dialog.reject()

    def test_lsf_step3_and_step4_resource_surfaces_use_lsf_meanings(self):
        lsf_profile = replace(
            PROFILE,
            execution_preset=replace(
                PROFILE.execution_preset,
                scheduler_kind=SchedulerKind.LSF,
                unset_slurm_export_env=False,
                lsf_resource_requirement_mode=(
                    LsfResourceRequirementMode.SPAN_RUSAGE
                ),
                slurm_aitranss_launch_mode=None,
                slurm_aitranss_srun_path=None,
            ),
        )
        retry = Step3RetryDialog(
            _snapshot(ProjectStepState.FAILED),
            lsf_profile,
            lsf_profile.execution_preset,
        )
        retry_text = "\n".join(
            label.text() for label in retry.findChildren(QLabel)
        )
        self.assertIn("MPI job slots / ranks:", retry_text)
        self.assertIn("Memory reservation (LSF rusage):", retry_text)
        self.assertNotIn("CPUs per task:", retry_text)
        retry.reject()

        step4 = AitranssStep4Dialog(
            _snapshot(),
            lsf_profile,
            verified_aitranss_path="/opt/aitranss/bin/aitranss.synthetic.x",
        )
        group = step4.findChild(QGroupBox, "step4ResourcesGroup")
        step4_text = "\n".join(
            label.text() for label in group.findChildren(QLabel)
        )
        self.assertEqual(group.title(), "Step-4 LSF resources")
        self.assertIn("LSF job slots / AITRANSS threads:", step4_text)
        self.assertIn("Memory reservation (LSF rusage):", step4_text)
        step4.reject()

        site_default_profile = replace(
            lsf_profile,
            execution_preset=replace(
                lsf_profile.execution_preset,
                lsf_resource_requirement_mode=(
                    LsfResourceRequirementMode.SITE_DEFAULT
                ),
            ),
        )
        site_default = AitranssStep4Dialog(
            _snapshot(),
            site_default_profile,
            verified_aitranss_path="/opt/aitranss/bin/aitranss.synthetic.x",
        )
        default_text = "\n".join(
            label.text()
            for label in site_default.findChild(
                QGroupBox, "step4ResourcesGroup"
            ).findChildren(QLabel)
        )
        self.assertFalse(site_default._memory.isEnabled())
        self.assertIn("Memory reservation (not submitted):", default_text)
        self.assertIn("No #BSUB -R", default_text)
        site_default.reject()

    def test_valid_defaults_enable_direct_submit_and_invalid_edit_disables_it(self):
        enabled_profile = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )
        dialog = AitranssStep4Dialog(
            _snapshot(),
            enabled_profile,
            verified_aitranss_path=(
                "/opt/aitranss/bin/aitranss.synthetic.x"
            ),
        )
        self.assertTrue(dialog._submit.isEnabled())
        self.assertEqual(dialog._threads.value(), 1)
        self.assertEqual(dialog._runtime.value(), 10.0)
        self.assertEqual(dialog._memory.value(), 100)
        self.assertEqual(
            dialog.findChild(QLabel, "step4EmailSummary").text(),
            "user@example.com",
        )
        dialog._integer_fields["lsurx"].setValue(53)
        self.application.processEvents()
        self.assertFalse(dialog._submit.isEnabled())
        self.assertIn("distinct", dialog._validation.text())
        dialog.reject()

    def test_retry_dialog_prepopulates_resources_and_changes_runtime_only_on_copy(self):
        snapshot = _snapshot(ProjectStepState.FAILED)
        enabled_profile = replace(
            PROFILE,
            email_notification_enabled=True,
            email_notification_recipient="user@example.com",
        )
        dialog = Step3RetryDialog(
            snapshot,
            enabled_profile,
            PROFILE.execution_preset,
        )
        self.assertEqual(dialog._ntasks.value(), PROFILE.execution_preset.ntasks)
        dialog._runtime.setValue(72.0)
        dialog._accept_settings()
        selected = dialog.selected_preset()
        self.assertEqual(selected.runtime_minutes, 4320)
        self.assertEqual(PROFILE.execution_preset.runtime_minutes, 2160)
        self.assertEqual(selected.launch_command, PROFILE.execution_preset.launch_command)
        self.assertEqual(
            dialog.findChild(QLabel, "step3RetryEmailSummary").text(),
            "user@example.com",
        )


if __name__ == "__main__":
    unittest.main()
