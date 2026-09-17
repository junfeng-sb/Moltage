## Purpose

规定 Moltage 在不分发第三方 Au electrode coordinate files 的前提下，确定性生成 2–10 layer canonical Au pyramids，并在 Au Tool、project provenance 与 AITRANSS Step 4 之间保持可验证的 geometry、reference-plane 和 `$nlayers` 语义。

## ADDED Requirements

### Requirement: Canonical parameterized Au pyramid

Moltage MUST 使用 `MoltageAuPyramidV1` canonical triangular-lattice / tetrahedral geometry 生成标准 Au pyramid。`pyramid_layers` MUST 为 2–10 的整数，默认值 MUST 为 6；layer `n` 的标准 atom count MUST 为 `n(n+1)(n+2)/6`。所有 lattice-neighbor pairs MUST 使用 `2.88372 Å` nearest-neighbor spacing，该参数 MUST 记录为从既有两份 Au56 core coordinate evidence 得到的 Moltage geometry parameter，并且 MUST NOT 被解释为旧 `2.89 Å` connectivity cutoff、universal equilibrium distance 或 experimental constant。

#### Scenario: Supported layer counts generate canonical sizes

- **WHEN** 用户依次选择 2、3、4、5、6、7、8、9、10 layers
- **THEN** generator 分别产生 Au4、Au10、Au20、Au35、Au56、Au84、Au120、Au165、Au220，且每个标准 Au 具有唯一 lattice identity 和有限、无重复的 coordinates

#### Scenario: Generated nearest-neighbor geometry is stable

- **WHEN** 任意受支持尺寸的 pyramid 被生成或在不同会话中以相同输入重新生成
- **THEN** lattice-neighbor distance 与 `2.88372 Å` 在明确的 floating-point tolerance 内一致，并且 atom ordering、layer ordering、lattice identity、connectivity 与 base-corner ordering 均确定性相同

#### Scenario: Unsupported layer count is rejected

- **WHEN** `pyramid_layers` 小于 2、大于 10、不是整数或以其他方式无效
- **THEN** 系统明确拒绝生成且不替换当前有效 structure、preview 或 electrode metadata

### Requirement: Shared size with independent side placement

Au Tool MUST 提供一个同时控制左右 electrode 的 `Pyramid layers` 输入，初始值为 6。2–6 layers MUST 作为正常推荐范围；7–10 layers MUST 仍可生成，但界面 MUST 在生成前清楚提示计算成本显著增加且通常不推荐超过 6 layers。左右 sides MUST 使用相同 layer count、canonical shape、lattice geometry 和 generator version，但 MUST 分别沿各自 `binding atom -> contact Au` outward direction 完成 alignment，并且 MAY 具有不同的 deterministic roll/orientation。

#### Scenario: Default two-sided preview uses Au56 pyramids

- **WHEN** 用户在两个 eligible contact-Au sites 上以默认设置生成 preview
- **THEN** 左右均使用 6-layer Au56 canonical pyramid、分别复用各自已有 contact Au 作为 apex，并仅将每侧其余 55 个 Au 加入 preview

#### Scenario: Large pyramid warning does not block generation

- **WHEN** 用户选择 7–10 layers
- **THEN** Au Tool 显示 larger-electrode cost warning，并在用户保留该选择时继续允许 preview 和 Done

#### Scenario: Side orientation is not coupled

- **WHEN** 两侧 outward directions 或 steric roll optimization 产生不同 orientation
- **THEN** 系统接受两侧独立 alignment/roll，只要它们共享相同 generator version、layer count 和 canonical lattice geometry；系统不得为了强制 mirror、roll 或 orientation 相同而改变任一侧

#### Scenario: Layer selection invalidates stale preview

- **WHEN** 用户在应用 Done 之前改变 `Pyramid layers`
- **THEN** 旧 preview 和其 metadata 不再可应用，系统以新 layer count 重新生成可审核 preview

### Requirement: Durable immutable electrode identities

每侧标准 pyramid metadata MUST 至少包含 side、generator identity/version、`pyramid_layers`、每个标准 atom 的 layer 与 integer lattice identity、standard-pyramid membership、deterministic local-to-global mapping、contact/apex identity、roll，以及三个有序 immutable base reference-corner identities。三个 corners MUST 是 canonical outermost base layer 的三个 corner atoms，并 MUST 能稳定转换为当前 `geometry.in` 的 1-based `lsurc/lsurx/lsury` 或 `rsurc/rsurx/rsury` values。坐标变化或后续允许的 lattice extension 不得改变这些 identities。

#### Scenario: Generator records the standard pyramid topology

- **WHEN** 一侧 pyramid preview 被生成
- **THEN** 每个标准 Au 都有唯一 layer/lattice identity，apex 映射到已有 contact Au，三个 base corners 具有确定性顺序且属于最外侧 base layer

#### Scenario: Coordinate-only edit preserves identities

- **WHEN** 已应用的 electrode structure 经受现有允许的 coordinate-only rotation、restart edit 或 rigid placement transform，但 atom order 和 identities 未改变
- **THEN** persisted local-to-global and reference-corner mappings 保持权威，surface indices 从映射直接取得而不根据 world coordinates、fixed suffix 或 geometry heuristic 重新猜测

#### Scenario: Incompatible identity change fails explicitly

- **WHEN** 当前 structure 的 atom order、elements、mapping cardinality、side partition 或 reference-corner identities 与 persisted metadata 不一致
- **THEN** Step 4、restart 或 retry preparation 明确失败并说明 electrode provenance 不一致，不使用 Au count、nearest geometry 或另一侧 mapping 作为 fallback

### Requirement: Electrode provenance is persisted before downstream use

新创建的 normal Step-3 continuation、direct Step-3 start，以及由 Step-3/Step-4 restart 产生的新项目 MUST 保存同一结构化 two-side electrode provenance。Step 4 recovery/submission、task restart、resource-only Step-3 retry 和 explicit self-energy retry MUST 使用 project 中已保存的 metadata；这些路径 MUST NOT 依赖 packaged electrode templates、A/B variant 或固定 59/58/116 atom counts。

#### Scenario: Normal Step-3 continuation records both sides

- **WHEN** 用户从成功的 Step 2 应用两个 generated pyramids 并提交 Step 3
- **THEN** authoritative project update 保存两侧完整 generator metadata，并与实际提交的 `geometry.in` atom identities/mapping 一致

#### Scenario: Direct Step-3 start records the same schema

- **WHEN** 用户从 eligible pre-optimized imported contact-Au geometry 应用两个 generated pyramids并直接提交 Step 3
- **THEN** project 使用与 normal continuation 相同的 two-side metadata schema，不创建 A/B-specific provenance branch

#### Scenario: Downstream operations use persisted mapping

- **WHEN** 成功 Step 3 被恢复、重新打开、resource-only resubmit、创建 restart draft 或进入 self-energy retry
- **THEN** surface corners 与完整 electrode membership 来自 authoritative project metadata，并在 remote geometry hash/atom identity 验证后使用

### Requirement: Evidence-gated AITRANSS nlayers

`pyramid_layers` 与 AITRANSS `$nlayers` MUST 保持独立。系统 MUST NOT 使用 `nlayers = pyramid_layers`、`nlayers = pyramid_layers - 2`、所有尺寸固定为 `4` 或其他未经核验的公式。自动推荐 MUST 仅适用于已有明确记录、经人工审核的 FHI-aims/AITRANSS manual 与合法 `electrodes.library` header evidence 所覆盖的 pyramid size；该 evidence MUST 说明对应的 library cluster core、额外 adatoms 与推荐 `N_a/$nlayers`。当前 reviewed evidence table MUST 为 `4 -> 2` 与 `5 -> 3`，并将来源标记为 `AIMS_RECOMMENDED`。6 layers MUST 以用户明确决定的 `4` 初始化并标记为 `USER_SPECIFIED`，不得表述为 AIMS/library recommendation。2、3、7、8、9、10 layers MUST 保持未设置，并要求用户在 Step-4 editor 中明确填写正整数后才能生成和提交新 `tcontrol`。所有预填值 MUST 可编辑；用户编辑或填写后，来源 MUST 为 `USER_SPECIFIED`。

#### Scenario: Reviewed evidence covers a selected size

- **WHEN** selected `pyramid_layers` 为 4 或 5
- **THEN** Step-4 editor 分别自动填入 `$nlayers=2` 或 `$nlayers=3`，并清楚标识为 `AIMS recommended` editable default 而不是 geometry layer count

#### Scenario: Six-layer default is explicitly user-specified

- **WHEN** selected `pyramid_layers` 为 6 且用户尚未编辑 `$nlayers`
- **THEN** Step-4 editor 自动填入 `$nlayers=4` 并标记为 `User specified`，且不得显示或记录为 AIMS/library recommendation

#### Scenario: Adatoms do not change the mapped canonical size

- **WHEN** reviewed AITRANSS `051414` fcc(111) evidence is evaluated
- **THEN** `natoms22` variants 的共同 Au20 core 与两个变化 adatoms 映射到 4 layers / `$nlayers=2`，`natoms37` variants 的共同 Au35 core 与两个变化 adatoms 映射到 5 layers / `$nlayers=3`，系统不以 22 或 37 作为 canonical pyramid atom count

#### Scenario: Evidence does not cover a selected size

- **WHEN** selected `pyramid_layers` 为 2、3 或 7–10
- **THEN** Step-4 editor 不沿用 `4` 或从 pyramid size 推导值，`$nlayers` 保持未设置，并提示用户参考其合法 FHI-aims/AITRANSS installation 中相似 Au cluster 的 `electrodes.library` header 手动填写

#### Scenario: User editing changes the displayed source

- **WHEN** 用户修改一个 `AIMS recommended` default 或填写一个原本未设置的 `$nlayers`
- **THEN** editor 接受有效 positive integer、将来源标记为 `User specified`，并使用该值生成后续新 `tcontrol`

#### Scenario: Missing nlayers blocks only new Step-4 materialization

- **WHEN** generated electrode 的 `$nlayers` 仍未设置
- **THEN** 已完成 Step 3 的 scheduler/program/scientific state 保持不变，但新的 `tcontrol` preview/submission 明确保持 incomplete，直至用户提供有效值

#### Scenario: Historical nlayers is preserved

- **WHEN** existing historical `tcontrol` 或 immutable prior attempt 已包含 `$nlayers`
- **THEN** viewing、recovery、retry provenance 和 historical files 保留该值及原始 bytes；系统不因新 recommendation table 或 pyramid metadata 重写它

### Requirement: Conservative historical project migration

Moltage MUST 继续读取受支持的 legacy project manifests，但 MUST 将 legacy electrode evidence 与新 generated provenance 明确区分。只有 legacy manifest、historical workflow invariants 和已验证 calculation files 能够唯一恢复两侧 electrode membership、contact/apex 和 reference corners 时，系统才可构造兼容 metadata；任何 ambiguity 或 evidence 缺失 MUST 明确阻断依赖该 mapping 的新 Step 4、restart 或 self-energy retry。Migration MUST NOT 修改已有 `geometry.in`、`control.in`、`tcontrol`、attempt files 或历史 `$nlayers`。

#### Scenario: Complete legacy direct-Step-3 mapping is recoverable

- **WHEN** legacy project 已保存两组完整、互不重叠、顺序有效的 Au59 local-to-global mappings
- **THEN** migration 可将它们标识为 legacy provenance，并从历史已知 mapping 唯一保留 contact/apex、electrode membership 和 base reference corners，而无需保留或加载旧 coordinate resources

#### Scenario: Legacy mapping is ambiguous

- **WHEN** legacy normal-flow project 缺少足以唯一确定任一侧 contact/apex、membership 或 reference corners 的 evidence
- **THEN** 读取项目及已有结果仍按不依赖该 mapping 的现有能力进行，但需要新 surface mapping 的操作明确失败，并说明无法安全迁移；系统不得猜测 mapping

#### Scenario: Legacy calculation files remain byte-preserved

- **WHEN** legacy manifest 在内存中迁移或之后因其他授权状态变化被保存为新 schema
- **THEN** migration 仅改变 managed project metadata，不改写既有 scientific input/output、submit scripts、attempt history 或 `$nlayers`

### Requirement: No distributed Au coordinate templates

Moltage 的 active standard-pyramid workflow 和 packaged distribution MUST NOT 包含或加载旧 `au_6layer_variant_a.xyz`、`au_6layer_variant_b.xyz` 或其他从 FHI-aims `electrodes.library` 复制的 electrode coordinate file。生成必须离线、确定性且仅依赖 Moltage 自有 generator parameters and user-selected layer count。

#### Scenario: Clean installation generates electrodes without template files

- **WHEN** 用户在不具有任何 packaged Au electrode `.xyz` 的 clean installation 中选择 2–10 layers
- **THEN** Au Tool 能生成相应 canonical pyramids，且正常 preview/Done 不访问 FHI-aims distribution、real server 或 external coordinate resource

#### Scenario: New six-layer behavior is intentionally Au56

- **WHEN** 新项目使用默认 6-layer setting
- **THEN** 每侧标准 pyramid 仅包含 56 个 Au including the reused apex，并且系统不追加旧 Au59 variants 的三个 asymmetric adatoms
