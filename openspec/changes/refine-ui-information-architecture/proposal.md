## Why

当前 `Calculation` 菜单把 FHI-aims、ORCA 和本地分析入口混在同一层，ORCA 的结果查看与重新提交入口又分散在 Geometry workspace 和 Project Manager 之间，导致用户难以判断动作属于哪个程序及哪个项目阶段。与此同时，ORCA WBL 的持久化成功状态与 presentation 恢复存在分叉，Cluster Execution Settings、export resolution 和全局 dropdown 也缺少明确的交互反馈。

本 change 在不改变任何 scientific workflow、scheduler 语义或 persisted schema 的前提下，集中整理这些已经明确定位的 UI 信息架构和交互问题。

## What Changes

- 将 `Calculation` 重组为带 disabled section headings 的 `FHI-aims`、`ORCA` 和 `Local Analysis` 层级；Electron Density Difference 归入 FHI-aims，且本轮不创建 Gaussian 占位项。
- 为 FHI-aims 和 ORCA 分别提供 Step 1 入口；两者继续复用 `NewCalculationProjectDialog` 和同一 submission pipeline，只预选并锁定对应 engine。
- 从 ORCA 菜单移除 `Resubmit Optimization...` 与 `View WBL Transmission`，改为在 Project Manager 底部按当前 snapshot capability 显示/启用，并复用现有 resubmission 与 WBL workspace 链路。
- 将 ORCA WBL view eligibility 收敛为 `ProjectRecoverySnapshot` 的单一 capability；在 WBL 已成功且 Frequency 或其他后续状态为 active 时仍恢复 presentation。artifact 不可读时保留 persisted WBL success 和绿色 indicator，只禁用查看并报告可理解的状态信息。
- 将 Cluster Execution Settings 的 Save 设为现有 theme 的 primary/default button，并以 dialog-open baseline 检测 Cancel、window close 和 Escape 路径中的未保存修改，提供 Save、Discard、Cancel 选择和用户可读的字段清单。
- 将 export resolution UI 限定为 `1x / 2x / 4x / 8x` combo choices，保持 `ViewExportRequest.scale_factor` 与 renderer/export validation 不变。
- 在全局 theme 中为 `QComboBox`（包括其 subclasses）恢复可见的右侧 dropdown arrow，不影响非-combo menu indicators；在 Moltage development skill 中记录对应 UI 原则。
- 仅增加和更新直接相关的 synthetic focused tests；apply 阶段不运行 full offline suite。

## Capabilities

### New Capabilities

- `calculation-workflow-navigation`: 定义按计算程序组织的 `Calculation` 菜单、engine-locked Step 1、Project Manager 中 ORCA resubmit/WBL view 入口，以及 WBL success/presentation capability 的一致行为。
- `cluster-settings-editing`: 定义 Cluster Execution Settings 的 primary Save 和跨所有退出路径的未保存修改处理。
- `desktop-selection-affordances`: 定义 export resolution 的离散 choices 和所有 combo/dropdown selection controls 的可见 dropdown indicator。

### Modified Capabilities

无。当前 repository 尚无 main OpenSpec specs；上述 observable behavior 作为新的 capabilities 建立。

## Impact

- 主要 implementation surface：`tools/molecule_viewer_demo.py`、`src/moltage/gui/projects_dialog.py`、`src/moltage/app/project_recovery.py`、`src/moltage/gui/project_submission.py`、`src/moltage/gui/cluster_execution_dialog.py`、`src/moltage/gui/view_export.py`、`src/moltage/gui/theme.py`、必要的 theme icon resource，以及 `.agents/skills/moltage-development/SKILL.md`。
- 主要 focused tests：`test_ui_r3_main_window.py`、`test_molecule_viewer_demo.py`、`test_orca_wbl_view.py`、`test_orca_wbl_workflow.py`、`test_projects_dialog.py`、`test_orca_submission_recovery.py`、`test_cluster_execution_dialog.py`、`test_runtime_configuration_dialog.py`、`test_view_export.py` 和 `test_gui_theme.py`。
- 不修改 `CalculationProject` / manifest schema、remote project layout、ORCA/FHI-aims/AITRANSS scientific logic、scheduler/runtime boundary、resubmit submit-once/provenance semantics 或 export renderer。
- 用户可见的菜单位置和 dialog 退出提示会改变；已有项目、profiles、结果文件和计算状态保持兼容。
