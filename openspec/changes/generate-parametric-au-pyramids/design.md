## Context

See `proposal.md` for motivation and `specs/parametric-au-electrodes/spec.md` for required behavior.

当前 electrode path 由两份 59-atom coordinate resources 驱动：`electrode_builder.py` 读取 A/B templates 并追加每侧 58 个 atoms，`electrode_surface.py` 再以固定 suffix、local indices 和 variant 重建 AITRANSS reference planes。正常 Step 3 continuation 尚未把这份 electrode identity 写入 project manifest；direct Step 3 只保存了 variant、roll 和 local-to-global mapping。因此删除 templates 前，必须同时替换 geometry source、runtime reconstruction 和 persisted provenance。

对两份现有 resources 的前 56 个 standard-core atoms 进行只读测量后，共得到 420 条 nearest-neighbor distances，mean 为 `2.883721780 Å`，range 为 `2.883629012–2.883824469 Å`，两份 core distance matrices 的最大差异为 `0.000180271 Å`。本 change 将 rounded mean `2.88372 Å` 作为 `MoltageAuPyramidV1` 的有来源 geometry parameter；旧 `2.89 Å` 只曾是 template connectivity cutoff，不作为坐标生成依据。

FHI-aims/AITRANSS manual 将 `$nlayers` 定义为与 reservoirs/interface 相连的 atomic layers，并要求参考用户安装中 `electrodes.library` 内相似 Au cluster 的 header。经用户明确授权，对一套合法 AITRANSS `051414` installation 完成了只读 header/geometry 验收：三个 fcc(111) `natoms22` variants 共享 ordered Au20 core、各有两个变化 adatoms，且 headers 一致指定 `$nlayers=2`；三个 `natoms37` variants 共享 ordered Au35 core、各有两个变化 adatoms，且 headers 一致指定 `$nlayers=3`。这直接支持 4- 与 5-layer canonical pyramids。`natoms18`/`natoms33` 的共同 cores 分别为 Au17/Au32，不是 tetrahedral sizes，不能用于额外映射；当前证据仍不覆盖 2、3、6–10 layers。

## Goals / Non-Goals

**Goals:**

- 以一个 deterministic、无 packaged coordinate templates 的 domain generator 产生 2–10 layer tetrahedral Au pyramids。
- 让 placement、project persistence、Step 4、restart 和 self-energy retry 共享同一份 durable atom/reference identity。
- 将 `$nlayers` recommendation 与 pyramid geometry 解耦，并把 evidence absence 表达为可见的 incomplete configuration。
- 用一个 bounded legacy adapter 保留能够被唯一解释的历史项目，而不让 legacy Au59 assumptions 继续进入新项目路径。

**Non-Goals:**

- 不实现 same-layer extension、hover preview、click-to-add 或未来 extension atom identities。
- 不要求左右 electrode 镜像、相同 roll 或相同 final orientation。
- 不重新设计 anchor placement、general connectivity、radii、scheduler、remote project layout 或 AITRANSS self-energy algorithm。
- 不复制、打包或从 real installation 下载 `electrodes.library` coordinate files；offline tests 不代表 real AITRANSS scientific acceptance。

## Decisions

### 1. Use an integer-lattice tetrahedron as the canonical geometry

`MoltageAuPyramidV1` 使用 nearest-neighbor spacing `s = 2.88372 Å` 以及三个等长、两两夹角 60° 的 basis vectors：

```text
b1 = s * (1, 0, 0)
b2 = s * (1/2, sqrt(3)/2, 0)
b3 = s * (1/2, sqrt(3)/6, sqrt(2/3))
```

layer `l` 包含所有 non-negative integer keys `(i, j, k)` where `i + j + k = l`，coordinate 为 `i*b1 + j*b2 + k*b3`。按 `(layer, i, j, k)` 的固定 lexicographic order 输出，使相同 version/layer count 的 local atom order 可重复。总 atom count 为 tetrahedral number：

```text
N(n) = n(n + 1)(n + 2) / 6
```

apex identity 为 `(0, 0, 0)`；base immutable reference corners 为 `(n-1, 0, 0)`、`(0, n-1, 0)` 和 `(0, 0, n-1)`。connectivity 由 lattice-neighbor identity 推导，不能依赖 covalent radii 或旧 `2.89 Å` cutoff。

选择 integer lattice 而不是程序化复刻 A/B Cartesian coordinates，是因为它直接表达 layer、neighbor 和 reference-corner semantics，并能在 rotation/translation 后保持 atom identity。选择 measured rounded mean 而不是 ideal bulk constant，是为了让默认 6-layer output 与当前 standard Au56 core 的实际 scale 保持一致，同时保留可审计的数值 provenance。

### 2. Keep canonical generation separate from side-specific placement

Au Tool 保存一个共享 `pyramid_layers` selection，并为左右两侧分别从相同 canonical geometry 创建 placement proposal。现有 outward-direction alignment、per-side placement validation、joint deterministic roll search 以及用户后续独立旋转能力继续使用；算法不得增加 left/right mirror 或 equal-roll constraint。

layer selection 默认 `6`，range 为 `2..10`。`7..10` 在 control 与 proposal preview 中显示 non-blocking high-cost warning。selection 变化会清除两侧 stale preview/proposal，避免确认与当前 layer count 不一致的 geometry。default six-layer placement 每侧把 apex 绑定到已存在的 contact atom，并新增其余 55 个 standard atoms；不再添加三个 asymmetric template adatoms。

选择复用现有 placement/alignment flow，而不是建立新的 orientation subsystem，可保持改动聚焦于 geometry source 和 identity，并保留已验证的左右独立行为。

### 3. Persist explicit standard-pyramid identity, not reconstructable hints

现有 `ProjectElectrodeClusterProvenance` 演进为 generic persisted electrode provenance。每侧记录至少包含：

- `side`；
- `geometry_model = "MoltageAuPyramidV1"` 或 bounded legacy model identifier；
- `pyramid_layers` 与 `nearest_neighbor_spacing_angstrom`；
- ordered atom identities：`local_index`、`layer_index`、integer `lattice_key`、`standard_pyramid_membership`；
- `local_to_global_atom_indices`，包括映射到既有 contact atom 的 apex；
- apex lattice identity、ordered three base reference-corner lattice identities；
- accepted placement roll/orientation data already needed by restart behavior。

reference corners 以 lattice identities 为权威，local/global indices 仅作为当前 structure mapping。rotation、translation 和未来 Phase 2 coordinate extension 均不得更换这三个 identities。Phase 1 生成的所有 electrode atoms 均为 standard members；field 现在仍显式持久化，以防未来 extension 把新 atoms 误当成 reference-plane candidates。

只保存 generator version/layer count 并在使用时重新推导 atom identities 的方案被拒绝，因为它不能证明一个历史或未来扩展后的 structure 是否仍与原始 mapping 一致，也无法满足 immutable reference identity 要求。

### 4. Make persisted provenance authoritative for every mapping-dependent operation

normal Step 3 continuation 与 direct Step 3 使用同一 schema。normal flow 在首次保存 authoritative Step 3 state 时原子性地附加两侧 accepted provenance；direct flow 在 initial project manifest 中保存同一结构。重新提交或 retry 复制 metadata，不重新生成。

`electrode_surface.py` 改为接收 project provenance 与当前 structure，并验证：mapping cardinality、global-index range/uniqueness、mapped element 为 Au、apex/contact relation、lattice identity completeness、三个 corners 存在且不共线，以及 structure coordinates 与记录的 rigid pyramid geometry 在 tolerance 内一致。验证成功后才生成 `ElectrodeSurfaceProposal`。Step 4 initial submission、restart、overlap/self-energy retry 及 recovery 均必须走此入口；缺失或冲突时明确失败，不 fallback 到 suffix/template search。

选择 project metadata 而不是 remote geometry heuristic，是因为 job lifecycle 已经拥有 project manifest，且 retry 必须重用用户实际确认的 electrode，而不是在已经优化或复制的 geometry 中猜测 identity。

### 5. Keep `$nlayers` as evidence-bound transport configuration

geometry provenance 不保存或推导 `recommended_nlayers`。新增一个窄接口 `nlayers_initial_value_for_pyramid(pyramid_layers)`，返回 optional `{value, source, evidence}`。`source` 只能为 `AIMS_RECOMMENDED` 或 `USER_SPECIFIED`；只有前者携带 reviewed library evidence。当前 reviewed evidence coverage 为 `2/9`，configured default coverage 为 `3/9`：

```text
pyramid_layers  canonical_core  ignored_variant_adatoms  initial_nlayers  source
4               Au20            2                         2                AIMS_RECOMMENDED
5               Au35            2                         3                AIMS_RECOMMENDED
6               Au56            n/a                       4                USER_SPECIFIED
```

证据来自 AITRANSS `051414` `fcc111.au.clusters.22_atoms/fcc111.cluster.natoms22.x1|x2|x3.aims` 与 `fcc111.au.clusters.37_atoms/fcc111.cluster.natoms37.x1|x2|x3.aims` 的一致 headers 和共同 ordered cores。evidence record 不保存 real hostname、username、installation path 或 coordinate contents。6-layer value `4` 只来自本 change 的明确用户决定，没有 library evidence，必须保持 `USER_SPECIFIED` provenance。2、3、7–10 layers 返回 `None`。

`TControlProposal` 允许 initial value 缺失，并携带可显示的 source；`TControlSettings` 仍要求最终 `$nlayers` 为 positive integer。Step-4 dialog 对 4/5 layers 显示 `AIMS recommended`，对 initial 6-layer value 和所有用户编辑/填写的值显示 `User specified`。修改 4/5 的自动值后立即切换 source，不根据“数值是否碰巧等于 recommendation”反推 provenance。缺失时显示未配置状态、manual/library-header guidance，并要求用户明确填写后才能 materialize a new `tcontrol.in`。source 是 Moltage editor/provenance metadata，不生成新的 AITRANSS directive；`tcontrol` 仍只写最终 integer value。如果以后经单独授权只读检查合法 installation，只记录 reviewed header facts/metadata，不复制 electrode coordinates，也不从 cluster name 或 atom count 猜 mapping。

既有 project 中已保存或已解析的 `$nlayers` 继续作为历史 task input 使用，不能被新 recommendation 覆盖或重写。将 `nlayers = pyramid_layers`、`pyramid_layers - 2`、全尺寸固定 `4`，以及从 manual 单个示例外推的方案均被拒绝，因为没有证据证明其 scientific validity。

### 6. Isolate Au59 knowledge inside schema migration only

project manifest schema 从当前 `6` 增至 `7`。schema 7 新项目仅写 generic provenance，不包含 A/B variant 或 fixed counts。

schema 4–6 direct-Step-3 records 已保存完整 59-entry local-to-global mapping，可由 bounded migration adapter 转成 `LegacyAu59V1` generic provenance。adapter 只保存/使用历史 ordering topology（core layer membership、three extra-atom classification、base-corner identities），不读取或携带旧 Cartesian resources；downstream code 只消费 generic identities。

缺少 mapping 的 legacy normal projects 仅在以下 evidence 全部成立时恢复：manifest schema 对应旧 workflow、structure 有完整且唯一的 historical 58+58 appended ordering、每侧 apex/contact 能由 expected nearest-neighbor relations 唯一识别、两侧 mapping 不重叠且通过 element/geometry validation。任何 ambiguity 都使 mapping-dependent Step 4/restart/retry 明确失败，并提示历史项目需要人工重建；不得选择“最接近”的 candidate。

迁移只影响 manifest 的 in-memory/current-schema representation；既有 remote/local calculation inputs、已生成 `control.in`/`tcontrol.in` 和历史 `$nlayers` 不改写。将 fixed 59/58/116 logic 保留在 active reconstruction 中的方案被拒绝；这些数值只允许出现在版本化 legacy decoder 及其 regression fixtures 中。

### 7. Remove coordinate resources and make packaging prove independence

删除两份旧 `.xyz` resources、A/B active enum/loader 及 packaging references。clean install 的 2–10 layer generation 必须仅依赖 project source 和 standard-library math/NumPy；不新增 runtime dependency。packaging tests 检查 wheel/installer/resource manifest 不再包含旧 electrode coordinates，generator tests 用 mathematical invariants 而不是 golden copy of third-party coordinates。

## Risks / Trade-offs

- [Measured spacing precision may imply more certainty than the source coordinates support] → 常量保留 `2.88372 Å` 五位小数，并在 code/documentation 中记录 sample count、range、mean 与“project geometry parameter”定义，不称为实验常数或 universal Au equilibrium value。
- [Large pyramids materially increase atom count and downstream cost] → GUI 对 7–10 layers 显示明确 warning，并测试 counts；仍按用户决定允许生成，不静默 clamp。
- [Legacy normal projects may lack enough identity evidence] → 使用 bounded proof-based migration；不唯一时只阻止 mapping-dependent operations，历史结果浏览与既有 files 保持可用。
- [No `$nlayers` auto recommendation is less convenient] → manual input 与 precise evidence guidance 保持可用；缺失 evidence 可见且不会产生 scientifically unsupported input。
- [Schema 7 cannot be understood by older Moltage builds] → migration only moves forward；发布说明标记 breaking behavior，用户回退旧 build 前应保留 project backup。既有 schema 1–6 files 仍由新 build 读取。
- [Reference mapping could be invalidated by atom reordering] → 每次 mapping-dependent operation 验证 explicit mapping 与 geometry；不通过时失败，而不是按 suffix 重新猜测。

## Migration Plan

1. 先加入 pure generator、identity records 与 invariant tests，不切换现有 UI path。
2. 演进 project schema/serializer，并加入 schema 4–6 direct 与 normal legacy migration regression tests；证明 ambiguity 会失败。
3. 将 Au Tool 切换至 generator，加入 layer selector/warning，并让 normal/direct Step 3 都持久化相同 provenance。
4. 将 Step 4、restart、recovery 和 self-energy retry 切换为 provenance reconstruction；随后移除 active template/fixed-count code。
5. 改造 `$nlayers` proposal/dialog validation，使当前 2–10 layers 在无 reviewed header evidence 时保持 unset/manual-required，并保护 historical values。
6. 删除 coordinate resources，更新 maintained documentation/packaging checks，运行 targeted tests 与 full offline suite。

Rollback 通过恢复 change 前代码/resources 完成；它不能安全读取新 schema 7 projects，因此 rollback 前必须保留这些 projects，并在恢复新版本后继续使用。rollback 不应降级或重写 project manifests。

## Open Questions

None. 当前 AIMS recommendation coverage 为 `2/9`，editable initial-default coverage 为 `3/9`；2、3、7–10 layers 的 library evidence 可在以后补充，但不改变已决定的缺失-evidence行为。
