from dataclasses import replace
from pathlib import Path
import unittest

from moltage.aims.transport_evidence import TransportSpinMode
from moltage.aitranss.transmission import (
    TransmissionDataError,
    TransmissionRequest,
    parse_te_dat,
    parse_transmission_request,
    submitted_tcontrol_bytes,
    validate_transmission_grid,
)


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase4c"
    / "TE.synthetic-nonspin.dat"
)


class AitranssTransmissionParserTests(unittest.TestCase):
    def test_synthetic_nonspin_fixture(self):
        result = parse_te_dat(FIXTURE.read_bytes())

        self.assertIs(result.spin_mode, TransportSpinMode.NONE)
        self.assertEqual(result.bias_volts, 0.0)
        self.assertEqual(result.fermi_energy_hartree, -0.2)
        self.assertEqual(len(result.points), 4)
        self.assertEqual(result.points[0].energy_hartree, -0.3)
        self.assertEqual(result.points[0].energy_relative_ev, -2.7211386)
        self.assertEqual(result.points[0].transmission_per_spin, 0.8)
        self.assertEqual(result.points[-1].energy_hartree, 0.0)
        self.assertEqual(
            result.header_lines[2],
            "#   E [Hartree]       E-EF [eV]       T(E) per spin",
        )

    def test_documented_blank_lines_and_fortran_d_notation_are_accepted(self):
        text = FIXTURE.read_text(encoding="utf-8")
        text = text.replace(
            "#   E [Hartree]       E-EF [eV]       T(E) per spin\n",
            "#   E [Hartree]       E-EF [eV]       T(E) per spin\n\n",
        ).replace("0.8000000000E+00", "0.8000000000D+00")

        result = parse_te_dat(text)

        self.assertEqual(result.points[0].transmission_per_spin, 0.8)

    def test_malformed_numeric_row_is_rejected(self):
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "0.00000000", "not-a-number"
        )
        with self.assertRaisesRegex(TransmissionDataError, "invalid energy"):
            parse_te_dat(text)

    def test_wrong_column_count_is_rejected(self):
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "0.8000000000E+00", "0.8000000000E+00 4"
        )
        with self.assertRaisesRegex(TransmissionDataError, "exactly three"):
            parse_te_dat(text)

    def test_non_finite_value_is_rejected(self):
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "0.8000000000E+00", "nan"
        )
        with self.assertRaisesRegex(TransmissionDataError, "non-finite"):
            parse_te_dat(text)

    def test_empty_and_truncated_results_are_rejected(self):
        with self.assertRaises(TransmissionDataError):
            parse_te_dat("")
        text = FIXTURE.read_text(encoding="utf-8").replace("#end\n", "")
        with self.assertRaisesRegex(TransmissionDataError, "#end"):
            parse_te_dat(text)

    def test_energy_order_violation_is_rejected(self):
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "-0.1000000000      2.72113860",
            "-0.2500000000      2.72113860",
        )
        with self.assertRaisesRegex(TransmissionDataError, "strictly increasing"):
            parse_te_dat(text)

    def test_unknown_comment_and_spin_header_are_not_silently_guessed(self):
        text = FIXTURE.read_text(encoding="utf-8").replace(
            "  -0.1000000000", "# extra\n  -0.1000000000"
        )
        with self.assertRaisesRegex(TransmissionDataError, "unsupported comment"):
            parse_te_dat(text)
        spin_text = FIXTURE.read_text(encoding="utf-8").replace(
            "#non-spin-polarized calculation",
            "#spin-polarized calculation",
        )
        with self.assertRaisesRegex(TransmissionDataError, "spin header"):
            parse_te_dat(spin_text)

    def test_attempt_tcontrol_authority_and_observed_grid(self):
        request = parse_transmission_request(_tcontrol())
        result = parse_te_dat(FIXTURE.read_bytes())

        self.assertEqual(request.output_filename, "TE.dat")
        self.assertIs(request.spin_mode, TransportSpinMode.NONE)
        validate_transmission_grid(result, request)

    def test_grid_accepts_inclusive_endpoint_and_rejects_invalid_bounds(self):
        result = parse_te_dat(FIXTURE.read_bytes())
        request = parse_transmission_request(_tcontrol())

        inclusive_request = replace(request, energy_end_hartree=0.0)
        validate_transmission_grid(result, inclusive_request)

        with self.assertRaisesRegex(TransmissionDataError, "truncated"):
            validate_transmission_grid(
                replace(result, points=result.points[:-1]),
                request,
            )
        overrun_request = replace(request, energy_end_hartree=-0.1)
        with self.assertRaisesRegex(TransmissionDataError, "beyond"):
            validate_transmission_grid(result, overrun_request)
        with self.assertRaisesRegex(TransmissionDataError, "spin header"):
            validate_transmission_grid(
                result,
                replace(request, spin_mode=TransportSpinMode.COLLINEAR),
            )

    def test_tcontrol_result_filename_must_be_safe(self):
        with self.assertRaisesRegex(TransmissionDataError, "unsafe"):
            parse_transmission_request(_tcontrol().replace("TE.dat", "../TE.dat"))

    def test_only_observed_runtime_tcontrol_annotations_are_removed_for_provenance(self):
        submitted = _tcontrol()
        annotated = submitted.replace(
            "$end\n",
            "$valence_electrons  2340\n"
            "$efermi   -0.200000000000\n"
            "$end\n",
        )

        self.assertEqual(
            submitted_tcontrol_bytes(annotated),
            submitted.encode("utf-8"),
        )
        request = parse_transmission_request(annotated)
        self.assertEqual(
            request.reported_fermi_energy_hartree,
            -0.2,
        )
        validate_transmission_grid(parse_te_dat(FIXTURE.read_bytes()), request)

        misplaced = annotated.replace(
            "$valence_electrons  2340\n",
            "",
        ).replace(
            "$ener -0.3000\n",
            "$ener -0.3000\n$valence_electrons  2340\n",
        )
        with self.assertRaisesRegex(TransmissionDataError, "placement"):
            submitted_tcontrol_bytes(misplaced)

    def test_efermi_only_runtime_annotation_is_accepted_and_removed(self):
        submitted = _tcontrol()
        annotated = submitted.replace(
            "$end\n",
            "$efermi   -0.200000000000\n$end\n",
        )

        self.assertEqual(
            submitted_tcontrol_bytes(annotated),
            submitted.encode("utf-8"),
        )
        request = parse_transmission_request(annotated)
        self.assertEqual(
            request.reported_fermi_energy_hartree,
            -0.2,
        )
        validate_transmission_grid(parse_te_dat(FIXTURE.read_bytes()), request)

        valence_only = annotated.replace(
            "$efermi   -0.200000000000\n",
            "$valence_electrons  2340\n",
        )
        with self.assertRaisesRegex(TransmissionDataError, "incomplete"):
            parse_transmission_request(valence_only)

    def test_annotated_tcontrol_fermi_energy_must_match_te_header(self):
        annotated = _tcontrol().replace(
            "$end\n",
            "$valence_electrons 2340\n"
            "$efermi -0.250000000000\n"
            "$end\n",
        )
        with self.assertRaisesRegex(TransmissionDataError, "Fermi energy"):
            validate_transmission_grid(
                parse_te_dat(FIXTURE.read_bytes()),
                parse_transmission_request(annotated),
            )


def _tcontrol() -> str:
    return (
        '#input data for the "aitranss" module\n'
        "$aims_input on\n"
        "$landauer on\n"
        "$coord file=geometry.in\n"
        "$natoms 1\n"
        "$basis file=basis-indices.out\n"
        "$read_omat file=omat.aims\n"
        "$scfmo file=mos.aims\n"
        "$nsaos 1\n"
        "$lsurc 1\n"
        "$lsurx 2\n"
        "$lsury 3\n"
        "$rsurc 4\n"
        "$rsurx 5\n"
        "$rsury 6\n"
        "$nlayers 4\n"
        "$s1i 0.1d0\n"
        "$s2i 0.05d0\n"
        "$s3i 0.025d0\n"
        "$ener -0.3000\n"
        "$estep 0.1000\n"
        "$eend 0.1000\n"
        "$output file=TE.dat\n"
        "$testing off\n"
        "$ecp on\n"
        "$end\n"
    )


if __name__ == "__main__":
    unittest.main()
