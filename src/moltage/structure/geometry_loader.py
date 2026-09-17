"""Extension-dispatched local geometry loading for the desktop application."""

from collections.abc import Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from moltage.aims.recovery import recover_submitted_structure
from moltage.domain.bond_display import (
    BondDisplayOrder,
    ConnectivitySource,
    single_bond_display_orders,
    validate_bond_display_orders,
)
from moltage.domain.connectivity import Connectivity
from moltage.domain.structure import MolecularStructure
from moltage.structure.connectivity import infer_connectivity
from moltage.structure.cube import (
    CubeCoordinateUnit,
    CubeScalarField,
    read_cube,
)
from moltage.structure.mol_v2000 import read_mol_v2000


class UnsupportedGeometryFormatError(ValueError):
    """Raised when local geometry extension dispatch has no supported reader."""


SUPPORTED_GEOMETRY_SUFFIXES = (
    ".xyz",
    ".mol",
    ".in",
    ".next_step",
    ".cube",
    ".cub",
)


def has_supported_geometry_extension(path: str | PathLike[str]) -> bool:
    """Return whether *path* has one of the explicitly supported suffixes."""

    return Path(path).suffix.casefold() in SUPPORTED_GEOMETRY_SUFFIXES


@dataclass(frozen=True, slots=True)
class LoadedGeometry:
    """One loaded structure plus local connectivity/display provenance."""

    structure: MolecularStructure
    connectivity: Connectivity
    connectivity_source: ConnectivitySource
    bond_display_orders: tuple[BondDisplayOrder, ...]
    scalar_field: CubeScalarField | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("loaded geometry structure is invalid")
        if not isinstance(self.connectivity, Connectivity):
            raise TypeError("loaded geometry connectivity is invalid")
        if self.connectivity.atom_count != len(self.structure):
            raise ValueError(
                "loaded geometry connectivity atom count does not match structure"
            )
        if not isinstance(self.connectivity_source, ConnectivitySource):
            raise TypeError("loaded geometry connectivity source is invalid")
        object.__setattr__(
            self,
            "bond_display_orders",
            validate_bond_display_orders(
                self.connectivity,
                self.bond_display_orders,
            ),
        )
        if self.scalar_field is not None and not isinstance(
            self.scalar_field,
            CubeScalarField,
        ):
            raise TypeError("loaded geometry scalar field is invalid")


def load_geometry(
    path: str | PathLike[str],
    covalent_radii: Mapping[str, float],
    *,
    multiplier: float,
    cube_coordinate_unit: CubeCoordinateUnit | None = None,
) -> LoadedGeometry:
    """Load one explicitly supported local molecular-geometry format."""

    source_path = Path(path)
    suffix = source_path.suffix.casefold()
    if suffix == ".xyz":
        from moltage.structure.xyz import read_xyz

        structure = read_xyz(source_path)
        connectivity = infer_connectivity(
            structure,
            covalent_radii,
            multiplier=multiplier,
        )
        return LoadedGeometry(
            structure,
            connectivity,
            ConnectivitySource.INFERRED,
            single_bond_display_orders(connectivity),
        )
    if suffix == ".mol":
        parsed = read_mol_v2000(source_path)
        return LoadedGeometry(
            parsed.structure,
            parsed.connectivity,
            ConnectivitySource.EXPLICIT,
            parsed.bond_display_orders,
        )
    if suffix in {".cube", ".cub"}:
        parsed = read_cube(
            source_path,
            coordinate_unit=cube_coordinate_unit,
        )
        connectivity = infer_connectivity(
            parsed.structure,
            covalent_radii,
            multiplier=multiplier,
        )
        return LoadedGeometry(
            parsed.structure,
            connectivity,
            ConnectivitySource.INFERRED,
            single_bond_display_orders(connectivity),
            parsed.scalar_field,
        )
    if suffix in {".in", ".next_step"}:
        control_path = source_path.with_name("control.in")
        control_text = control_path.read_bytes() if control_path.is_file() else None
        structure = recover_submitted_structure(
            control_text=control_text,
            geometry_text=source_path.read_bytes(),
            source_name=source_path.name,
        )
        connectivity = infer_connectivity(
            structure,
            covalent_radii,
            multiplier=multiplier,
        )
        return LoadedGeometry(
            structure,
            connectivity,
            ConnectivitySource.INFERRED,
            single_bond_display_orders(connectivity),
        )
    raise UnsupportedGeometryFormatError(
        f"unsupported geometry file extension {source_path.suffix!r}; "
        "choose an XYZ (.xyz), MOL V2000 (.mol), FHI-aims geometry "
        "(.in or .next_step), or Cube (.cube or .cub) file"
    )
