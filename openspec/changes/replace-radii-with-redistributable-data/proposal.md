## Why

Moltage 当前打包的 covalent radii 与 van-der-Waals radii 虽有科学来源，但其再分发条件不适合作为未来公开且可能商业使用的软件资源边界。需要用来源、定义和许可均明确允许公开与商业再分发的数据替换它们，同时验证数值变化不会给现有显示与轻量几何判断带来未经识别的回归。

## What Changes

- 分别为 covalent radii 和 van-der-Waals radii 选择可追溯的替代数据源；在修改数据前，确认其元素覆盖、radius definition、单位以及允许公开和商业再分发的许可条件。
- 用通过人工审核的数据替换现有 packaged tables；不要求与当前数值逐项相同，但不得降低当前实际支持的元素覆盖范围。
- 在 resource metadata 与 maintained documentation 中记录来源版本或稳定定位信息、citation、license、单位、radius definition，以及 Moltage 所做的选择或转换。
- 将 loader 中与当前特定来源绑定的验证改为对必要 provenance 和科学语义的通用验证；继续对缺失、无效或未支持的数据明确失败，不增加 silent fallback。
- 在替换前后比较逐元素数值，并用 synthetic fixtures 验证 connectivity、steric placement 与显示相关行为；发现有意义的行为变化时先报告并取得人工决定，不通过未获批准的算法或阈值调整掩盖差异。
- 回归验证确认 Pyykkö Au covalent radius 不适合作为 packaged Au59 bulk-template 的既有成键判据后，按用户明确决定仅为该模板采用 Moltage 内部 `2.89 angstrom` Au–Au cutoff；这不是 covalent-radius table 的覆盖值或通用分子 connectivity 设置。
- 本 change 不处理 Au placement defaults、Au electrode `.xyz`、其他 scientific resources、LICENSE、scheduler/runtime 或 Git history。

## Capabilities

### New Capabilities

- `scientific-radius-resources`: 规定 Moltage 随软件分发的 covalent / van-der-Waals radii 必须具备可核验且允许公开与商业再分发的来源、明确的科学语义和覆盖范围，并在替换时经过行为回归验证。

### Modified Capabilities

None.

## Impact

预期实施将影响 `resources/chemistry/covalent_radii.toml`、`resources/chemistry/vdw_radii.toml`、对应 resource loaders、Au59 template connectivity 的内部 cutoff、直接使用这些数据的 synthetic tests，以及描述其 provenance 和用途的 maintained documentation。它不引入运行时网络访问或新外部 dependency，不修改通用分子 connectivity multiplier、steric algorithms、persisted schema 或 Au electrode `.xyz`。
