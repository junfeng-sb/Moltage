## Purpose

定义 Moltage 如何按 server profile 独立配置、发现和验证 ORCA runtime，使 ORCA 与 FHI-aims/AITRANSS 可按需使用而不依赖特定集群、安装路径或版本名称。

## ADDED Requirements

### Requirement: Server settings separate shared cluster data from program hierarchy
Moltage SHALL 将 connection、remote workspace、scheduler command/configuration 和 site defaults 作为 shared server-level settings，并将 FHI-aims 与 ORCA 呈现为并列的 program settings 区域。AITRANSS SHALL 作为 FHI-aims transport toolchain 的子标签呈现在 FHI-aims program 区域内，而不是作为与 FHI-aims、ORCA 并列的 program。保存或使用一个 profile MUST NOT 要求与当前 workflow 无关的 runtime 已配置。

#### Scenario: Existing FHI profile has no ORCA runtime
- **WHEN** 用户打开或保存一个可用于既有 FHI-aims/AITRANSS workflow、但没有 ORCA runtime 的 profile
- **THEN** Moltage 保留该 profile 的既有可用性，在 FHI-aims program 页面内显示其 FHI-aims/AITRANSS 配置，并把并列的 ORCA program 显示为未配置

#### Scenario: ORCA workflow does not require FHI runtimes
- **WHEN** profile 的 shared server/scheduler settings 和 ORCA runtime 完整，但 FHI-aims 或 AITRANSS runtime 未配置
- **THEN** Moltage 允许该 profile 用于 ORCA workflow，不把无关 runtime 报告为 ORCA submission blocker

### Requirement: ORCA runtime configuration is minimal and profile-scoped
Moltage SHALL 为每个 server profile 独立保存 ORCA runtime，包括 canonical absolute POSIX `orca` executable path、明确的 environment preparation mode 及其所需配置、和已验证的 version evidence。Environment mode SHALL 仅使用现有受支持的 `None`、`Modules` 或受信任 `Setup Script` 语义；Moltage MUST NOT 要求独立的 basis、frequency、MPI launcher 或 WBL utility path 才能保存 ORCA runtime。

#### Scenario: User saves a manually configured executable
- **WHEN** 用户在 ORCA settings 中填写一个通过 remote validation 的绝对 `orca` executable，并选择适用的 environment
- **THEN** Moltage 将该 runtime 只保存到当前 profile，并在再次打开时恢复相同配置和验证证据

#### Scenario: Optional environment is blank
- **WHEN** ORCA executable 在 login environment 中可用且用户选择 `None`
- **THEN** Moltage 不生成 module load 或 setup-script command，也不要求额外 environment path

### Requirement: Runtime validation fails closed
Moltage SHALL 在把 ORCA runtime 标记为 usable 前验证 configured path 是 canonical absolute regular executable、basename 为 `orca`，并在所选 environment 中解析到相同 canonical path。Version detection SHALL 保存实际获得的 evidence；缺失、冲突或不受支持的 version evidence MUST NOT 被猜测或替换。

#### Scenario: Manual path is valid
- **WHEN** remote validation 确认 configured path 是可执行 regular file、environment 解析到同一路径并返回可解析的 ORCA version
- **THEN** Moltage 将 runtime 标记为 verified，并记录 exact executable path、version 和 detection source

#### Scenario: Configured path is stale or mismatched
- **WHEN** configured path 不存在、不可执行、不是 regular file，或 environment 中解析到另一个 executable
- **THEN** Moltage 报告具体不一致并阻止 ORCA submission，不回退到 PATH 中的其他 `orca`

#### Scenario: Version evidence is unavailable
- **WHEN** executable identity 可验证但 version 无法可靠解析
- **THEN** Moltage 将 version 标记为 unverified，并仅允许明确标记为 version-common 的设置；不得将其声称为某个受支持版本

### Requirement: Slurm ORCA discovery is bounded and site-neutral
用户明确启动 ORCA discovery 时，Moltage SHALL 只在 Slurm profile 上使用有界 evidence sources：login/current `PATH`、当前已配置 environment，以及名称匹配 ORCA 且数量受限的 module catalog candidates。每个 candidate MUST 在隔离的 environment 中经 `command -v`、canonical resolution 和 regular-executable validation；discovery MUST NOT 递归扫描 filesystem、读取任意 wrapper/setup script 内容、使用固定站点路径或选择固定 ORCA version。

#### Scenario: ORCA is available on login PATH
- **WHEN** login environment 中 `command -v orca` 返回一个通过 validation 的 canonical executable
- **THEN** discovery 将它作为带有来源和 version evidence 的 candidate 返回

#### Scenario: ORCA is exposed by a module
- **WHEN** bounded module catalog 中一个 ORCA-named module 在隔离 load 后暴露有效 `orca`
- **THEN** discovery 返回 executable、module load configuration 和实际 version evidence，而不依赖 module 名中的版本字符串作为唯一证据

#### Scenario: Multiple valid candidates exist
- **WHEN** discovery 找到多个不同 canonical executable 或 environment candidates
- **THEN** Moltage 展示所有有效候选并要求用户明确选择，不自动选择最高版本、最新名称或第一个结果

#### Scenario: No candidate is found
- **WHEN** 所有批准的 bounded sources 都未产生 verified candidate
- **THEN** Moltage 明确报告自动发现未成功并保留 manual configuration 入口，不启动 filesystem-wide search

### Requirement: Automatic ORCA discovery is limited to Slurm in this change
Moltage SHALL 为 Slurm profile 提供上述 ORCA automatic discovery；LSF profile SHALL 提供相同的 manual path/environment entry 和 remote validation，但本 change MUST NOT 声称或执行 LSF ORCA automatic discovery。

#### Scenario: User configures ORCA on LSF
- **WHEN** 当前 profile 使用 LSF 且用户打开 ORCA settings
- **THEN** Moltage 允许手动填写并验证 ORCA runtime，同时将 automatic discovery 明确显示为当前不受支持

### Requirement: Supported version evidence controls available input choices
Moltage SHALL 识别 ORCA `5.0.x`、`6.0.x` 和 `6.1.x` version families，并只展示对 detected version 有维护证据的 structured input choices。Version-neutral discovery MUST NOT 通过 executable location、module naming 或搜索顺序推断 version support。

#### Scenario: Supported family is detected
- **WHEN** runtime validation 获得属于受支持 family 的 exact version
- **THEN** ORCA settings 只提供该 family 的 documented compatible choices，并保存 exact version evidence

#### Scenario: Unsupported family is detected
- **WHEN** executable 返回不属于受支持 matrix 的 version
- **THEN** Moltage 保留该 discovery evidence但明确阻止未经验证的 ORCA input/submission，不把它静默映射为最接近版本

### Requirement: Legacy server profiles remain compatible
Profile schema migration SHALL 保留既有 connection、scheduler、site selectors、FHI-aims、AITRANSS 和 remote workspace behavior。缺少 ORCA fields 的旧 profile SHALL 迁移为 `ORCA runtime unresolved`，不得自动复制其他 runtime path、module 或 executable。

#### Scenario: Legacy profile is loaded
- **WHEN** Moltage 加载本 capability 之前保存的合法 profile
- **THEN** profile 保持原 workflow 可用、ORCA runtime 为空，并且仅在用户显式保存后写出新 schema

### Requirement: Discovery and saving do not imply execution success
Runtime discovery/validation SHALL 只执行用户明确触发的 read-only remote checks。保存 runtime MUST NOT 提交 scheduler job、运行 ORCA calculation，或声称 scheduler、program 或 scientific success。

#### Scenario: Offline or remote preflight succeeds
- **WHEN** runtime candidate validation 成功
- **THEN** Moltage 只报告 executable/environment/version evidence 已验证，并把实际 ORCA calculation 保留给单独的用户授权 submission
