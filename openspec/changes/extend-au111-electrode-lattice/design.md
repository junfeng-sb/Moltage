## Context

See `proposal.md` for motivation and `specs/au111-electrode-lattice-extension/spec.md` for required behavior.

Phase 1 已将标准 electrode 定义为 `MoltageAuPyramidV1`：2–10 layers、`2.88372 Å` nearest-neighbor spacing、non-negative integer keys、稳定 apex/base corners 与 schema-7 project provenance。当前 `AppliedElectrodePlacement` 要求 applied structure 与 accepted standard preview 完全相等；`ProjectElectrodeClusterProvenance` 只允许 canonical standard identities 或 bounded legacy atoms；`electrode_surface.py` 还假设两个 standard mappings 恰好覆盖 generated suffix。因此 Phase 2 不能仅在 structure 末尾追加 Au，必须同步扩展 accepted state、persistent identity 与 downstream membership，同时保留 Phase 1 standard core 的所有 invariants。

本 change 不需要 real SSH/HPC、FHI-aims 或 AITRANSS evidence。唯一 quantitative geometry input 仍是 Phase 1 已审核的 `2.88372 Å` lattice spacing；candidate filtering 只增加 numerical geometry tolerance，不引入新的 scientific radius 或 placement parameter。

## Goals / Non-Goals

**Goals:**

- 提供一个 pure backend API，从当前 authoritative side/layer occupancy 计算 finite、deterministic candidate set，并以 immutable add result 支持连续 extension。
- 让 signed extension identities 与 standard-pyramid identities 分离，但共同使用同一 canonical lattice frame。
- 让 schema-8 manifest、Step-3/Step-4/restart/retry paths 与 surface resolver 保存并验证 extensions，而不改变 standard reference corners。
- 让未来 Phase 3 viewer 只能消费本 change 返回的 candidates，而不需要复制 geometry rules。

**Non-Goals:**

- 不修改 `MoltageAuPyramidV1` generation、layer count、spacing、standard ordering、placement/alignment、roll 或 collision search。
- 不实现 hover、ghost atom、mouse-nearest selection、click handler、viewer pick radius、undo/redo UI 或任何其他 Phase 3 behavior。
- 不支持 free XYZ Au placement、out-of-plane adatoms、删除/移动 extension atoms、改变 layer identity 或 arbitrary lattice editing。
- 不修改 `$nlayers` recommendation/value/source、AITRANSS directives、FHI-aims input、radii、anchor defaults、scheduler、server profile 或 remote project layout。
- 不从 historical Cartesian geometry 猜测 extension provenance，也不重新设计 project/workspace persistence。

## Decisions

### 1. Keep the finite standard generator unchanged and add a separate signed extension lattice

新增一个窄的 domain-owned lattice-extension module，而不放宽 `AuPyramidAtomIdentity` 的 non-negative-key invariant。Standard atoms 继续由 Phase 1 generator 产生；extension site 使用独立 typed identity：

```text
side                 LEFT | RIGHT
layer_index          0 .. pyramid_layers - 1
lattice_key          (i, j, k), signed integers, i + j + k = layer_index
origin               LATTICE_EXTENSION
```

同层六个 neighbor deltas 固定为：

```text
(+1, -1,  0)  (-1, +1,  0)
(+1,  0, -1)  (-1,  0, +1)
( 0, +1, -1)  ( 0, -1, +1)
```

这些 deltas 保持 key sum，因此不会把 candidate 移到另一 layer。允许负分量使 lattice 可以越过 canonical triangular footprint；standard identity 仍只接受 non-negative components。选择独立 identity 而不是修改 Phase 1 generator，是为了让 standard membership、tetrahedral count 与 immutable reference-corner proof 保持原义，并使 legacy nonstandard atoms 不会被误当成 extensions。

### 2. Reconstruct an identity-anchored affine lattice frame, never a best-fit geometry

每侧 candidate coordinate 使用当前 mapped standard sites `(0,0,0)`、`(1,0,0)`、`(0,1,0)`、`(0,0,1)` 建立 authoritative frame。所有受支持 pyramids 至少有 2 layers，因此四个 identities 必然存在。令其 world coordinates 为 `p000`、`p100`、`p010`、`p001`，则任意 signed key 的 coordinate 为：

```text
p(i,j,k) = p000
         + i * (p100 - p000)
         + j * (p010 - p000)
         + k * (p001 - p000)
```

在建立 frame 前，完整 standard mapping 必须继续通过现有 generated-electrode rigid validation（当前 tolerance `1e-6 Å`）。这不是对任意 XYZ 点做 fitting：四个 anchor identities 由 provenance 唯一指定，basis 来自已接受的 canonical mapping，公式没有 optimization 或 nearest-point choice。因而 valid rigid rotation/translation 会自然传播到 candidates，non-rigid or ambiguous geometry 会明确失败。选择从当前 authoritative geometry 建立 frame而不是另外持久化 rotation matrix，可避免 duplicated state 在合法 coordinate-only transforms 后失配。

### 3. Use one occupancy graph with two bounded seed modes and one generic boundary rule

对每个 `side + layer_index`，occupancy 是该 layer 的 standard keys 与已添加 extension keys 的 union。实现以 hash set 和固定 neighbor deltas工作，不枚举无限 lattice。

- `|occupied| == 1` 仅对 canonical apex layer 合法，返回该 site 的 6 neighbors。
- `|occupied| == 2` 要求两 sites 相邻，返回两者 neighbor sets 的两个 common sites。
- 其余状态使用 generic boundary rule：遍历每条 occupied neighbor edge，求其两个 triangular third sites；恰好一个 third site occupied 时，另一个是 raw candidate。两个均 occupied 的 interior edge不产生 candidate，两个均 empty 的 edge不在 generic mode扩展。

Raw candidates 经统一 filter、set dedup 后按 `(side order, layer_index, i, j, k)` 排序。普通 canonical layer 从第一帧即进入 generic mode；apex 在第三个 atom 构成 triangle 后也永久按 occupancy 进入 generic mode。选择从 current occupancy 计算 mode，而不持久化 `SEED`/`BOUNDARY` flag，可避免派生状态漂移，并能在每次 add 后自然重算。

### 4. Apply one validation pipeline to enumeration and mutation

Candidate enumeration 与 add-time revalidation 使用同一 pure validator：

1. side 必须对应当前 cluster，layer 必须在 standard pyramid range 内，key sum 必须等于 layer；
2. key 不得已被该 side 的 standard/extension occupancy 使用；
3. coordinate 不得在 `1e-6 Å` numerical tolerance 内与任何现有 Au coordinate 重合；
4. candidate 与任意 existing Au 的距离不得小于 `spacing - tolerance`；
5. same-side identities at lattice distance one 是合法 neighbors，并在实际距离为 `spacing` within tolerance 时通过；cross-side 或无 identity 的 Au 不因距离接近而被推定为 lattice neighbor。

此规则只防止 canonical lattice 不可能产生的 Au–Au overlap；它不声称 `2.88372 Å` 是 universal steric threshold，也不检查 non-Au molecular sterics。Generic covalent/vdW radii、connectivity multiplier 与 anchor parameters 不参与。Add operation 接收 typed candidate identity，但在构建任何新 object 前重新计算当前 candidate set并要求 exact identity/coordinate match；因此 stale 或伪造 candidate 是 atomic no-op，不需要依赖 UI revision token。

### 5. Evolve the accepted placement immutably instead of creating a second workspace architecture

保留现有 `AppliedElectrodePlacement` 作为 Au Tool 后续 submission 的 authoritative record，但增加 default-empty ordered extension records。其 `proposal` 仍代表不可变的 accepted standard preview；validation 改为要求：

- `proposal.preview_structure/connectivity` 是 current state 的完整 prefix/base；
- 每个额外 atom 只能由按顺序可重放的 validated extension record解释；
- 原 standard atom coordinates、mapping 和 bonds 不被 add operation 修改；
- `added_au_indices` 等于 standard generated indices 加 ordered extension global indices。

Pure `add_lattice_extension(current, candidate)` 返回新的 `AppliedElectrodePlacement`，追加恰好一个 Au，并只新增连接到所有 current same-side lattice-distance-one neighbors 的 bonds。它不按 Cartesian distance创建 bonds，也不连接另一 side；失败时旧 immutable object 原样保留。选择演进现有 record 而不是引入 parallel workspace/project manager，可让 normal/direct Step 3 eligibility 与现有 provenance conversion继续拥有一个 source of truth。

### 6. Persist extensions as a separate schema-8 collection

`ProjectElectrodeClusterProvenance` 增加 default-empty `lattice_extensions`，元素为独立 `ProjectElectrodeLatticeExtension` record：

```text
origin             "LATTICE_EXTENSION"
layer_index        integer
lattice_key        [i, j, k] signed integers
global_atom_index  non-negative integer
```

Array order是该 side 的 addition order；side 由 containing cluster 唯一确定。现有 `atom_identities` 与 `local_to_global_atom_indices` 继续只描述 accepted standard core（或既有 bounded legacy record），所以 canonical count/order validation与 legacy semantics不需重定义。

Manifest schema 从 7 增至 8。Schema 8 明确读写 `lattice_extensions`；schema 7 迁移时为每侧提供 empty tuple；schemas 1–6 继续先走现有 bounded legacy decoder，再得到 empty tuple。Parser/domain validation拒绝 duplicate keys、standard-key collisions、duplicate or cross-side global indices、invalid layer sums、non-Au mappings与不完整 fields，并按 global atom order重放 extension sequence验证每一步当时确为合法 candidate。Migration不从 geometry、suffix或 atom count推断 extension，也不改写 scientific files或 historical `$nlayers`。

选择 separate collection 而不是令 `standard_pyramid_member=False` 同时表示 legacy adatom 与 extension，可避免两个来源语义冲突，并让当前 legacy migration保持封闭。Older builds无法读取 schema 8 是明确 forward-schema boundary；schema-7 projects仍向后兼容。

### 7. Keep reference corners standard-only and make membership extension-aware

`electrode_surface.py` 的 standard mapping与 corner lookup继续只消费原有 `atom_identities`。Source-prefix/suffix coverage validation改为：两侧所有 non-apex standard indices、legacy mapped extras和 schema-8 extension global indices的 union必须恰好覆盖 generated geometry suffix；不要求每侧 records各自连续。每个 extension coordinate必须等于其 key在 authoritative side frame中的位置 within tolerance。

`left/right electrode membership` 对 generated schema-8 clusters返回 standard globals加 extensions；legacy records保持当前 standard-only self-energy membership，避免在本 change改变历史科学行为。`lsurc/lsurx/lsury` 与 `rsurc/rsurx/rsury` 始终来自三个原 standard corner keys。Normal/direct Step 3、resource retry、restart、recovery和 self-energy retry继续复制整个 frozen cluster record，因此无需新的 scheduler或remote branch；focused regression tests负责证明没有路径丢失 extension collection。

选择扩展 membership而不重新计算 surface，是为了满足新增 Au属于实际 electrode的语义，同时保持已审核 reference-plane identity和 AITRANSS interface definition不变。

### 8. Treat `$nlayers` and Phase 3 interaction as consumers outside this domain change

Extension APIs不导入或调用 `aitranss/nlayers.py`，也不改变 `pyramid_layers`。Existing `TControlProposal` initial-value table、user edits和 historical values保持不动；测试只验证 extension前后输入对象相等。

本 change不接入 `molecule_viewer.py`、`molecule_scene.py` 或 mouse handlers。它只提供 immutable candidate snapshots和 add results。Future `interact-with-au111-extension-sites` 必须按 candidate identity调用此 API，并在 add前接受 backend revalidation；viewer不得重新实现 neighbor、boundary、overlap或 coordinate rules。

## Risks / Trade-offs

- [Unlimited extension can create large structures and candidate sets] → 使用 occupied-key hash sets与 constant-degree neighbor traversal，使每次重算随当前 occupancy线性增长；不增加未经用户批准的 atom limit。
- [Schema-8 global mappings can be corrupted or reordered] → 解析后验证 exact suffix coverage、side disjointness、identity-derived coordinates并按 addition order重放；失败显式阻断 mapping-dependent operation。
- [A non-rigid manual edit makes identity-derived coordinates inconsistent] → 在 candidate generation与 downstream resolution前验证 standard frame和 extensions；不以 best-fit or nearest-site自动修复。
- [Cross-side geometries use independent frames and can approach each other] → Candidate filter检查当前 structure中的全部 Au sub-spacing overlaps，但只允许同一 side的 identity-proven lattice neighbor关系创建 bonds。
- [Older Moltage builds cannot understand extended projects] → 仅在 new schema中保存 extensions，maintained documentation/release note明确 forward incompatibility；schema 7及更早 supported inputs仍由新 build读取。
- [Changing suffix accounting can regress current Step 4] → 保留 standard-only corner lookup并为 zero-extension schema-7/schema-8 paths建立 regression tests，另测 asymmetric and interleaved extension mappings。

## Migration Plan

1. 添加 signed lattice identity、frame、candidate enumeration/validation与纯 domain tests，不改变 Phase 1 generator或 GUI。
2. 演进 `AppliedElectrodePlacement`，实现 immutable add/recompute operation，并以 synthetic 2–10 layer、left/right、apex seed和boundary cases验证。
3. 将 project schema提升到 8，增加 separate extension records、deterministic serialization、schema-7 empty migration及 malformed/replay rejection tests。
4. 使 normal/direct Step 3和 restart/recovery/retry provenance pass-through接受完整 schema-8 records；调整 surface suffix validation、extension coordinate proof与 full electrode membership，同时保持 corner lookup不变。
5. 更新仅与 extension persistence/reference invariance直接相关的 maintained documentation，运行 focused synthetic tests与 full offline suite；不连接 external environment。

Rollback可恢复 change前 source/tests，但含 extensions 的 schema-8 projects不能安全交给旧 build；rollback前必须保留这些 projects，待支持 schema-8的版本恢复后继续使用。Rollback不得降级 manifest、删除 extension records或重写 scientific files。
