## 1. Domain and persistence

- [x] 1.1 Add `OrcaOptimizationOrigin` and `OrcaImportProvenance` (with `OrcaImportWavefunctionReadiness`) to `orca/project_evidence.py`, add `origin` to `OrcaOptimizationResultEvidence`, and make `succeeded` require `scheduler_succeeded` only for `MOLTAGE_SUBMITTED`.
- [x] 1.2 Add `ProjectStepRecord.orca_import_provenance`, valid only on `ORCA_OPTIMIZATION`, and raise `PROJECT_SCHEMA_VERSION` to 11 with schemas 1–10 as legacy.
- [x] 1.3 Serialize and parse the new origin and import provenance in `remote/project_manifest.py`, migrating schemas ≤10 to `MOLTAGE_SUBMITTED` with no import provenance.

## 2. ORCA evidence

- [x] 2.1 Add `parse_rendered_orca_scientific_identity` to `orca/input_writer.py`, reading charge/multiplicity back from the literal `* xyz` header and resolving method/basis/dispersion only on unambiguous existing catalog tokens.
- [x] 2.2 Add `orca/import_evidence.py` owning candidate-group discovery by exact stem and aggregated read-only validation built from the existing `parse_orca_optimization_output`, `parse_rendered_orca_structure`, and `parse_orca_final_xyz`; introduce no new ORCA output grammar.

## 3. Remote and application

- [x] 3.1 Add `remote_file_sha256`, `copy_remote_files_with_verified_digests`, and `discard_imported_step_inputs` to `remote/step_inputs.py` using safely quoted exact paths with no wildcard or recursive deletion.
- [x] 3.2 Promote the existing `orca_2json` capability check in `app/orca_wbl.py` to a public `verify_orca_wbl_utilities` and consume it from both the WBL service and the import service without duplicating it.
- [x] 3.3 Add `app/orca_import.py` with a validation operation and an import operation that allocates the managed directory, copies and verifies the four artifacts, writes the manifest last, marks the local index, and cleans up fail-closed.
- [x] 3.4 Preserve imported origin in `app/project_recovery.py::_recover_orca_optimization` so refresh never writes a scheduler success or downgrades an imported optimization, and correct the imported early-return message in `app/orca_recovery.py`.

## 4. GUI

- [x] 4.1 Add `gui/orca_import_dialog.py` with the `Import Existing ORCA Optimization` dialog, server selection, editable/browsable remote path, managed project name, read-only destination preview, candidate selection, and user-facing validation results.
- [x] 4.2 Add the validation and import `QRunnable` workers following the existing remote-worker lifecycle, with stop-token cancellation, busy-state gating, single in-flight import, and no callbacks into destroyed widgets.
- [x] 4.3 Add `Projects > Import Existing Calculation... > ORCA Optimization...` in `tools/molecule_viewer_demo.py`, always enabled, wired to one shared controller and the existing service dependencies.
- [x] 4.4 Disable `Resubmit Optimization...` for imported projects in `gui/projects_dialog.py` with an explicit reason.

## 5. Validation

- [x] 5.1 Add synthetic evidence tests: single valid group, multiple groups requiring selection, missing wavefunction, missing normal termination, non-convergence, stem mismatch, unreadable artifact, and ambiguous keyword line.
- [x] 5.2 Add synthetic import-service tests against a fake remote filesystem asserting source immutability, copied-file set, checksum verification, destination collision, fail-closed cleanup, timeout and cancellation.
- [x] 5.3 Add persistence tests: imported Step 1 `SUCCEEDED` with imported origin and no scheduler identity, Step 2 `NOT_STARTED`, reload consistency, refresh without downgrade, and schema 1–10 readability.
- [x] 5.4 Add WBL integration tests: imported project enters the existing Step 2 dialog and service, uses the imported optimized structure for detection, converts through the verified utility, runs no SCF or optimization, reads no FHI-aims artifact, and writes results to the managed workspace.
- [x] 5.5 Add Qt tests: menu entry enabled with no molecule loaded, server selection, manual path entry, browse write-back, button states during validation, enabled/disabled `Import`, multi-candidate selection, missing-profile guidance, and success feedback.
- [x] 5.6 Run focused tests for each touched module, then the ORCA project/import/WBL integration tests, then the full offline suite once because this change touches schema, persistence, remote file handling, and cross-layer workflow.
- [x] 5.7 Run `openspec validate import-existing-orca-optimization-for-wbl --strict` after proposal completion and again after apply completion; report exact evidence and any scope deviation.

## 6. `*xyzfile` inputs (user-approved revision, 2026-09-22)

- [x] 6.1 Extract `render_orca_xyz_block` from `orca/input_writer.py::_render` without changing rendered bytes.
- [x] 6.2 In `orca/import_evidence.py`, recognize exactly one `*xyzfile <charge> <multiplicity> <path>` header, resolve its path (absolute as written, relative against the source directory), reject `..`, the result's own `<stem>.xyz`, several headers, or a coexisting inline block, read the file with `structure/xyz.py::parse_xyz`, and replace only that line with the inline block; run the existing parsers on the inlined input.
- [x] 6.3 Add optional `source_coordinate_path` / `source_coordinate_sha256` to `OrcaImportProvenance` (both or neither) and serialize them, reading absent keys as no coordinate file.
- [x] 6.4 In `app/orca_import.py`, read and hash the coordinate file during validation, recheck it before import, upload the inlined `orca_opt.inp` through `upload_new_files_atomically` with server-side SHA256 verification, and copy the other three artifacts unchanged.
- [x] 6.5 State the inlined coordinate source in the import dialog's validation result.
- [x] 6.6 Add evidence, service, persistence, refresh and dialog tests; run the focused ORCA import/manifest/WBL tests and the full offline suite once, because the provenance manifest changed.
