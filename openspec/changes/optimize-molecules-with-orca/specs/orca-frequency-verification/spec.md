## Purpose

定义 Moltage 如何在已验证 ORCA 优化后按用户选择提交独立 frequency calculation，并以可审计证据报告 vibrational modes 与 ORCA-reported imaginary modes，而不改变优化结果。

## ADDED Requirements

### Requirement: Frequency is an optional post-optimization stage
Moltage SHALL 仅在 ORCA optimization 已成功且用户明确选择后创建 frequency stage。Frequency MUST 是独立 job/state；其 submission、failure、cancel、timeout 或 scientific result MUST NOT 覆盖已保存的 optimization success，也 MUST NOT 自动启动 WBL。

#### Scenario: User does not request frequency
- **WHEN** optimization 完成且用户未选择 frequency
- **THEN** Moltage 不生成 frequency input、不提交 job，并将 minimum verification 保持为 not performed

#### Scenario: Frequency job fails
- **WHEN** frequency scheduler 或 ORCA program execution 失败
- **THEN** frequency stage 报告自身失败，optimization stage 仍保持原 verified outcome

### Requirement: User selects analytical or numerical frequency mode
Moltage SHALL 提供 `FREQ` 和 `NUMFREQ` 两种明确 mode。只有当前 method/runtime combination 有 documented analytical-frequency support 时才能选择 `FREQ`；否则 `FREQ` MUST 被禁用并说明原因，而 `NUMFREQ` 可在有支持证据时选择并显示其较高计算成本。

#### Scenario: Analytical frequency is supported
- **WHEN** detected ORCA version 和 selected method 支持 analytical Hessian
- **THEN** 用户可以选择 `FREQ`，generated input 使用对应 reviewed keyword

#### Scenario: Analytical frequency is unsupported
- **WHEN** selected method 没有 analytical Hessian support evidence
- **THEN** `FREQ` 不可提交，Moltage 不尝试用未经验证的 analytical mode，并可提供受支持的 `NUMFREQ` 选项

### Requirement: Scientific settings are inherited from optimization
Frequency SHALL 使用 verified optimized geometry，并只读继承 optimization 的 method、basis、dispersion、charge 和 multiplicity。用户 MAY 为 frequency 单独选择 scheduler resources 和可选 `%MaxCore`，但 MUST NOT 在同一 project stage 中把 inherited electronic-structure settings 改成不同计算而仍声称它验证了该 optimization。

#### Scenario: Frequency settings are prepared
- **WHEN** 用户从 completed optimization 创建 frequency stage
- **THEN** inherited scientific settings 以只读方式显示，resources 和 optional `%MaxCore` 可按 frequency job 单独设置

#### Scenario: Persisted inherited settings disagree
- **WHEN** recovered frequency record 与其 source optimization 的 method、basis、charge 或 multiplicity evidence 不一致
- **THEN** Moltage 将 frequency result 标记为 invalid/unverified，不把它用于 minimum classification

### Requirement: Frequency input and launch follow the ORCA runtime contract
Moltage SHALL 确定性生成独立 frequency `.inp`，通过当前 profile 中 verified absolute ORCA executable 直接启动，并保持 scheduler task slots、`%pal nprocs`、environment 和 optional `%MaxCore` 的相同 validation rules。Runtime 或 source geometry stale 时 MUST 在 remote mutation 前失败。

#### Scenario: Frequency job is submitted
- **WHEN** source optimization、frequency mode、runtime 和 resources 均通过 validation，且用户确认 submission
- **THEN** Moltage 原子上传并提交独立 frequency input/script，保存 job ID、submitted hashes 和 source optimization identity

#### Scenario: Runtime changed after optimization
- **WHEN** frequency submission 时 configured ORCA executable/environment 不再通过 validation
- **THEN** Moltage 阻止 submission 并要求重新验证，不回退到 optimization 时未验证的 PATH state

### Requirement: Frequency result requires layered evidence
Moltage SHALL 分别记录 scheduler state、ORCA normal termination、`VIBRATIONAL FREQUENCIES` section presence/parseability、nonempty parseable `.hess`、mode count 和 ORCA-reported imaginary-mode evidence。只有实际取得的 evidence 才能决定 frequency status；缺失 evidence MUST 标记为 `UNVERIFIED` 或 failure，不得由 optimization success 推断。

#### Scenario: Complete frequency evidence has no reported imaginary modes
- **WHEN** scheduler/program 成功、frequency section 和 `.hess` 均有效，mode count 一致，且 ORCA output 未将任何 mode 标记为 imaginary
- **THEN** Moltage 报告 `FREQUENCY_COMPLETED` 和 `NO_IMAGINARY_MODES_REPORTED`，并保留 mode-count evidence

#### Scenario: ORCA reports imaginary modes
- **WHEN**完整 frequency evidence 中一个或多个 modes 被 ORCA 明确标记为 imaginary
- **THEN** Moltage 报告 `FREQUENCY_COMPLETED` 和 `IMAGINARY_MODES_REPORTED`，并保存对应 mode identifiers/values

#### Scenario: Frequency artifacts are incomplete
- **WHEN** output 缺少可解析 frequency section、`.hess` 缺失/为空/不可解析，或 mode counts 不一致
- **THEN** Moltage 不声称完整 frequency verification，并报告缺失或冲突的具体 evidence

### Requirement: Imaginary-mode classification follows ORCA evidence
Moltage SHALL 使用 ORCA output 明确报告的 imaginary-mode annotation 进行 classification，不得在本 change 中发明独立的 numerical threshold。用户可见文字 MUST 表述为“未发现 ORCA-reported imaginary modes”或等价限定语，不得把该结果单独表述为已证明 global minimum。

#### Scenario: Small signed frequency has no ORCA imaginary annotation
- **WHEN** parsed numerical value 接近零但 output 未按 ORCA format 标记为 imaginary
- **THEN** Moltage 不自行基于新阈值改判该 mode，并保留原始 parsed evidence供用户检查

### Requirement: Frequency recovery is read-only until user action
刷新、恢复或查看 frequency result SHALL 只读取当前 attempt 的 verified artifacts 和 scheduler/program evidence。它 MUST NOT 自动重新提交 frequency、修改 optimization geometry 或开始 WBL。

#### Scenario: User refreshes an incomplete frequency job
- **WHEN** recovery 发现 job terminal 但 scientific artifacts 不完整
- **THEN** Moltage 报告当前 evidence 和 `UNVERIFIED` state，并等待用户决定，不自动创建 replacement job
