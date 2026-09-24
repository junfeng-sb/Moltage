# Synthetic legacy WBL display artifacts

These files are synthetic, not server captures or research data. They were
serialized with the unmodified `orca/wbl_artifacts.py` from Moltage tag `v0.2.1`
(result schema v2), rather than the current writer. The input is the fabricated
two-MO-per-spin S/C/H evidence from `test_orca_wbl._sh_wavefunction_json`, with
manual contact AOs (8, 11) and (23, 26) in zero-based internal notation and the
test's explicit 0.4/0.5 eV couplings. The all-`a` source digest is a test marker,
not the hash of a real wavefunction.

Purpose: pin genuine pre-change serializer shape and numerical display values
without relying on Git being installed in the test environment. Derived v1
compatibility cases in the test are labelled as such; they are not claimed to
be captured historical calculations.
