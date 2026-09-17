"""Bounded per-workspace history for authoritative geometry edit state."""

from dataclasses import dataclass

from moltage.domain.bond_display import (
    BondDisplayOrder,
    validate_bond_display_orders,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.junction import AppliedAuPlacement
from moltage.domain.electrode import AppliedElectrodePlacement
from moltage.domain.structure import MolecularStructure
from moltage.visualization.measurements import MeasurementSessionSnapshot


@dataclass(frozen=True, slots=True)
class GeometryEditState:
    """The authoritative model state restored by one Geometry Undo or Redo."""

    structure: MolecularStructure
    connectivity: Connectivity
    bond_display_orders: tuple[BondDisplayOrder, ...]
    measurements: MeasurementSessionSnapshot
    applied_result: AppliedAuPlacement | None
    applied_electrode_result: AppliedElectrodePlacement | None
    confirmed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("geometry history structure must be MolecularStructure")
        if not isinstance(self.connectivity, Connectivity):
            raise TypeError("geometry history connectivity must be Connectivity")
        if self.connectivity.atom_count != len(self.structure):
            raise ValueError(
                "geometry history connectivity does not match its structure"
            )
        object.__setattr__(
            self,
            "bond_display_orders",
            validate_bond_display_orders(
                self.connectivity,
                self.bond_display_orders,
            ),
        )
        if not isinstance(self.measurements, MeasurementSessionSnapshot):
            raise TypeError(
                "geometry history measurements must be a session snapshot"
            )
        if self.applied_result is not None and not isinstance(
            self.applied_result,
            AppliedAuPlacement,
        ):
            raise TypeError("geometry history contact-Au result is invalid")
        if self.applied_electrode_result is not None and not isinstance(
            self.applied_electrode_result,
            AppliedElectrodePlacement,
        ):
            raise TypeError("geometry history electrode result is invalid")
        if not isinstance(self.confirmed, bool):
            raise TypeError("geometry history confirmation state must be boolean")


class GeometryEditHistory:
    """Five-level-style linear history shared by all Geometry edit types."""

    __slots__ = ("_maximum_depth", "_redo_states", "_undo_states")

    def __init__(self, maximum_depth: int = 5) -> None:
        if isinstance(maximum_depth, bool) or not isinstance(maximum_depth, int):
            raise TypeError("geometry Undo depth must be an integer")
        if maximum_depth <= 0:
            raise ValueError("geometry Undo depth must be positive")
        self._maximum_depth = maximum_depth
        self._undo_states: list[GeometryEditState] = []
        self._redo_states: list[GeometryEditState] = []

    def __len__(self) -> int:
        return len(self._undo_states)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_states)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_states)

    def clear(self) -> None:
        self._undo_states.clear()
        self._redo_states.clear()

    def push(self, state: GeometryEditState) -> None:
        _require_state(state)
        self._undo_states.append(state)
        self._trim(self._undo_states)
        self._redo_states.clear()

    def undo(self, current: GeometryEditState) -> GeometryEditState | None:
        return self._restore(
            current,
            source=self._undo_states,
            destination=self._redo_states,
        )

    def redo(self, current: GeometryEditState) -> GeometryEditState | None:
        return self._restore(
            current,
            source=self._redo_states,
            destination=self._undo_states,
        )

    def _restore(
        self,
        current: GeometryEditState,
        *,
        source: list[GeometryEditState],
        destination: list[GeometryEditState],
    ) -> GeometryEditState | None:
        _require_state(current)
        if not source:
            return None
        restored = source.pop()
        destination.append(current)
        self._trim(destination)
        return restored

    def _trim(self, states: list[GeometryEditState]) -> None:
        if len(states) > self._maximum_depth:
            del states[: len(states) - self._maximum_depth]


def _require_state(state: GeometryEditState) -> None:
    if not isinstance(state, GeometryEditState):
        raise TypeError("geometry history requires GeometryEditState")
