## Context

See `proposal.md` for motivation and the three delta specs for observable behavior. The current server-profile store is schema 11: shared connection data lives in `ServerProfile`, scheduler/job defaults and the historical FHI runtime live in `SlurmExecutionPreset`, while AITRANSS has its own optional runtime field. `ClusterExecutionDialog` currently presents scheduler, resources, environment, FHI-aims and AITRANSS in one large form, and `RuntimeConfigurationDialog` contains two program tabs.

The current project manifest is schema 8 and `CalculationProject` requires exactly four ordered FHI-aims/AITRANSS steps. Submission and recovery already provide the reusable SSH, managed-workspace, atomic upload/hash, submit-once, scheduler status/cancel and download-progress boundaries, but their scientific input and success checks are FHI-specific.

ORCA's documented operational constraints shape this design:

- parallel jobs must invoke the main ORCA driver by full path and must not start it through `mpirun`; the driver starts its own parallel modules ([ORCA 6.1 parallel manual](https://www.faccts.de/docs/orca/6.1/manual/contents/essentialelements/parallel.html), [ORCA 5.0 parallel tutorial](https://www.faccts.de/docs/orca/5.0/tutorials/first_steps/parallel.html));
- omitting `%MaxCore` is legal and leaves ORCA's documented default of 4096 MB per process; `%MaxCore` is a planning value, not a hard upper bound ([ORCA 6.1 memory tutorial](https://www.faccts.de/docs/orca/6.1/tutorials/first_steps/memory.html));
- normal termination alone does not prove optimization convergence; ORCA documents separate convergence and maximum-cycle evidence ([ORCA 6.1 optimization manual](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations.html));
- `FREQ`/`AnFreq` and `NUMFREQ` are distinct, numerical Hessians are the general fallback, and ORCA explicitly annotates imaginary modes ([ORCA 6.1 frequency manual](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/frequencies.html), [ORCA 6.1 frequency tutorial](https://www.faccts.de/docs/orca/6.1/tutorials/prop/freq.html)).

The user-supplied Slurm script is evidence of one working invocation pattern only. Its project name, module version and site resources are not copied into source, tests or defaults.

## Goals / Non-Goals

**Goals:**

- Add ORCA through separate typed settings, rendering, parsing and orchestration while reusing existing remote safety and viewer surfaces.
- Make server settings visibly program-scoped without redesigning the connection, scheduler or job-default models.
- Keep ORCA input deterministic, version-aware and constrained to reviewed choices.
- Persist enough submitted settings and result evidence to recover a project without reinterpreting current GUI state.
- Keep optimization, optional frequency and future WBL as distinct user-authorized stages.

**Non-Goals:**

- Do not implement WBL transmission, ORCA orbital/wavefunction analysis, constraints, solvent models, relativistic models, custom basis files, compound jobs or raw keyword blocks.
- Do not add LSF automatic ORCA discovery, new schedulers, new SSH authentication, scratch staging or a generalized runtime-plugin architecture.
- Do not restructure the legacy FHI project workflow, rename `SlurmExecutionPreset`, or combine ORCA with existing FHI-specific input/parsing code.
- Do not install ORCA, run it during offline tests, connect to real HPC during apply, or claim real-cluster acceptance without a separately approved operation.

## Decisions

### 1. Add a separate optional ORCA runtime to the existing profile

Add a frozen `OrcaRuntimeConfiguration` owned by `ServerProfile`, containing:

- canonical absolute `executable_path` whose basename is `orca`;
- resolved `RuntimeEnvironment` (`NONE`, `MODULES`, or `SCRIPT`, never saved as `AUTO`);
- `version_text`, normalized semantic `version`, supported `version_family`, and `detection_source` evidence.

Extend saved runtime hints with one ORCA executable/environment request so an unsuccessful search or manual edit can be restored without promoting it to verified runtime. Do not place ORCA inside `FhiAimsRuntimeConfiguration` or infer it from an FHI module. Raise `server_profiles.json` to schema 12; schemas 1–11 load with `orca_runtime=None` and an empty ORCA hint. Existing values are otherwise byte-semantically preserved when reserialized.

Alternative considered: create a generic list of arbitrary programs. Rejected because only three known program configurations exist and a plugin-style schema would broaden migration and validation beyond this change.

### 2. Reshape the existing settings dialog around the program hierarchy

Use shared server settings plus one top-level program tab/page container in Cluster Execution Settings:

- shared `General / Cluster`: connection identity (read-only in this dialog), remote workspace, scheduler detection/manual command location, Slurm/LSF selectors and resource defaults;
- top-level `FHI-aims`: an inner FHI-aims runtime subtab for executable, species root, MPI launcher, environment, discovery and manual configuration, plus an `AITRANSS` subtab for its executable, environment and approved launch policy;
- top-level `ORCA`: absolute executable, environment, version/status, manual validation, and Slurm-only discovery.

Extract/reuse the current FHI/AITRANSS page widgets rather than changing their domain behavior, but nest AITRANSS under FHI-aims to reflect that it belongs to the FHI-aims transport workflow. Each runtime subtab/page owns its own discovery button/status, so failure on one runtime neither clears another runtime nor prevents saving the server-level profile. LSF shows ORCA discovery as unavailable in this release while leaving manual validation enabled. A future Gaussian integration would add another top-level program page beside FHI-aims and ORCA; this change adds no inactive Gaussian placeholder.

Alternative considered: make AITRANSS a third top-level program beside FHI-aims and ORCA. Rejected because AITRANSS is part of the FHI-aims transport toolchain, not an independent calculation-engine category. Adding ORCA inside the current mixed runtime dialog was also rejected because it preserves the misleading implication that all runtimes form one required bundle.

### 3. Implement ORCA discovery as a small bounded pipeline, not an extension of FHI heuristics

Create an ORCA-specific discovery service that reuses the existing remote command runner, shell quoting, environment rendering and candidate presentation. For a Slurm profile it checks, in order without priority-based auto-selection:

1. `command -v orca` in the login environment;
2. the exact user-configured ORCA environment, if present;
3. a bounded module catalog filtered case-insensitively for an ORCA module name, loading each bounded candidate in isolation before `command -v orca`.

For each result, use `readlink -f`, require `test -f` and `test -x`, require basename `orca`, and run only `<absolute-orca> --version` as an identity probe. It never submits work or supplies a molecular input. It does not recursively search directories, enumerate generic `/opt`, `/share` or home paths, inspect wrapper/setup-script contents, or prefer a version based on module spelling. Candidate identity is `(canonical executable, normalized environment, parsed version evidence)`; multiple distinct identities reach the existing explicit-selection pattern.

If `--version` is not parseable, retain `PATH_VERIFIED_VERSION_UNVERIFIED`. The user may explicitly continue only with the version-common catalog, and the saved evidence remains unverified; no guessed family is stored. Versions outside `5.0.x`, `6.0.x`, and `6.1.x` are shown as detected but unsupported for generation in this change.

Alternative considered: search common installation directories or choose the highest module version. Rejected because neither proves the user's intended licensed installation or compatible environment.

### 4. Use a reviewed version-capability catalog for input controls

Add ORCA-specific immutable catalog data with exact rendered tokens and version-family support. The initial conservative surface is:

- standard DFT methods common to the supported families: `BP86`, `BLYP`, `PBE`, `TPSS`, `B3LYP`, `PBE0`, `M062X`, `TPSSH`;
- composite methods: `B97-3C`, `PBEH-3C`, `R2SCAN-3C`, only in version families where the corresponding versioned manual documents them;
- `WB97M-D4REV` for ORCA 6.0/6.1, where it is documented; it embeds revised D4 parameters but still requires an orbital basis;
- orbital bases: `DEF2-SVP`, `DEF2-TZVP`, `DEF2-TZVPP`, `DEF2-QZVP`, filtered against the submitted elements and the versioned basis documentation;
- optional dispersion: `NONE`, `D3ZERO`, `D3BJ`, `D4`, enabled only for catalog combinations with documented parameters and disabled for composite or dispersion-inclusive methods;
- optimization convergence: `LOOSEOPT`, `OPT`, `TIGHTOPT`, `VERYTIGHTOPT`;
- coordinate system: `REDUNDANT` or `CARTESIAN` (`COPT` combined with the selected convergence where required);
- SCF convergence: `DEFAULT` (omit), `STRONGSCF`, `TIGHTSCF`, `VERYTIGHTSCF`.

Method and basis selectors start with no selection. `OPT`, `REDUNDANT`, and `DEFAULT` may be initial UI selections because they directly represent documented ORCA defaults rather than Moltage scientific recommendations. Every selected token is reconstructed from catalog identity, never accepted as raw text. Catalog entries carry their official manual URL and supported version families so tests can prove filtering. The full ORCA keyword universe is intentionally not exposed.

Alternative considered: populate every keyword from documentation or allow an advanced free-text line. Rejected because exhaustive compatibility is not established and raw input defeats validation.

### 5. Keep scientific settings and resource settings typed and stage-specific

Add immutable `OrcaOptimizationSettings` and `OrcaFrequencySettings`. Optimization stores catalog identities, charge, multiplicity, convergence options and submitted atom identity/order. Frequency references the source optimization settings/hash and stores only its mode plus its own resources.

Charge defaults to `0` and multiplicity to `1`; both remain editable. Before generation compute

`electron_count = sum(atomic_numbers) - charge`

and require electron-count/multiplicity parity to be physically representable (even electron count with odd multiplicity, or odd electron count with even multiplicity). This is an input-consistency check, not a prediction of the correct charge or spin state, so Moltage never changes either value.

Reuse the current scheduler resource model as initial job defaults and save the values actually submitted with the ORCA stage. Initial ORCA execution is pure process parallelism: `cpus_per_task=1`, scheduler task slots equal `%pal nprocs`, and thread environment values are set to one to avoid unrequested nested threading.

`max_core_mb: int | None` is per process. Blank renders no `%maxcore`; ORCA then uses its documented 4096 MB/process default. When a value is present, show the transparent estimate

`configured_ORCA_memory_MB = nprocs × max_core_mb`.

Only issue a capacity warning when the scheduler memory value has comparable semantics (for current Slurm per-node memory, compare distributed process placement against node memory). LSF `rusage[mem]` remains site-defined and is not converted into a false total-memory guarantee. In all cases the UI states that `%MaxCore` is not a hard process limit.

Alternative considered: derive `%MaxCore` automatically from scheduler memory. Rejected because ORCA can exceed it and scheduler memory semantics differ by site; an inferred value could terminate otherwise valid calculations.

### 6. Add deterministic ORCA input and scheduler renderers

Create a small `moltage.orca` package for catalog/settings, input rendering and result parsing. `orca_opt.inp` is rendered from normalized settings in a stable order:

1. reviewed simple-input tokens for method/basis/dispersion/optimization/SCF;
2. `%pal` with the exact scheduler process count;
3. optional `%maxcore` in MB;
4. explicit Angstrom Cartesian `* xyz charge multiplicity` coordinates using the existing stable geometry precision.

Do not introduce a second geometry rounding policy. A composite method omits separate basis and dispersion; `WB97M-D4REV` omits a separate dispersion token but includes the selected basis.

Add ORCA batch renderers beside, not inside, FHI-specific renderers. They reuse validated Slurm/LSF site-directive and structured LSF resource helpers, render the selected runtime environment, change to the managed task directory, then execute exactly:

`<quoted-absolute-orca> orca_opt.inp > orca_opt.out 2>&1`

The frequency equivalent uses `orca_freq.inp`/`orca_freq.out`. No `srun`, `mpirun`, `which`, shell-composed user keyword or bare executable is rendered.

Alternative considered: reuse the FHI launch-command field. Rejected because its MPI launcher semantics conflict with ORCA's driver contract and would obscure the no-`mpirun` requirement.

### 7. Extend the project schema with an explicit workflow discriminator

Raise project/manifest schema from 8 to 9 and add `CalculationWorkflowKind` with `FHI_AIMS_AITRANSS` and `ORCA`. Schemas 1–8 migrate to `FHI_AIMS_AITRANSS` and retain exactly the existing four steps. Extend step kinds with `ORCA_OPTIMIZATION` and `ORCA_FREQUENCY`, but replace global `tuple(ProjectStepKind)` assumptions with a workflow-specific ordered-step function:

- FHI workflow: unchanged four steps and folders;
- ORCA workflow: optimization at project root; frequency is absent until explicitly created, then uses `frequency/`.

Attach typed ORCA submitted settings/runtime evidence and typed result evidence only to ORCA stages. Existing FHI restart/electrode constraints remain conditional on the FHI workflow and are not loosened. The local project index continues to reference the manifest and gains only the workflow label needed for dispatch/display.

WBL is not added to the enum or manifest in this change. `.gbw` readiness is an evidence field on optimization, not a fake WBL stage.

Alternative considered: create a separate unrelated ORCA project store. Rejected because it would duplicate project identity, scheduler state, remote path and Project Manager behavior. Making every legacy step optional was also rejected because it would weaken existing invariants.

### 8. Add ORCA-specific orchestration behind existing remote boundaries

Create separate application services for ORCA submission and recovery. The GUI chooses calculation engine before opening the engine-specific settings dialog. ORCA orchestration reuses:

- current molecule atoms/order and viewer;
- profile/credential selection;
- safe remote project naming and managed metadata directory;
- in-memory bundle completion before remote mutation;
- atomic upload, SHA-256 verification and submit-once handling;
- existing Slurm/LSF status and cancel services;
- download progress and Project Manager entry points.

The ORCA service does not call FHI input generation or linker/Au workflow planning. Existing connectivity/linker metadata remains attached to the displayed structure for later features but does not change ORCA optimization input.

Optimization submission persists `orca_opt.inp`, submit script and manifest hashes before scheduler submission. A transport timeout after submission produces `UNKNOWN`; recovery queries the recorded scheduler/job identity and never auto-resubmits. Retry behavior is not added in this first stage.

Alternative considered: add ORCA branches throughout `project_submission.py` and `project_recovery.py`. Rejected because those modules encode FHI step semantics; thin workflow dispatch plus separate ORCA services is a smaller compatibility risk.

### 9. Treat optimization outcome as a four-part evidence decision

Persist independent evidence for:

1. scheduler terminal state;
2. `ORCA TERMINATED NORMALLY`;
3. `THE OPTIMIZATION HAS CONVERGED`, while recognizing the documented maximum-cycle nonconvergence message;
4. final `orca_opt.xyz` integrity.

Final XYZ validation requires nonempty UTF-8 text, one frame with finite Angstrom coordinates, exact atom count, and exact ordered element symbols from the submitted input. Do not reorder atoms or substitute the final trajectory frame. Only all four positive results produce `SUCCEEDED`; missing evidence is `UNVERIFIED`/incomplete, and negative evidence is `FAILED` with a specific reason.

Retain/download `orca_opt.out`, `orca_opt.xyz`, optional `orca_opt_trj.xyz`, and optional `orca_opt.gbw`. Store hashes for files used as evidence. Missing `.gbw` leaves optimization success intact but sets `wbl_input_ready=False`. Viewing the optimized structure uses the existing scene and does not overwrite the submitted geometry record.

Alternative considered: treat scheduler success or normal termination as optimization success. Rejected because the ORCA manual explicitly documents normal termination after nonconvergence.

### 10. Model frequency as a later independent job

After verified optimization, expose a separate user action that creates `ORCA_FREQUENCY`. It embeds the verified optimized coordinates and read-only inherits method, basis, dispersion, charge and multiplicity. The user selects `FREQ` or `NUMFREQ`, job resources and optional `%MaxCore`.

Catalog entries carry `analytical_frequency_supported`; `FREQ` is disabled without evidence, while `NUMFREQ` is available for reviewed methods and shows a cost warning. The renderer does not combine `OPT` and frequency in one input.

Frequency recovery requires scheduler success, normal termination, a parseable `VIBRATIONAL FREQUENCIES` section and a nonempty parseable `orca_freq.hess`. Validate the Hessian/frequency dimensions against `3N` and retain mode identifiers and values. Imaginary classification uses only ORCA's literal `***imaginary mode***` annotation; no new cm⁻¹ threshold is introduced. Persist separate classifications such as `FREQUENCY_COMPLETED`, `NO_IMAGINARY_MODES_REPORTED`, `IMAGINARY_MODES_REPORTED`, `UNVERIFIED`, and `FAILED` while leaving optimization unchanged.

Alternative considered: automatically append `FREQ` to optimization. Rejected because it couples cost and failure semantics and violates the user's explicit stage boundary.

### 11. Gate version `0.2.0` behind implementation acceptance

Do not change package version while applying functional code. After focused synthetic tests, the full offline suite, OpenSpec strict validation and a separately authorized successful real Slurm acceptance, update the project version and changelog to `0.2.0` as a final explicit task. LSF rendering/manual configuration is testable offline, but no real LSF ORCA acceptance is claimed unless separately performed.

Alternative considered: assign `0.2.0` when scaffolding lands. Rejected because the user requires the complete first stage to pass before versioning.

## Risks / Trade-offs

- [ORCA module catalogs vary substantially between sites] → Use only bounded name-filtered candidates, keep manual configuration first-class, and never promise discovery success.
- [Invoking `orca --version` may not yield parseable evidence on every supported package] → Keep path verification separate from version verification and restrict explicit continuation to the version-common catalog.
- [A conservative catalog omits valid ORCA combinations] → Prefer an explicit unsupported choice over raw keywords; expand the catalog only through later evidence-backed changes.
- [Project schema 9 touches code that assumes four FHI steps] → Centralize workflow-specific step sequences, migrate schemas 1–8 explicitly, and run the entire existing FHI suite unchanged.
- [ORCA output formatting can vary by patch version] → Parse documented stable markers with synthetic fixtures from each supported family; unknown output remains unverified rather than guessed.
- [Scheduler and ORCA memory concepts are not identical] → Label units/scope, avoid automatic `%MaxCore`, and make warnings advisory rather than claims of exact consumption.
- [Analytical Hessian support varies by method/version] → Store the support flag in the reviewed catalog; offer `NUMFREQ` without silently changing the user's selection.
- [Offline tests cannot prove a site's licensed ORCA/MPI installation works] → Report offline validation honestly and reserve real Slurm/LSF acceptance for explicit user authorization.

## Migration Plan

1. Add ORCA domain/catalog/runtime types and schema-12 profile migration, preserving schemas 1–11.
2. Recompose Server Settings into shared settings and top-level FHI-aims/ORCA program pages, with AITRANSS nested under FHI-aims, then add ORCA manual validation plus Slurm-only discovery.
3. Add deterministic optimization/frequency input and Slurm/LSF renderers with synthetic golden tests.
4. Add schema-9 workflow discrimination and migrate project schemas 1–8 to the unchanged FHI workflow.
5. Add ORCA submission/recovery services, Project Manager dispatch and optimized-geometry viewer integration.
6. Add optional frequency-stage UI and evidence parsing.
7. Run focused synthetic tests, then `tools/run_tests.ps1 -Full`, then strict OpenSpec validation. Do not connect to external infrastructure.
8. After separate user authorization, perform real Slurm acceptance with user-selected runtime/settings. Only after acceptance and user approval update version/changelog to `0.2.0`.

Rollback before public data creation is code-only. Once schema-12 profiles or schema-9 ORCA manifests are saved, older builds are not expected to read those newly written documents; original schema 1–11 profiles and schema 1–8 FHI manifests remain readable by the new build and are not rewritten merely by loading.
