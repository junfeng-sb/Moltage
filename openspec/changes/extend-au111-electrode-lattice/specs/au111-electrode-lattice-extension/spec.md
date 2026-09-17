## Purpose

规定 Moltage 如何从既有 canonical Au pyramid 的稳定 lattice identity 生成、验证并添加 Au(111) same-layer extension atoms，同时持久化其 provenance，且不改变标准 pyramid reference plane 或 AITRANSS `$nlayers` 语义。

## ADDED Requirements

### Requirement: Deterministic same-layer lattice identity

Moltage MUST 将每个 extension site 表达为一个特定 electrode `side`、`layer_index` 与 signed integer `lattice_key`。该 key MUST 使用现有 canonical triangular-lattice basis，三个分量之和 MUST 等于 `layer_index`，并且 MUST 允许标准 pyramid 边界外所需的负分量。Candidate Cartesian coordinate MUST 由当前 side 的 authoritative standard-pyramid lattice frame 确定性得到；鼠标位置、viewer orientation、屏幕投影或任意 XYZ 拟合 MUST NOT 决定合法 site 或其坐标。

#### Scenario: Same identity is deterministic across evaluations

- **WHEN** 相同 side、有效 rigid electrode geometry、layer occupancy 与 lattice identity 被重复评估
- **THEN** 系统返回相同的 candidate key、coordinate 与 deterministic ordering

#### Scenario: Rigid side transform preserves lattice semantics

- **WHEN** 一侧完整 electrode 经有效 rigid rotation 或 translation 后重新计算 candidates
- **THEN** candidate lattice identities 保持不变，coordinates 仅应用同一 side transform，另一侧 orientation 和 viewer state 不参与计算

#### Scenario: Invalid lattice frame is rejected

- **WHEN** 当前 mapped standard pyramid 已被非刚性修改或无法建立唯一的 authoritative lattice frame
- **THEN** candidate generation 明确失败且不从现有 XYZ 点、距离近邻或另一侧 electrode 猜测替代 frame

### Requirement: Apex and two-site seed candidates

Moltage MUST 对尚不能使用普通 boundary-edge rule 的 occupied layer 提供仅有的两种 seed behavior。只有一个 occupied site 的 apex layer MUST 返回该 site 在同层 triangular lattice 上的 6 个 nearest-neighbor sites；只有两个相邻 occupied sites 的 layer MUST 返回该 lattice edge 的两个等边三角形第三顶点。两个 occupied sites 不相邻时 MUST 明确拒绝该 seed state。只要 occupied sites 已形成可用于普通 boundary-edge extension 的 boundary，系统 MUST 停止使用 seed special cases。

#### Scenario: Canonical apex exposes six sites

- **WHEN** apex layer 仅包含 immutable apex site `(0, 0, 0)`
- **THEN** 系统以 canonical same-layer neighbor directions 返回恰好 6 个唯一 candidates，且每个 candidate 到 apex 的距离均为当前 canonical Au lattice spacing

#### Scenario: First apex extension exposes two triangle completions

- **WHEN** apex layer 仅包含 apex 与一个已添加且相邻的 occupied site
- **THEN** 系统返回该 edge 两侧恰好两个等边三角形第三顶点 candidates

#### Scenario: Two nonadjacent sites are not a valid seed

- **WHEN** 某 layer 只有两个 occupied sites 且其 lattice keys 不相邻
- **THEN** 系统明确报告 invalid occupancy state，不生成推测 candidates

#### Scenario: Completed seed transitions to boundary generation

- **WHEN** apex layer 的第三个 site 被添加并形成第一个 occupied triangle
- **THEN** 后续 candidates 只由通用 boundary-edge rule 产生，不继续应用 one-site 或 two-site seed rule

### Requirement: Generic boundary-edge candidate generation

Moltage MUST 在任意受支持 pyramid layer 上从 occupied same-layer lattice graph 的 boundary edges 生成 candidates。一个普通 boundary edge MUST 为两个相邻 occupied sites，且其两个合法 triangular third sites 中恰有一个已 occupied；另一个未 occupied site 才可成为 outward candidate。Interior edges、没有形成 boundary 的孤立 edge 以及非相邻 atom pairs MUST NOT 产生普通 candidates。结果 MUST 去重并使用稳定排序，且不同 layers 与左右 sides MUST 独立计算。

#### Scenario: Standard non-apex layer has finite boundary candidates

- **WHEN** 任一 non-apex canonical layer 尚未扩展
- **THEN** 系统仅从该 layer 的 perimeter boundary edges 返回有限、唯一且稳定排序的 outward candidates

#### Scenario: Interior edge does not generate a candidate

- **WHEN** 一个 occupied edge 的两个 triangular third sites 均已 occupied
- **THEN** 该 interior edge 不产生 candidate

#### Scenario: Repeated additions recompute the boundary

- **WHEN** 一个 boundary candidate 被成功添加
- **THEN** 系统使用更新后的 occupancy 重新计算该 side/layer 的 candidate set，并允许继续添加而不设人工数量上限

### Requirement: Uniform candidate validation and stale rejection

Seed 与普通 candidates MUST 经过相同 validation：candidate 必须仍属于请求的 side 和 layer、lattice key 当前未 occupied、coordinate 不得与其他 layer 或另一 electrode 的 Au 发生 overlap，且不得形成短于一个 canonical lattice spacing 的非法 Au–Au separation；同一 side 上由 canonical lattice identity 证明的 nearest neighbors 除外。Validation MUST 使用现有 lattice spacing 与明确的 numerical geometry tolerance，不得改用 generic covalent/vdW radii、global connectivity multiplier 或 anchor defaults。Add operation MUST 在 mutation 前针对当前 structure、connectivity 与 provenance 重新生成或等价地重新验证 candidate；stale、伪造或已失效 candidate MUST 被拒绝且状态保持不变。

#### Scenario: Occupied candidate is rejected

- **WHEN** candidate key 在选定 side/layer 已被 standard 或 extension Au 占据
- **THEN** 系统不返回或不接受该 candidate，且不添加重复 Au

#### Scenario: Cross-layer overlap is rejected

- **WHEN** candidate coordinate 与另一 layer 的已有 electrode Au 重合，或与非合法 lattice-neighbor Au 形成 sub-spacing separation
- **THEN** candidate 被排除且当前 structure、connectivity 和 provenance 不变

#### Scenario: Candidate cannot cross electrode sides

- **WHEN** 调用方尝试把 LEFT candidate 应用于 RIGHT electrode，反之亦然
- **THEN** 系统明确拒绝 side mismatch，且不得改写任一 side 的 occupancy

#### Scenario: Stale candidate is rejected after state change

- **WHEN** candidate 产生后 occupancy、structure 或 electrode provenance 已变化，使其不再属于当前合法 candidate set
- **THEN** add operation 在任何 atom mutation 前拒绝该 candidate，不使用旧 coordinate 继续添加

### Requirement: Candidate addition creates a traceable lattice atom

Moltage MUST 仅通过 validated candidate add operation 创建 extension Au。成功添加 MUST 将一个真实 Au 以 candidate 的确定性 coordinate 追加到 working structure，保存其 side/layer/signed lattice identity，将其连接到当前同一 side 中所有 lattice-distance-one neighbors（包括合法 same-layer 与 cross-layer neighbors），并保留既有 atoms、coordinates 与 bonds。不得按 Cartesian proximity 创建额外 bonds，不得连接另一 side。失败 MUST 为 atomic no-op。

#### Scenario: Valid candidate becomes one real Au

- **WHEN** 当前 candidate 在 mutation 前重新验证仍合法
- **THEN** working structure 仅追加一个 Au，extension occupancy 和 provenance 增加同一 lattice identity，`added_au_indices` 包含其 global index，并返回重算后的 candidates

#### Scenario: New atom connects to every lattice neighbor

- **WHEN** 新 site 在同一 side 的当前 occupied lattice 中有多个 lattice-distance-one neighbors
- **THEN** connectivity 恰好加入连接这些 neighbors 的 bonds，不遗漏 cross-layer lattice neighbor，也不因 spatial proximity 连接 non-neighbor 或另一 side

#### Scenario: Failed add is atomic

- **WHEN** candidate validation、mapping validation 或 structure update 任一失败
- **THEN** 原 structure、connectivity、standard metadata、extension metadata 与 reference identities 完全不变

### Requirement: Durable extension provenance and project persistence

Moltage MUST 在每侧 electrode provenance 中将 immutable standard-pyramid records 与 ordered user-added lattice-extension records 分开保存。每个 extension record MUST 保存 layer、signed lattice key、global atom index 和明确的 lattice-extension origin；side MUST 由所属 cluster record 唯一确定。Project persistence MUST round-trip 两侧不同数量和顺序的 extensions，不得只保存最终 XYZ 后重新猜测 identities。

#### Scenario: Extended project round-trips exactly

- **WHEN** 左右 sides 各自具有零个或多个 extensions 的 project 被序列化后重新读取
- **THEN** 每个 extension 的 side membership、addition order、layer、signed lattice key 与 global atom mapping 精确恢复，standard records 与 immutable corners 不变

#### Scenario: Malformed extension provenance is rejected

- **WHEN** persisted extensions 包含 duplicate lattice keys、duplicate/global cross-side mappings、invalid layer sums、non-Au mappings、standard-key collisions 或缺失 required fields
- **THEN** project load 明确失败，不删除记录、不按 coordinates 修复，也不把 extension 当作 standard atom

#### Scenario: Workflow copies extension provenance unchanged

- **WHEN** extended electrode 进入 normal/direct Step 3、resource-only resubmission、restart、recovery 或 self-energy retry
- **THEN** 当前 authoritative project operation 原样保留两侧 extension identities 和 mappings，不重新生成、重排或丢弃它们

### Requirement: Immutable standard reference plane with complete electrode membership

Same-layer extension MUST NOT 改变 standard pyramid `geometry_model`、`pyramid_layers`、apex identity、standard atom identities/mapping 或三个 ordered immutable reference-corner identities。Step 4 surface indices MUST 继续只由原始三个 standard reference corners 得到；完整 left/right electrode membership MUST 同时包含该 side 的 standard atoms 和 validated extension atoms。Restart、recovery 与 self-energy retry MUST 使用相同规则，且不得按 atom count、suffix、最低坐标或距离重新选择 reference corners。

#### Scenario: Reference corners survive asymmetric extension

- **WHEN** 用户只在一侧或在左右不同 layers 添加不同数量的 Au
- **THEN** 两侧 Step-4 reference-corner identities 与添加前完全相同，而完整 electrode membership 分别包含各自新增 extensions

#### Scenario: Extension mapping conflict blocks downstream use

- **WHEN** 当前 geometry 与 persisted extension global mapping、element、lattice coordinate 或 side membership 不一致
- **THEN** mapping-dependent Step 4、restart 或 retry 明确失败，不退回 fixed counts、surface search 或 standard-only membership

### Requirement: Extension does not change pyramid or nlayers semantics

Adding same-layer Au MUST NOT 改变 `pyramid_layers`，也 MUST NOT 创建、修改、重新推荐或重新标记 AITRANSS `$nlayers`。已有 `$nlayers` value 和 source MUST 按当前 project/task semantics 保持原样；缺失值也 MUST 继续缺失，直到用户按既有流程明确提供。

#### Scenario: Addition preserves an existing nlayers value

- **WHEN** 一个 electrode 在已有 `$nlayers` value/source 的情况下增加任意 same-layer extensions
- **THEN** 后续 Step-4 preparation 使用完全相同的 `$nlayers` value/source，且不因 extension count 或 footprint 改写它

#### Scenario: Addition does not fill missing nlayers

- **WHEN** selected pyramid size 当前没有 `$nlayers` 且用户添加 extension Au
- **THEN** `$nlayers` 仍未配置，新 Step-4 materialization 继续按既有规则要求用户输入，不从新增 atom 数量推导数值

### Requirement: Conservative schema compatibility

Moltage MUST 将现有 schema 7 project 迁移为没有 lattice extensions 的 current representation，并继续支持已接受的 older legacy migration behavior。Migration MUST NOT 从 geometry、atom suffix 或 Au count 推测 historical extensions，也 MUST NOT 重写既有 scientific inputs、outputs、attempt history 或 `$nlayers`。包含 extensions 的新 schema MAY 被旧 builds 视为 unsupported，但当前 build MUST 明确保存 schema evolution。

#### Scenario: Schema 7 project gains an empty extension collection

- **WHEN** current build 读取有效 schema 7 generated-electrode project
- **THEN** standard provenance 与 workflow state 保持不变，每侧 extension collection 明确为空

#### Scenario: Legacy project is not assigned inferred extensions

- **WHEN** schema 1–6 project 经现有 bounded legacy migration 读取
- **THEN** 系统保持现有 recoverable/ambiguous outcome，但不把任何 historical Au 自动标记为 lattice extension

#### Scenario: Existing calculation files remain unchanged

- **WHEN** project metadata migration 或 extended-project retry/recovery 发生
- **THEN** 已存在的 `geometry.in`、`control.in`、`tcontrol`、scheduler scripts、outputs 与 historical `$nlayers` 不因 schema migration 被重写
