## Context

See `proposal.md` for motivation and `specs/au111-electrode-lattice-interaction/spec.md` for required behavior.

Phase 2 已提供 `AuLatticeExtensionSite(side, layer_index, lattice_key)`、deterministic candidate enumeration、stale-safe immutable add result 和 schema-8 provenance。现有 Geometry viewer 已具有 40 ms hover throttling、click/drag 区分、camera-aware atom projection、互斥 pick modes、visual-only translucent `PreviewAtom`、per-workspace `GeometryEditHistory` 以及 active-workspace routing。

两个 current-state constraints 决定本设计：现有 `steric_clearance_for_point` 有意排除 binding atom 的 graph-distance 0–2 atoms，因此不能直接证明 extension candidate 对全部 non-Au geometry 安全；而 coordinate-only rotation 只更新 working structure，accepted `AppliedElectrodePlacement` 仍保存 authoritative canonical placement/provenance。Phase 3 必须复用现有 clearance semantics并桥接这两个表示，但不得修改 Phase 2 lattice generation 或建立 Cartesian identity fitting。

## Goals / Non-Goals

**Goals:**

- 用一个窄的 interaction-evaluation layer 将 Phase 2 canonical candidates 映射到 current working geometry并标记 `AVAILABLE`/`BLOCKED`。
- 让 viewer 只处理 opaque stable identity、current coordinates、availability color 和 projection picking，不拥有 lattice science。
- 在 hover path 中避免 Phase 2 enumeration 与 full-geometry collision scan，并把正式 add 纳入现有 atomic Geometry Undo/Redo。
- 保持 working coordinates 可独立刚性变化，同时让 accepted canonical state 继续承担 Phase 2 add/provenance authority。

**Non-Goals:**

- 不修改或优化 Phase 2 candidate generation、boundary/seed rules、stale validation或 manifest model。
- 不增加新的 collision radius、multiplier、distance cutoff、radii resource或 scientific parameter。
- 不实现 arbitrary Au placement、extension delete/move、manual lattice editing、non-rigid recovery或 canonical-to-XYZ fitting。
- 不改变 generic torsion algorithm、geometry writer precision、workflow/scheduler、FHI-aims/AITRANSS input、`pyramid_layers` 或 `$nlayers`。
- 不处理 corrupt manifest enumeration failure 或 Phase 2 enumeration 的进一步性能优化。

## Decisions

### 1. Add one Phase-3 interaction snapshot without changing Phase 2 records

在 junction/application boundary 增加窄的 immutable interaction records：每项包含 Phase 2 `AuLatticeExtensionSite` identity、current-world coordinate、`AVAILABLE | BLOCKED` 和可选 blocking evidence；snapshot 属于一个 Geometry workspace。生成 snapshot 时只调用一次 Phase 2 enumeration，再将结果映射和分类。

不在 `AuLatticeExtensionCandidate` 上增加 UI state，因为 Phase 2 candidate 表达 lattice legality，而 `BLOCKED` 是依赖当前完整 working geometry 的 transient interaction state。也不把该状态写入 schema 8、project manifest 或 server profile。

### 2. Preserve canonical authority and apply only a one-way current-frame mapping

`AppliedElectrodePlacement` 继续保存 canonical standard/extension structure，供 Phase 2 enumeration、fresh identity resolution 和 provenance conversion使用。Phase 3 从每侧 persisted standard local-to-global mapping中取 current working structure 的 `(0,0,0)`、`(1,0,0)`、`(0,1,0)`、`(0,0,1)` sites，复用 `AuLatticeFrame.from_standard_mapping` 建立当前 side frame并验证完整 mapped standard geometry仍为刚性；现有 extensions也必须落在各自 identity 的 current-frame coordinate within现有 Phase 2 geometry tolerance。

每个 Phase 2 candidate只借用 identity，current-world coordinate由 `current_frame.coordinate(identity.lattice_key)` 单向得到。不从 working XYZ反解 key，不更新 canonical candidate float，也不持久化 transform matrix。

Click 时先从新枚举的 canonical Phase 2 candidates按 identity取得 fresh typed candidate，并让现有 `add_lattice_extension`完成 canonical revalidation/provenance更新。Phase 3随后把同一 identity的 current-frame coordinate追加到 working structure，并复用 backend返回的新 Connectivity和applied record。这保留了“canonical provenance + coordinate-edited working geometry”的当前模型，也避免修改 `AppliedElectrodePlacement` 的 immutable standard-prefix invariant。

### 3. Use existing Au lattice clearance and existing vdW radius-sum semantics

Interaction classification先对 mapped current coordinate调用现有 `has_au_lattice_clearance`，检查当前 working structure中的全部 Au；合法 nearest-neighbor spacing自然通过，coincident/sub-spacing Au按 Phase 2规则成为 `BLOCKED`。随后对全部 non-Au atoms执行现有 steric formula：

```text
clearance = distance(candidate, atom) - (vdW_radius(Au) + vdW_radius(atom))
AVAILABLE iff every non-Au clearance >= 0
```

实现应在现有 steric owner中增加一个窄的无 graph-distance exclusion入口或复用其底层 radius/distance primitive，而不能直接调用会排除局部 atoms 的当前 placement helper。数值等于零时继续沿用既有 numerical handling；不引入 extra tolerance、scale或 fallback。任何必需 element radius缺失或 coordinate invalid均使 snapshot构建显式失败并禁止 add，而不是把 unverified site显示为可用。

被判为 `BLOCKED` 的项仍保留在 snapshot；blocking只控制颜色和 addability，不改变 Phase 2 candidate set。

### 4. Give visualization opaque pick targets, not lattice responsibilities

给 `MoleculeViewerWidget`/`MoleculeScene` 一个小型 projection-target输入：opaque stable token、world coordinate和Au visual radius。Viewer复用现有 pointer throttle、render scale、projected atom radius/minimum pick radius、depth handling和stable order，在 extension pick mode下返回唯一 token。它不导入 lattice generator，不计算 candidate identity，也不执行 collision检查。

主窗口将 token保存为 `AuLatticeExtensionSite` 并从 workspace snapshot查找 interaction record。只有选中 identity变化时才更新现有 `PreviewAtom` pipeline：`AVAILABLE`使用当前普通Au preview color，`BLOCKED`使用红色，两者沿用现有 preview opacity/radius。同一时刻传入零或一个 preview；ghost保持 unpickable。Mouse leave、mode exit、no-hit和workspace switch清除 token和ghost。

不使用 VTK picker去拾取 ghost，因为 ghost故意不可拾取；click使用与hover相同的 cached projection-target selection，避免通过 ghost XYZ找回identity。

### 5. Centralize cache invalidation at existing authoritative mutation paths

在 `_GeometryWorkspace` 保存 transient snapshot和当前 hover identity，不写磁盘。一个集中 helper负责清空 preview/targets并标记cache invalid。进入extension mode或目标workspace切换后按需重建一次。

以下现有成功路径必须调用invalidator：electrode Done/rebuild或structure replacement、formal extension add、`_commit_working_structure` coordinate mutation、`_restore_geometry_edit_state` Undo/Redo、workspace route/switch。Camera motion不失效，因为它不改变candidate identities/coordinates；viewer在pick时使用当前camera重新projection。Snapshot构建或validation失败显示明确operation status且不给viewer targets。

### 6. Revalidate both identity and availability before one atomic edit

Click handler仅接受当前hover的stable identity，并按以下顺序准备结果：

1. 再检查active workspace仍可执行atom-count edit且未成为submitted immutable geometry；
2. 重新枚举Phase 2 canonical candidates并按identity解析fresh candidate；
3. 从current working geometry重建side frame并重新执行完整`AVAILABLE/BLOCKED`判定；
4. 只有仍为`AVAILABLE`才调用Phase 2 add并构造新的working structure/Connectivity/applied record；
5. 完整next state准备成功后，向现有Geometry history压入一次previous snapshot、替换viewer和authoritativeworkspace state、清除old ghost并重建candidate snapshot。

`BLOCKED` click只保留红色preview并给出简短status；stale、frame-invalid和backend failure均为no-op且不写history。Viewer presentation failure也不得留下partially committed model state。

### 7. Let existing Connectivity carry extension atoms through rigid rotation

Phase 2已将新增Au连接到该side所有lattice-distance-one neighbors。现有bond rotation按Connectivity component执行刚性transform，因此extension atom一经添加便与对应electrode component共同移动；Phase 3不增加独立rotation implementation或side transform state。Rotation完成后authoritative working-structure path使cache失效，下一snapshot通过current standard mapping重建frame并验证extensions。

若用户操作造成standard/extension mapping非刚性或extension未随side移动，frame validation显式禁用preview/add。系统不改变identity、reference corners或provenance来适配该geometry。

### 8. Reuse current editability and history ownership

Au Tool中增加一个仅在accepted two-side placement后可用的checkableextension control，并接入现有互斥`_ViewerPickMode`。Active Geometry routing沿用current read-only、coordinate-only、submission-running和workflow-state gates；不新建project-leveleditability model。

每次成功add是一个Geometry history operation，snapshot同时保存working structure、Connectivity和updated `AppliedElectrodePlacement`，因此Undo/Redo自然恢复extension provenance和global mapping。Preview、candidate snapshot和hover token是derived transient state，不进入history，恢复后重新计算。

### 9. Derive hover guides from canonical topology and the current rigid layer frame

Phase-3 interaction record为每个 cached candidate 增加纯派生的 guide facts：按 Phase 2 当前使用的同侧 `lattice_distance_squared == 1` topology 排序得到的 existing global Au neighbor indices/current coordinates，以及从该 side 的 current `AuLatticeFrame` 得到的两个 independent same-layer basis vectors。Focused tests必须把这些 predicted neighbor indices与 unchanged Phase-2 add result实际新增的 Connectivity edges逐项比较，防止 GUI 或 Phase 3 建立第二套 distance-based bond rule。

`MoleculeScene` 接收一个 immutable visual guide，由独立、不可拾取的 actors批量绘制：candidate到每个 predicted neighbor的虚线，以及以 candidate为中心的有限 triangular-lattice patch。Patch只使用 supplied current-world center和same-layer basis；中心 opacity最高，外圈通过 per-vertex alpha平滑降至透明，grid lines沿三个 triangular-lattice directions。其 finite visual extent、opacity和line width只是 presentation constants，不参与 candidate legality、Connectivity或任何 scientific input。

主窗口在 hovered identity变化时从 cached interaction record构造一个 guide；`AVAILABLE`使用跨light/dark背景保持清晰的蓝色系 guide，`BLOCKED`复用红色状态色。相机运动由既有3D actor自然重投影，不更新guide geometry或candidate cache。现有 ghost cleanup helper同时清除guide；click fresh validation仍只消费canonical identity，不读取任何rendered line/plane数据。

## Risks / Trade-offs

- [Free-atom vdW radius sums may conservatively block a scientifically plausible nearby site] → 红色preview保留site可见性，但不允许绕过；本change不调参，任何未来scientificrule变化必须独立审核。
- [Canonical applied geometry and rotated working geometry intentionally differ] → 只做identity到current rigid frame的单向映射；Phase 2仍在canonical state上revalidate，current addability另在working geometry上revalidate。
- [A missed invalidation could expose stale projection or status] → 所有authoritative geometry/history/workspace paths集中调用一个invalidator，click仍执行independent fresh revalidation作为最终防线。
- [Many candidates increase projection work] → Mouse move只遍历bounded cached snapshot并沿用40 ms throttle；不在本change优化Phase 2 enumeration或增加candidate limit。
- [Red may have variable contrast across themes] → 使用明确高饱和红色并通过existing translucent preview pipeline做light/dark synthetic presentation checks，不改变global theme palette或atom colors。
- [Viewer update can fail after model preparation] → 先构造完整immutable next state，按现有structure-replacement error boundary提交；失败不push history或更新workspace authority。

## Migration Plan

1. 增加current-frame mapping和full-geometry availability evaluator，以synthetic rigid rotations、Au/non-Au overlap与missing-radius cases验证，不改Phase 2 API。
2. 增加viewer projection targets、stable token hover/click和normal/red single-preview lifecycle tests。
3. 在existing Electrode Builder与per-workspace state接入mode、cache invalidation、fresh click revalidation和Geometry Undo/Redo。
4. 增加normal/recovered/imported editable paths、read-only/submitted gates、independent sides/layers、rotation-after-add与add-after-rotation regression tests。
5. 更新仅与Phase 3交互直接相关的maintained documentation，运行focused synthetic tests及full offline suite；不连接external environment。

Rollback可移除Phase 3 interaction UI/evaluator而不改变schema 8或已保存extension provenance；通过Phase 3创建并已持久化的extensions仍是Phase 2合法data，旧的Phase 2-aware build可读取但不提供interactive editor。
