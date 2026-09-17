## Purpose

为用户提供按计算程序和工作流阶段组织的统一入口，并确保 ORCA 项目动作与已持久化的项目状态、可读结果保持一致。

## ADDED Requirements

### Requirement: Calculation menu is organized by calculation program
系统 SHALL 将 `Calculation` 菜单按外部计算程序和本地分析分组，并使用不可点击的 section heading 区分这些类别。

#### Scenario: User opens the Calculation menu
- **WHEN** 用户打开 `Calculation` 菜单
- **THEN** 系统显示不可点击的 `External Programs` heading、`FHI-aims` submenu、`ORCA` submenu、不可点击的 `Local Analysis` heading，以及 `Local Tight-Binding Transmission...`
- **THEN** 系统不显示尚未实现的 Gaussian placeholder

#### Scenario: User opens the FHI-aims submenu
- **WHEN** 用户打开 `FHI-aims` submenu
- **THEN** 系统按 `Transmission` section 显示 `Step 1 — Molecule Optimization...`、`Step 2 — Molecule–Au Optimization...`、`Step 3 — Transport Convergence...` 和 `Step 4 — Transmission...`
- **THEN** 系统按 `Other Analysis` section 显示 `Electron Density Difference...`

#### Scenario: User opens the ORCA submenu
- **WHEN** 用户打开 `ORCA` submenu
- **THEN** 系统按 `Transmission` section 显示 `Step 1 — Optimization...` 和 `Step 2 — WBL Transmission...`
- **THEN** 系统按 `Other Analysis` section 显示 `Run Frequency...`
- **THEN** 系统不在该 submenu 中显示 `Resubmit Optimization...` 或 `View WBL Transmission`

### Requirement: Program-specific Step 1 actions use the existing submission flow
系统 SHALL 让 FHI-aims 与 ORCA 的 Step 1 入口复用现有新计算 dialog 和 submission flow，同时预选并锁定对应 calculation engine。

#### Scenario: User starts FHI-aims Step 1
- **WHEN** 用户选择 `FHI-aims > Step 1 — Molecule Optimization...`
- **THEN** 新计算 dialog 以 FHI-aims 作为不可更改的 calculation engine
- **THEN** 后续提交继续使用现有 FHI-aims submission behavior

#### Scenario: User starts ORCA Step 1
- **WHEN** 用户选择 `ORCA > Step 1 — Optimization...`
- **THEN** 新计算 dialog 以 ORCA 作为不可更改的 calculation engine
- **THEN** 后续提交继续使用现有 ORCA submission behavior

### Requirement: ORCA optimization resubmission is a project-scoped action
系统 SHALL 仅在 Project Manager 中为符合现有 resubmission 条件的 ORCA optimization project 提供 `Resubmit Optimization...` 动作，并保持既有 resubmission semantics。

#### Scenario: Selected project is eligible for resubmission
- **WHEN** 用户选择一个 failed 或 aborted、且具有可恢复结构和 optimization settings 的 ORCA optimization project
- **THEN** Project Manager 显示并启用 `Resubmit Optimization...`

#### Scenario: Selected project is not eligible for resubmission
- **WHEN** 当前选择不是符合既有 resubmission 条件的 ORCA optimization project
- **THEN** Project Manager 不允许执行 ORCA optimization resubmission

#### Scenario: User confirms an eligible resubmission
- **WHEN** 用户对符合条件的 ORCA optimization project 执行 `Resubmit Optimization...`
- **THEN** 系统使用新的 project identity 和现有 `_resubmit` naming behavior 提交任务
- **THEN** 系统保持既有 active-job cancellation、submit-once、input hash 和 provenance behavior

#### Scenario: Retrieved geometry is edited and submitted normally
- **WHEN** 用户取回 ORCA structure、编辑 geometry，并从普通新计算入口提交
- **THEN** 系统将其作为普通新 submission 处理，而不是 project resubmission

### Requirement: ORCA WBL viewing is a project-scoped capability
系统 SHALL 在 Project Manager 中依据当前 project snapshot 的统一 WBL viewing capability 显示和启用 `View WBL Transmission`，并复用现有 WBL presentation workspace。

#### Scenario: Successful WBL presentation is readable
- **WHEN** 选中的 ORCA project 已持久化 WBL success 且 presentation artifact 可读
- **THEN** Project Manager 显示并启用 `View WBL Transmission`
- **THEN** 执行该动作会打开现有 WBL transmission workspace

#### Scenario: Project has no viewable WBL result
- **WHEN** 选中的 project 未成功完成 WBL，或成功结果没有可读 presentation artifact
- **THEN** Project Manager 不允许打开 WBL transmission workspace

### Requirement: Persisted WBL success survives later ORCA activity
系统 SHALL 将已持久化的 ORCA WBL success 与当前 active ORCA step 分开恢复，使后续 Frequency 或其他 activity 不会抹去已完成 WBL 的状态。

#### Scenario: Frequency is active after WBL success
- **WHEN** ORCA WBL 已成功完成且 Frequency 当前处于 active state
- **THEN** WBL status indicator 仍显示成功
- **THEN** 如果 WBL presentation artifact 可读，`View WBL Transmission` 仍可用

#### Scenario: Successful WBL presentation cannot be read
- **WHEN** project 已持久化 WBL success，但 presentation artifact 缺失、损坏或不可读
- **THEN** project refresh 仍然完成且 WBL status indicator 仍显示成功
- **THEN** WBL viewing 被禁用，并向用户显示可理解的 artifact diagnostic
- **THEN** 系统不得把 presentation read failure 误报为 scheduler、program 或 WBL calculation failure
