from pathlib import Path
import unittest

from moltage.aitranss.output import (
    AITRANSS_ELECTRODE_INTERFACE_OVERLAP_REASON,
    AITRANSS_SELF_ENERGY_FILE_FORMAT_DETAIL,
    AITRANSS_SELF_ENERGY_FILE_FORMAT_REASON,
    AitranssFailureCode,
    aitranss_failure_detail,
    aitranss_failure_reason,
    classify_aitranss_fatal_output,
    has_aitranss_transmission_success,
)


SYNTHETIC_FATAL_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase4a"
    / "aitranss_electrode_interface_overlap.out"
)
SYNTHETIC_SELF_ENERGY_FORMAT_FATAL_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "phase4b"
    / "aitranss_self_energy_file_format_error.out"
)


class AitranssOutputTests(unittest.TestCase):
    def test_synthetic_output_has_typed_self_energy_format_failure(self):
        evidence = SYNTHETIC_SELF_ENERGY_FORMAT_FATAL_OUTPUT.read_bytes()
        result = classify_aitranss_fatal_output(evidence)

        self.assertIs(
            result,
            AitranssFailureCode.SELF_ENERGY_FILE_FORMAT_ERROR,
        )
        self.assertEqual(
            aitranss_failure_reason(result),
            AITRANSS_SELF_ENERGY_FILE_FORMAT_REASON,
        )
        self.assertEqual(
            aitranss_failure_detail(result),
            AITRANSS_SELF_ENERGY_FILE_FORMAT_DETAIL,
        )

    def test_synthetic_output_has_typed_interface_overlap_failure(self):
        result = classify_aitranss_fatal_output(SYNTHETIC_FATAL_OUTPUT.read_bytes())

        self.assertIs(
            result,
            AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP,
        )
        self.assertEqual(
            aitranss_failure_reason(result),
            AITRANSS_ELECTRODE_INTERFACE_OVERLAP_REASON,
        )

    def test_harmless_case_and_whitespace_changes_are_accepted(self):
        output = """
            build UP a SELF-energy >>>
            LEFT electrode, surface atoms: AU
            RIGHT   electrode,   surface atoms: au
            your left and right electrodes share the same atoms:
            PLEASE, check your <tcontrol> file ...
            stop: transport module will terminate now!
        """

        self.assertIs(
            classify_aitranss_fatal_output(output),
            AitranssFailureCode.ELECTRODE_INTERFACE_OVERLAP,
        )

    def test_generic_left_right_atoms_and_stop_are_not_enough(self):
        output = """
            left electrode atoms loaded
            right electrode atoms loaded
            STOP
        """

        self.assertIsNone(classify_aitranss_fatal_output(output))

    def test_missing_self_energy_context_is_not_a_match(self):
        output = """
            left electrode, surface atoms: au
            right electrode, surface atoms: au
            your LEFT and RIGHT electrodes share the same atoms:
            please, check your <tcontrol> file ...
            STOP : transport module will terminate now!
        """

        self.assertIsNone(classify_aitranss_fatal_output(output))

    def test_out_of_order_fatal_lines_are_not_a_match(self):
        output = """
            BUILD UP A SELF-ENERGY >>>
            right electrode, surface atoms: au
            left electrode, surface atoms: au
            your LEFT and RIGHT electrodes share the same atoms:
            please, check your <tcontrol> file ...
            STOP : transport module will terminate now!
        """

        self.assertIsNone(classify_aitranss_fatal_output(output))

    def test_self_energy_format_near_misses_do_not_match(self):
        near_misses = (
            "STOP [SUB. get_self_energy_data]: wrong format of the self-energy file",
            """
                === reading file <self.energy.retry02.in> ===
                STOP [SUB. get_hsource_data]: wrong format of the self-energy file
            """,
            """
                === reading file <other.retry02.in> ===
                STOP [SUB. get_self_energy_data]: wrong format of the self-energy file
            """,
            """
                STOP [SUB. get_self_energy_data]: wrong format of the self-energy file
                === reading file <self.energy.retry02.in> ===
            """,
        )

        for output in near_misses:
            with self.subTest(output=output):
                self.assertIsNone(classify_aitranss_fatal_output(output))

    def test_synthetic_positive_marker_pair_is_required_in_order(self):
        output = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "phase4c"
            / "aitranss-success.minimal.out"
        ).read_bytes()

        self.assertTrue(
            has_aitranss_transmission_success(
                output,
                expected_output_filename="TE.dat",
            )
        )
        self.assertFalse(
            has_aitranss_transmission_success(
                output,
                expected_output_filename="other.dat",
            )
        )
        for near_miss in (
            b'** aitranss : all done **\n',
            b'transmission is written to a file "TE.dat"\n',
            (
                b'** aitranss : all done **\n'
                b'transmission is written to a file "TE.dat"\n'
            ),
        ):
            with self.subTest(near_miss=near_miss):
                self.assertFalse(
                    has_aitranss_transmission_success(
                        near_miss,
                        expected_output_filename="TE.dat",
                    )
                )

    def test_known_fatal_prevents_positive_classification(self):
        combined = (
            SYNTHETIC_FATAL_OUTPUT.read_bytes()
            + b'\ntransmission is written to a file "TE.dat"\n'
            + b'** aitranss : all done **\n'
        )

        self.assertFalse(
            has_aitranss_transmission_success(
                combined,
                expected_output_filename="TE.dat",
            )
        )


if __name__ == "__main__":
    unittest.main()
