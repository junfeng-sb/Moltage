"""Compact periodic-table selector for the supported viewer elements."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from moltage.visualization.molecule_scene import ELEMENT_COLORS_RGB


_ELEMENTS_Z_1_TO_118 = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg",
    "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr",
    "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf",
    "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po",
    "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm",
    "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs",
    "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
)

_MAIN_GROUPS = {
    1: (1, 18),
    2: (1, 2, 13, 14, 15, 16, 17, 18),
    3: (1, 2, 13, 14, 15, 16, 17, 18),
    4: tuple(range(1, 19)),
    5: tuple(range(1, 19)),
    6: tuple(range(1, 19)),
    7: tuple(range(1, 19)),
}
_PERIOD_RANGES = {
    1: range(1, 3),
    2: range(3, 11),
    3: range(11, 19),
    4: range(19, 37),
    5: range(37, 55),
    6: (55, 56, 57, *range(72, 87)),
    7: (87, 88, 89, *range(104, 119)),
}


class PeriodicTableDialog(QDialog):
    """Select one element while exposing unsupported elements as disabled."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("periodicTableDialog")
        self.setWindowTitle("Select Replacement Element")
        self.setModal(True)
        self._selected_element: str | None = None
        self._element_buttons: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        heading = QLabel(
            "Select an element, then click atoms in the Geometry view to replace them."
        )
        heading.setWordWrap(True)
        outer.addWidget(heading)

        table = QGridLayout()
        table.setHorizontalSpacing(3)
        table.setVerticalSpacing(3)
        for period, atomic_numbers in _PERIOD_RANGES.items():
            groups = _MAIN_GROUPS[period]
            for atomic_number, group in zip(atomic_numbers, groups, strict=True):
                self._add_element_button(table, atomic_number, period - 1, group - 1)

        lanthanide_label = QLabel("Lanthanides")
        lanthanide_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        table.addWidget(lanthanide_label, 7, 0, 1, 3)
        for offset, atomic_number in enumerate(range(58, 72), start=3):
            self._add_element_button(table, atomic_number, 7, offset)

        actinide_label = QLabel("Actinides")
        actinide_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        table.addWidget(actinide_label, 8, 0, 1, 3)
        for offset, atomic_number in enumerate(range(90, 104), start=3):
            self._add_element_button(table, atomic_number, 8, offset)
        outer.addLayout(table)

        note = QLabel("Elements beyond Bi are not supported by the current molecular viewer.")
        note.setObjectName("periodicTableSupportNote")
        outer.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    @property
    def selected_element(self) -> str | None:
        return self._selected_element

    @property
    def element_buttons(self) -> dict[str, QPushButton]:
        return dict(self._element_buttons)

    def _add_element_button(
        self,
        layout: QGridLayout,
        atomic_number: int,
        row: int,
        column: int,
    ) -> None:
        element = _ELEMENTS_Z_1_TO_118[atomic_number - 1]
        button = QPushButton(f"{atomic_number}\n{element}", self)
        button.setObjectName(f"element_{element}")
        button.setAccessibleName(f"Element {element}, atomic number {atomic_number}")
        button.setFixedSize(43, 42)
        supported = element in ELEMENT_COLORS_RGB
        button.setEnabled(supported)
        button.setToolTip(
            f"Replace with {element}"
            if supported
            else f"{element} is not supported by the current viewer"
        )
        if supported:
            button.clicked.connect(
                lambda _checked=False, symbol=element: self._select(symbol)
            )
        layout.addWidget(button, row, column)
        self._element_buttons[element] = button

    def _select(self, element: str) -> None:
        self._selected_element = element
        self.accept()
