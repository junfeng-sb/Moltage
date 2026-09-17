"""Immutable domain records for parameterized Au-electrode preparation."""

from dataclasses import dataclass
from math import dist, isfinite

from moltage.domain.anchor import AnchorCandidate
from moltage.domain.au_pyramid import MoltageAuPyramid
from moltage.domain.au_lattice_extension import (
    AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM,
    AuLatticeExtension,
    AuLatticeExtensionError,
    AuLatticeFrame,
    has_au_lattice_clearance,
    lattice_distance_squared,
    raw_extension_candidate_keys,
)
from moltage.domain.connectivity import Bond, Connectivity
from moltage.domain.structure import MolecularStructure


Vector3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ElectrodeContactSite:
    """One recognized occupied anchor and its authoritative contact Au."""

    anchor: AnchorCandidate
    contact_au_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, AnchorCandidate):
            raise TypeError("electrode contact site anchor must be an AnchorCandidate")
        _validate_index(self.contact_au_index, "contact Au index")
        if len(self.anchor.attached_au_indices) != 1:
            raise ValueError(
                "electrode contact site requires exactly one attached contact Au"
            )
        if self.anchor.attached_au_indices[0] != self.contact_au_index:
            raise ValueError(
                "contact Au index must equal the anchor's attached Au index"
            )

    @property
    def sort_key(self) -> tuple[int, int]:
        return self.anchor.binding_atom_index, self.contact_au_index


@dataclass(frozen=True, slots=True)
class ElectrodeClusterProposal:
    """Placed canonical pyramid plus its complete local/global identity mapping."""

    site: ElectrodeContactSite
    side: str
    pyramid: MoltageAuPyramid
    roll_degrees: int
    transformed_coordinates: tuple[Vector3, ...]
    local_to_global_indices: tuple[int, ...]
    mapped_bonds: tuple[Bond, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.site, ElectrodeContactSite):
            raise TypeError("cluster proposal site must be an ElectrodeContactSite")
        normalized_side = str(self.side).upper()
        if normalized_side not in {"LEFT", "RIGHT"}:
            raise ValueError("cluster proposal side must be LEFT or RIGHT")
        if not isinstance(self.pyramid, MoltageAuPyramid):
            raise TypeError("cluster proposal requires a canonical Au pyramid")
        if isinstance(self.roll_degrees, bool) or not isinstance(
            self.roll_degrees, int
        ):
            raise TypeError("cluster roll angle must be an integer number of degrees")
        if self.roll_degrees < 0 or self.roll_degrees >= 360:
            raise ValueError("cluster roll angle must be normalized to [0, 360)")

        coordinates = tuple(
            _finite_vector(point, "transformed pyramid coordinate")
            for point in self.transformed_coordinates
        )
        expected_count = len(self.pyramid.structure)
        if len(coordinates) != expected_count:
            raise ValueError(
                "cluster proposal coordinate count must match its canonical pyramid"
            )
        mapping = tuple(self.local_to_global_indices)
        if len(mapping) != expected_count:
            raise ValueError(
                "cluster proposal mapping count must match its canonical pyramid"
            )
        for atom_index in mapping:
            _validate_index(atom_index, "pyramid-local to global index")
        if len(set(mapping)) != len(mapping):
            raise ValueError("pyramid-local to global indexes must be unique")
        if mapping[self.pyramid.apex_local_index] != self.site.contact_au_index:
            raise ValueError("pyramid apex must map to the existing contact Au index")

        mapped_bonds = tuple(self.mapped_bonds)
        if any(not isinstance(bond, Bond) for bond in mapped_bonds):
            raise TypeError("mapped pyramid bonds must contain Bond instances")
        mapped_set = set(mapping)
        if any(
            bond.first_index not in mapped_set or bond.second_index not in mapped_set
            for bond in mapped_bonds
        ):
            raise ValueError("mapped pyramid bonds must remain within their cluster")
        pairs = tuple((bond.first_index, bond.second_index) for bond in mapped_bonds)
        if len(set(pairs)) != len(pairs):
            raise ValueError("mapped pyramid bonds must be unique")

        object.__setattr__(self, "side", normalized_side)
        object.__setattr__(self, "transformed_coordinates", coordinates)
        object.__setattr__(self, "local_to_global_indices", mapping)
        object.__setattr__(
            self,
            "mapped_bonds",
            tuple(
                sorted(
                    mapped_bonds,
                    key=lambda bond: (bond.first_index, bond.second_index),
                )
            ),
        )

    @property
    def pyramid_layers(self) -> int:
        return self.pyramid.pyramid_layers

    @property
    def apex_atom_index(self) -> int:
        return self.local_to_global_indices[self.pyramid.apex_local_index]

    @property
    def new_atom_indices(self) -> tuple[int, ...]:
        return tuple(
            global_index
            for identity, global_index in zip(
                self.pyramid.atom_identities,
                self.local_to_global_indices,
                strict=True,
            )
            if identity.local_index != self.pyramid.apex_local_index
        )


@dataclass(frozen=True, slots=True)
class ElectrodePlacementProposal:
    """One immutable preview whose exact structure is authoritative for Done."""

    source_structure: MolecularStructure
    source_connectivity: Connectivity
    clusters: tuple[ElectrodeClusterProposal, ...]
    preview_structure: MolecularStructure
    preview_connectivity: Connectivity
    minimum_intercluster_distance: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_structure, MolecularStructure):
            raise TypeError("proposal source must be a MolecularStructure")
        if not isinstance(self.source_connectivity, Connectivity):
            raise TypeError("proposal source connectivity must be a Connectivity")
        if self.source_connectivity.atom_count != len(self.source_structure):
            raise ValueError("proposal source connectivity must match its structure")
        clusters = tuple(self.clusters)
        if len(clusters) not in {1, 2}:
            raise ValueError("electrode preview requires exactly one or two clusters")
        if any(not isinstance(item, ElectrodeClusterProposal) for item in clusters):
            raise TypeError("electrode preview contains an invalid cluster proposal")
        if clusters != tuple(sorted(clusters, key=lambda item: item.site.sort_key)):
            raise ValueError("electrode preview clusters must use deterministic site order")
        if len({item.site for item in clusters}) != len(clusters):
            raise ValueError("electrode preview clusters must target unique sites")
        if len({item.apex_atom_index for item in clusters}) != len(clusters):
            raise ValueError("electrode preview clusters must use unique contact Au atoms")
        if len({item.pyramid.pyramid_layers for item in clusters}) != 1:
            raise ValueError("both electrode sides must use the same pyramid layer count")
        if len(clusters) == 2 and tuple(item.side for item in clusters) != (
            "LEFT",
            "RIGHT",
        ):
            raise ValueError("two-cluster preview must use LEFT then RIGHT side identities")

        if not isinstance(self.preview_structure, MolecularStructure):
            raise TypeError("proposal preview must be a MolecularStructure")
        if not isinstance(self.preview_connectivity, Connectivity):
            raise TypeError("proposal preview connectivity must be a Connectivity")
        expected_atom_count = len(self.source_structure) + sum(
            len(cluster.new_atom_indices) for cluster in clusters
        )
        if len(self.preview_structure) != expected_atom_count:
            raise ValueError(
                "electrode preview atom count must equal source plus generated atoms"
            )
        if self.preview_connectivity.atom_count != expected_atom_count:
            raise ValueError("electrode preview connectivity must match its structure")
        if self.preview_structure.atoms[: len(self.source_structure)] != (
            self.source_structure.atoms
        ):
            raise ValueError("electrode preview must preserve source atoms as its prefix")

        next_new_index = len(self.source_structure)
        mapped_bonds: list[Bond] = []
        for cluster in clusters:
            if cluster.apex_atom_index >= len(self.source_structure):
                raise ValueError("electrode apex must map to a preserved source atom")
            count = len(cluster.new_atom_indices)
            if cluster.new_atom_indices != tuple(
                range(next_new_index, next_new_index + count)
            ):
                raise ValueError("generated electrode atoms must follow cluster/local order")
            next_new_index += count
            for local_index, global_index in enumerate(
                cluster.local_to_global_indices
            ):
                atom = self.preview_structure[global_index]
                if atom.element != "Au":
                    raise ValueError("every mapped electrode atom must be Au")
                if (atom.x, atom.y, atom.z) != cluster.transformed_coordinates[
                    local_index
                ]:
                    raise ValueError("preview coordinates must equal stored coordinates")
            mapped_bonds.extend(cluster.mapped_bonds)
        if set(self.preview_connectivity.bonds) != set(
            self.source_connectivity.bonds
        ) | set(mapped_bonds):
            raise ValueError("preview connectivity contains unexpected bonds")

        minimum = self.minimum_intercluster_distance
        if len(clusters) == 1:
            if minimum is not None:
                raise ValueError(
                    "one-cluster preview cannot have an intercluster distance"
                )
        else:
            if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                raise TypeError("two-cluster minimum distance must be numeric")
            minimum = float(minimum)
            if not isfinite(minimum) or minimum < 0.0:
                raise ValueError(
                    "two-cluster minimum distance must be finite and non-negative"
                )
            object.__setattr__(self, "minimum_intercluster_distance", minimum)
        object.__setattr__(self, "clusters", clusters)

    @property
    def pyramid_layers(self) -> int:
        return self.clusters[0].pyramid_layers

    @property
    def added_au_indices(self) -> tuple[int, ...]:
        return tuple(
            atom_index
            for cluster in self.clusters
            for atom_index in cluster.new_atom_indices
        )


@dataclass(frozen=True, slots=True)
class AppliedElectrodePlacement:
    """The accepted standard preview plus ordered immutable lattice extensions."""

    structure: MolecularStructure
    connectivity: Connectivity
    proposal: ElectrodePlacementProposal
    added_au_indices: tuple[int, ...]
    lattice_extensions: tuple[AuLatticeExtension, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, ElectrodePlacementProposal):
            raise TypeError("applied electrode result requires its accepted proposal")
        if not isinstance(self.structure, MolecularStructure):
            raise TypeError("applied electrode structure must be a MolecularStructure")
        if not isinstance(self.connectivity, Connectivity):
            raise TypeError("applied electrode connectivity must be a Connectivity")

        base_structure = self.proposal.preview_structure
        base_connectivity = self.proposal.preview_connectivity
        extensions = tuple(self.lattice_extensions)
        if any(not isinstance(item, AuLatticeExtension) for item in extensions):
            raise TypeError(
                "applied lattice extensions must contain AuLatticeExtension records"
            )
        if len(self.structure) != len(base_structure) + len(extensions):
            raise ValueError(
                "applied electrode structure must equal the standard preview plus "
                "its extensions"
            )
        if self.structure.atoms[: len(base_structure)] != base_structure.atoms:
            raise ValueError("applied electrode standard atoms must remain unchanged")
        if self.structure.comment != base_structure.comment:
            raise ValueError("applied electrode structure comment must remain unchanged")
        if self.connectivity.atom_count != len(self.structure):
            raise ValueError("applied electrode connectivity must match its structure")

        expected_extension_indices = tuple(
            range(len(base_structure), len(self.structure))
        )
        if tuple(item.global_atom_index for item in extensions) != (
            expected_extension_indices
        ):
            raise ValueError(
                "lattice extensions must preserve their global append order"
            )
        cluster_by_side = {
            cluster.side: cluster for cluster in self.proposal.clusters
        }
        if any(item.side not in cluster_by_side for item in extensions):
            raise ValueError("lattice extension side has no accepted standard cluster")
        if any(
            item.layer_index >= cluster_by_side[item.side].pyramid_layers
            for item in extensions
        ):
            raise ValueError("lattice extension layer is outside the standard pyramid")
        extension_identities = tuple(
            (item.side, item.layer_index, item.lattice_key) for item in extensions
        )
        if len(set(extension_identities)) != len(extension_identities):
            raise ValueError("lattice extension identities must be unique")
        standard_identities = {
            (cluster.side, identity.layer_index, identity.lattice_key)
            for cluster in self.proposal.clusters
            for identity in cluster.pyramid.atom_identities
        }
        if standard_identities & set(extension_identities):
            raise ValueError("lattice extension identity collides with the standard core")

        frames = {
            cluster.side: AuLatticeFrame.from_standard_mapping(
                side=cluster.side,
                pyramid_layers=cluster.pyramid_layers,
                spacing_angstrom=(
                    cluster.pyramid.nearest_neighbor_spacing_angstrom
                ),
                coordinates_by_key=tuple(
                    (
                        identity.lattice_key,
                        _atom_coordinates(
                            self.structure,
                            cluster.local_to_global_indices[identity.local_index],
                        ),
                    )
                    for identity in cluster.pyramid.atom_identities
                ),
            )
            for cluster in self.proposal.clusters
        }
        occupied = {
            (cluster.side, layer_index): {
                identity.lattice_key
                for identity in cluster.pyramid.atom_identities
                if identity.layer_index == layer_index
            }
            for cluster in self.proposal.clusters
            for layer_index in range(cluster.pyramid_layers)
        }
        existing_au_coordinates = [
            _atom_coordinates(self.structure, atom.index)
            for atom in self.structure.atoms[: len(base_structure)]
            if atom.element == "Au"
        ]
        for extension in extensions:
            atom = self.structure[extension.global_atom_index]
            if atom.element != "Au":
                raise ValueError("every lattice extension atom must be Au")
            frame = frames[extension.side]
            expected_coordinates = frame.coordinate(extension.lattice_key)
            if dist(
                _atom_coordinates(self.structure, extension.global_atom_index),
                expected_coordinates,
            ) > AU_LATTICE_GEOMETRY_TOLERANCE_ANGSTROM:
                raise ValueError(
                    "lattice extension coordinate conflicts with its signed identity"
                )
            layer_occupancy = occupied[(extension.side, extension.layer_index)]
            try:
                candidates = raw_extension_candidate_keys(
                    layer_occupancy,
                    layer_index=extension.layer_index,
                )
            except AuLatticeExtensionError as error:
                raise ValueError(str(error)) from error
            if extension.lattice_key not in candidates:
                raise ValueError(
                    "lattice extension cannot be replayed in global append order"
                )
            if not has_au_lattice_clearance(
                expected_coordinates,
                existing_au_coordinates,
                spacing_angstrom=frame.spacing_angstrom,
            ):
                raise ValueError("lattice extension overlaps existing Au geometry")
            layer_occupancy.add(extension.lattice_key)
            existing_au_coordinates.append(expected_coordinates)

        electrode_identities: dict[
            str, list[tuple[tuple[int, int, int], int, bool]]
        ] = {side: [] for side in cluster_by_side}
        for cluster in self.proposal.clusters:
            electrode_identities[cluster.side].extend(
                (
                    identity.lattice_key,
                    cluster.local_to_global_indices[identity.local_index],
                    False,
                )
                for identity in cluster.pyramid.atom_identities
            )
        for extension in extensions:
            electrode_identities[extension.side].append(
                (extension.lattice_key, extension.global_atom_index, True)
            )
        extension_bonds: list[Bond] = []
        for records in electrode_identities.values():
            for offset, (first_key, first_index, first_extension) in enumerate(records):
                for second_key, second_index, second_extension in records[offset + 1 :]:
                    if not (first_extension or second_extension):
                        continue
                    if lattice_distance_squared(first_key, second_key) != 1:
                        continue
                    extension_bonds.append(
                        Bond(
                            first_index,
                            second_index,
                            dist(
                                _atom_coordinates(self.structure, first_index),
                                _atom_coordinates(self.structure, second_index),
                            ),
                        )
                    )
        expected_bonds = set(base_connectivity.bonds) | set(extension_bonds)
        if set(self.connectivity.bonds) != expected_bonds:
            raise ValueError(
                "applied electrode connectivity changed a standard bond or contains "
                "a non-lattice extension bond"
            )
        added = tuple(self.added_au_indices)
        expected_added = self.proposal.added_au_indices + expected_extension_indices
        if added != expected_added:
            raise ValueError(
                "applied electrode indexes must equal standard plus extension mappings"
            )
        object.__setattr__(self, "added_au_indices", added)
        object.__setattr__(self, "lattice_extensions", extensions)


def _validate_index(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _finite_vector(value: Vector3, name: str) -> Vector3:
    components = tuple(value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three components")
    numeric: list[float] = []
    for component in components:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise TypeError(f"{name} components must be numeric")
        number = float(component)
        if not isfinite(number):
            raise ValueError(f"{name} components must be finite")
        numeric.append(number)
    return numeric[0], numeric[1], numeric[2]


def _atom_coordinates(
    structure: MolecularStructure,
    atom_index: int,
) -> Vector3:
    atom = structure[atom_index]
    return atom.x, atom.y, atom.z
