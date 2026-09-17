## Context

See `proposal.md` for motivation and `specs/runtime-data-boundary/spec.md` for the behavior contract.

Current production composition passes `%APPDATA%\Moltage\density_results` to `DensityWorkflowService`; the service itself intentionally accepts an injected cache directory so focused tests can use `TemporaryDirectory`. `app/paths.py` already owns every other non-secret per-user path, and `app/package_resources.py` resolves immutable resources from source or `_MEIPASS` without consulting current working directory.

The inspected root `tmp/` contains only untracked visual-QA screenshots, one redirected test log, one synthetic preview script, and bytecode, with no source/test/doc reference. The inspected root UUID directory contains a valid schema-1 `ELECTRON_DENSITY_DIFFERENCE` manifest whose task ID matches the directory and whose last recorded attempt is `RUNNING`; its profile is no longer present locally. This state is therefore orphaned but still user runtime data. Five other valid density caches already reside under the former `%APPDATA%\AIMS-Transport\density_results`; two include downloaded payloads and all five are terminal.

## Goals / Non-Goals

**Goals:**

- Make the production density-cache owner explicit and independently testable.
- Prevent current working directory from becoming an implicit runtime-data fallback.
- Preserve the existing dependency injection used by focused workflow tests.
- Replace the one-off UUID ignore with durable category rules and regression visibility.
- Clean only the two investigated repository-root artifacts, preserving user state before removal.

**Non-Goals:**

- No generic workspace/cache manager, path registry, or remote-subsystem redesign.
- No change to density manifest schema, remote project layout, scheduler/program/scientific states, exports, or scientific inputs.
- No automatic scan for root-level UUID directories.
- No automatic migration, deletion, or interpretation of former `%APPDATA%\AIMS-Transport\density_results` caches.
- No SSH/HPC operation and no cleanup of unrelated build, distribution, virtual-environment, or ignored cache directories.

## Decisions

### 1. Keep path ownership in `app/paths.py`

Add a narrow `density_results_path()` accessor returning `application_data_directory() / "density_results"`, and use it at the production density-service composition point. Keep `DensityWorkflowService(connection_service, cache_directory)` unchanged so tests and future non-production adapters can still supply an isolated explicit root.

This is preferred over teaching the workflow service to inspect `%APPDATA%` because application-data policy already belongs to `app/paths.py`; it is also preferred over a general path registry because this change has one concrete missing accessor.

### 2. Treat absence of safe application data as an error

Reuse the existing `ApplicationDataPathError` behavior. Do not add a current-directory, executable-directory, repository-directory, or filesystem-root fallback.

The alternative fallback would make installed behavior dependent on launch location and could silently recreate the original pollution.

### 3. Validate the composition boundary without creating runtime data

Extend focused tests to patch `APPDATA`, change current working directory to a distinct temporary location, and assert that `density_results_path()` and the production density-service factory resolve beneath the patched per-user root. The construction assertion also verifies that the working directory remains empty. Existing workflow tests continue to inject their own temporary cache root.

This is preferred over launching the full GUI because service construction is the exact regression boundary and needs no Qt event loop, network operation, or scientific fixture.

### 4. Keep ignore rules categorical and deliberately expose arbitrary UUID roots

Retain existing rules for `/tmp/`, Python/test caches, virtual environments, `build/`, `dist/`, and root copies of known application state including `/density_results/`. Remove the exact historical UUID entry after its data is relocated. Do not add scientific extension globs or a generic UUID-directory glob.

The alternative generic UUID ignore would reduce accidental staging risk but would also hide a future recurrence of the defect. The durable control is the application-data path plus its test; an unexpected new root UUID should remain visible in `git status`.

### 5. Separate one-time local cleanup from product behavior

The apply workflow may act only on the two already inspected absolute source paths. It must not search for similar names.

- For the orphaned density task, first resolve and verify the source and destination, require the destination to be absent, copy the single manifest to `%APPDATA%\Moltage\density_results/<task-id>/`, compare SHA-256 and byte length, and only then remove the original file and now-empty directory. A conflict or verification failure stops without overwrite or source deletion.
- For `tmp/`, re-confirm that the exact resolved directory is under the historical repository, enumerate its contents, and remove that exact directory only after apply authorization. It is not migrated because it contains no project dependency or user workflow state.

This is preferred over product startup migration: scanning a repository is outside application ownership, and former application-data density caches can contain large Cube payloads that must not be synchronously copied by this narrowly scoped change.

### 6. Document only durable ownership boundaries

Update the existing configuration/architecture evidence only where needed to state that density caches are per-user application data and that runtime storage is independent of current working directory. Do not add a general filesystem design or restate unrelated workflows.

## Risks / Trade-offs

- [The orphaned manifest still cannot be opened without its removed server profile] → Preserve it rather than claiming recovery; relocation establishes correct ownership but does not invent profile identity or task authority.
- [Deleting `tmp/` is not recoverable through Git] → Delete only the exact inspected untracked directory after explicit apply authorization and report its contents/count before removal.
- [An arbitrary future UUID directory is not ignored] → Keep it intentionally visible; the new path-level test prevents the known production route from generating it.
- [Tests and packaging still create ignored caches/build outputs inside the source tree] → Retain their existing category ignores; this change does not misclassify normal developer artifacts as product runtime data.
- [Former application-data density caches remain in the legacy location] → Leave them untouched to avoid an unrequested large migration; this does not pollute the repository.

## Migration Plan

1. Implement and validate the named density-results path accessor and production composition update.
2. Update the categorical ignore/documentation boundary and run focused offline tests.
3. Run the full offline suite and confirm no non-ignored repository artifact appears.
4. With explicit apply authorization still in force, relocate the exact orphaned manifest using copy, hash/length verification, and source removal; stop on any destination conflict.
5. Re-enumerate and delete only the exact investigated `tmp/` directory.
6. Confirm the canonical repository has no tracked/untracked runtime data and report remaining ignored developer artifacts separately.

Rollback of tracked changes is a normal Git revert before publication. The relocated manifest can be copied back if required because its bytes are preserved. The deleted `tmp/` scratch content has no repository backup and is intentionally non-recoverable.
