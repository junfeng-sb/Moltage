## Context

See `proposal.md` for motivation and the three capability specs for observable behavior.

当前实现的相关职责已经存在，但入口和 eligibility 判断分散：`tools/molecule_viewer_demo.py` 构建 `Calculation` 菜单并承接 submission orchestration；`ProjectsDialog` 已具有 WBL view request chain，但 ORCA resubmit 仍由主窗口菜单发起；`ProjectRecoverySnapshot` 尚未表达 WBL presentation capability，且 ORCA recovery 只在部分 active-step 路径读取 presentation。Cluster Execution Settings、view export 和 global theme 各自已有可复用机制，不需要新 framework。

本 change 跨越 GUI composition、project recovery 和 theme resources，但不改变任何 persisted schema、scientific input、scheduler operation、remote layout 或 renderer contract。所有测试使用 synthetic/local state，不连接真实 HPC。

## Goals / Non-Goals

**Goals:**

- 以最小菜单重排明确 FHI-aims、ORCA 和 local analysis 的归属。
- 将 ORCA resubmit 与 WBL view 变为所选 project 的 context action，并复用既有业务链路。
- 让 persisted WBL success、presentation readability 和当前 ORCA active step 得到一致恢复。
- 复用现有 theme、validation 和 export contracts，补齐清楚的 desktop interaction feedback。
- 通过 focused synthetic tests 覆盖本 change 的直接行为。

**Non-Goals:**

- 不增加 Gaussian 或任何新 calculation engine。
- 不改变 ORCA resubmission、WBL computation、FHI-aims、AITRANSS、scheduler/runtime 或 scientific semantics。
- 不修改 manifest/profile schema、remote project layout、export renderer 或 scale validation contract。
- 不拆分 `MainWindow`、重构 project recovery architecture，或建立通用 menu/transaction framework。
- 不重做 Projects dialog、Cluster Execution Settings 或 theme 的整体视觉设计。

## Decisions

### 1. Recompose the existing Calculation menu without a menu framework

`tools/molecule_viewer_demo.py` 将继续在现有 menu construction location 创建 actions。增加一个局部 helper 生成 disabled `QAction` section heading，并按 spec 建立 `FHI-aims`、`ORCA` submenus 和 `Local Analysis` section。既有 action callbacks 与 enablement routing 保留，只改变 action ownership 和位置。

**Rationale:** 此 change 只有一个菜单需要分组；抽象 registry 或 declarative menu framework 会扩大范围并增加 historical architecture exception。

**Alternative considered:** 为所有 menus 建立统一 descriptor/registry。拒绝，因为没有当前需求证据且会造成无关重构。

### 2. Add an optional engine lock to the existing new-project dialog

`NewCalculationProjectDialog` 增加 optional `preselected_engine` input。提供时，dialog 选择对应已支持 engine 并禁用 engine selector；未提供时保持现有 behavior。两个 Step 1 menu actions仍进入 `_submit_new_optimization_project` 的共同 submission path，只传入各自 engine。FHI-aims restart 等既有 fixed-step flow 不被改写。

**Rationale:** engine-specific menu 需要避免用户在 dialog 中切换到另一程序，但 submission validation、settings collection 和 request construction 不应复制。

**Alternative considered:** 为 FHI-aims 和 ORCA 创建独立 dialogs。拒绝，因为会复制现有表单和 submission logic。

### 3. Move ORCA project actions while preserving their existing orchestration

`ProjectsDialog` 增加底部 `Resubmit Optimization...` 与 `View WBL Transmission` buttons。前者仅在现有 ORCA optimization resubmit eligibility 成立时显示/启用，并通过一个 typed signal 把 selected project context 交回主窗口；主窗口调用从现有 menu handler 提取的共同 resubmit orchestration。后者直接复用现有 `_view_selected_orca_wbl` → `_request_orca_wbl_view` → request signal chain。

原 ORCA menu actions 被移除，而 cancellation-before-resubmit、new project ID、`_resubmit` naming、submit-once、hash 与 provenance 逻辑仍由现有 backend/request path 完成。普通 retrieved-and-edited geometry 继续走 normal new submission。

**Rationale:** action location 应反映 selected project context；信号只搬运已有 context，不将 submission logic复制到 dialog。

**Alternative considered:** Project Manager 直接构建并提交 ORCA request。拒绝，因为会复制主窗口 orchestration 并扩大 GUI responsibilities。

### 4. Make WBL viewability a recovery-snapshot capability

`ProjectRecoverySnapshot` 增加唯一的 `can_view_orca_wbl` capability：要求 ORCA project 已持久化 WBL success 且 recovered presentation 非空。主窗口与 Projects dialog 均依赖该 capability，删除各自重复的 eligibility expression。

ORCA recovery 增加局部 best-effort presentation loader。只要 persisted state 表示 WBL success，context recovery 都尝试恢复 presentation，而不依赖当前 active step 是否仍为 WBL。presentation read error 被转换为 snapshot/status diagnostic，presentation 保持为空；错误不得反向改变 persisted WBL result、green indicator、scheduler state 或 program outcome。Frequency recovery 复用相同 helper，从而避免 optional WBL artifact failure 使整个 refresh 失败。

**Rationale:** “计算成功”与“presentation 当前可读”是两个不同事实；snapshot 是两个 UI surface 已共同消费的 recovery boundary。

**Alternative considered:** 让每个 UI action自行打开文件并捕获异常。拒绝，因为会继续造成 indicator 与 action eligibility 分叉。

### 5. Detect Cluster Settings changes from raw editable state

`ClusterExecutionDialog` 在 widgets、working runtime configuration 和初始 remote discovery display 完成后，记录一次 normalized editable-state baseline。state 由 stable field key、human-readable label 与当前 raw/editable value 组成，覆盖 scheduler resources/admission、output、modules 和 program/runtime fields。

dirty detection 不调用 save-time validated collectors，因为用户可能在输入尚不完整时关闭 dialog。`Cancel`、`Escape` 和 window close 汇入同一 guarded dismissal path：先保留现有 active-worker close guard，再比较 baseline；changed prompt 的 `Save` 复用现有 validation/save path，`Discard` 显式关闭，`Cancel` 返回 dialog。successful save 仍通过 `accept()` 关闭。`Save` 被设为 default/auto-default，以使用现有 `QPushButton:default` theme styling。

**Rationale:** snapshot diff 足以覆盖单 dialog session；transaction model 不会增加可见价值。

**Alternative considered:** 持续维护 per-widget dirty flags。拒绝，因为 programmatic initialization、dependent controls 和 working-copy data 容易产生 false positive。

### 6. Change only the export scale selector

`ViewExportDialog` 将 scale `QSpinBox` 替换为 `QComboBox`，四个 items 的 display text 为 `1x / 2x / 4x / 8x`，item data 为整数 `1 / 2 / 4 / 8`。summary 和 request construction 读取 selected item data。`ViewExportRequest`、format choices、render sizing 与 renderer implementation 不变。

**Rationale:** 离散 options 能准确表达受支持的 UI choices，同时保持 downstream contract。

**Alternative considered:** 保留 spin box 并跳过部分 values。拒绝，因为 control 仍暗示连续选择。

### 7. Supply themed combo arrows through the existing asset mechanism

在 `resources/icons/` 增加 light/dark dropdown SVG assets，并由 `theme.py` 的现有 `_theme_asset_url` 机制选择。global stylesheet 增加 `QComboBox::down-arrow` rule，并保留现有 `QComboBox::drop-down` hit area。selector 自然覆盖 `QFontComboBox` 等 subclasses；不添加或修改 `QToolButton` indicator selectors。

`.agents/skills/moltage-development/SKILL.md` 增加一条简短 UI 原则：任何作为 combo/dropdown 实现的 interactive selection control 必须保留右侧可见 indicator，除非存在 documented UI reason。

**Rationale:** 使用现有 theme asset pattern 可确保所有 light/dark themes 的对比度，不依赖 platform-native arrow 是否被全局 stylesheet 抹除。

**Alternative considered:** 用 Unicode glyph 或为每个 dialog 单独设置 icon。拒绝，因为 rendering/font 不稳定且会造成不一致。

### 8. Validation is focused and offline

apply 只运行 proposal 中列出的十个直接相关 test modules，并在 artifact 实施完成后运行一次 `openspec validate refine-ui-information-architecture --strict`。不运行 full offline suite；该 suite 留给下一次正式 pre-commit snapshot，符合当前 repository validation policy。

## Risks / Trade-offs

- **[Risk]** Menu text/order assertions may be brittle after exact hierarchy changes. → **Mitigation:** 更新现有 menu tests 以验证批准的 hierarchy、heading disabled state 和 action callback，而非额外引入 snapshot framework。
- **[Risk]** A stale or unreadable WBL artifact could still be confused with calculation failure. → **Mitigation:** recovery diagnostic 明确描述 presentation unavailable，并以 persisted step result 独立驱动 WBL indicator。
- **[Risk]** Cluster dialog initialization could be misclassified as a user edit. → **Mitigation:** baseline 只在全部 initial widget/working-copy population 完成后记录，并对 values 做稳定 normalization。
- **[Risk]** Programmatic dialog rejection could unexpectedly trigger a dirty prompt. → **Mitigation:** successful save 使用 `accept()`；明确 discard 使用内部 bypass，只让 user-initiated dismiss paths进入 prompt。
- **[Risk]** SVG arrow contrast may vary across six themes. → **Mitigation:** light/dark assets跟随 theme polarity，并在每个受支持 theme 的 stylesheet-focused test 中验证 resolved asset rule。

## Migration Plan

本 change 不需要 persisted-data migration。部署仅替换 UI composition/recovery behavior并增加 packaged icon resources；已有 projects、profiles、results 和 settings 原样读取。rollback 可恢复相关 Python、skill 和 SVG changes，不需要转换用户数据。
