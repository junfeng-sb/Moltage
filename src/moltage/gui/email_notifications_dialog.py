"""Small profile-owned scheduler-native email settings dialog."""

from dataclasses import replace

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from moltage.app.server_profiles import ServerProfileRepository
from moltage.domain.scheduler import scheduler_display_name
from moltage.domain.server_profile import ServerProfile


class EmailNotificationsDialog(QDialog):
    """Edit only the selected server profile's native scheduler mail preference."""

    def __init__(
        self,
        profile: ServerProfile,
        repository: ServerProfileRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(profile, ServerProfile):
            raise TypeError("email settings require a saved server profile")
        if not isinstance(repository, ServerProfileRepository):
            raise TypeError("email settings require a server profile repository")
        self._profile = profile
        self._repository = repository
        self._saved_profile: ServerProfile | None = None
        preset = profile.execution_preset
        scheduler_name = (
            scheduler_display_name(preset.scheduler_kind)
            if preset is not None
            else "Scheduler"
        )

        self.setWindowTitle("Email Notifications")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._server = QLabel(profile.name, self)
        self._server.setObjectName("emailNotificationServer")
        form.addRow("Server:", self._server)

        self._enabled = QCheckBox("Enable completion email", self)
        self._enabled.setObjectName("emailNotificationsEnabled")
        self._enabled.setChecked(profile.email_notification_enabled)
        form.addRow("", self._enabled)

        self._recipient = QLineEdit(self)
        self._recipient.setObjectName("emailNotificationRecipient")
        self._recipient.setText(profile.email_notification_recipient or "")
        form.addRow("Recipient:", self._recipient)

        self._delivery = QLabel(f"{scheduler_name} native mail", self)
        self._delivery.setObjectName("emailNotificationDelivery")
        form.addRow("Delivery:", self._delivery)
        layout.addLayout(form)

        explanation = QLabel(
            f"A notification is sent when the {scheduler_name} job ends.\n"
            "The application does not need to remain open.",
            self,
        )
        explanation.setObjectName("emailNotificationExplanation")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._save = buttons.addButton(
            "Save",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._save.setObjectName("emailNotificationSave")
        self._save.clicked.connect(self._save_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def saved_profile(self) -> ServerProfile:
        if self._saved_profile is None:
            raise RuntimeError("email notification settings were not saved")
        return self._saved_profile

    @Slot()
    def _save_settings(self) -> None:
        try:
            updated = replace(
                self._profile,
                email_notification_enabled=self._enabled.isChecked(),
                email_notification_recipient=self._recipient.text(),
            )
            self._repository.save(updated)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Invalid email notification settings",
                str(error),
            )
            return
        self._saved_profile = updated
        self.accept()
