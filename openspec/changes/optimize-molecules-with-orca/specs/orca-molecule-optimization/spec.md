## Purpose

定义 Moltage 如何从当前分子结构生成、提交、恢复和验证 ORCA geometry optimization，同时保持 scheduler、program termination、scientific convergence 与后续 WBL readiness 的证据边界。

## ADDED Requirements

### Requirement: Optimization settings are structured and explicit
Moltage SHALL 通过 structured controls 收集 ORCA method、basis、可选 dispersion、charge、multiplicity、optimization convergence、coordinate system 和 SCF convergence。Method 和 basis MUST 初始为空且由用户明确选择；Moltage MUST NOT 接受 raw ORCA keyword/directive text 作为这些设置的替代。

#### Scenario: Required electronic-structure choice is missing
- **WHEN** 用户未选择 method 或该 method 所需的 basis
- **THEN** Moltage 阻止 input generation，并指出缺失字段而不填入默认 method/basis

#### Scenario: Composite method controls dependent choices
- **WHEN** 用户选择一个 documented composite method
- **THEN** Moltage 只启用该 method 允许的 dependent settings，并且不额外生成其已内含的 basis 或 dispersion keyword

#### Scenario: Version-aware choice is unavailable
- **WHEN** 当前 runtime version 没有某个 method、basis 或 option 的支持证据
- **THEN** Moltage 不把该 choice 作为可提交选项，且不通过相近名称替代

### Requirement: Charge and multiplicity defaults remain user-controlled
新 ORCA optimization 的 charge SHALL 初始为 `0`，multiplicity SHALL 初始为 `1`。Moltage SHALL 根据当前元素核电荷、charge 和 multiplicity 检查 electron-count parity，并在不一致时阻止提交；Moltage MUST NOT 静默修改用户选择。

#### Scenario: Default values are compatible
- **WHEN** 当前结构的 electron count 与 charge `0`、multiplicity `1` parity 一致且用户确认提交
- **THEN** Moltage 可以按这些值生成 input

#### Scenario: Multiplicity parity is inconsistent
- **WHEN** electron count 和 selected multiplicity 的 parity 不一致
- **THEN** Moltage 报告 charge/multiplicity validation error，并要求用户修改或取消，不自动改成另一个 spin state

### Requirement: Resource settings map consistently to ORCA parallelism
Moltage SHALL 将 ORCA process count 与 scheduler task slots 和 `%pal nprocs` 保持一致，并为初始 ORCA workflow 使用每 task 一个 CPU thread 的 pure-process execution model。Walltime 和 scheduler memory SHALL 作为 job resources；`%MaxCore` SHALL 是可选的、用户填写的 per-process MB value。

#### Scenario: MaxCore is omitted
- **WHEN** 用户将 `%MaxCore` 留空
- **THEN** generated `.inp` 不包含 `%maxcore`，submission 仍可继续，并向用户说明 ORCA 将使用自身默认 memory behavior

#### Scenario: MaxCore is provided
- **WHEN** 用户提供合法正整数 `%MaxCore`
- **THEN** `.inp` 包含该 per-process MB value，且 Moltage 对 `nprocs × MaxCore` 与 scheduler memory 的明显冲突给出提示而不虚构精确 peak usage

#### Scenario: Parallel resources disagree
- **WHEN** scheduler task slots 与 `%pal nprocs` 无法形成一致配置
- **THEN** Moltage 在 remote mutation 前拒绝 submission，不生成两个不同的 process counts

### Requirement: ORCA optimization input is deterministic and reviewable
Moltage SHALL 由当前 structure 和已验证 structured settings 确定性生成 UTF-8 `orca_opt.inp`，显式使用 Angstrom Cartesian `* xyz <charge> <multiplicity>` coordinates，并仅包含当前 runtime version 支持的 reviewed keywords。相同 normalized inputs MUST 生成相同 bytes。

#### Scenario: Input is generated
- **WHEN** structure、runtime version 和所有 required settings 通过 validation
- **THEN** Moltage 生成包含 selected optimization、SCF、method/basis、parallel 和 optional memory settings 的完整 `orca_opt.inp`，并允许用户在提交前查看

#### Scenario: Unsupported or unsafe setting is supplied
- **WHEN** persisted data 或调用方提供不在 reviewed allowlist 中的 keyword/value
- **THEN** Moltage 拒绝 input generation，不将该内容原样写入 `.inp`

### Requirement: ORCA launches directly through its configured executable
Moltage SHALL 为 Slurm 和 LSF 生成使用既有 scheduler site selectors、resources 和 environment semantics 的 submit script，并通过 verified absolute ORCA executable 直接执行 `orca_opt.inp`、把 stdout/stderr 写入 `orca_opt.out`。Script MUST NOT 使用 `srun`、`mpirun`、runtime `which` 或 PATH-resolved bare `orca`。

#### Scenario: Slurm optimization script is rendered
- **WHEN** verified Slurm profile 和 ORCA settings 被用于 submission
- **THEN** script 包含适用的 nonblank Slurm site directives、environment preparation、direct absolute ORCA invocation 和与 `%pal` 一致的 task resources

#### Scenario: LSF optimization script is rendered
- **WHEN** verified LSF profile 和 ORCA settings 被用于 submission
- **THEN** script 包含适用的 nonblank LSF site directives、selected structured resource policy、environment preparation和 direct absolute ORCA invocation

#### Scenario: Runtime is incomplete
- **WHEN** executable/environment/version evidence 缺失、stale 或不一致
- **THEN** Moltage 在创建 remote project 或提交 scheduler job 前失败，不回退到其他程序 runtime 或 PATH command

### Requirement: Submission preserves existing remote-safety semantics
ORCA optimization SHALL 使用现有 managed remote workspace、safe project naming、atomic upload/hash verification、submit-once 和 uncertain-submission handling。每次 real remote mutation 或 scheduler submission MUST 由用户明确触发；timeout 或 transport ambiguity MUST NOT 自动重提 job。

#### Scenario: Verified submission succeeds
- **WHEN** input bundle 已在 memory 中完成、用户确认、remote files 原子上传且 scheduler 返回唯一 job ID
- **THEN** Moltage 持久化实际 submitted bytes/hash、scheduler kind、job ID、runtime evidence 和本次 settings

#### Scenario: Submission outcome is ambiguous
- **WHEN** scheduler submit command 的 transport outcome 不确定或 job ID 无法唯一解析
- **THEN** Moltage 把 attempt 标记为 `UNKNOWN` 并要求用户刷新/核对，不自动再次提交

### Requirement: ORCA projects are distinct from legacy FHI projects
Moltage SHALL 为 project 保存明确 workflow/engine identity。旧 project SHALL 保持既有 FHI-aims/AITRANSS four-step semantics；新 ORCA project SHALL 只包含已创建的 ORCA stages，并且不得预先把 future WBL stage 表示为存在、完成或隐式排队。

#### Scenario: Legacy project is loaded
- **WHEN** Moltage 读取没有 workflow discriminator 的历史 project/manifest
- **THEN** 它按 legacy FHI-aims/AITRANSS workflow 迁移和显示，既有 step state、input 和 result 不变

#### Scenario: ORCA optimization project is created
- **WHEN** 用户从当前 molecule structure 确认创建 ORCA optimization
- **THEN** project 记录 ORCA workflow identity 和 optimization stage，不创建 FHI Step 1–4 records 或 WBL success state

### Requirement: Optimization success requires layered evidence
Moltage SHALL 分开保存和显示 scheduler terminal state、ORCA normal termination、optimization convergence 和 optimized geometry validation。Optimization success SHALL 仅在 scheduler success、ORCA normal termination、documented optimization-converged marker 和有效 `orca_opt.xyz` 同时成立时报告；任一证据缺失 MUST 成为具体 failure 或 `UNVERIFIED` state。

#### Scenario: Optimization converges successfully
- **WHEN** scheduler reports success、`orca_opt.out` 包含 ORCA normal termination 与 optimization convergence evidence，且 `orca_opt.xyz` 通过 validation
- **THEN** Moltage 标记 optimization 为 successful，并分别保留每层 evidence

#### Scenario: ORCA terminates normally without convergence
- **WHEN** output 有 normal termination 但只有 maximum-cycle/nonconvergence evidence 或缺少 convergence marker
- **THEN** Moltage 不报告 optimization success，并明确区分 program completion 与 scientific nonconvergence/unverified convergence

#### Scenario: Scheduler fails or times out
- **WHEN** scheduler terminal state 是 failure、timeout 或 cancel
- **THEN** Moltage 保留该 scheduler outcome，不因残留 output marker 把 optimization 标记为 successful

### Requirement: Optimized geometry is validated before display or reuse
Moltage SHALL 要求 `orca_opt.xyz` 可读、非空、coordinates finite，且 atom count 和 ordered element symbols 与 submitted structure 一致。失败时 MUST 保留原 viewer geometry，并报告具体 mismatch；不得重排、补原子或使用 trajectory frame 静默替代 final geometry。

#### Scenario: Optimized XYZ is valid
- **WHEN** final XYZ 的 atom count、ordered symbols 和 finite coordinates 全部匹配
- **THEN** Moltage 允许在现有 molecule viewer 中显示该优化结构并将其标记为 verified result

#### Scenario: Optimized XYZ is malformed or mismatched
- **WHEN** final XYZ 缺失、为空、不可解析、含 non-finite coordinate 或元素顺序不匹配
- **THEN** Moltage 不替换当前 structure，并将 optimization result 标记为 invalid/unverified

### Requirement: Recovery preserves calculation artifacts and readiness boundaries
Moltage SHALL 支持刷新和恢复 ORCA optimization，保留 `orca_opt.inp`、`orca_opt.out`、有效 `orca_opt.xyz`、可选 trajectory 和 `.gbw` evidence。`.gbw` 缺失 MUST NOT 否定其他证据已确认的 optimization success，但 MUST 使 future WBL readiness 明确为 unavailable/incomplete。

#### Scenario: Optimization succeeds without GBW
- **WHEN** optimization success evidence 完整但 `.gbw` 缺失或无效
- **THEN** Moltage 保持 optimization successful，同时显示 future WBL input not ready

#### Scenario: User opens recovered optimized structure
- **WHEN** 已完成项目的 verified `orca_opt.xyz` 被取回
- **THEN** Moltage 在复用的 viewer 中显示结果，不改变原始 submitted structure evidence

### Requirement: Optimization does not imply minimum verification or WBL execution
未执行 frequency 时，Moltage SHALL 只声称 geometry optimization converged，不得声称 stationary point 是 minimum。Optimization completion SHALL NOT 自动提交 frequency 或 WBL transmission。

#### Scenario: Optimization completes with no follow-up selected
- **WHEN** optimization success 被确认且用户未明确启动后续 stage
- **THEN** project 保持在 optimization-completed state，不创建远程 frequency/WBL job，也不声称相关 scientific result
