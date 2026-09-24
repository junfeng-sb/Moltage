# Completion validation

## Scope

This change implements only explicit ORCA WBL Step 2 reruns with replacement
results and per-user persistence of the accepted Bond Detection factor. Other
uncommitted ORCA import, report, projection and refresh changes already present
in the worktree are not attributed to this change.

No real SSH/HPC operation, scheduler submission, scientific formula change,
installation build, version change, commit or push is part of this validation.

Implementation files owned by this change:

- `src/moltage/app/orca_wbl.py`, `src/moltage/remote/wbl_results.py`,
  `src/moltage/remote/step_inputs.py`, `src/moltage/domain/calculation_project.py`;
- `src/moltage/app/user_view_preferences.py`,
  `src/moltage/gui/bond_detection_dialog.py`, `src/moltage/gui/orca_dialogs.py`,
  `src/moltage/gui/projects_dialog.py`, `tools/molecule_viewer_demo.py`.

Added/extended regression coverage is in `test_orca_wbl_workflow.py`,
`test_orca_wbl_view.py`, `test_orca_dialogs.py`, `test_projects_dialog.py`,
`test_user_view_preferences.py` and `test_ui_r6_r1_live_preview.py` under
`tests/unit/`. Documentation changes cover the changelog, the architecture,
configuration and workflow notes, and chapters 01, 04 and 06 in both manuals.

## Implemented behavior

- Terminal WBL stages may run again; active stages reject duplicate requests.
  A successful previous result must match the result explicitly confirmed in
  the dialog. The dialog prefills prior settings and identifies replacement.
- Replacement verifies exact artifact sets with server-side SHA256. New files
  are staged before publication; the previous files remain recoverable until
  the new manifest commits. Verified failures restore the previous result and
  settings. Unknown remote outcomes retain evidence and report paths, rather
  than guessing. Backup cleanup failure is reported without invalidating a
  committed result. The optimization is not rerun.
- A newly opened replacement result retires the previous project chart; its
  curve and TXT export use the new data.
- Local `view_preferences.json` schema 3 adds `bond_threshold_factor`. Legacy
  schema 1/2 retains 1.1 until explicitly saved and preserves theme/lighting.
  Startup restores the accepted factor. OK saves; Cancel does not. A failed
  save restores the original factor and inferred graphs. Explicit MOL bonds,
  the connectivity formula and radii are unchanged.

## Tests

On 2026-09-24, the explicit focused/direct integration command passed:

```powershell
./tools/run_tests.ps1 -Tests tests/unit/test_orca_wbl_workflow.py,tests/unit/test_orca_wbl_view.py,tests/unit/test_orca_dialogs.py,tests/unit/test_projects_dialog.py,tests/unit/test_user_view_preferences.py,tests/unit/test_gui_theme.py,tests/unit/test_cube_viewer.py,tests/unit/test_ui_r6_r1_live_preview.py,tests/unit/test_orca_import_wbl.py,tests/unit/test_orca_persistence.py,tests/unit/test_orca_wbl.py
```

Final focused result: **238 passed in 43.28s**. Coverage includes successful replacement,
stale authorization, duplicate rejection, failed conversion/upload/renames/
manifest writes, lost acknowledgements, uncertain outcomes, server-side hashes,
GUI prefill/Cancel/new result display, preference migration, restart, independent
preference preservation and save-failure rollback. Additional remediation
regressions cover disconnected conversion with recoverable old metadata,
partial uploads, local-index failure after commit, huge malformed integers,
replacement display failure and truthful restoration of the old curve.

The initial full offline run passed **1661 tests, 2 skipped** before audit
remediation. The final `./tools/run_tests.ps1 -Full` run passed **1672 tests,
2 skipped in 211.46s**. Both skips were existing runtime-query tests requiring
Bash on PATH. They were subsequently run with the already installed Git Bash
added only to the child process PATH: **2 passed, 39 deselected in 1.37s**.
No software installation or persistent environment change was needed.
A remediation test run
initially exposed an invalid leading-dot snapshot filename rejected by the
existing upload validator; the snapshot now uses an accepted safe basename,
and subsequent focused runs passed. No validator was weakened.

## Independent review and disposition

Two read-only Claude CLI reviews on 2026-09-24 reported no blocking findings.
They did not run tests, inspect real credentials or connect to HPC. Their
findings resulted in these narrowly scoped fixes:

- **Disconnected rerun:** preserve and server-verify the full previous project
  manifest before replacing the successful record with RUNNING. This retains
  old settings and artifact hashes after disconnection. Verified success or
  rollback removes the copy; uncertain state retains it and reports the path.
- **Staging cleanup:** remove only owned empty or checksum-verified staging;
  report and retain unverified partial uploads. No recursive project cleanup.
- **Committed result vs. presentation/local cache:** an index write failure
  becomes a warning, not calculation failure. A new result cannot inherit the
  old plot; a verified rollback restores the old presentation with a truthful
  status message. Regression tests cover these separate outcomes.
- **Malformed preference:** range-check before floating-point finiteness so
  extremely large integers yield the normal preference error.
- **Diagnostic refinements after follow-up review:** name both possible backup
  locations, report partial metadata/staging creation, and do not describe a
  verified rollback as an uncertain calculation. These refinements were
  verified in the final 238-test focused run.

The approved fail-closed boundary remains: an uncertain RUNNING record is not
automatically unlocked or rolled back after restart. It requires manual
inspection of the live manifest, old manifest copy and result locations.
Blindly copying an old revision over the current manifest is not safe. Failed
attempts without a previous successful result retain the existing single-record
retry semantics rather than acquiring a new attempt-history system.

## Documentation and PDF review

The changelog, architecture/configuration/workflow notes and both user manuals
describe the same behavior. Both PDFs were rebuilt during apply and reopened
for completion review on 2026-09-24: English 63 pages, Chinese 57 pages.
Poppler rendering and visual inspection covered English pages 16, 37, 50-52
and Chinese pages 15, 34, 46-48. These include the Bond Detection explanation,
Project Manager rerun entry, WBL replacement instructions and result/export
section. The PDFs were rebuilt again after the explicit manual-recovery note;
English pages 51-52 and Chinese pages 47-48 were rendered and visually checked
again. No clipping or overlap was found in the inspected pages.

## OpenSpec and limitations

Proposal strict validation passed earlier in this change. Apply strict
validation uses `openspec validate rerun-wbl-and-remember-bond-factor --strict`
once after completion. Result: **Change 'rerun-wbl-and-remember-bond-factor' is valid**
(exit 0). No archive is requested. `git diff --check` passed on
2026-09-24. Offline synthetic validation does not establish live cluster
compatibility or recovery from every possible external filesystem failure.

No scope deviation: the audit fixes complete the existing replacement,
retained-evidence, cleanup, presentation and preference-validation boundaries.
No automatic remote recovery, new scientific behavior or architecture redesign
was introduced. No release/package/tag or Git operation was performed.
