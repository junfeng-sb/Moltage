# Validation - 2026-09-22

## Implemented scope

- NCS Auto resolves to `S_ALL_P_LEGACY`: every p-type AO on the selected S,
  without radial or directional filtering. Other linker defaults, explicit
  directional/manual modes, all-MO summation and spin conventions are unchanged.
- The existing overlap-based Lowdin implementation is retained. A complete,
  S-orthonormal synthetic MO set agrees with the original coefficient-SVD route;
  this is not a claim that rounded/ill-conditioned real inputs are equivalent.
- WBL GUI and SVG use red Alpha / blue Beta, a thinner dashed raw spin sum,
  model/leading-MO details and triangle markers. Closed-shell remains total-only.
  Existing text export, display settings, probes and image-size controls remain.
- Current v3 report evidence is checked for completeness/consistency. Historical
  v1/v2 curve/summary reading survives missing or inconsistent optional report
  details with an explicit unavailable reason. A frozen synthetic v2 fixture was
  produced using the v0.2.1 serializer, not copied from a real calculation.

## Tests actually run

`tools/run_tests.ps1 -Full`: **1605 passed, 2 skipped**, 235.98 seconds.
The two runtime-discovery Bash checks are unavailable in this session (no Bash
on PATH); no dependency was installed and no output/warnings were suppressed.

After the full run, the independent review prompted small report-reader,
provenance-wording and SVG-test corrections. The scientific weights/formula
were not changed in those corrections. The following focused/direct integration
selection was rerun on the final code: **140 passed**, 8.23 seconds.

- `tests/unit/test_orca_wbl.py`
- `tests/unit/test_orca_dialogs.py`
- `tests/unit/test_orca_wbl_view.py`
- `tests/unit/test_orca_wbl_workflow.py`
- `tests/unit/test_transmission_view.py`
- `tests/unit/test_transmission_settings.py`
- `tests/unit/test_orca_persistence.py`
- `tests/unit/test_orca_import_wbl.py`

Coverage includes tiny positive legacy weights; AO/shell/component boundaries;
explicit overrides; settings/artifact round trips; old serializer compatibility;
nonzero SVG paths/markers at small output dimensions; report metadata; axis/style
updates; closed-shell versus spin-resolved display; lossless text export and
unchanged source artifacts.

## Independent and visual review

The installed Claude CLI was restricted to Read/Glob/Grep in plan mode. Its
initial and follow-up findings were addressed within scope. The final scoped
review confirmed all four follow-up fixes and found no remaining blocker. It did
not run tests, inspect PDFs, connect to a server or modify the repository.

Local Qt captures of synthetic open/closed-shell views and the persisted SVG
were inspected. The English and Chinese PDFs were rebuilt; changed ORCA pages
and adjacent pagination were rendered with Poppler and inspected. The manuals
now use a new synthetic report screenshot with an adjacent provenance note;
the older README release screenshot is preserved.

OpenSpec strict validation is run at proposal and apply completion; there is no
archive, commit, packaging or release operation in this task.

## Deliberately unverified

No real HPC operation or private research calculation was performed. Reopening
an old result changes only its presentation, not its computed transmission.
For numerical comparison, import the same completed ORCA optimization into a
separate comparison project, keep Gamma/Fermi/energy settings identical and run
WBL again. No SCF or geometry optimization is required. Saved NCS Auto settings
with a direction override remain readable but must be made explicit/consistent
before a new calculation; the override is never silently discarded.
