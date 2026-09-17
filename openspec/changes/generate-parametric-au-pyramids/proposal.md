## Why

Moltage 当前依赖两份来源于 FHI-aims 的固定 Au59 coordinate resources，并把 6-layer、A/B variant、固定 atom counts 与 AITRANSS surface mapping 绑定在一起；这既不适合公开分发，也无法支持用户选择 2–10 layers。需要以 Moltage 自有、可验证的 canonical Au pyramid generator 取代这些资源，同时让 transport reference plane 和 `$nlayers` 具有明确、可追溯且不会猜测的语义。

## What Changes

- 新增 `MoltageAuPyramidV1` canonical triangular-lattice / tetrahedral generator，支持统一的 `pyramid_layers = 2..10`，默认 `6`；标准 atom count 为 tetrahedral number，因此 6 layers 生成 Au56。
- 将 generator 的 Au–Au nearest-neighbor spacing 定义为 `2.88372 Å`。该值来自现有两份 Au59 resources 的前 56 个 core atoms：共 420 条 nearest-neighbor distances 的均值为 `2.883721780 Å`，观测范围为 `2.883629012–2.883824469 Å`；它不是旧 `2.89 Å` template-connectivity cutoff 的复用。
- 在现有 Au Tool 中增加一个左右共用的 `Pyramid layers` 选择器；2–6 为正常推荐范围，7–10 仍可生成但显示显著计算成本警告。左右 electrode 使用相同 layer count、canonical shape 与 lattice geometry，分别沿各自 outward direction placement/alignment，并继续允许不同 roll/orientation。
- generator 为每个标准 Au 持久化 side、layer、integer lattice identity、standard-pyramid membership、local-to-global mapping，以及三个 immutable base reference corners；Step 4、restart 和 self-energy retry 使用该 metadata，不再重新加载 template、依赖 A/B variant、固定 Au59 suffix 或固定 local indices。
- `$nlayers` 与 `pyramid_layers` 保持独立。经用户授权对一套合法 AITRANSS `051414` installation 进行只读验收后，fcc(111) library headers 直接支持 `pyramid_layers=4 -> $nlayers=2`（Au20 core + 2 variant adatoms）和 `pyramid_layers=5 -> $nlayers=3`（Au35 core + 2 variant adatoms）；这两个 editable defaults 标记为 `AIMS recommended`。`pyramid_layers=6 -> $nlayers=4` 作为用户明确决定的 editable default，标记为 `User specified`，不得表述为 AIMS/library recommendation。2、3、7–10 layers 不设默认值，Step-4 UI 要求用户明确填写后才能提交。任何值经用户编辑后均标记为 `User specified`。
- **BREAKING**：新建项目不再使用或打包 `au_6layer_variant_a.xyz` / `au_6layer_variant_b.xyz`，删除 active model 中的 A/B variant 和固定 59/58/116 atom-count assumptions；默认 6-layer electrode 从旧 Au59 有意变为标准 Au56。
- 将 project manifest 演进为能够保存两侧 generator metadata，并在 normal Step-3 continuation、direct Step-3、restart 和 retry 路径中保持该 provenance。旧 profile 继续兼容；历史项目只在能够唯一恢复 mapping 时迁移，否则明确失败。既有 calculation inputs、已提交文件和历史 `$nlayers` 不重写。
- 更新直接描述固定 Au59 行为的 maintained documentation 与 synthetic tests；不复制 `electrodes.library` 中的 electrode coordinate files，也不以 offline tests 声称 real AITRANSS scientific acceptance。
- 本 Phase 1 不实现 same-layer lattice extension、hover preview 或 click-to-add；这些仍属于后续独立 changes。

## Capabilities

### New Capabilities

- `parametric-au-electrodes`: 规定 2–10 layer canonical Au pyramid generation、Au Tool layer selection、持久化 electrode/reference-plane provenance、保守历史 migration，以及证据约束的 AITRANSS `$nlayers` 处理。

### Modified Capabilities

None.

## Impact

预期影响 electrode domain records、`junction/electrode_builder.py`、`junction/electrode_surface.py`、Au Tool workspace state、Step-3 submission/project-manifest persistence、Step-4 recovery/restart/self-energy retry、`aitranss/tcontrol.py`、packaged electrode resources、相关 synthetic tests，以及当前固定 Au59 的 maintained documentation。预计 project manifest schema 需要一次向后兼容演进；不新增 runtime dependency、不连接真实 HPC、不修改 scheduler、FHI-aims input science、anchor placement、general connectivity/radii 或 remote project layout。
