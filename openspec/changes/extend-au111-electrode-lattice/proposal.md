## Why

`MoltageAuPyramidV1` 已能生成带稳定 lattice identity 与 immutable reference corners 的标准 Au pyramid，但当前 accepted electrode 仍是不可扩展的固定 canonical footprint。为了在不引入自由 XYZ 放置、不破坏 Step 4 provenance 的前提下支持后续交互式编辑，需要先建立一个纯 backend、可持久化且可独立验证的 Au(111) same-layer lattice extension capability。

## What Changes

- 新增基于现有 `MoltageAuPyramidV1` canonical integer-lattice basis 的 same-layer candidate generation；candidate 由 `side + layer_index + signed lattice_key` 唯一标识，其坐标不依赖鼠标位置、viewer orientation 或任意 Cartesian 猜测。
- 对只有 apex 一个 occupied site 的 layer 生成 6 个 triangular-lattice nearest neighbors；当该 layer 只有两个相邻 occupied sites 时生成两个等边三角形第三顶点；形成普通 boundary 后统一使用 boundary-edge candidate generation，不继续增加 seed 特例。
- 对 seed 与普通 candidates 统一执行 deterministic deduplication、occupancy、cross-layer Au overlap、same-side membership 与 stale-candidate revalidation；不通过验证的 site 不得添加，也不得 fallback 到自由放置。
- 新增纯 domain/application add operation：只能把当前重新验证后仍合法的 candidate 转换为真实 Au，追加到 structure，连接其全部合法 same-side lattice neighbors，然后重新计算 candidates；允许连续添加且不设人为数量上限。
- 在 electrode metadata 中明确区分 immutable standard-pyramid atoms 与 user-added lattice-extension atoms，并持久化每个 extension 的 side、layer、signed lattice identity、global atom mapping 与 addition provenance。
- 将 project manifest 演进为保存 extension provenance；旧 schema 7 projects 读取时获得空 extension collection，既有 legacy electrode records 不通过 Cartesian geometry 猜测 extensions。
- Step 4、restart、recovery、Step-3 retry 与 self-energy retry 继续使用标准 pyramid 的 apex 和三个 immutable reference corners，同时把合法 extension atoms 纳入对应 side 的完整 electrode membership。
- same-layer extension 不改变 `pyramid_layers`、canonical standard-pyramid metadata、reference-corner identities 或 `$nlayers` value/source/recommendation。
- **BREAKING**：包含 lattice extensions 的新 project manifest 使用更新后的 schema，旧 Moltage builds 不保证能够读取；当前版本仍须向后读取 schema 7 及既有受支持 legacy manifests。
- 本 Phase 2 不加入 hover、ghost preview、mouse-nearest selection 或 click-to-add；这些只属于后续独立 change `interact-with-au111-extension-sites`。

## Capabilities

### New Capabilities

- `au111-electrode-lattice-extension`: 规定 Au(111) same-layer lattice candidates、确定性添加、extension provenance、project persistence、reference-plane invariance 与 downstream membership semantics。

### Modified Capabilities

None.

## Impact

预期影响 electrode domain records、candidate/add service、accepted placement state、`app/electrode_provenance.py`、`domain/calculation_project.py`、`remote/project_manifest.py`、`junction/electrode_surface.py`，以及 Step-3/Step-4/restart/retry provenance pass-through 的 synthetic tests。Project manifest schema 预计从 7 演进到 8；不新增 runtime dependency，不修改 `MoltageAuPyramidV1` generator、Au placement/orientation、scheduler、FHI-aims/AITRANSS scientific inputs、`$nlayers`、radii、species definitions 或 remote project layout，也不执行真实 SSH/HPC operation。
