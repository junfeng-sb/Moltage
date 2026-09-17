## Context

See `proposal.md` for motivation and `specs/hpc-site-configuration/spec.md` for observable requirements. The current profile store is schema 10. `SlurmExecutionPreset` is the established scheduler-owned persistence object even for LSF; FHI-aims scripts are rendered in `moltage.remote.slurm`, while Step 4 AITRANSS has a separate renderer in `moltage.aitranss.slurm`. Existing retry paths replace selected resource values on a preset and therefore can preserve additional profile fields without introducing a new profile/job model.

## Goals / Non-Goals

**Goals:**

- Represent only the approved Slurm/LSF site selectors and render them consistently for existing FHI-aims and AITRANSS jobs.
- Replace the Step 4 bare `srun` call with an explicit, fail-closed launch policy.
- Let an LSF profile choose between no `-R` request and the already-supported structured `span/rusage` request.
- Preserve schema 1–10 readability and current LSF behavior without inventing admission values.

**Non-Goals:**

- Do not split or rename `SlurmExecutionPreset`, introduce a scheduler plugin layer, or redesign submission models.
- Do not add raw scheduler directives/resource expressions, new launchers, scratch staging, authentication modes, schedulers, SSH timeout settings, or MPI ABI claims.
- Do not alter project manifests, remote project layout, scientific inputs, scheduler-state parsing, or scientific-success semantics.

## Decisions

### 1. Extend the existing preset with scheduler-qualified fields

Add optional normalized fields directly to `SlurmExecutionPreset`:

- `slurm_account`, `slurm_partition`, `slurm_qos`
- `lsf_queue`, `lsf_project`
- `lsf_resource_requirement_mode`
- `slurm_aitranss_launch_mode`, `slurm_aitranss_srun_path`

The first three are valid only for `SchedulerKind.SLURM`; the LSF fields and resource mode are valid only for `SchedulerKind.LSF`; the AITRANSS launch fields are Slurm-only. `None` is the canonical representation of a blank selector. This keeps the change on the current persistence/rendering seam instead of introducing a parallel server-configuration hierarchy.

Alternative considered: split server environment from job defaults now. Rejected because it would create a broad migration and touch unrelated submission APIs; the approved scope only needs additional profile fields.

### 2. Validate site selectors as data, not directive fragments

Use one shared scheduler-identifier normalizer that accepts a non-empty safe single-line token and rejects whitespace, control characters, quotes, `#`, and shell/directive separators. The GUI stores blank input as `None`; renderers construct the directive name themselves.

Alternative considered: accept arbitrary `#SBATCH`, `#BSUB`, or `-R` text. Rejected because it would combine configuration with executable syntax and exceed the approved scope.

### 3. Do not auto-discover admission selectors

Scheduler discovery continues to establish scheduler identity, command locations, and LSF client initialization only. It does not query or choose account, partition, QoS, queue, or project. Blank fields are displayed as using the scheduler/site default, rather than as a failed discovery.

Alternative considered: parse `sinfo`, `sacctmgr`, `bqueues`, or similar listings and pick a candidate. Rejected because visibility does not prove authorization or user intent.

### 4. Model Slurm AITRANSS launch as an explicit narrow policy

Introduce a two-value enum for `DIRECT` and `SRUN`, plus an optional absolute `srun` path. `DIRECT` renders `exec <absolute-aitranss-executable>`. `SRUN` renders `exec <absolute-srun> --ntasks=1 <absolute-aitranss-executable>` and requires an absolute path whose basename is `srun`.

The AITRANSS page in the existing manual runtime dialog gains a launch-mode selector and a conditional `srun executable` field. Existing bounded discovery may offer an already verified FHI `srun` launcher or test the exact sibling `<scheduler-bin>/srun`; it does not search the filesystem or select `SRUN` merely because a candidate exists. A selected `SRUN` path is verified through the existing remote executable boundary before it is accepted for submission. No launch mode silently falls back to the other.

Alternative considered: always run AITRANSS directly. Rejected because current installations may rely on Slurm job-step launch. Reusing any FHI launcher was also rejected because an FHI `mpirun` is not a valid substitute for `srun` here.

### 5. Represent the two approved LSF resource modes with an enum

Add `SITE_DEFAULT` and `SPAN_RUSAGE` only. `SITE_DEFAULT` omits `#BSUB -R`. `SPAN_RUSAGE` calls the existing structured renderer using host/rank/memory values and therefore preserves current syntax and validation.

The LSF page gains one resource-policy selector. When `SITE_DEFAULT` is selected, controls whose values would only feed `-R` are disabled or explicitly marked as not submitted; rank count, walltime, output, queue, and project remain active. The Step 4 resource UI likewise must not present its memory value as effective when the selected profile omits `-R`.

Alternative considered: a user-editable resource template. Rejected because it is effectively a general LSF expression editor and would require a much larger validation/security contract.

### 6. Centralize only the newly shared directive fragments

Add small pure helpers beside the existing batch rendering code for:

- Slurm site selector directives;
- LSF site selector directives;
- optional LSF structured resource directive;
- Slurm AITRANSS launch command.

Use them from both the FHI-aims renderer and Step 4 renderer. Existing Step 1/2/3, restart, retry, density, and Step 4 orchestration remain unchanged and receive the behavior through the preset/renderers they already use. Existing resource-only retry `replace(...)` operations preserve the new profile fields; no new task-level override UI is introduced because none currently exists for scheduler admission selectors.

Alternative considered: consolidate all scheduler renderers. Rejected as unrelated architecture work.

### 7. Use schema 11 with explicit compatibility defaults

Raise `server_profiles.json` to schema 11 and keep schema 1–10 readers:

- all legacy admission selector fields migrate to `None`;
- legacy LSF profiles migrate to `SPAN_RUSAGE` so their scripts remain byte-semantically equivalent for resource requests;
- legacy Slurm profiles retain historical `SRUN` intent for Step 4;
- an existing absolute FHI launcher is reused only when its basename is `srun`;
- otherwise the migrated profile remains readable with unresolved Step 4 `srun` path, and Step 4 submission is blocked until bounded verification or manual configuration supplies one, or the user explicitly selects `DIRECT`.

New Slurm profiles do not guess a Step 4 launch mode; Step 4 remains unavailable until the user chooses `DIRECT` or completes `SRUN`. New LSF profiles default to `SITE_DEFAULT`; existing LSF profiles retain `SPAN_RUSAGE` through migration. Saving writes schema 11; loading alone does not rewrite the file.

Alternative considered: migrate every old Slurm profile to `DIRECT` or construct an unverified `<scheduler-bin>/srun`. Rejected because either would silently change execution semantics or promote an inferred path to verified configuration.

### 8. Keep GUI additions on existing scheduler/runtime pages

The Slurm page receives three optional text inputs: Account, Partition, and QoS. The LSF page receives Queue, Project, and the resource-policy selector. The existing AITRANSS manual runtime page receives the Slurm launch controls only when the working scheduler is Slurm. Switching scheduler pages never reinterprets hidden values from the other scheduler.

No new top-level dialog, server model, or generalized advanced-options editor is added.

## Risks / Trade-offs

- [Some migrated Slurm profiles cannot safely recover the former PATH-resolved `srun`] → Keep the profile loadable, report only Step 4 launch as incomplete, and require explicit completion rather than preserving unsafe behavior.
- [A site's valid scheduler identifier may use characters outside the conservative token contract] → Fail with a field-specific message; expand the validator only with evidence, never accept directive text as a workaround.
- [`SITE_DEFAULT` cannot promise host placement or memory reservation] → Disable/annotate the affected controls and state that the scheduler owns those decisions.
- [LSF `SPAN_RUSAGE` memory semantics remain site-dependent] → Preserve it only as an explicit opt-in structured mode and make no cross-site memory-scope claim.
- [Offline script tests cannot establish external scheduler acceptance] → Use synthetic tests for this change and reserve real SSH/HPC acceptance for separate user authorization.

## Migration Plan

1. Add enums, fields, validation, and schema-11 serialization while retaining all older readers.
2. Add GUI controls and bounded `srun` verification without changing unrelated profile fields.
3. Update shared rendering helpers and both FHI-aims/AITRANSS renderers.
4. Run targeted synthetic migration, GUI, rendering, and submission regression tests; then run the existing full offline suite if targeted tests pass.
5. Rollback is code-only: schema-11 files containing new fields will not be readable by older releases, so no downgrade compatibility is claimed. Existing schema 1–10 files remain unchanged until explicitly saved by the new release.
