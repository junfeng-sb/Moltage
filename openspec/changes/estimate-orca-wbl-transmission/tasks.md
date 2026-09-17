## 1. Scientific Domain

- [x] 1.1 Add immutable WBL settings, contact-subspace provenance and result models with no numeric `Gamma_0` defaults.
- [x] 1.2 Add strict ORCA JSON/overlap/spin/basis parsing and reviewed def2 valence-shell mapping; reject unsupported evidence without fallback.
- [x] 1.3 Add geometry-derived/manual contact directions, symmetric Löwdin projection and all-MO alpha/beta/total WBL calculation.
- [x] 1.4 Add deterministic CSV/JSON/SVG rendering with top-two per-spin contributions and hashes.
- [x] 1.5 Distinguish restricted multiplicity-1 total-only results from unrestricted spin-resolved results without duplicating closed-shell spatial orbitals.
- [x] 1.6 Persist an explicit spin treatment in v2 result artifacts while keeping legacy v1 spin-resolved artifacts and manifests readable.

## 2. Workflow and Remote Evidence

- [x] 2.1 Add schema-compatible optional `ORCA_WBL_TRANSMISSION` stage after optimization and before optional frequency.
- [x] 2.2 Add capability-verified remote `orca_2json` extraction and atomic artifact persistence without a new quantum-chemistry job.
- [x] 2.3 Integrate WBL refresh/presentation without changing optimization/frequency or FHI transmission semantics.

## 3. GUI

- [x] 3.1 Add the explicit WBL settings dialog under `Calculation > ORCA` with correct readiness gates; keep WBL/frequency out of the Project Manager footer.
- [x] 3.2 Add an alpha/beta/total ORCA WBL result view compatible with current-view export.
- [x] 3.3 Render exactly two ORCA project indicators (optimization/WBL), persist WBL `RUNNING`/terminal progress, and open completed transmission from the second indicator or Calculation menu.
- [x] 3.4 Recover incomplete optimization input without FHI artifacts and resubmit with persisted settings after exact active-Job cancellation.
- [x] 3.5 Reuse domain linker detection for two-contact prefill, contact-driven linker resolution, and numbered viewer manual selection.
- [x] 3.6 Simplify the WBL form, add descriptive Advanced projection/provenance controls, unit-safe numeric widgets, right-`Gamma_0` mirroring, and versioned energy defaults.
- [x] 3.7 Propagate WBL preparation, running, success, and failure feedback to the originating ORCA Geometry workspace.
- [x] 3.8 Use shared typographic `10ⁿ` labels for current logarithmic transmission charts and the ORCA WBL SVG export.

## 4. Validation and Documentation

- [x] 4.1 Add synthetic parser/projection/calculation/artifact tests, including core-S-2p exclusion, restricted/unrestricted spin and missing-overlap failure.
- [x] 4.2 Add synthetic migration, remote orchestration and Qt workflow tests; no real SSH, ORCA or HPC operations.
- [x] 4.3 Update maintained English documentation with WBL hypothesis/evidence/limitations and no real environment data.
- [x] 4.4 Run focused tests, the full offline suite and strict OpenSpec validation; report exact evidence and scope deviations.
- [x] 4.5 Add synthetic tests for two-light presentation, ORCA-only geometry reads, resubmission cancellation ordering, unresolved-state fail-closed behavior and settings prefill.
- [x] 4.6 Verify terminal-success publication, queued GUI completion in all project views, presentation-failure state preservation, and ORCA/FHI footer isolation with synthetic tests.
- [x] 4.7 Add focused synthetic tests for contact prefill/manual selection, dynamic automatic projection labels, numeric defaults/mirroring, and workspace-visible WBL status.
- [ ] 4.8 Run the full offline suite once for the new scientific UI default and validate the updated OpenSpec change strictly.
- [x] 4.9 Add focused synthetic coverage for closed-shell total-only calculation, open-shell spin resolution, unrestricted-singlet rejection, v1 migration, result viewing and `10ⁿ` labels.
