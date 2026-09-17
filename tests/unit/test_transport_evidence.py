import unittest

from moltage.aims.transport_evidence import (
    OUT_OF_MEMORY_REASON,
    SLURM_TASK_OUT_OF_MEMORY,
    TIMEOUT_REASON,
    TransportEvidenceError,
    TransportSpinMode,
    has_exact_normal_termination,
    has_slurm_task_oom_signature,
    has_slurm_timeout_signature,
    parse_aims_natoms,
    parse_aims_nsaos,
    parse_transport_spin_mode,
)


class TransportEvidenceTests(unittest.TestCase):
    def test_real_format_natoms_and_mos_nsaos_are_parsed(self):
        self.assertEqual(
            parse_aims_natoms(
                b"header\n  | Number of atoms                   :      134\n"
            ),
            134,
        )
        self.assertEqual(
            parse_aims_nsaos(
                b"$scfmo.aims   iteration:    1   format(4d20.14)\n"
                b"     1  a eigenvalue=-.32D+04   nsaos=  512\n",
                source_name="mos.aims",
            ),
            512,
        )

    def test_natoms_and_nsaos_must_be_positive_and_unambiguous(self):
        with self.assertRaises(TransportEvidenceError):
            parse_aims_natoms("| Number of atoms : 0")
        with self.assertRaisesRegex(TransportEvidenceError, "conflicting"):
            parse_aims_natoms(
                "| Number of atoms : 2\n| Number of atoms : 3\n"
            )
        with self.assertRaises(TransportEvidenceError):
            parse_aims_nsaos("nsaos=0", source_name="mos.aims")

    def test_exact_normal_marker_and_spin_mode(self):
        self.assertTrue(has_exact_normal_termination("Have a nice day.\n"))
        self.assertFalse(has_exact_normal_termination("x Have a nice day. x"))
        self.assertIs(
            parse_transport_spin_mode("spin none\n"),
            TransportSpinMode.NONE,
        )
        self.assertIs(
            parse_transport_spin_mode("# spin none\nspin collinear # active\n"),
            TransportSpinMode.COLLINEAR,
        )

    def test_exact_slurm_timeout_signature(self):
        observed = (
            "[2030-01-02T03:04:05.006] error: *** STEP 41001.0 ON "
            "compute001 CANCELLED AT 2030-01-02T03:04:05 DUE TO TIME LIMIT ***"
        )
        self.assertTrue(has_slurm_timeout_signature(observed))
        self.assertFalse(
            has_slurm_timeout_signature("job time limit reached")
        )
        self.assertEqual(TIMEOUT_REASON, "运行时间到达设定上限")

    def test_task_oom_requires_both_supported_slurm_line_shapes(self):
        event = (
            "[2030-01-02T03:04:05.006] error: Detected 1 oom_kill event "
            "in StepId=41002.0. Some of the step tasks have been OOM Killed."
        )
        task = "srun: error: compute001: task 7: Out Of Memory"

        self.assertTrue(
            has_slurm_task_oom_signature(f"{event}\n{task}\n")
        )
        for near_miss in (
            event,
            task,
            "OOM",
            "memory exhausted",
            event.replace("OOM Killed", "killed"),
            task.replace("Out Of Memory", "out of memory"),
        ):
            with self.subTest(near_miss=near_miss):
                self.assertFalse(
                    has_slurm_task_oom_signature(near_miss)
                )
        self.assertEqual(SLURM_TASK_OUT_OF_MEMORY, "SLURM_TASK_OUT_OF_MEMORY")
        self.assertEqual(OUT_OF_MEMORY_REASON, "任务因内存不足终止")


if __name__ == "__main__":
    unittest.main()
