"""Validated molecular-orbital Cube requests and deterministic rendering."""

from dataclasses import dataclass
from enum import Enum
from math import ceil, isfinite
import re

from moltage.domain.structure import MolecularStructure


class OrbitalCubeValidationError(ValueError):
    """Raised when an orbital Cube request is incomplete or ambiguous."""


class FrontierOrbital(Enum):
    """Frontier-state tokens accepted by the requested FHI-aims workflow."""

    HOMO_MINUS_2 = "homo-2"
    HOMO_MINUS_1 = "homo-1"
    HOMO = "homo"
    LUMO = "lumo"
    LUMO_PLUS_1 = "lumo+1"
    LUMO_PLUS_2 = "lumo+2"


FRONTIER_ORBITAL_ORDER = tuple(FrontierOrbital)

_BOHR_TO_ANGSTROM = 0.529177210903
_DEFAULT_CLUSTER_PADDING_BOHR = 14.0


@dataclass(frozen=True, slots=True)
class OrbitalCubeOutputSettings:
    """Requested frontier labels, absolute states, and optional grid spacing."""

    frontier_orbitals: tuple[FrontierOrbital, ...] = ()
    eigenstate_indices: tuple[int, ...] = ()
    grid_spacing_angstrom: float | None = None

    def __post_init__(self) -> None:
        frontier = tuple(self.frontier_orbitals)
        if any(not isinstance(item, FrontierOrbital) for item in frontier):
            raise OrbitalCubeValidationError(
                "frontier orbital selections must use supported labels"
            )
        if len(set(frontier)) != len(frontier):
            raise OrbitalCubeValidationError(
                "duplicate frontier orbital selections are not allowed"
            )
        selected = frozenset(frontier)
        object.__setattr__(
            self,
            "frontier_orbitals",
            tuple(item for item in FRONTIER_ORBITAL_ORDER if item in selected),
        )

        indices = tuple(self.eigenstate_indices)
        if any(
            isinstance(index, bool) or not isinstance(index, int)
            for index in indices
        ):
            raise OrbitalCubeValidationError(
                "orbital eigenstate numbers must be integers"
            )
        if any(index <= 0 for index in indices):
            raise OrbitalCubeValidationError(
                "orbital eigenstate numbers must be greater than zero"
            )
        if len(set(indices)) != len(indices):
            raise OrbitalCubeValidationError(
                "duplicate orbital eigenstate numbers are not allowed"
            )
        object.__setattr__(self, "eigenstate_indices", tuple(sorted(indices)))

        spacing = self.grid_spacing_angstrom
        if spacing is not None:
            if isinstance(spacing, bool) or not isinstance(spacing, (int, float)):
                raise OrbitalCubeValidationError(
                    "Cube grid spacing must be numeric"
                )
            normalized = float(spacing)
            if not isfinite(normalized) or normalized <= 0.0:
                raise OrbitalCubeValidationError(
                    "Cube grid spacing must be finite and greater than zero"
                )
            if not frontier and not indices:
                raise OrbitalCubeValidationError(
                    "Cube grid spacing requires at least one requested orbital"
                )
            object.__setattr__(self, "grid_spacing_angstrom", normalized)

    @property
    def has_outputs(self) -> bool:
        return bool(self.frontier_orbitals or self.eigenstate_indices)


def default_step1_orbital_cubes() -> OrbitalCubeOutputSettings:
    """Return the six user-approved default frontier-orbital requests."""

    return OrbitalCubeOutputSettings(
        frontier_orbitals=FRONTIER_ORBITAL_ORDER,
    )


def render_orbital_cube_directives(
    settings: OrbitalCubeOutputSettings,
    structure: MolecularStructure | None = None,
) -> tuple[str, ...]:
    """Render canonical FHI-aims output/cube sub-directive blocks."""

    if not isinstance(settings, OrbitalCubeOutputSettings):
        raise TypeError("orbital Cube settings must be OrbitalCubeOutputSettings")
    grid_lines: tuple[str, ...] = ()
    if settings.grid_spacing_angstrom is not None:
        if not isinstance(structure, MolecularStructure) or not structure:
            raise OrbitalCubeValidationError(
                "an explicit Cube grid spacing requires the molecular structure"
            )
        grid_lines = _render_compatible_cluster_grid(
            structure,
            settings.grid_spacing_angstrom,
        )

    lines: list[str] = []
    requests: tuple[FrontierOrbital | int, ...] = (
        *settings.frontier_orbitals,
        *settings.eigenstate_indices,
    )
    for state in requests:
        token = state.value if isinstance(state, FrontierOrbital) else str(state)
        lines.extend(
            (
                f"output cube eigenstate {token}",
                f"cube filename {orbital_cube_filename(state)}",
                *grid_lines,
            )
        )
    return tuple(lines)


def parse_generated_orbital_cube_directives(
    directives: tuple[str, ...],
) -> tuple[OrbitalCubeOutputSettings, tuple[str, ...]]:
    """Remove and recover only canonical application-generated Cube blocks."""

    if not isinstance(directives, tuple) or any(
        not isinstance(line, str) for line in directives
    ):
        raise TypeError("generated directives must be a tuple of strings")
    frontier: list[FrontierOrbital] = []
    indices: list[int] = []
    spacings: list[float | None] = []
    remaining: list[str] = []
    index = 0
    prefix = "output cube eigenstate "
    while index < len(directives):
        line = directives[index]
        if line.startswith("output cube"):
            if not line.startswith(prefix):
                raise OrbitalCubeValidationError(
                    "generated control.in contains an unsupported Cube output"
                )
            token = line[len(prefix) :]
            state: FrontierOrbital | int
            try:
                state = FrontierOrbital(token)
            except ValueError:
                if re.fullmatch(r"[1-9][0-9]*", token) is None:
                    raise OrbitalCubeValidationError(
                        "generated orbital Cube state is unsupported"
                    ) from None
                state = int(token)
            filename_index = index + 1
            if filename_index >= len(directives):
                raise OrbitalCubeValidationError(
                    "generated orbital Cube request is missing its filename"
                )
            expected_filename = f"cube filename {orbital_cube_filename(state)}"
            if directives[filename_index] != expected_filename:
                raise OrbitalCubeValidationError(
                    "generated orbital Cube request has an unexpected filename"
                )
            index = filename_index + 1
            spacing: float | None = None
            if index < len(directives) and directives[index].startswith(
                "cube origin "
            ):
                spacing, index = _parse_compatible_cluster_grid(
                    directives,
                    index,
                )
            spacings.append(spacing)
            if isinstance(state, FrontierOrbital):
                frontier.append(state)
            else:
                indices.append(state)
            continue
        if line.startswith("cube "):
            raise OrbitalCubeValidationError(
                "generated control.in contains an orphan Cube sub-directive"
            )
        remaining.append(line)
        index += 1

    unique_spacings = set(spacings)
    if len(unique_spacings) > 1:
        raise OrbitalCubeValidationError(
            "generated orbital Cube requests use inconsistent grid spacing"
        )
    settings = OrbitalCubeOutputSettings(
        frontier_orbitals=tuple(frontier),
        eigenstate_indices=tuple(indices),
        grid_spacing_angstrom=(spacings[0] if spacings else None),
    )
    return settings, tuple(remaining)


def orbital_cube_filename(state: FrontierOrbital | int) -> str:
    """Return the deterministic working-directory filename for one state."""

    if isinstance(state, FrontierOrbital):
        label = {
            FrontierOrbital.HOMO_MINUS_2: "HOMO_minus_2",
            FrontierOrbital.HOMO_MINUS_1: "HOMO_minus_1",
            FrontierOrbital.HOMO: "HOMO",
            FrontierOrbital.LUMO: "LUMO",
            FrontierOrbital.LUMO_PLUS_1: "LUMO_plus_1",
            FrontierOrbital.LUMO_PLUS_2: "LUMO_plus_2",
        }[state]
        return f"orbital_{label}.cube"
    if isinstance(state, bool) or not isinstance(state, int) or state <= 0:
        raise OrbitalCubeValidationError(
            "orbital Cube filename requires a positive eigenstate number"
        )
    return f"orbital_state_{state}.cube"


def parse_eigenstate_indices_text(text: str) -> tuple[int, ...]:
    """Parse a comma/whitespace-separated list of positive state numbers."""

    if not isinstance(text, str):
        raise TypeError("orbital eigenstate input must be text")
    normalized = text.strip()
    if not normalized:
        return ()
    fields = tuple(field for field in re.split(r"[\s,]+", normalized) if field)
    if any(re.fullmatch(r"[1-9][0-9]*", field) is None for field in fields):
        raise OrbitalCubeValidationError(
            "orbital eigenstate numbers must be positive integers separated "
            "by commas or spaces"
        )
    return OrbitalCubeOutputSettings(
        eigenstate_indices=tuple(int(field) for field in fields)
    ).eigenstate_indices


def format_eigenstate_indices_text(indices: tuple[int, ...]) -> str:
    """Format validated absolute eigenstate numbers for one editable field."""

    normalized = OrbitalCubeOutputSettings(
        eigenstate_indices=indices
    ).eigenstate_indices
    return ", ".join(str(index) for index in normalized)


def _format_positive_real(value: float) -> str:
    text = f"{float(value):.15g}"
    if "e" not in text and "E" not in text:
        return text
    mantissa, exponent = text.lower().split("e")
    return f"{mantissa}E{int(exponent)}"


def _render_compatible_cluster_grid(
    structure: MolecularStructure,
    spacing: float,
) -> tuple[str, ...]:
    """Render the long-supported explicit ``cube edge`` grid syntax.

    Supported legacy FHI-aims releases predate ``cube edge_density``.  The explicit origin and
    three axis-aligned edges are supported by both that release and current
    releases.  The 14-Bohr padding follows FHI-aims' documented cluster
    default while allowing the user-selected spacing to control resolution.
    """

    padding = _DEFAULT_CLUSTER_PADDING_BOHR * _BOHR_TO_ANGSTROM
    coordinates = tuple((atom.x, atom.y, atom.z) for atom in structure)
    minima = tuple(min(point[axis] for point in coordinates) for axis in range(3))
    maxima = tuple(max(point[axis] for point in coordinates) for axis in range(3))
    origin = tuple(value - padding for value in minima)
    counts = tuple(
        max(1, ceil((maximum - minimum + 2.0 * padding) / spacing))
        for minimum, maximum in zip(minima, maxima, strict=True)
    )
    value = _format_positive_real(spacing)
    return (
        "cube origin " + " ".join(_format_real(item) for item in origin),
        f"cube edge {counts[0]} {value} 0.0 0.0",
        f"cube edge {counts[1]} 0.0 {value} 0.0",
        f"cube edge {counts[2]} 0.0 0.0 {value}",
    )


def _parse_compatible_cluster_grid(
    directives: tuple[str, ...],
    index: int,
) -> tuple[float, int]:
    if index + 3 >= len(directives):
        raise OrbitalCubeValidationError(
            "generated Cube grid is incomplete"
        )
    origin_fields = directives[index].split()
    if len(origin_fields) != 5:
        raise OrbitalCubeValidationError(
            "generated Cube origin is malformed"
        )
    for value in origin_fields[2:]:
        _parse_finite_real(value, "generated Cube origin")

    spacings: list[float] = []
    for axis, line in enumerate(directives[index + 1 : index + 4]):
        fields = line.split()
        if (
            len(fields) != 6
            or fields[:2] != ["cube", "edge"]
            or re.fullmatch(r"[1-9][0-9]*", fields[2]) is None
        ):
            raise OrbitalCubeValidationError(
                "generated Cube edge is malformed"
            )
        vector = tuple(
            _parse_finite_real(value, "generated Cube edge")
            for value in fields[3:]
        )
        nonzero = tuple(component != 0.0 for component in vector)
        expected = tuple(component == axis for component in range(3))
        if nonzero != expected or vector[axis] <= 0.0:
            raise OrbitalCubeValidationError(
                "generated Cube edges must be positive and axis-aligned"
            )
        spacings.append(vector[axis])
    if len(set(spacings)) != 1:
        raise OrbitalCubeValidationError(
            "generated Cube edges use inconsistent grid spacing"
        )
    return spacings[0], index + 4


def _parse_finite_real(text: str, label: str) -> float:
    try:
        value = float(text.replace("D", "E").replace("d", "e"))
    except ValueError:
        raise OrbitalCubeValidationError(f"{label} is not numeric") from None
    if not isfinite(value):
        raise OrbitalCubeValidationError(f"{label} must be finite")
    return value


def _format_real(value: float) -> str:
    numeric = float(value)
    if numeric == 0.0:
        return "0.0"
    text = f"{numeric:.15g}"
    if "e" not in text and "E" not in text:
        return text
    mantissa, exponent = text.lower().split("e")
    return f"{mantissa}E{int(exponent)}"
