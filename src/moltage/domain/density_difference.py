"""Fixed-geometry two-fragment density analysis, independent of transport steps."""

from dataclasses import dataclass, replace
from math import ceil, isfinite, prod
import re

from moltage.domain.structure import MolecularStructure


COMPONENTS = ("total", "subset1", "subset2")


@dataclass(frozen=True, slots=True)
class FragmentPartition:
    """Ordered global indexes; fragment-local indexes never replace these IDs."""

    structure: MolecularStructure
    subset1: tuple[int, ...]
    subset2: tuple[int, ...]

    def __post_init__(self):
        if not isinstance(self.structure, MolecularStructure) or len(self.structure) < 2:
            raise ValueError("Two non-empty fragments require at least two atoms.")
        for name in ("subset1", "subset2"):
            indexes = tuple(getattr(self, name))
            if not indexes or any(type(i) is not int or not 0 <= i < len(self.structure) for i in indexes):
                raise ValueError("Each fragment requires valid atom indexes.")
            if len(set(indexes)) != len(indexes):
                raise ValueError("Fragment atom indexes must not repeat.")
            object.__setattr__(self, name, tuple(sorted(indexes)))
        if set(self.subset1) & set(self.subset2):
            raise ValueError("The two fragments must not overlap.")
        if set(self.subset1) | set(self.subset2) != set(range(len(self.structure))):
            raise ValueError("The two fragments must cover every atom.")

    def indexes(self, component: str) -> tuple[int, ...]:
        if component not in COMPONENTS:
            raise ValueError("Unknown density component.")
        return tuple(range(len(self.structure))) if component == "total" else getattr(self, component)

    def geometry(self, component: str) -> MolecularStructure:
        return MolecularStructure(tuple(
            replace(self.structure.atoms[global_index], index=local_index)
            for local_index, global_index in enumerate(self.indexes(component))
        ))


def parse_atom_selection(text: str, atom_count: int) -> tuple[int, ...]:
    """Parse the UI's 1-based comma/range notation into unique global indexes."""
    if not text.strip():
        return ()
    selected: set[int] = set()
    for token in text.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", token)
        if match is None:
            raise ValueError("Use atom numbers and ranges, for example 1-20,25,31-40.")
        first = int(match[1])
        last = int(match[2]) if match[2] is not None else first
        if not 1 <= first <= last <= atom_count:
            raise ValueError(f"Atom numbers must be between 1 and {atom_count}; ranges must increase.")
        selected.update(range(first - 1, last))
    return tuple(sorted(selected))


def format_atom_selection(indexes) -> str:
    ordered = sorted(set(indexes))
    ranges = []
    for index in ordered:
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1][1] = index
        else:
            ranges.append([index, index])
    return ",".join(str(a + 1) if a == b else f"{a + 1}-{b + 1}" for a, b in ranges)


@dataclass(frozen=True, slots=True)
class DensityGrid:
    """One common orthogonal grid. Coordinates in Å; scalar values in bohr^-3."""

    center: tuple[float, float, float]
    dimensions: tuple[int, int, int]
    spacing: float

    def __post_init__(self):
        if len(self.center) != 3 or any(not isfinite(float(v)) for v in self.center):
            raise ValueError("Grid center must contain three finite coordinates.")
        if len(self.dimensions) != 3 or any(type(n) is not int or n < 2 for n in self.dimensions):
            raise ValueError("Grid dimensions must contain three integers of at least two.")
        if isinstance(self.spacing, bool) or not isfinite(self.spacing) or self.spacing <= 0:
            raise ValueError("Grid spacing must be finite and positive.")

    @classmethod
    def around(cls, structure, spacing=0.1, padding=14 * 0.529177210903):
        # FHI-aims documented cluster-grid starting values, not a convergence claim.
        if not structure or not isfinite(spacing) or spacing <= 0 or not isfinite(padding) or padding < 0:
            raise ValueError("Provide a structure, positive grid spacing and non-negative padding.")
        xyz = tuple(zip(*((a.x, a.y, a.z) for a in structure)))
        center = tuple((min(axis) + max(axis)) / 2 for axis in xyz)
        dimensions = tuple(max(3, 2 * ceil(((max(axis) - min(axis)) / 2 + padding) / spacing) + 1) for axis in xyz)
        return cls(center, dimensions, float(spacing))

    @property
    def point_count(self):
        return prod(self.dimensions)

    @property
    def origin(self):
        return tuple(c - (n - 1) * self.spacing / 2 for c, n in zip(self.center, self.dimensions))
