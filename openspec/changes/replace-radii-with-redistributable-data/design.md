## Context

See `proposal.md` - Why。当前 covalent 与 van-der-Waals 数据分别存放在两个 packaged TOML resources 中，通过现有 loaders 提供给 connectivity、steric placement 和部分 display behavior。Covalent loader 主要验证单位和数值完整性；van-der-Waals loader 还将当前 Alvarez DOI 作为固定身份检查。现有表的支持范围分别为 H–Bi，以及 H–Bi 中除 Pm 外的元素。

本 change 会改变 packaged scientific data。通用分子 connectivity 仍使用既有 covalent-radius sum 与 multiplier，steric placement 仍使用既有 van-der-Waals clearance 逻辑。回归验证发现 packaged Au59 templates 的 bulk-metal spacing 与获批 Pyykkö Au single-bond radius 不兼容；用户随后明确批准该模板单独使用 Moltage 内部 `2.89 angstrom` Au–Au cutoff。

## Goals / Non-Goals

**Goals:**

- 为两类 radii 各自选择科学定义清楚、覆盖充分且明确允许公开与商业再分发的数据源。
- 保持现有 resource/load interfaces 和离线 packaged-data 模式。
- 用 source-agnostic validation 核验 provenance、license、单位、定义、覆盖与数值完整性。
- 在替换前提供可审核的数值差异和 synthetic behavior regression evidence。

**Non-Goals:**

- 不修改通用分子 connectivity、steric placement 或 rendering algorithms、thresholds 和 multipliers；唯一例外是用户明确批准的 packaged Au59 template `2.89 angstrom` 内部 connectivity cutoff。
- 不处理 `au_placement_defaults.toml`、Au electrode `.xyz` 或其他 scientific tables。
- 不引入在线 scientific database、运行时下载、第三方 scientific package 或新的 persisted schema。
- 不在本 change 中制定项目总 LICENSE 或重写 Git history。

## Decisions

### 1. Source approval precedes all data edits

先分别形成 covalent 与 van-der-Waals 候选来源记录，包括 authoritative source、stable version/locator、radius definition、原始单位、元素覆盖、license text/location、公开与商业再分发判断和 attribution obligations。只有用户明确批准候选来源后，才可修改 TOML values、loader source validation 或相关 expected values；不能确认许可或定义时停止该数据集的替换。

这样把事实核验与数据实施分开，避免用“公开可访问”误代“允许再分发”。备选方案是实施者自行选择最方便的数据源并直接改表；该方案缺少明确 authorization，予以拒绝。

### 2. Preserve the two packaged tables and their existing consumers

继续使用独立的 `covalent_radii.toml` 和 `vdw_radii.toml`，并保持现有 loader entry points 与 downstream call pattern。每个 resource 的 metadata 至少记录 source/citation、stable locator 或 version、license 与 license locator、`angstrom` units、radius definition、coverage，以及 Moltage 的 selection/transformation note。

Loader 将验证这些通用字段和数据完整性，不再以某个旧 DOI 作为永久合法值。替代方案包括把数值移入 Python source，或运行时访问外部数据库；前者弱化 provenance/data review，后者破坏离线确定性，均不采用。

### 3. Normalize once to Å and preserve current coverage

获批来源若使用其他单位，转换在 resource preparation 时一次性完成，并在 metadata 中记录原始单位与转换；运行时只读取 Å，不动态换算。两个表分别采用与获批来源一致且明确记录的 radius definition，不混用 covalent 与 van-der-Waals values。

为控制 compatibility，首轮 replacement 只要求并保留现有支持集合：covalent H–Bi；van-der-Waals H–Bi excluding Pm。候选来源覆盖更多元素时不借本 change 扩展产品行为；候选来源缺少现有元素时不能用另一来源或经验常数静默补齐。

### 4. Treat changed values as an evidence-gated compatibility change

实施时生成不包含真实环境信息的逐元素 comparison summary，至少包括覆盖差异、absolute difference 和 relative difference。随后运行：resource metadata/value validation；现有 reference synthetic connectivity fixtures；靠近 cutoff 的 synthetic connectivity cases；steric placement/selection cases；以及依赖默认 radii 加载的 display smoke coverage。

精确数值一致不是 acceptance criterion。Acceptance 要求是：变化与获批来源一致，通用算法保持不变，并且没有未经解释的明显行为回归。如果 established synthetic outputs 发生有意义变化，应将其原因和影响提交用户决定；不得通过未获批准的 multiplier、threshold 或选择性 fixture 修改隐藏差异。用户可以明确批准有证据支持、范围受限的 compatibility 处理。

### 5. Keep provenance with the resources and maintained user documentation

TOML metadata 作为每套数据的直接 provenance record；maintained documentation 简要说明数据用途、定义、来源、license 和 Moltage 的整理方式。若获批 license 要求特定 attribution/notice 形式，实施必须按该要求放置 notice；在来源获批前不预设或虚构具体法律文本。

### 6. Keep packaged Au59 topology independent from covalent-radius provenance

两个 packaged Au59 templates 使用显式 Moltage 内部 cutoff `2.89 angstrom` 推断其显示与 builder 所需的模板内部连接。该值来自本次用户明确决定，只用于这些已验证的 Au59 coordinate resources；它不写入 covalent-radii table、不改变通用 connectivity multiplier，也不宣称为 universal equilibrium distance 或实验常数。保留 `load_electrode_template(..., covalent_radii=...)` 的显式 override 行为，以避免无关 API 破坏；默认 packaged-resource path 使用固定 cutoff。

## Risks / Trade-offs

- [候选来源许可看似开放但不允许商业再分发] → 以权威 license text 为准；无法确认即阻断，不根据网站可访问性推断。
- [同名 radius 的科学定义不同] → 每张表只采用一个定义明确且适合现有用途的 coherent dataset，并在 metadata/docs 中显式记录。
- [数值变化跨越 connectivity 或 steric cutoff] → 使用边界 synthetic cases 和既有 reference behavior 对比；变化由用户审核，只有明确批准的窄范围 compatibility 处理才可实施。
- [严格保持现有覆盖减少可选来源] → coverage compatibility 优先；若确需缩减或混合来源，另行确认并更新 artifacts，而不是在 apply 中临时决定。
- [将旧表保留为 comparison fixture 会继续分发现有来源数据] → 不复制旧表到新 fixture；comparison 从变更前版本生成摘要，repository 只保留必要 evidence，不新增旧数据副本。

## Migration Plan

1. 完成两个候选来源的 source/license/scientific-semantics review，并取得用户明确批准；任一数据集未通过时停止对应实施。
2. 在同一受控修改中更新获批 TOML data/metadata、通用 loader validation、直接 tests 和 maintained documentation。
3. 生成逐元素 comparison summary，运行受影响 synthetic tests；任何有意义差异先交由用户审核，并只实施用户明确批准的 fixture 或 compatibility 决定。
4. 运行 full offline suite，且只陈述实际取得的 validation evidence。
5. 如果 replacement 未满足 license、coverage、integrity 或 regression gate，回退本 change 的 radii-related edits；不以旧数据 silent fallback 形成混合状态。
