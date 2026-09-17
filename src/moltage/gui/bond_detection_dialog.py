"""Small session-local editor for the connectivity threshold factor."""

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


MINIMUM_BOND_THRESHOLD_FACTOR = 0.01
MAXIMUM_BOND_THRESHOLD_FACTOR = 10.00


class BondDetectionDialog(QDialog):
    """Present the existing dimensionless connectivity multiplier clearly."""

    preview_factor_changed = Signal(float)

    def __init__(
        self,
        current_factor: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if (
            isinstance(current_factor, bool)
            or not isinstance(current_factor, (int, float))
            or not math.isfinite(current_factor)
            or not (
                MINIMUM_BOND_THRESHOLD_FACTOR
                <= current_factor
                <= MAXIMUM_BOND_THRESHOLD_FACTOR
            )
        ):
            raise ValueError("bond threshold factor must be between 0.01 and 10.00")

        self.setWindowTitle("Bond Detection")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._factor = QDoubleSpinBox(self)
        self._factor.setObjectName("bondThresholdFactor")
        self._factor.setDecimals(2)
        self._factor.setRange(
            MINIMUM_BOND_THRESHOLD_FACTOR,
            MAXIMUM_BOND_THRESHOLD_FACTOR,
        )
        self._factor.setSingleStep(0.10)
        self._factor.setKeyboardTracking(True)
        self._factor.setValue(float(current_factor))
        self._factor.setAccessibleName("Bond threshold factor")
        form.addRow("Bond threshold factor:", self._factor)
        layout.addLayout(form)

        explanation = QLabel(
            "Dimensionless factor for approximate distance-based bond detection.\n"
            "A bond is detected when:\n"
            "distance <= factor × (covalent radius of atom A + "
            "covalent radius of atom B)",
            self,
        )
        explanation.setObjectName("bondDetectionFormula")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._factor.valueChanged.connect(self.preview_factor_changed.emit)

    def selected_factor(self) -> float:
        """Return the validated factor displayed by the dialog."""

        return self._factor.value()
