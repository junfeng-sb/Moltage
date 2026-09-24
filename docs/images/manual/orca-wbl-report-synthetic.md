# WBL report screenshot provenance

`orca-wbl-report-synthetic.png` is a capture of the current
`OrcaWblTransmissionView`, not a server capture or research result.

Input is the fabricated `_sh_wavefunction_json(unrestricted=True)` in
`tests/unit/test_orca_wbl.py`, with that module's structure/connectivity helper
and settings. The screenshot uses manual AO indices `(8, 11)` and `(23, 26)`
(internal zero-based indices), Gamma values 0.4/0.5 eV, a 0 eV Fermi reference,
and an energy window of -4 to +4 eV at 0.01 eV spacing. The illustrative right
contact's `CALIBRATED` label exercises that UI state; it does not assert a real
calibration.

The figure was produced by `calculate_orca_wbl` ->
`wbl_presentation_from_result` -> `OrcaWblTransmissionView`, displayed at
1250 x 930 pixels and captured with QWidget.grab(). It demonstrates UI behavior
only and contains no private molecular data, account, path, or scheduler output.
