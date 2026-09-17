from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from moltage.app.server_profiles import ServerProfileRepository
from moltage.domain.scheduler import SchedulerKind
from moltage.domain.server_profile import LsfResourceRequirementMode
from moltage.gui.email_notifications_dialog import (
    EmailNotificationsDialog,
)
from phase2b1_test_support import profile


class EmailNotificationsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = ServerProfileRepository(
            Path(self.temporary.name) / "profiles.json"
        )
        self.server = profile()
        self.repository.save(self.server)

    def test_dialog_is_small_native_slurm_configuration_only(self) -> None:
        dialog = EmailNotificationsDialog(self.server, self.repository)
        text = "\n".join(
            widget.text()
            for widget in (
                *dialog.findChildren(QLabel),
                *dialog.findChildren(QCheckBox),
            )
        )

        self.assertEqual(dialog.windowTitle(), "Email Notifications")
        self.assertEqual(
            dialog.findChild(QLabel, "emailNotificationServer").text(),
            "ExampleCluster",
        )
        self.assertIsNotNone(
            dialog.findChild(QCheckBox, "emailNotificationsEnabled")
        )
        self.assertEqual(len(dialog.findChildren(QLineEdit)), 1)
        self.assertEqual(
            dialog.findChild(QLabel, "emailNotificationDelivery").text(),
            "Slurm native mail",
        )
        self.assertIn("does not need to remain open", text)
        self.assertNotIn("SMTP", text)
        self.assertNotIn("Password", text)
        dialog.reject()

    def test_save_persists_and_reopen_displays_profile_owned_settings(self) -> None:
        dialog = EmailNotificationsDialog(self.server, self.repository)
        dialog._enabled.setChecked(True)
        dialog._recipient.setText("  user@example.com  ")
        dialog._save.click()

        saved = self.repository.load().profiles[0]
        self.assertTrue(saved.email_notification_enabled)
        self.assertEqual(
            saved.email_notification_recipient,
            "user@example.com",
        )
        self.assertEqual(dialog.saved_profile(), saved)

        reopened = EmailNotificationsDialog(saved, self.repository)
        self.assertTrue(reopened._enabled.isChecked())
        self.assertEqual(reopened._recipient.text(), "user@example.com")
        reopened.reject()

    def test_lsf_profile_uses_lsf_native_delivery_text(self) -> None:
        lsf = replace(
            self.server,
            execution_preset=replace(
                self.server.execution_preset,
                scheduler_kind=SchedulerKind.LSF,
                unset_slurm_export_env=False,
                lsf_resource_requirement_mode=(
                    LsfResourceRequirementMode.SPAN_RUSAGE
                ),
                slurm_aitranss_launch_mode=None,
                slurm_aitranss_srun_path=None,
            ),
        )
        dialog = EmailNotificationsDialog(lsf, self.repository)

        self.assertEqual(
            dialog.findChild(QLabel, "emailNotificationDelivery").text(),
            "LSF native mail",
        )
        self.assertIn("LSF job ends", dialog.findChild(
            QLabel, "emailNotificationExplanation"
        ).text())
        dialog.reject()

    def test_cancel_does_not_mutate_saved_profile(self) -> None:
        original_bytes = self.repository.path.read_bytes()
        dialog = EmailNotificationsDialog(self.server, self.repository)
        dialog._enabled.setChecked(True)
        dialog._recipient.setText("cancelled@example.com")
        button_box = dialog.findChild(QDialogButtonBox)

        button_box.button(QDialogButtonBox.StandardButton.Cancel).click()

        self.assertEqual(self.repository.path.read_bytes(), original_bytes)
        self.assertEqual(self.repository.load().profiles[0], self.server)

    def test_invalid_enabled_address_is_blocked_before_persistence(self) -> None:
        original_bytes = self.repository.path.read_bytes()
        dialog = EmailNotificationsDialog(self.server, self.repository)
        dialog._enabled.setChecked(True)
        dialog._recipient.setText("user@example.com\n#SBATCH --mail-type=ALL")

        with patch.object(QMessageBox, "critical") as critical:
            dialog._save.click()

        critical.assert_called_once()
        self.assertEqual(self.repository.path.read_bytes(), original_bytes)
        self.assertEqual(dialog.result(), 0)


if __name__ == "__main__":
    unittest.main()
