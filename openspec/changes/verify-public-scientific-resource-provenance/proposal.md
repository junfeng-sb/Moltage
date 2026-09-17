## Why

`resources/anchors/au_placement_defaults.toml` 当前只将数值称为 project starting parameters，尚未明确它们是 Moltage 自行选择的 heuristic defaults，而不是实验常数、universal equilibrium geometry 或逐值转载自特定论文。公开分发前需要把这一 scientific boundary 准确写入 resource metadata 和 maintained documentation，避免对数值来源与适用范围作过度声明。

## What Changes

- 将现有 Au placement values 正式记录为 Moltage-defined、user-editable initial-geometry defaults / heuristic starting parameters。
- 明确这些数值只用于构造后续可优化的 initial geometry，不是 universal equilibrium geometry、实验常数，也不得声称其精确数值直接来自某篇论文。
- 保留当前参数值和 placement behavior；本 change 不重新选择数值、不改变算法或默认生成结果。
- 允许 maintained documentation 将 primary literature 用作某类 anchor geometry 合理范围的 scientific background/support，但不得将其误写为当前精确数值的 provenance。
- 将 covalent / van-der-Waals radii 的替换移至独立 change；Au electrode `.xyz` 的来源与再分发问题仍由另一个独立 change 处理。

## Capabilities

### New Capabilities

None. 本 change 只校正 resource provenance、scientific semantics 和 documentation，不改变 observable behavior，因而继续使用 `skip_specs: true`。

### Modified Capabilities

None.

## Impact

未来实施仅影响 `resources/anchors/au_placement_defaults.toml` 的 metadata、直接验证该 metadata 的 tests，以及描述该 resource 的 maintained documentation。它不改变 placement values、Au placement/electrode generation、covalent or van-der-Waals radii、runtime behavior 或其他 scientific algorithms；radii replacement 与 Au electrode provenance 明确不在本 change scope 内。
