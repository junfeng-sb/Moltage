## Purpose

定义 Moltage 在已支持的 SSH + Slurm/LSF 环境中如何保存并应用 scheduler site configuration，使新服务器通过 profile 配置即可使用，而不依赖隐含的某台集群默认值或 PATH 偶然状态。

## ADDED Requirements

### Requirement: Scheduler-specific site selectors are optional profile data
Moltage SHALL allow each Slurm profile to store optional `account`, `partition`, and `qos` values, and each LSF profile to store optional `queue` and `project` values. A blank value SHALL mean that the corresponding scheduler/site default is intentionally used; Moltage MUST NOT guess or auto-select any of these values.

#### Scenario: Configured Slurm selectors are rendered
- **WHEN** a Slurm profile has one or more configured site selectors and Moltage renders a new FHI-aims or AITRANSS batch script
- **THEN** the script contains only the corresponding configured `#SBATCH` selectors and preserves blank selectors as omitted directives

#### Scenario: Configured LSF selectors are rendered
- **WHEN** an LSF profile has a configured `queue` or `project` and Moltage renders a new FHI-aims or AITRANSS batch script
- **THEN** the script contains the corresponding `#BSUB` selector and omits each blank selector

#### Scenario: Site selectors remain scheduler-specific
- **WHEN** a profile changes scheduler type or malformed persisted data contains selectors for the other scheduler
- **THEN** Moltage does not reinterpret or render those foreign-scheduler values and requires a valid scheduler-specific configuration

#### Scenario: Resource-only retry preserves site configuration
- **WHEN** an existing retry flow changes only supported job resource values
- **THEN** the selected profile's scheduler site configuration is preserved in the newly rendered script unless that existing flow already supplies an explicit supported override

### Requirement: Scheduler selector input is constrained
Moltage SHALL accept scheduler selector values only as validated single-line scheduler identifiers. It MUST reject control characters, line breaks, directive prefixes, quoting constructs, or other input that could create an additional scheduler directive, and it MUST NOT expose a raw scheduler-directive input.

#### Scenario: Unsafe selector is rejected before rendering
- **WHEN** a user or persisted profile supplies a selector containing unsafe directive syntax
- **THEN** Moltage reports the field as invalid before upload or scheduler submission and renders no partial directive

### Requirement: Slurm AITRANSS launch policy is explicit
For Step 4 on Slurm, Moltage SHALL support exactly `direct` and `srun` AITRANSS launch modes. `direct` SHALL execute the configured absolute AITRANSS executable without a launcher. `srun` SHALL execute it through a configured and remotely verified absolute executable whose basename is `srun`; Moltage MUST NOT emit or execute a bare PATH-resolved `srun` command.

#### Scenario: Direct AITRANSS launch
- **WHEN** the selected Slurm profile uses `direct` launch mode
- **THEN** the Step 4 script executes the configured AITRANSS executable directly after preparing its configured environment and contains no `srun` invocation

#### Scenario: Verified srun launch
- **WHEN** the selected Slurm profile uses `srun` launch mode with a verified absolute `srun` path
- **THEN** the Step 4 script launches exactly one task through that absolute path and the configured absolute AITRANSS executable

#### Scenario: Missing srun path fails closed
- **WHEN** `srun` mode is selected but no absolute verified `srun` path is available
- **THEN** Moltage reports the incomplete launch configuration before remote mutation or submission and does not fall back to PATH lookup or `direct` mode

#### Scenario: Unsupported launcher type is rejected
- **WHEN** a Step 4 AITRANSS launch configuration names any mode other than `direct` or `srun`
- **THEN** Moltage rejects the configuration rather than translating it to another launcher

### Requirement: LSF resource requirement policy is structured and optional
Moltage SHALL support exactly two LSF resource-requirement modes: `site-default` and `span-rusage`. In `site-default` mode it SHALL omit `#BSUB -R`. In `span-rusage` mode it SHALL render only the existing structured `span[...] rusage[mem=...G]` expression from the validated host, rank, and memory fields. Moltage MUST NOT accept arbitrary `-R` text.

#### Scenario: LSF site default is requested
- **WHEN** an LSF profile uses `site-default` resource mode
- **THEN** newly rendered FHI-aims and AITRANSS scripts omit `#BSUB -R` while retaining their other configured job and site directives

#### Scenario: Existing structured LSF policy is requested
- **WHEN** an LSF profile uses `span-rusage` resource mode
- **THEN** newly rendered scripts retain the existing validated host-placement and memory-reservation behavior for their respective task resources

#### Scenario: Inapplicable resource fields are not represented as effective
- **WHEN** `site-default` mode is selected
- **THEN** the configuration UI clearly indicates that Moltage will not submit its host-placement or memory-reservation values as an LSF `-R` requirement

### Requirement: Existing server profiles remain loadable without invented values
The profile store SHALL migrate schema 1 through schema 10 without inventing scheduler admission selectors. Existing LSF profiles SHALL retain `span-rusage` behavior. Existing Slurm profiles SHALL retain the historical Step 4 `srun` intent only when an absolute `srun` candidate can be obtained from existing profile evidence and remotely verified; otherwise the profile SHALL remain loadable but Step 4 launch configuration SHALL be reported as incomplete until the user chooses `direct` or supplies a verified `srun` path.

#### Scenario: Legacy selectors migrate to site defaults
- **WHEN** a schema 1–10 profile is loaded
- **THEN** missing Slurm and LSF selector fields become blank site-default selections without changing its scheduler, runtime, resources, or remote workspace

#### Scenario: Legacy LSF resource behavior is preserved
- **WHEN** an existing LSF profile is migrated
- **THEN** its resource mode is `span-rusage` and its previously rendered resource semantics remain unchanged

#### Scenario: Legacy Slurm srun evidence is reusable
- **WHEN** an existing Slurm profile contains an absolute FHI-aims `srun` launcher or a scheduler-directory-derived `srun` candidate that passes remote verification
- **THEN** Moltage may reuse that exact path for the migrated Step 4 `srun` launch policy

#### Scenario: Legacy bare srun is not preserved silently
- **WHEN** an existing Slurm profile has no absolute `srun` candidate that can be verified
- **THEN** Moltage keeps the profile readable, marks Step 4 launch configuration incomplete, and requires explicit user completion instead of executing bare `srun`

### Requirement: Configuration evidence and execution state remain distinct
Automatic discovery SHALL report only evidence it actually verifies. Saving or applying scheduler site configuration SHALL NOT itself submit a job or claim runtime, scheduler, program, or scientific success.

#### Scenario: Discovery cannot determine an admission selector
- **WHEN** scheduler discovery verifies commands but has no authoritative evidence for account, partition, QoS, queue, or project
- **THEN** those fields remain blank and Moltage does not populate a probable value

#### Scenario: Offline validation does not claim cluster acceptance
- **WHEN** synthetic tests validate configuration, migration, or script rendering without connecting to a real cluster
- **THEN** the result is reported only as offline validation and no external-cluster acceptance is claimed
