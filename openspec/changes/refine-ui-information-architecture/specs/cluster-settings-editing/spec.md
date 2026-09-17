## Purpose

确保 Cluster Execution Settings 清楚区分保存与退出，并在用户通过任一关闭路径丢弃尚未保存的服务器设置前获得明确选择。

## ADDED Requirements

### Requirement: Save is the primary Cluster Execution Settings action
Cluster Execution Settings SHALL 使用现有 theme 的 primary/default button behavior 突出显示 `Save`，而不引入独立的 dialog-only styling。

#### Scenario: Dialog is opened
- **WHEN** Cluster Execution Settings 完成初始加载并显示
- **THEN** `Save` 使用当前 theme 的 primary/default button appearance
- **THEN** 初始加载的值构成此次 dialog session 的 unchanged baseline

### Requirement: Unsaved changes are detected on every dismiss path
系统 SHALL 在用户通过 `Cancel`、window close 或 `Escape` 尝试关闭 Cluster Execution Settings 时，将当前可编辑值与 dialog-open baseline 比较。

#### Scenario: User dismisses an unchanged dialog
- **WHEN** 当前可编辑值与 dialog-open baseline 相同，且用户选择 `Cancel`、window close 或 `Escape`
- **THEN** dialog 直接关闭且不显示 unsaved-changes prompt

#### Scenario: User dismisses a changed dialog
- **WHEN** 至少一个可编辑值不同于 dialog-open baseline，且用户选择 `Cancel`、window close 或 `Escape`
- **THEN** 系统显示以 `You have unsaved changes.` 开头的 prompt
- **THEN** prompt 以用户可读名称列出发生变化的字段
- **THEN** prompt 提供 `Save`、`Discard` 和 `Cancel` 三个动作

### Requirement: Unsaved-change choices preserve validation and cancellation semantics
系统 SHALL 让 unsaved-changes prompt 的三个动作分别复用现有保存校验、明确丢弃或返回编辑的行为。

#### Scenario: User chooses Save from the prompt
- **WHEN** 用户在 unsaved-changes prompt 中选择 `Save`
- **THEN** 系统执行现有 settings validation 和 save behavior
- **THEN** dialog 仅在 validation 与 save 成功后关闭

#### Scenario: Prompt Save fails validation
- **WHEN** 用户选择 `Save`，但现有 validation 拒绝当前值
- **THEN** dialog 保持打开以便用户修正设置
- **THEN** 系统不丢弃当前编辑

#### Scenario: User chooses Discard from the prompt
- **WHEN** 用户在 unsaved-changes prompt 中选择 `Discard`
- **THEN** dialog 关闭且本次未保存编辑不生效

#### Scenario: User chooses Cancel from the prompt
- **WHEN** 用户在 unsaved-changes prompt 中选择 `Cancel`
- **THEN** prompt 关闭且 settings dialog 保持打开

#### Scenario: Background operation currently prevents closing
- **WHEN** dialog 中现有 background operation 正在运行并触发现有 close guard
- **THEN** 系统保持该 guard，不因新增 unsaved-changes flow 绕过或终止该 operation
