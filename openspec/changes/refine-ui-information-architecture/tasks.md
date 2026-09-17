## 1. ORCA Recovery Capability

- [x] 1.1 Add the single `ProjectRecoverySnapshot` WBL-view capability and update both GUI consumers to use it; verify readable successful WBL snapshots enable viewing and all other snapshots do not in `tests/unit/test_orca_wbl_view.py` and `tests/unit/test_projects_dialog.py`.
- [x] 1.2 Recover successful WBL presentation independently of the currently active ORCA step, with non-fatal artifact diagnostics on read failure; verify WBL remains successful during active Frequency, readable artifacts remain viewable, and unreadable artifacts neither fail refresh nor erase success in `tests/unit/test_orca_submission_recovery.py`, `tests/unit/test_orca_wbl_workflow.py`, and `tests/unit/test_orca_wbl_view.py`.

## 2. Calculation Navigation and Project Actions

- [x] 2.1 Add optional engine preselection/locking to the existing new-project dialog and route both program-specific Step 1 actions through the shared submission flow; verify FHI-aims and ORCA lock the requested engine while ordinary existing dialog behavior remains available in `tests/unit/test_molecule_viewer_demo.py`.
- [x] 2.2 Recompose `Calculation` into the approved disabled headings, FHI-aims/ORCA submenus, and local-analysis section without a Gaussian placeholder; verify exact hierarchy, disabled headings, action ownership, and callbacks in `tests/unit/test_ui_r3_main_window.py` and `tests/unit/test_molecule_viewer_demo.py`.
- [x] 2.3 Add project-scoped ORCA resubmit and WBL-view buttons to Project Manager, remove their duplicate Calculation-menu actions, and reuse the existing main-window resubmit and WBL request chains; verify eligibility, visibility, new resubmit identity/`_resubmit` behavior, cancellation/provenance continuity, and ordinary edited-geometry submission behavior in `tests/unit/test_projects_dialog.py`, `tests/unit/test_molecule_viewer_demo.py`, and `tests/unit/test_orca_wbl_view.py`.

## 3. Cluster Settings Exit Safety

- [x] 3.1 Mark Cluster Execution Settings `Save` as the existing themed default action and capture a normalized raw editable-state baseline only after initial population; verify initial state is clean, user edits are detected across scheduler/runtime fields, and programmatic initialization is not marked dirty in `tests/unit/test_cluster_execution_dialog.py` and `tests/unit/test_runtime_configuration_dialog.py`.
- [x] 3.2 Route Cancel, window close, and Escape through the same Save/Discard/Cancel prompt while preserving validation and active-worker guards; verify unchanged close, changed-field labels, successful and failed prompt-save, discard, cancel-return, and active-worker cases in `tests/unit/test_cluster_execution_dialog.py`.

## 4. Selection Affordances

- [x] 4.1 Replace the export scale spin box with `1x`, `2x`, `4x`, and `8x` combo items carrying integer data, without changing request or renderer contracts; verify exact choices, default, selected request values, summaries, and existing validation in `tests/unit/test_view_export.py`.
- [x] 4.2 Add light/dark combo-arrow SVG resources through the existing theme asset mechanism and a global `QComboBox::down-arrow` rule that leaves `QToolButton` indicators unchanged; verify every supported theme resolves a visible arrow and subclass coverage in `tests/unit/test_gui_theme.py`.
- [x] 4.3 Add the approved combo/dropdown indicator principle to `.agents/skills/moltage-development/SKILL.md`; verify the skill contains one concise rule and no unrelated development-policy changes.

## 5. Focused Validation

- [x] 5.1 Run the ten focused modules `test_ui_r3_main_window.py`, `test_molecule_viewer_demo.py`, `test_orca_wbl_view.py`, `test_orca_wbl_workflow.py`, `test_projects_dialog.py`, `test_orca_submission_recovery.py`, `test_cluster_execution_dialog.py`, `test_runtime_configuration_dialog.py`, `test_view_export.py`, and `test_gui_theme.py`; verify all collected tests pass offline without real HPC access, and do not run the full offline suite for this change.
- [x] 5.2 Run `openspec validate refine-ui-information-architecture --strict` once after implementation artifacts stop changing; verify strict validation passes and report any scope deviation instead of broadening the change.
