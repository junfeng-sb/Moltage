## Why

Phase 2 已提供可持久化、按 canonical identity 验证的 Au(111) lattice extension，但 Geometry viewer 尚不能让用户预览和添加这些 sites。Phase 3 只把该 backend capability 接入现有交互，同时让当前完整 geometry 中被遮挡或重叠的合法 lattice site 可见但不可添加。

## What Changes

- 在已应用双侧 pyramid 的可编辑 Geometry workspace 中提供互斥的 lattice-extension 交互模式；左右 side 和各 layer 共同参与 mouse-nearest selection，不生成自由 XYZ。
- 缓存 Phase 2 返回的 candidate set。Mouse move 仅从缓存候选的 viewer projection 选择一个最近 site，复用 `PreviewAtom` 显示至多一个半透明 Au；mouse leave、退出模式和 workspace 切换清除 ghost。
- Hover ghost 同时显示两类 visual-only guides：按 Phase 2 canonical lattice-neighbor topology 得到的预期 Au–Au bonds 使用虚线，candidate 所属 current rigid layer 使用以 candidate 为中心向外渐隐的 triangular-lattice 网格平面。GUI 不以距离猜测 bond，也不从 pointer/XYZ 生成 layer geometry；`AVAILABLE` 使用高对比度正常 guide color，`BLOCKED` 使用红色 guides。
- 保留所有 Phase 2 合法 candidates。Phase 3 依据当前完整 geometry 将被选 site 标记为 `AVAILABLE` 或 `BLOCKED`：前者显示普通 Au preview 并允许 click；后者显示红色 preview 且 click 不添加。判断复用现有原子距离、vdW radius-sum steric rule 和 Au lattice clearance，不新增 radii、multiplier 或 scientific threshold。
- Click 只携带 `side + layer_index + signed lattice_key`；在当前 electrode state 和完整 geometry 上重新验证，再交给现有 Phase 2 add operation。失败为 no-op；成功刷新 working geometry、Connectivity、applied provenance、viewer 与 candidate cache，并记录一次 Geometry Undo/Redo。
- 根据 persisted standard atom mapping 在当前 rigid side geometry 中确定 candidate 的显示/添加坐标；已添加 extension Au 通过其 same-side Connectivity 和 global atom mapping 成为该电极的正常组成部分，随 standard atoms 一起接受既有刚性旋转。旋转不改变 canonical lattice identity、standard pyramid provenance 或 reference-corner identity；无法证明 rigid mapping 时明确禁止添加，而不从 XYZ 猜测 identity。
- 添加、Undo/Redo、pyramid 重建、electrode 位置改变和 workspace 切换均使相应缓存失效；read-only、已提交或其他 immutable geometry 不允许添加。
- 不修改 Phase 2 candidate generation、`MoltageAuPyramidV1`、`pyramid_layers`、`$nlayers`、geometry writer precision、通用 radii 数据、global Connectivity multiplier、server/scheduler 或 scientific input。

## Capabilities

### New Capabilities

- `au111-electrode-lattice-interaction`: 规定 cached candidate hover、普通/红色 preview、完整 geometry 的 addability、identity-based click、rigid side placement、Undo/Redo 与 immutable-workspace protection。

### Modified Capabilities

None. 当前项目尚无 main OpenSpec specs；Phase 2 的 lattice validity 和 add operation 不在本 change 中改写。

## Impact

预期只影响现有 Geometry workspace/Au Tool 路由、`MoleculeViewerWidget`/`MoleculeScene` 的投影、`PreviewAtom` 与不可拾取的 hover-guide actors、窄的 current-geometry placement validation、`GeometryEditHistory` 接线，以及相关 synthetic domain/GUI/workflow tests 和直接描述该交互的 maintained documentation。沿用 schema 8 extension provenance，无 project/profile migration 或新的 runtime dependency；planning 与 tests 均不连接真实 SSH/HPC，也不运行 FHI-aims/AITRANSS。
