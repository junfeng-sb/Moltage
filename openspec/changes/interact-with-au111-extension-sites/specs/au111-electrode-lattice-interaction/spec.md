## Purpose

规定 Moltage 如何在 Geometry viewer 中以稳定 lattice identity 预览、判定并添加 Phase 2 提供的 Au(111) extension sites，同时保持完整结构安全、可撤销编辑和既有 electrode provenance。

## ADDED Requirements

### Requirement: Extension interaction is available only for mutable accepted electrodes

Moltage MUST 只在当前 Geometry workspace 已具有 accepted two-side electrode placement 且 geometry 仍可编辑时提供 lattice-extension interaction。该交互 MUST 与现有 atom、bond、placement 和 measurement pick modes 互斥。Read-only、coordinate-only restriction 不允许 atom-count change 的 workspace，以及已经提交而成为 immutable input 的 geometry MUST NOT 接受 extension add。

#### Scenario: Editable accepted electrodes can enter extension mode

- **WHEN** 当前 Geometry workspace 已应用两侧 canonical Au pyramids、尚未提交且允许 atom-count edit
- **THEN** 用户可以在现有 Electrode Builder 中进入 lattice-extension interaction

#### Scenario: Immutable geometry cannot add an extension

- **WHEN** workspace 为 read-only、其 workflow 只允许 coordinate edits，或当前 geometry 已成为 submitted immutable input
- **THEN** lattice-extension interaction 不允许正式添加 Au，且不会改变 structure、Connectivity 或 provenance

### Requirement: Hover selection consumes a cached Phase 2 candidate set

Moltage MUST 缓存当前 electrode state 的完整 Phase 2 candidate set。Mouse movement MUST 只使用当前 camera projection 从该缓存中选择 pointer hit area 内最近的一个 candidate，并 MUST NOT 在每个 mouse-move event 中重新枚举 lattice candidates。Viewer MUST NOT 从 pointer coordinates、arbitrary XYZ 或当前 camera orientation 产生新的 lattice site。

#### Scenario: Repeated mouse movement does not enumerate candidates

- **WHEN** electrode geometry/state 未改变且用户在 viewer 内连续移动 pointer
- **THEN** 系统从同一 cached candidate snapshot 选择 mouse-nearest site，不重复执行 Phase 2 candidate enumeration

#### Scenario: No candidate is under the pointer

- **WHEN** pointer 未命中任何 cached candidate 的现有 viewer pick envelope
- **THEN** 系统不显示 lattice-extension preview，也不创建任意 XYZ candidate

#### Scenario: Overlapping projected candidates are deterministic

- **WHEN** 多个 cached candidates 的投影都处于 pointer hit area
- **THEN** 系统按 screen proximity、可见 depth 与稳定 candidate order 确定唯一 mouse-nearest candidate

### Requirement: Candidate availability preserves blocked lattice sites

Moltage MUST 保留 Phase 2 枚举的每个合法 lattice candidate，并针对当前完整 working geometry 将其判定为 `AVAILABLE` 或 `BLOCKED`。Au–Au 检查 MUST 沿用 Phase 2 已有 lattice clearance；candidate 与所有现有 non-Au atoms 的检查 MUST 沿用项目已有 atom-distance 与 vdW radius-sum steric semantics。该判定 MUST NOT 新增 radius data、global multiplier、scientific threshold 或从缺失数据进行 fallback。

#### Scenario: Candidate has full-geometry clearance

- **WHEN** Phase 2 candidate 在当前 working geometry 中通过既有 Au lattice clearance，且与每个 non-Au atom 均不违反既有 vdW radius-sum clearance
- **THEN** candidate 状态为 `AVAILABLE`

#### Scenario: Candidate overlaps the current structure

- **WHEN** Phase 2 candidate 在当前 working geometry 中与现有 Au 形成既有规则禁止的 separation，或与任一 non-Au atom 违反既有 vdW radius-sum clearance
- **THEN** candidate 状态为 `BLOCKED`，但 candidate identity 仍保留在当前 interaction snapshot 中

#### Scenario: Collision evidence cannot be evaluated

- **WHEN** 当前 geometry 缺少判定所需的合法 coordinates 或已配置 vdW radius data
- **THEN** 系统明确报告 interaction validation 不完整且不允许 add，不猜测 clearance 或替代 scientific data

### Requirement: One translucent preview communicates availability

Moltage MUST 复用现有 visual-only `PreviewAtom` presentation，同时最多显示一个 mouse-nearest candidate。`AVAILABLE` candidate MUST 显示为正常的半透明 Au preview；`BLOCKED` candidate MUST 显示为红色半透明 Au preview。Preview MUST NOT 成为 structure atom、Connectivity member、project provenance 或 calculation input。

#### Scenario: Available site uses normal Au preview

- **WHEN** mouse-nearest candidate 状态为 `AVAILABLE`
- **THEN** viewer 在其 current-world coordinate 显示一个正常半透明 Au ghost

#### Scenario: Blocked site uses red preview

- **WHEN** mouse-nearest candidate 状态为 `BLOCKED`
- **THEN** viewer 在相同合法 lattice site 显示一个红色半透明 Au ghost，以表明当前 geometry 下不可添加

#### Scenario: Preview lifecycle ends

- **WHEN** pointer 离开 viewer、未命中 candidate、用户退出 extension mode 或切换 workspace
- **THEN** lattice-extension preview 被清除，base geometry 保持不变

### Requirement: Hover preview shows canonical bonds and the current layer plane

Moltage MUST 在显示一个 lattice-extension ghost 时，同时以 visual-only 虚线显示该 candidate 按 unchanged Phase 2 canonical lattice-neighbor topology 添加后将获得的全部 Au–Au Connectivity edges。Moltage MUST 同时显示 candidate 所属 current rigid layer 的局部 triangular-lattice 网格平面；平面 MUST 以 candidate 为中心并向外逐渐降低 opacity。Bond targets、layer orientation 和 grid basis MUST 来自同一 canonical identity/current rigid frame，MUST NOT 由 GUI 使用 atom-distance guessing、pointer position、camera orientation 或 arbitrary XYZ 推导。所有 guide actors MUST 不可拾取，且 MUST NOT 成为 structure、Connectivity、provenance 或 calculation input。

#### Scenario: Available candidate shows normal topology guides

- **WHEN** mouse-nearest candidate 为 `AVAILABLE`
- **THEN** viewer 显示该 ghost、其全部 canonical post-add bond 虚线，以及与其 current layer 对齐的渐隐 triangular grid plane

#### Scenario: Blocked candidate keeps explanatory topology visible

- **WHEN** mouse-nearest candidate 为 `BLOCKED`
- **THEN** viewer 保留相同 canonical bond/layer guides 但使用红色状态样式，且 click 仍不添加

#### Scenario: Hover guide does not change candidate work

- **WHEN** pointer 在同一 cached candidate 上移动或 camera-only navigation 改变投影
- **THEN** guides 随当前 3D scene 正确显示且不触发 Phase 2 candidate enumeration、collision re-evaluation 或 geometry mutation

#### Scenario: Hover guide lifecycle ends with the ghost

- **WHEN** no-hit、mouse leave、drag、mode exit、workspace switch 或 cache invalidation 清除当前 ghost
- **THEN** predicted bonds 与 layer plane 同时清除，不留下可拾取或持久化状态

### Requirement: Click uses canonical identity and current-state revalidation

Moltage MUST 以 `side + layer_index + signed lattice_key` 或等价 canonical identity 处理 click。XYZ MUST 仅用于 ghost 和最终 current-world atom coordinate，MUST NOT 用于通过 float equality 重新识别 candidate。Click MUST 先从当前 Phase 2 candidate set 按 identity 解析 fresh candidate，再针对当前完整 geometry 重新判定 availability，最后才可调用 Phase 2 add operation。

#### Scenario: Available identity remains valid at click

- **WHEN** 用户点击 `AVAILABLE` preview，且同一 canonical identity 在当前 Phase 2 state 和完整 geometry 中重新验证仍可添加
- **THEN** 系统正式添加恰好一个对应 Au atom

#### Scenario: Blocked preview is clicked

- **WHEN** 用户点击状态为 `BLOCKED` 的红色 preview
- **THEN** 系统不调用正式 add，不改变 structure、Connectivity、provenance 或 history

#### Scenario: Cached candidate becomes stale

- **WHEN** hover 后 electrode occupancy、geometry 或 provenance 变化，使 cached identity 不再合法或不再 `AVAILABLE`
- **THEN** click 在任何 mutation 前被拒绝，不以 cached XYZ 或 float comparison 继续添加

### Requirement: Working coordinates follow the current rigid electrode frame

Moltage MUST 使用 persisted standard atom mapping 与 canonical lattice identity 将 candidate 映射到对应 side 的当前 rigid working frame。正式添加的 extension Au MUST 成为该 side electrode Connectivity 与 global atom mapping 的正常成员；后续对该 electrode 的既有 rigid rotation/transform MUST 同时移动 standard pyramid atoms 和所有 extension Au。Transform MUST NOT 改变 extension side/layer/lattice key、standard pyramid provenance、apex 或 reference-corner identities。

#### Scenario: Candidate is previewed after electrode rotation

- **WHEN** 一侧 standard pyramid 和现有 extensions 已一起完成有效 rigid transform，随后 cache 被刷新
- **THEN** 相同 candidate identity 映射到该 side 的 transformed world coordinate，另一侧和 viewer orientation 不改变其 lattice identity

#### Scenario: Added extension rotates with its electrode

- **WHEN** extension Au 已正式加入一侧 electrode，用户随后通过既有合法操作刚性旋转该侧
- **THEN** extension Au 与该侧 standard atoms 接受同一 transform，且其 provenance 和 atom mapping 保持不变

#### Scenario: Current side is not a valid rigid mapping

- **WHEN** current standard/extension atom mapping 不能证明为其 persisted lattice identity 的 rigid side geometry
- **THEN** 系统明确阻止 preview/add，不从当前 XYZ 猜测 lattice identity、frame 或修复方式

### Requirement: Successful add refreshes authoritative editable state

成功 add MUST 原子性地更新 current working structure、Connectivity、accepted electrode extension provenance 和 viewer，随后刷新 candidate snapshot。每次成功 add MUST 作为一次现有 Geometry Undo/Redo edit；blocked、stale 或失败 click MUST NOT 创建 history entry。Undo/Redo MUST 恢复与其 geometry 对应的 provenance 并重新计算 candidates。

#### Scenario: Successful add refreshes all dependent state

- **WHEN** 一个 current `AVAILABLE` candidate 被成功添加
- **THEN** 新 Au、其 lattice-neighbor Connectivity、extension provenance 和 displayed geometry 一致更新，旧 preview 清除，并从新 occupancy 重新计算 candidates

#### Scenario: Undo and Redo preserve interaction state

- **WHEN** 用户 Undo 或 Redo 一次成功 lattice extension
- **THEN** structure、Connectivity 和 extension provenance 一起恢复到对应 snapshot，candidate cache 失效并按恢复后的 state 重算

#### Scenario: Failed update is atomic

- **WHEN** add-time validation或后续 authoritative state preparation 失败
- **THEN** 当前 geometry、Connectivity、provenance、history 和 calculation eligibility 保持 add 前状态

### Requirement: Candidate cache follows electrode state and workspace ownership

Candidate cache MUST 属于其 Geometry workspace，并在正式 add、Undo/Redo、pyramid rebuild、electrode coordinate change 或 workspace switch 后失效并重新计算。Camera-only navigation MUST NOT 改变 Phase 2 candidate identities；projection MUST 使用当前 camera。左右 electrodes 和不同 layers MUST 保持独立 identity 与 availability。

#### Scenario: Geometry change invalidates the cache

- **WHEN** electrode 发生正式 add、Undo/Redo、rebuild、rotation 或其他 coordinate mutation
- **THEN** 下一次 interaction 使用重新计算的 candidate positions 和 availability，而不是旧 snapshot

#### Scenario: Workspace switch cannot reuse another workspace cache

- **WHEN** 用户从一个 Geometry workspace 切换到另一个再进入 extension interaction
- **THEN** preview 被清除，目标 workspace 从自身 electrode state 重新建立 cache

#### Scenario: Camera motion reprojects without lattice enumeration

- **WHEN** 仅 camera orientation、zoom 或 viewport 改变而 electrode state 不变
- **THEN** mouse-nearest selection 使用新 projection，但不重新生成 lattice identities

### Requirement: Extension interaction preserves frozen scientific semantics

Phase 3 interaction MUST NOT 修改 Phase 2 lattice generation/validation、standard pyramid generator、reference corners、`pyramid_layers`、`$nlayers`、geometry writer numeric precision、通用 radii tables、global Connectivity multiplier、scheduler 或 scientific input semantics。GUI MUST NOT 添加 free-XYZ Au 或修改 Phase 2 candidate coordinates。

#### Scenario: Interaction does not change downstream invariants

- **WHEN** 用户预览、添加、旋转、Undo 或 Redo lattice extensions
- **THEN** standard pyramid identities/reference corners 和既有 `$nlayers` value/source 保持不变，保存与提交继续使用现有 geometry precision 和 schema-8 provenance semantics
