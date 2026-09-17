"""Synthetic scientific/format fixtures; not real FHI-aims acceptance."""

from array import array
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import numpy as np

from moltage.domain.structure import Atom, MolecularStructure
from moltage.domain.density_difference import FragmentPartition, DensityGrid, parse_atom_selection, format_atom_selection, COMPONENTS
from moltage.aims.density_difference import DensitySettings, DensityElectronicState, build_density_inputs, parse_density_output
from species_test_support import synthetic_species_library
from moltage.aims.geometry_writer import render_geometry_in
from moltage.app.density_results import (
    DensityResult,
    build_density_result,
    density_display_dimensions,
    export_density_result,
    resample_density_field_for_display,
    write_density_cube,
)
from moltage.app.density_workflow import render_density_script
from moltage.structure.cube import CubeScalarField
from moltage.domain.server_profile import SlurmMailSettings, FhiAimsRuntimeConfiguration, RuntimeEnvironment, RuntimeEnvironmentMode
from phase2b1_test_support import synthetic_slurm_preset


def partition():
    return FragmentPartition(MolecularStructure((Atom(0, "Au", 0.125, 0, 0), Atom(1, "H", 1.1, -0.3, 0.2), Atom(2, "Au", 2.1, 0, 0))), (0, 2), (1,))


def output(structure, charges):
    rows = ["Self-consistency cycle converged.", "Performing Hirshfeld analysis of fragment charges and moments."]
    for atom, charge in zip(structure, charges):
        rows.extend((f"  | Atom {atom.index + 1}: {atom.element}", f"  | Hirshfeld charge        : {charge}"))
    return "\n".join((*rows, "Have a nice day.")) + "\n"


def component_files(destination, part, grid, *, bad_grid=False):
    dirs = {}
    for name, values, charges in (("total", (5, 3, 1, 2, 2, 2, 2, 2), (0.1, -0.2, 0.1)),
                                  ("subset1", (2, 2, 2, 1, 1, 1, 1, 1), (0, 0)),
                                  ("subset2", (1, 2, 1, 1, 1, 1, 1, 1), (0,))):
        directory = Path(destination) / name
        directory.mkdir()
        dirs[name] = directory
        (directory / "aims.out").write_text(output(part.geometry(name), charges))
        field = CubeScalarField(grid.dimensions, grid.origin,
                                ((grid.spacing, 0, 0), (0, grid.spacing, 0), (0, 0, grid.spacing)), array("d", values))
        if bad_grid and name == "subset2": field = replace(field, origin_angstrom=(9, 9, 9))
        write_density_cube(directory / "density.cube", part.geometry(name), field)
    return dirs


class DensityCoreTests(unittest.TestCase):
    def test_exact_current_geometry_and_stable_fragment_indexes(self):
        part = partition()
        self.assertEqual(part.geometry("subset1").atoms[1], replace(part.structure.atoms[2], index=1))
        bundle = build_density_inputs(part, DensitySettings(), DensityGrid((0, 0, 0), (2, 2, 2), 0.1), synthetic_species_library())
        files = dict(bundle.files)
        for name in COMPONENTS:
            self.assertEqual(files[name + "/geometry.in"].decode(), render_geometry_in(part.geometry(name)))
            text = files[name + "/control.in"].decode()
            globals = text.split("# Official species")[0]
            for keyword in ("output cube total_density", "output hirshfeld", "cube_content_unit bohr", "cube filename density.cube"):
                self.assertIn(keyword, globals)
            for forbidden in ("relax_geometry", "output aitranss", "restart", "delta_density", "empty"):
                self.assertNotIn(forbidden, globals)
        grids = [tuple(line for line in files[n + "/control.in"].decode().splitlines() if line.startswith("cube")) for n in COMPONENTS]
        self.assertEqual(grids[0], grids[1])
        self.assertEqual(grids[1], grids[2])
        self.assertIn(b"3,Au,subset1,2", files["atom_map.csv"])

    def test_partition_selection_charge_validation(self):
        self.assertEqual(parse_atom_selection("1-2, 2, 3", 3), (0, 1, 2))
        self.assertEqual(format_atom_selection((0, 1, 3)), "1-2,4")
        for text in ("0", "3-2", "4", "1,,2", "1;2"):
            with self.assertRaises(ValueError): parse_atom_selection(text, 3)
        for first, second in (((0,), ()), ((0,), (0, 1, 2)), ((0,), (1,))):
            with self.assertRaises(ValueError): FragmentPartition(partition().structure, first, second)
        with self.assertRaises(ValueError): DensitySettings(subset1=DensityElectronicState(1))

    def test_strict_output_not_scheduler_success(self):
        part = partition()
        text = output(part.structure, (0.1, -0.2, 0.1))
        self.assertEqual(parse_density_output(text, part.structure), (0.1, -0.2, 0.1))
        for invalid in (text.replace("cycle converged.", "cycle not converged."), text.replace("Have a nice day.", "STOP"),
                        text.replace("Hirshfeld charge", "Mulliken charge"), text.replace("Atom 2: H", "Atom 2: C"),
                        text.replace(": -0.2", ": NaN")):
            with self.assertRaises(ValueError): parse_density_output(invalid, part.structure)

    def test_subtraction_hirshfeld_mapping_interpolation_and_export(self):
        part = partition()
        grid = DensityGrid((0, 0, 0), (2, 2, 2), 0.1)
        with tempfile.TemporaryDirectory() as root:
            dirs = component_files(root, part, grid)
            result = build_density_result(part, grid, dirs)
            self.assertEqual(tuple(result.difference.values)[:3], (2, -1, -2))
            self.assertEqual(result.electron_gains, (-0.1, 0.2, -0.1))
            self.assertEqual(tuple(result.interpolated(0).values), (0,) * 8)
            self.assertEqual(tuple(result.interpolated(1, difference=False).values), tuple(result.total.values))
            self.assertEqual(tuple(result.interpolated(0, difference=False).values), tuple(result.reference.values))
            self.assertEqual(result.interpolated(0.5).values[0], 1)
            path = export_density_result(result, Path(root) / "report")
            self.assertTrue((path / "difference.cube").is_file())
            self.assertIn("not physical time", (path / "report.html").read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError): export_density_result(result, path)

    def test_grid_mismatch_never_resampled(self):
        with tempfile.TemporaryDirectory() as root:
            part, grid = partition(), DensityGrid((0, 0, 0), (2, 2, 2), 0.1)
            dirs = component_files(root, part, grid, bad_grid=True)
            with self.assertRaises(ValueError): build_density_result(part, grid, dirs)

    def test_display_resampling_preserves_extent_and_linear_field(self):
        dimensions = (5, 4, 3)
        values = array(
            "d",
            (
                x + 2.0 * y + 3.0 * z
                for x in range(dimensions[0])
                for y in range(dimensions[1])
                for z in range(dimensions[2])
            ),
        )
        source = CubeScalarField(
            dimensions,
            (1.0, 2.0, 3.0),
            ((0.2, 0.0, 0.0), (0.0, 0.3, 0.0), (0.0, 0.0, 0.4)),
            values,
        )

        displayed = resample_density_field_for_display(source, 3)

        self.assertEqual(density_display_dimensions(dimensions, 3), (3, 3, 3))
        self.assertEqual(displayed.dimensions, (3, 3, 3))
        self.assertEqual(displayed.origin_angstrom, source.origin_angstrom)
        np.testing.assert_allclose(
            displayed.axis_vectors_angstrom,
            ((0.4, 0.0, 0.0), (0.0, 0.45, 0.0), (0.0, 0.0, 0.4)),
        )
        expected = np.asarray(
            [
                x + 2.0 * y + 3.0 * z
                for x in (0.0, 2.0, 4.0)
                for y in (0.0, 1.5, 3.0)
                for z in (0.0, 1.0, 2.0)
            ]
        )
        np.testing.assert_allclose(np.asarray(displayed.values), expected)
        self.assertIs(resample_density_field_for_display(source, 80), source)

    def test_display_basis_never_replaces_full_resolution_result(self):
        part = partition()
        source = CubeScalarField(
            (5, 4, 3),
            (0.0, 0.0, 0.0),
            ((0.1, 0.0, 0.0), (0.0, 0.1, 0.0), (0.0, 0.0, 0.1)),
            array("d", range(60)),
        )
        result = DensityResult(
            part,
            source,
            source,
            source,
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (),
        )

        basis = result.display_basis(3)

        self.assertEqual(tuple(field.dimensions for field in basis), ((3, 3, 3),) * 3)
        self.assertIs(result.total, source)
        self.assertIs(result.reference, source)
        self.assertIs(result.difference, source)
        self.assertEqual(result.difference.dimensions, (5, 4, 3))

    def test_one_batch_three_foreground_runs_and_native_mail(self):
        preset = replace(synthetic_slurm_preset(), fhi_runtime=FhiAimsRuntimeConfiguration(
            "/opt/aims/bin/aims.x", "/opt/mpi/bin/mpirun", RuntimeEnvironment(RuntimeEnvironmentMode.NONE)))
        text = render_density_script(preset, uuid4(), {n: "/work/example/" + n for n in COMPONENTS}, SlurmMailSettings("user@example.org"))
        self.assertEqual(text.count("exec /opt/mpi/bin/mpirun"), 3)
        self.assertIn("set -e", text)
        self.assertIn("--mail-type=END,FAIL", text)
        self.assertNotIn("sbatch", text)
        self.assertNotIn("salloc", text)
        self.assertNotIn("tee ", text)
        self.assertLess(text.index("# total"), text.index("# subset1"))
        self.assertLess(text.index("# subset1"), text.index("# subset2"))
