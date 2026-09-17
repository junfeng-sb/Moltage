"""Display-only bond-order metadata kept separate from scientific connectivity."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from moltage.domain.connectivity import Connectivity


class ConnectivitySource(StrEnum):
    """How the currently displayed molecular graph was established."""

    INFERRED = "inferred"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class BondDisplayOrder:
    """Visual strand count for one undirected logical connectivity edge."""

    first_index: int
    second_index: int
    order: int

    def __post_init__(self) -> None:
        for name in ("first_index", "second_index", "order"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"bond display {name} must be an integer")
        if self.first_index < 0 or self.second_index < 0:
            raise ValueError("bond display atom indexes must be non-negative")
        if self.first_index == self.second_index:
            raise ValueError("a bond display edge must connect two atoms")
        if self.first_index > self.second_index:
            first_index = self.second_index
            second_index = self.first_index
            object.__setattr__(self, "first_index", first_index)
            object.__setattr__(self, "second_index", second_index)
        if self.order not in {1, 2, 3}:
            raise ValueError("bond display order must be 1, 2, or 3")

    @property
    def edge(self) -> tuple[int, int]:
        return self.first_index, self.second_index


def single_bond_display_orders(
    connectivity: Connectivity,
) -> tuple[BondDisplayOrder, ...]:
    """Return one visual strand for every logical edge."""

    _require_connectivity(connectivity)
    return tuple(
        BondDisplayOrder(bond.first_index, bond.second_index, 1)
        for bond in connectivity
    )


def validate_bond_display_orders(
    connectivity: Connectivity,
    orders: Iterable[BondDisplayOrder],
) -> tuple[BondDisplayOrder, ...]:
    """Require exactly one display-order record for every logical edge."""

    _require_connectivity(connectivity)
    records = tuple(orders)
    if any(not isinstance(record, BondDisplayOrder) for record in records):
        raise TypeError(
            "bond display metadata must contain BondDisplayOrder instances"
        )
    by_edge: dict[tuple[int, int], BondDisplayOrder] = {}
    for record in records:
        if record.second_index >= connectivity.atom_count:
            raise ValueError(
                "bond display atom index is outside the connectivity atom range"
            )
        if record.edge in by_edge:
            raise ValueError(
                f"duplicate bond display metadata for atom indexes {record.edge}"
            )
        by_edge[record.edge] = record
    logical_edges = {
        (bond.first_index, bond.second_index) for bond in connectivity
    }
    if set(by_edge) != logical_edges:
        missing = tuple(sorted(logical_edges - set(by_edge)))
        extra = tuple(sorted(set(by_edge) - logical_edges))
        raise ValueError(
            "bond display metadata must match logical connectivity exactly; "
            f"missing={missing}, extra={extra}"
        )
    return tuple(by_edge[edge] for edge in sorted(logical_edges))


def normalized_bond_display_orders(
    connectivity: Connectivity,
    orders: Iterable[BondDisplayOrder] | None,
) -> tuple[BondDisplayOrder, ...]:
    """Validate supplied metadata, or explicitly initialize all edges as single."""

    if orders is None:
        return single_bond_display_orders(connectivity)
    return validate_bond_display_orders(connectivity, orders)


def remap_bond_display_orders(
    source_connectivity: Connectivity,
    source_orders: Iterable[BondDisplayOrder],
    result_connectivity: Connectivity,
    old_to_new_indices: Sequence[int | None],
) -> tuple[BondDisplayOrder, ...]:
    """Preserve surviving orders through an authoritative atom-index mapping."""

    source_records = validate_bond_display_orders(
        source_connectivity,
        source_orders,
    )
    mapping = tuple(old_to_new_indices)
    if len(mapping) != source_connectivity.atom_count:
        raise ValueError(
            "bond display remapping must cover every source atom index"
        )
    result_edges = {
        (bond.first_index, bond.second_index) for bond in result_connectivity
    }
    preserved: dict[tuple[int, int], int] = {}
    for record in source_records:
        first = mapping[record.first_index]
        second = mapping[record.second_index]
        if first is None or second is None:
            continue
        edge = tuple(sorted((first, second)))
        if edge not in result_edges:
            raise ValueError(
                "a mapped surviving bond is absent from result connectivity: "
                f"{edge}"
            )
        preserved[edge] = record.order
    return tuple(
        BondDisplayOrder(first, second, preserved.get((first, second), 1))
        for first, second in sorted(result_edges)
    )


def extend_bond_display_orders(
    source_connectivity: Connectivity,
    source_orders: Iterable[BondDisplayOrder],
    result_connectivity: Connectivity,
) -> tuple[BondDisplayOrder, ...]:
    """Preserve a source graph that is an exact prefix/subgraph of a result."""

    if result_connectivity.atom_count < source_connectivity.atom_count:
        raise ValueError("extended connectivity cannot remove source atoms")
    source_records = validate_bond_display_orders(
        source_connectivity,
        source_orders,
    )
    source_by_edge = {record.edge: record.order for record in source_records}
    result_edges = {
        (bond.first_index, bond.second_index) for bond in result_connectivity
    }
    missing = tuple(sorted(set(source_by_edge) - result_edges))
    if missing:
        raise ValueError(
            "extended connectivity cannot remove source bonds: "
            f"{missing}"
        )
    return tuple(
        BondDisplayOrder(first, second, source_by_edge.get((first, second), 1))
        for first, second in sorted(result_edges)
    )


def _require_connectivity(connectivity: Connectivity) -> None:
    if not isinstance(connectivity, Connectivity):
        raise TypeError("bond display metadata requires Connectivity")
