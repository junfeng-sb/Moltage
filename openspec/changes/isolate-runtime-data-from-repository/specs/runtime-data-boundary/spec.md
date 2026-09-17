## Purpose

定义 Moltage 对 packaged resources、per-user persistent state、temporary data、user-selected output、remote calculation data 与 developer artifacts 的稳定边界，使正常运行和开发操作不再把 repository root 当作隐式可写 workspace。

## ADDED Requirements

### Requirement: Production runtime storage is independent of the current working directory

Moltage MUST resolve non-secret persistent application state under the current user's `%APPDATA%\Moltage` directory and MUST NOT derive that location from the process current working directory. Density-task manifests and recovered density payloads MUST use the application-owned `density_results` child of that directory.

#### Scenario: Density workflow is constructed while the repository is the current directory
- **WHEN** the production density workflow service is constructed while any repository or other arbitrary directory is the process current working directory
- **THEN** its persistent cache root is `%APPDATA%\Moltage\density_results`
- **AND** construction creates no file or directory in the current working directory

#### Scenario: Per-user application data is unavailable
- **WHEN** the operating system does not provide a safe per-user application-data base
- **THEN** Moltage reports an explicit application-data path error
- **AND** it does not fall back to the current working directory, installation directory, or filesystem root

### Requirement: Runtime data follows its declared ownership boundary

Moltage SHALL read packaged static resources from the source/package resource root, SHALL use an operating-system temporary directory for short-lived downloads that do not require persistence, SHALL keep application-owned local caches under per-user application data, and SHALL write exported results only to a destination explicitly selected by the user. Remote calculation files SHALL remain under the configured remote project workspace.

#### Scenario: A temporary orbital Cube is displayed
- **WHEN** Moltage downloads an orbital Cube only for immediate parsing and display
- **THEN** the download uses an operating-system temporary directory
- **AND** the temporary file is removed when the bounded operation finishes
- **AND** no copy is written to the repository or installation directory

#### Scenario: A user exports local output
- **WHEN** the user confirms an input, image, or density-result export destination
- **THEN** Moltage writes only to that explicit destination
- **AND** it does not substitute the repository root when the destination is absent or invalid

#### Scenario: Runtime or scheduler discovery is executed
- **WHEN** the user performs an existing server runtime or scheduler discovery operation
- **THEN** the discovery creates no local runtime workspace in the repository
- **AND** its existing remote read-only and authorization boundaries remain unchanged

### Requirement: Generated repository content is excluded by category without hiding source regressions

The repository MUST exclude known developer-generated categories such as root `tmp`, Python bytecode/cache, test cache, virtual environments, and build/distribution outputs. It MUST NOT use broad scientific filename-extension exclusions or a broad arbitrary-UUID-directory exclusion that could hide tracked fixtures or a recurrence of repository-root runtime storage.

#### Scenario: Normal test and packaging artifacts are produced
- **WHEN** the existing test or packaging tools create their documented cache, bytecode, build, or distribution outputs
- **THEN** those category-owned paths remain outside version control
- **AND** legitimate tracked files with `.in`, `.out`, `.dat`, `.cube`, `.xyz`, `.mol`, or `.json` extensions remain eligible for tracking

#### Scenario: A new arbitrary UUID directory appears at repository root
- **WHEN** a future process creates an arbitrary UUID-named directory directly under repository root
- **THEN** Git does not silently ignore it solely because its name resembles a UUID
- **AND** the unexpected path remains visible for investigation

### Requirement: Identified legacy artifacts are handled according to data ownership

An identified development scratch directory with no repository references MAY be deleted during an explicitly authorized cleanup. An identified density-task manifest MUST be treated as user runtime state rather than temporary garbage and MUST be moved outside the repository only after the destination is proven non-conflicting and the preserved file content is verified. Existing application-data caches outside the repository MUST remain unchanged by this cleanup.

#### Scenario: Development scratch has no repository dependency
- **WHEN** the identified root `tmp` contents have no source, test, documentation, fixture, or packaged-resource references
- **THEN** an explicitly authorized cleanup may delete that directory
- **AND** no tracked project content is removed

#### Scenario: An orphaned density-task directory is relocated
- **WHEN** the identified repository-root density-task directory has a valid task manifest and the intended per-user destination does not exist
- **THEN** the cleanup preserves the directory under the per-user density-results boundary
- **AND** verifies the preserved manifest content before removing the repository-root copy

#### Scenario: The density-task destination conflicts
- **WHEN** the intended per-user destination already exists or content verification fails
- **THEN** the cleanup stops without deleting or overwriting either copy
- **AND** reports the exact conflict for human resolution

#### Scenario: Legacy application-data caches already reside outside the repository
- **WHEN** cache entries exist under the former application-data directory
- **THEN** this change neither copies nor deletes those entries
- **AND** it makes no claim that those entries are current workflow authority
