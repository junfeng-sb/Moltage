"""Controlled common-grid subtraction and separately labelled Hirshfeld changes."""

from array import array
from collections.abc import Callable
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
import csv
import json
import numpy as np

from moltage.aims.density_difference import parse_density_output
from moltage.domain.density_difference import COMPONENTS
from moltage.structure.cube import BOHR_TO_ANGSTROM, CubeCoordinateUnit, read_cube


@dataclass(frozen=True)
class DensityResult:
    partition: object
    total: object
    reference: object
    difference: object
    total_charges: tuple
    fragment_charges: tuple
    electron_gains: tuple
    sources: tuple
    manifest: dict | None = None

    def interpolated(self, fraction, *, difference=True):
        return interpolate_density_fields(
            self.total,
            self.reference,
            self.difference,
            fraction,
            difference=difference,
        )

    def display_basis(self, maximum_axis_points):
        """Return a view-only basis; validated/exported fields stay unchanged."""

        return tuple(
            resample_density_field_for_display(field, maximum_axis_points)
            for field in (self.total, self.reference, self.difference)
        )


def interpolate_density_fields(
    total,
    reference,
    density_difference,
    fraction,
    *,
    difference=True,
):
    """Interpolate one already selected full or display-resolution basis."""

    if not 0 <= fraction <= 1:
        raise ValueError("Density interpolation must be between 0 and 1")
    if fraction == 1:
        return density_difference if difference else total
    if fraction == 0 and not difference:
        return reference
    values = np.frombuffer(density_difference.values, dtype=np.float64) * fraction
    if not difference:
        values += np.frombuffer(reference.values, dtype=np.float64)
    data = array("d")
    data.frombytes(values.tobytes())
    return replace(density_difference, values=data)


def density_display_dimensions(dimensions, maximum_axis_points):
    if (
        isinstance(maximum_axis_points, bool)
        or not isinstance(maximum_axis_points, int)
        or maximum_axis_points < 2
    ):
        raise ValueError("Display grid limit must be an integer of at least two")
    return tuple(min(int(value), maximum_axis_points) for value in dimensions)


def resample_density_field_for_display(field, maximum_axis_points):
    """Trilinearly resample one regular field while preserving its full extent."""

    target_dimensions = density_display_dimensions(
        field.dimensions,
        maximum_axis_points,
    )
    if target_dimensions == field.dimensions:
        return field

    values = np.frombuffer(field.values, dtype=np.float64).reshape(
        field.dimensions
    )
    axes = sorted(
        range(3),
        key=lambda axis: field.dimensions[axis] / target_dimensions[axis],
        reverse=True,
    )
    for axis in axes:
        source_count = values.shape[axis]
        target_count = target_dimensions[axis]
        if source_count == target_count:
            continue
        coordinates = np.linspace(
            0.0,
            source_count - 1,
            target_count,
            dtype=np.float64,
        )
        lower = np.floor(coordinates).astype(np.intp)
        upper = np.minimum(lower + 1, source_count - 1)
        weights_shape = [1, 1, 1]
        weights_shape[axis] = target_count
        weights = (coordinates - lower).reshape(weights_shape)
        values = (
            np.take(values, lower, axis=axis) * (1.0 - weights)
            + np.take(values, upper, axis=axis) * weights
        )

    resampled = array("d")
    resampled.frombytes(np.ascontiguousarray(values).tobytes())
    axis_vectors = tuple(
        (
            vector
            if source_count == target_count
            else tuple(
                component * (source_count - 1) / (target_count - 1)
                for component in vector
            )
        )
        for vector, source_count, target_count in zip(
            field.axis_vectors_angstrom,
            field.dimensions,
            target_dimensions,
        )
    )
    return replace(
        field,
        dimensions=target_dimensions,
        axis_vectors_angstrom=axis_vectors,
        values=resampled,
    )


def validate_component(directory, structure, grid):
    directory = Path(directory)
    charges = parse_density_output((directory / "aims.out").read_bytes(), structure)
    parsed = read_cube(directory / "density.cube", coordinate_unit=CubeCoordinateUnit.BOHR)
    if len(parsed.structure) != len(structure):
        raise ValueError("Density Cube atom count differs from its input geometry")
    for actual, expected in zip(parsed.structure, structure):
        if actual.element != expected.element or not np.allclose(
            (actual.x, actual.y, actual.z), (expected.x, expected.y, expected.z), rtol=0, atol=3e-6
        ):
            raise ValueError("Density Cube geometry differs from the fixed input")
    field = parsed.scalar_field
    if field.dimensions != grid.dimensions or not np.allclose(
        field.origin_angstrom, grid.origin, rtol=0, atol=3e-6
    ) or not np.allclose(field.axis_vectors_angstrom, np.eye(3) * grid.spacing, rtol=0, atol=3e-6):
        raise ValueError("Density Cube does not match the explicitly shared grid")
    return field, charges


def build_density_result(
    partition,
    grid,
    directories,
    *,
    progress: Callable[[str], None] | None = None,
):
    def report(message):
        if progress is None:
            return
        try:
            progress(message)
        except Exception:
            pass

    fields, charges = {}, {}
    labels = {"total": "Total", "subset1": "Subset 1", "subset2": "Subset 2"}
    for index, name in enumerate(COMPONENTS, 1):
        report(f"Validating {labels[name]} ({index}/3)…")
        fields[name], charges[name] = validate_component(directories[name], partition.geometry(name), grid)
    # All fields must share the actual serialized grid, not just a nearby requested grid.
    for name in COMPONENTS[1:]:
        if (fields[name].origin_angstrom, fields[name].axis_vectors_angstrom) != (
            fields["total"].origin_angstrom, fields["total"].axis_vectors_angstrom
        ):
            raise ValueError("Cube grids differ; implicit resampling is not supported")
    report("Building full-resolution density difference…")
    reference = np.frombuffer(fields["subset1"].values, dtype=np.float64) + np.frombuffer(fields["subset2"].values, dtype=np.float64)
    difference = np.frombuffer(fields["total"].values, dtype=np.float64) - reference
    def field(values):
        buffer = array("d")
        buffer.frombytes(values.tobytes())
        return replace(fields["total"], values=buffer)
    fragment_charges = [0.0] * len(partition.structure)
    for name in COMPONENTS[1:]:
        for index, charge in zip(partition.indexes(name), charges[name]):
            fragment_charges[index] = charge
    gains = tuple(f - t for f, t in zip(fragment_charges, charges["total"]))
    return DensityResult(partition, fields["total"], field(reference), field(difference),
                         charges["total"], tuple(fragment_charges), gains,
                         tuple(str(directories[n]) for n in COMPONENTS))


def export_density_result(result, output_directory):
    """Export to a new user-selected directory; never overwrite an existing report."""
    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "atom_charges.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("total_atom", "element", "subset", "subset_atom", "Hirshfeld_q_total_e", "Hirshfeld_q_fragment_e", "electron_gain_e"))
        for subset in COMPONENTS[1:]:
            for local, index in enumerate(result.partition.indexes(subset), 1):
                writer.writerow((index + 1, result.partition.structure.atoms[index].element, subset, local,
                                 result.total_charges[index], result.fragment_charges[index], result.electron_gains[index]))
    totals = [(name, sum(result.electron_gains[i] for i in result.partition.indexes(name))) for name in COMPONENTS[1:]]
    with (destination / "fragment_charges.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("subset", "Hirshfeld_electron_gain_e"))
        writer.writerows(totals)
    write_density_cube(destination / "difference.cube", result.partition.structure, result.difference)
    if result.manifest is not None:
        (destination / "analysis_manifest.json").write_text(json.dumps(result.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    voxel = abs(float(np.linalg.det(result.difference.axis_vectors_angstrom))) / BOHR_TO_ANGSTROM ** 3
    integral = float(np.sum(np.frombuffer(result.difference.values, dtype=np.float64)) * voxel)
    html = ("<!doctype html><meta charset='utf-8'><title>Electron Density Difference</title>"
            "<h1>Electron Density Difference</h1><p>Δρ = ρtotal − ρsubset1 − ρsubset2. "
            "Positive: electron accumulation; negative: depletion. Units: electrons/bohr³.</p>"
            "<p>Fixed coordinates; independent fragment references; no ghost atoms or geometry relaxation.</p>"
            "<p>Atom and fragment numbers are changes in Hirshfeld populations (qfragment − qtotal), "
            "not a fixed-space integration of Δρ. Positive values mean electron gain.</p>"
            f"<p>Hirshfeld fragment gains: {escape(str(totals))}</p>"
            f"<p>Finite-grid rectangular quadrature of Δρ: {integral:.8g} e. "
            "This is a grid diagnostic, not an exact electron count or convergence certificate; "
            "all-electron core peaks and finite boundaries require grid-convergence checks.</p>"
            "<p>Density Interpolation is a mathematical interpolation, not physical time, current or an electron trajectory.</p>"
            "<h2>Source component directories</h2><pre>" + escape("\n".join(result.sources)) + "</pre>"
            "<p>See analysis_manifest.json for settings, atom mapping, job/attempt provenance and input/output SHA256 values.</p>")
    (destination / "report.html").write_text(html, encoding="utf-8")
    return destination


def write_density_cube(path, structure, field):
    # Element order is not inferred from visualization colors; use the official element table.
    from moltage.aims.species_library import element_for_atomic_number
    numbers = {element_for_atomic_number(n): n for n in range(1, 87)}
    with Path(path).open("x", encoding="ascii", newline="\n") as handle:
        handle.write("Moltage electron density difference\nCoordinates: bohr; density: electrons/bohr^3\n")
        handle.write(f"{len(structure)} " + " ".join(f"{v / BOHR_TO_ANGSTROM:.12g}" for v in field.origin_angstrom) + "\n")
        for count, axis in zip(field.dimensions, field.axis_vectors_angstrom):
            handle.write(f"{count} " + " ".join(f"{v / BOHR_TO_ANGSTROM:.12g}" for v in axis) + "\n")
        for atom in structure:
            handle.write(f"{numbers[atom.element]} 0 " + " ".join(f"{v / BOHR_TO_ANGSTROM:.12g}" for v in (atom.x, atom.y, atom.z)) + "\n")
        for offset in range(0, len(field.values), 6):
            handle.write(" ".join(f"{v:.12E}" for v in field.values[offset:offset + 6]) + "\n")
