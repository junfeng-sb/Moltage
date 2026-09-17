"""Native total-density inputs and strict single-point/Hirshfeld evidence."""

from dataclasses import dataclass, field
from math import isfinite
import re

from moltage.aims.geometry_writer import render_geometry_in
from moltage.aims.optimization_settings import SpeciesAccuracy, SpinInitializationMode, SpinSettings, XCFunctional
from moltage.aims.species_library import (
    SpeciesDefinitionProvider,
    SpeciesRequirement,
)
from moltage.domain.density_difference import COMPONENTS, DensityGrid, FragmentPartition


@dataclass(frozen=True, slots=True)
class DensityElectronicState:
    charge: float = 0.0
    spin: SpinSettings = field(default_factory=SpinSettings)

    def __post_init__(self):
        if isinstance(self.charge, bool) or not isfinite(self.charge):
            raise ValueError("Charge must be finite.")
        if not isinstance(self.spin, SpinSettings):
            raise ValueError("Invalid spin settings.")
        if self.spin.enabled and self.spin.initialization_mode is not SpinInitializationMode.UNIFORM_DEFAULT:
            raise ValueError("Density MVP uses an explicit uniform initial moment per atom.")


@dataclass(frozen=True, slots=True)
class DensitySettings:
    """A single-point scientific surface, not a transport-convergence setting."""

    total: DensityElectronicState = field(default_factory=DensityElectronicState)
    subset1: DensityElectronicState = field(default_factory=DensityElectronicState)
    subset2: DensityElectronicState = field(default_factory=DensityElectronicState)
    xc: XCFunctional = XCFunctional.PBE
    species_accuracy: SpeciesAccuracy = SpeciesAccuracy.TIGHT
    occupation_width: float = 0.01
    n_max_pulay: int = 10
    charge_mix_param: float = 0.2
    sc_accuracy_rho: float = 1e-5
    sc_accuracy_eev: float = 1e-3
    sc_accuracy_etot: float = 1e-6
    sc_iter_limit: int = 500

    def __post_init__(self):
        if any(not isinstance(getattr(self, name), DensityElectronicState) for name in COMPONENTS):
            raise ValueError("All three electronic states are required.")
        if abs(self.total.charge - self.subset1.charge - self.subset2.charge) > 1e-10:
            raise ValueError("Total charge must equal Subset 1 charge + Subset 2 charge.")
        if not isinstance(self.xc, XCFunctional) or not isinstance(self.species_accuracy, SpeciesAccuracy):
            raise ValueError("Unsupported XC or species preset.")
        for name in ("occupation_width", "sc_accuracy_rho", "sc_accuracy_eev", "sc_accuracy_etot", "charge_mix_param"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive.")
        if self.charge_mix_param > 1:
            raise ValueError("charge_mix_param must not exceed one.")
        for name in ("n_max_pulay", "sc_iter_limit"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer.")


@dataclass(frozen=True, slots=True)
class DensityInputBundle:
    partition: FragmentPartition
    settings: DensitySettings
    grid: DensityGrid
    # Relative path, exact UTF-8 bytes. No remote or GUI state.
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class DensityInputPlan:
    """Immutable density-task plan materialized after species acquisition."""

    partition: FragmentPartition
    settings: DensitySettings
    grid: DensityGrid

    def __post_init__(self):
        _validate_density_plan(self.partition, self.settings, self.grid)

    @property
    def species_requirements(self) -> tuple[SpeciesRequirement, ...]:
        return tuple(
            SpeciesRequirement(element, self.settings.species_accuracy)
            for element in dict.fromkeys(
                atom.element for atom in self.partition.structure
            )
        )

    def materialize(self, species_library: SpeciesDefinitionProvider):
        return build_density_inputs(
            self.partition,
            self.settings,
            self.grid,
            species_library,
        )


def build_density_inputs(partition, settings, grid, species_library):
    _validate_density_plan(partition, settings, grid)
    if not isinstance(species_library, SpeciesDefinitionProvider):
        raise TypeError(
            "species_library must implement SpeciesDefinitionProvider"
        )
    blocks = {element: species_library.load(element, settings.species_accuracy)
              for element in dict.fromkeys(a.element for a in partition.structure)}
    files = []
    for component in COMPONENTS:
        structure = partition.geometry(component)
        state = getattr(settings, component)
        lines = ["# Fixed-geometry electron density; independent fragment reference",
                 f"xc {settings.xc.value}",
                 "spin collinear" if state.spin.enabled else "spin none",
                 "relativistic atomic_zora scalar", f"charge {state.charge:.15g}"]
        if state.spin.enabled:
            lines.append(f"default_initial_moment {state.spin.uniform_initial_moment:.15g}")
            if state.spin.fixed_spin_moment is not None:
                lines.append(f"fixed_spin_moment {state.spin.fixed_spin_moment:.15g}")
        if settings.xc in {XCFunctional.PBE0, XCFunctional.B3LYP}:
            lines.append("RI_method LVL_fast")
        lines.extend([f"occupation_type gaussian {settings.occupation_width:.15g}", "mixer pulay",
                      f"n_max_pulay {settings.n_max_pulay}", f"charge_mix_param {settings.charge_mix_param:.15g}"])
        lines.extend(f"{name} {getattr(settings, name):.15g}" for name in
                     ("sc_accuracy_rho", "sc_accuracy_eev", "sc_accuracy_etot", "sc_iter_limit"))
        lines.extend(["output hirshfeld", "cube_content_unit bohr", "output cube total_density",
                      "cube filename density.cube", "cube origin " + " ".join(f"{v:.15g}" for v in grid.center)])
        for axis, count in enumerate(grid.dimensions):
            edge = [0.0, 0.0, 0.0]
            edge[axis] = grid.spacing
            lines.append(f"cube edge {count} " + " ".join(f"{v:.15g}" for v in edge))
        lines.extend(
            [
                "",
                "# Species",
                f"# Moltage species accuracy: {settings.species_accuracy.value}",
            ]
        )
        lines.extend(blocks[element].text.rstrip() for element in dict.fromkeys(a.element for a in structure))
        files.extend(((f"{component}/geometry.in", render_geometry_in(structure).encode()),
                      (f"{component}/control.in", ("\n".join(lines) + "\n").encode())))
    mapping = ["total_atom,element,subset,subset_atom"]
    for component in COMPONENTS[1:]:
        for local, global_index in enumerate(partition.indexes(component), 1):
            mapping.append(f"{global_index + 1},{partition.structure.atoms[global_index].element},{component},{local}")
    files.append(("atom_map.csv", ("\n".join(mapping) + "\n").encode()))
    return DensityInputBundle(partition, settings, grid, tuple(files))


def _validate_density_plan(partition, settings, grid):
    if (
        not isinstance(partition, FragmentPartition)
        or not isinstance(settings, DensitySettings)
        or not isinstance(grid, DensityGrid)
    ):
        raise TypeError(
            "Density inputs require a partition, settings and common grid."
        )


_HIRSHFELD_START = "Performing Hirshfeld analysis of fragment charges and moments."
_ATOM = re.compile(r"\|\s*Atom\s+(\d+)\s*:\s*([A-Z][a-z]?)\s*")
_CHARGE = re.compile(r"\|\s*Hirshfeld charge\s*:\s*(\S+)\s*")


def parse_density_output(text: str | bytes, structure) -> tuple[float, ...]:
    """Require converged, normally ended single-point output and complete charges.

    Grammar evidence: FHI-aims GSS2014 tutorial (Atom rows), and the
    independently published aimstools output reader (Hirshfeld charge label).
    This is an original strict parser, not copied code or a runtime dependency.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    lines = [line.strip() for line in text.splitlines()]
    if "Self-consistency cycle converged." not in lines:
        raise ValueError("FHI-aims SCF convergence is not established.")
    if "Have a nice day." not in lines:
        raise ValueError("FHI-aims normal termination is not established.")
    starts = [i for i, line in enumerate(lines) if line == _HIRSHFELD_START]
    if not starts:
        raise ValueError("Hirshfeld analysis is missing.")
    charges = []
    pending = None
    for line in lines[starts[-1] + 1:]:
        if match := _ATOM.fullmatch(line):
            if pending is not None or len(charges) >= len(structure):
                raise ValueError("Incomplete or duplicate Hirshfeld atom record.")
            index, element = int(match[1]) - 1, match[2]
            if index != len(charges) or element != structure.atoms[index].element:
                raise ValueError("Hirshfeld atom order/elements do not match geometry.")
            pending = index
        elif match := _CHARGE.fullmatch(line):
            if pending is None:
                raise ValueError("Hirshfeld charge has no matching atom.")
            value = float(match[1].replace("D", "E").replace("d", "e"))
            if not isfinite(value):
                raise ValueError("Hirshfeld charge must be finite.")
            charges.append(value)
            pending = None
            if len(charges) == len(structure):
                break
    if len(charges) != len(structure):
        raise ValueError("Hirshfeld analysis is incomplete.")
    return tuple(charges)
