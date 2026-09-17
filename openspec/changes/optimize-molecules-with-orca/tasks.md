## 1. ORCA Domain and Evidence Parsers

- [x] 1.1 Add ORCA version evidence parsing and the reviewed 5.0.x/6.0.x/6.1.x method, basis, dispersion, optimization, SCF and frequency capability catalog; verify synthetic tests cover exact supported tokens, composite-method locking, `WB97M-D4REV` version filtering, unknown versions and unsupported versions.
- [x] 1.2 Add immutable ORCA optimization/frequency settings with charge/multiplicity parity, process/resource and optional `%MaxCore` validation; verify unit tests cover defaults, missing method/basis, incompatible combinations, blank MaxCore and invalid parity without automatic correction.
- [x] 1.3 Add deterministic `orca_opt.inp` and `orca_freq.inp` renderers using the existing geometry precision; verify golden-byte tests cover standard DFT, composite DFT, `WB97M-D4REV`, Cartesian/redundant optimization, SCF choices, `%pal`, optional `%maxcore`, inherited frequency settings and raw-keyword rejection.
- [x] 1.4 Add ORCA optimization output/XYZ evidence parsing; verify synthetic tests distinguish normal termination, converged optimization, maximum-cycle nonconvergence, malformed/mismatched XYZ, missing artifacts and `.gbw`-independent optimization success.
- [x] 1.5 Add ORCA frequency output/Hessian evidence parsing; verify synthetic tests cover `VIBRATIONAL FREQUENCIES`, 3N dimensional consistency, ORCA `***imaginary mode***` annotations, no reported imaginary modes, incomplete `.hess`, conflicting mode counts and no invented numerical threshold.

## 2. Server Profile and Runtime Configuration

- [x] 2.1 Add profile-scoped `OrcaRuntimeConfiguration` and ORCA discovery hints, raise the profile store to schema 12, and verify schema 1–11 migrations plus schema-12 round trips preserve every existing non-ORCA field and leave legacy ORCA unresolved.
- [x] 2.2 Implement remote manual ORCA validation for Slurm and LSF using canonical absolute executable, selected environment and version evidence; verify synthetic command-runner tests reject stale/mismatched/non-executable paths and never run a molecular calculation or fall back to another PATH executable.
- [x] 2.3 Implement bounded Slurm-only ORCA discovery over login PATH, configured environment and bounded ORCA-named module candidates; verify synthetic tests cover module-name/version diversity, isolated loads, multiple-candidate selection, unparseable version evidence, no fixed installation paths, no recursive scan and no automatic newest-version choice.
- [x] 2.4 Recompose Cluster Execution Settings into shared `General / Cluster` settings plus top-level `FHI-aims` and `ORCA` program pages, with `AITRANSS` as a subtab under FHI-aims; verify Qt tests assert this hierarchy, show that missing unrelated runtimes do not block profile saving, preserve existing FHI/AITRANSS values across page changes, expose Slurm ORCA discovery and expose LSF ORCA manual validation only.

## 3. Scheduler Script Rendering

- [x] 3.1 Add a deterministic Slurm ORCA renderer that reuses validated site selectors/resources and invokes only the configured absolute ORCA driver; verify script tests cover blank/nonblank admission fields, environment modes, `%pal`/task equality, output redirection and absence of `srun`, `mpirun`, `which` and bare `orca`.
- [x] 3.2 Add the corresponding LSF ORCA renderer using existing queue/project and `SITE_DEFAULT`/`SPAN_RUSAGE` policies; verify synthetic tests cover both resource policies, blank selectors, direct absolute invocation and no LSF auto-discovery assumption.
- [x] 3.3 Add renderer/preflight validation for optional `%MaxCore` and resource comparison; verify tests prove blank values omit `%maxcore`, provided values remain per-process MB, comparable Slurm conflicts warn without changing values, and site-defined LSF memory is not presented as an exact total.

## 4. Workflow and Manifest Compatibility

- [x] 4.1 Add `CalculationWorkflowKind` and workflow-specific ordered step/folder validation, raise project manifests to schema 9, and verify schemas 1–8 migrate to the unchanged four-step `FHI_AIMS_AITRANSS` workflow.
- [x] 4.2 Add `ORCA_OPTIMIZATION` plus on-demand `ORCA_FREQUENCY` stage records and typed submitted/runtime/result evidence; verify manifest round-trip tests cover hashes, settings, scheduler kind/state, output evidence, source-optimization identity and absence of any WBL stage.
- [x] 4.3 Update local project indexing/dispatch to retain and display workflow identity without weakening legacy electrode/restart invariants; verify existing FHI project/index tests remain unchanged and new synthetic ORCA projects reopen through the ORCA path.

## 5. ORCA Optimization Submission and Recovery

- [x] 5.1 Add an ORCA optimization submission service that completes the bundle in memory and reuses managed project naming, atomic upload/hash verification and submit-once handling; verify fake-remote tests cover Slurm and LSF success, preflight failure before remote mutation, unique job-ID persistence and ambiguous submission becoming `UNKNOWN` without retry.
- [x] 5.2 Add calculation-engine selection and the structured ORCA optimization dialog with no preselected method/basis; verify Qt tests cover version-filtered controls, charge `0`, multiplicity `1`, optional MaxCore, composite/dependent controls, resource mapping, parity errors and submission confirmation.
- [x] 5.3 Integrate ORCA project status/cancel/refresh into Project Manager using existing scheduler services; verify fake scheduler tests keep scheduler, program and convergence states separate for queued, running, successful, failed, timed-out, cancelled and unknown jobs.
- [x] 5.4 Add ORCA optimization recovery and artifact retrieval with byte/hash evidence and progress reporting; verify fake-remote tests cover valid optimized geometry, malformed/ordered-element mismatch, output/trajectory retention, missing `.gbw` readiness and no automatic resubmission.
- [x] 5.5 Display a recovered verified `orca_opt.xyz` in the existing molecule viewer without overwriting submitted geometry or changing linker/connectivity metadata; verify focused viewer/controller tests cover the result switch and preservation of the original project evidence.

## 6. Optional Frequency Stage

- [x] 6.1 Add the post-optimization frequency action and dialog with read-only inherited method/basis/dispersion/charge/multiplicity, explicit `FREQ`/`NUMFREQ`, independent resources and optional MaxCore; verify Qt tests disable unsupported analytical frequency, show numerical-cost guidance and create nothing until user confirmation.
- [x] 6.2 Add independent frequency submission/recovery using the verified optimized geometry and ORCA runtime contract; verify fake-remote tests cover atomic upload, submit-once, scheduler/program failures, stale runtime, source-setting mismatch and preservation of optimization success.
- [x] 6.3 Display persisted frequency evidence and classifications without claiming a global minimum; verify tests cover `FREQUENCY_COMPLETED`, `NO_IMAGINARY_MODES_REPORTED`, `IMAGINARY_MODES_REPORTED`, `UNVERIFIED` and `FAILED`, and prove no frequency outcome starts WBL or rewrites optimization state.

## 7. Documentation and Offline Validation

- [x] 7.1 Update maintained English user/developer documentation for server-setting pages, supported ORCA versions/options, manual LSF configuration, MaxCore semantics, optimization/frequency evidence and the fact that WBL is not yet implemented; verify documentation contains no real server, account, path, job or research-project data.
- [x] 7.2 Run the focused ORCA/profile/project/GUI/scheduler test set and verify all new fixtures are synthetic, no test opens SSH or executes ORCA, and every changed behavior has a passing scenario-level test.
  - Validation: 281 focused tests passed; no failures or external operations.
  - Output-path regression validation: 19 focused tests passed; no failures or external operations.
- [x] 7.3 Run `tools/run_tests.ps1 -Full` offline and record the exact collected/passed/failed/skipped counts and exit code without claiming unrun external validation.
  - Validation: 1383 collected, 1381 passed, 0 failed, 2 skipped; exit code 0.
- [x] 7.4 Run `openspec validate optimize-molecules-with-orca --strict` and verify the change remains valid with no scope deviation.
  - Validation: strict validation passed.

## 8. Explicitly Authorized Acceptance and Version Gate

- [ ] 8.1 After separate explicit user authorization, perform a read-only/runtime-preflight plus one user-configured real Slurm ORCA optimization acceptance and record scheduler, ORCA termination, optimization convergence, XYZ and `.gbw` evidence separately; do not perform this task during ordinary apply or offline validation.
- [ ] 8.2 Only after tasks 1–7 and approved task 8.1 pass, update package version and changelog to `0.2.0`, run the focused version/package checks, and present the resulting diff for user acceptance; do not assign the version earlier.
