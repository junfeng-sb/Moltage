## Purpose

规定 Moltage 随软件分发的 covalent radii 与 van-der-Waals radii 数据在来源、许可、科学定义、覆盖范围和替换验证方面必须满足的公开发布边界。

## ADDED Requirements

### Requirement: Redistributable source evidence

Moltage 分发的每套 radius dataset MUST 具有可核验的 source identity、citation 或稳定定位信息，以及明确允许公开分发和商业再分发的 license evidence。项目 metadata MUST 区分 source data 与 Moltage 所做的筛选、单位转换或整理；在这些事实无法确认时，系统不得将候选数据作为发布资源接受，也不得用推测补全 provenance。

#### Scenario: Candidate source satisfies the release boundary

- **WHEN** 候选 radius dataset 的来源、版本或稳定定位信息、license 和适用条件均已核验，且 license 明确允许公开与商业再分发
- **THEN** 该数据可以进入后续 replacement review，并在 packaged resource metadata 中记录相应 provenance 和 attribution obligations

#### Scenario: Redistribution terms remain unresolved

- **WHEN** 候选数据的来源或公开与商业再分发条件无法从权威材料确认
- **THEN** replacement MUST 保持 blocked 或 unverified，现有数据不得被该候选数据静默替换

### Requirement: Explicit radius semantics and compatible coverage

Covalent radii 与 van-der-Waals radii MUST 分别记录其 radius definition 和单位；Moltage 使用的数值 MUST 以 Å 表达，或通过有记录且可复核的转换得到 Å。替代数据 MUST 至少覆盖当前各自已支持的元素集合：covalent radii 为 H–Bi，van-der-Waals radii 为 H–Bi 中除 Pm 外的元素；任何缩减覆盖范围的行为都需要单独、明确确认。

#### Scenario: Replacement preserves supported elements

- **WHEN** 已审核的替代数据被整理为 Moltage resources
- **THEN** 每个当前支持元素都具有 canonical symbol 和有限、正值的 Å radius，并且 metadata 明确说明该表所采用的 radius definition

#### Scenario: Required element is absent or invalid

- **WHEN** 计算或显示请求需要的元素在相应 dataset 中缺失，或其 radius 不是有效正值
- **THEN** 系统 MUST 明确报告 unsupported or invalid radius data，不得使用另一元素、另一类 radius 或隐式常数作为 fallback

### Requirement: Deterministic offline resources

通过审核的 radius datasets MUST 随 Moltage 作为静态 resources 分发，并在离线环境中提供确定性结果。正常运行和默认测试 MUST NOT 依赖网络查询、开发者本机数据或外部 scientific database；resource validation MUST 检查必要 provenance、单位、定义和数据完整性，而不得永久绑定被替换来源的特定 DOI。

#### Scenario: Clean offline installation loads radius data

- **WHEN** 用户在没有网络和开发者本地配置的干净安装中调用依赖 radius data 的现有功能
- **THEN** 系统从 packaged resources 加载已审核的数据，并执行与在线服务无关的完整性验证

#### Scenario: Resource metadata is incomplete

- **WHEN** packaged radius resource 缺少必要的 provenance、license、单位或 radius definition metadata
- **THEN** 系统 MUST 明确拒绝该 resource，而不是假定旧来源或默认语义

### Requirement: Replacement regression evidence

替换数据前 MUST 记录当前表与候选表的元素覆盖和逐元素数值差异，并用 synthetic fixtures 验证依赖 radius data 的 connectivity、steric placement 和 display-related behavior。该 change MUST NOT 为维持旧结果而静默修改现有算法、threshold 或 multiplier；有意义且无法解释的行为差异 MUST 在 replacement 完成前报告并取得用户明确确认。任何获批准的 compatibility 处理 MUST 限定到已证实的 consumer，不得改变无关 scientific behavior。

#### Scenario: Replacement retains expected lightweight behavior

- **WHEN** 候选数据完成逐元素比较并运行受影响的 synthetic regression tests
- **THEN** 结果显示现有算法与阈值未被修改，且既有受保护行为没有未经解释的明显回归

#### Scenario: Candidate values alter protected behavior

- **WHEN** 候选数据导致 established synthetic connectivity、steric selection 或 display behavior 出现有意义的变化
- **THEN** 该差异 MUST 被记录为待审核 evidence，replacement 不得通过自动调整算法或阈值继续完成

#### Scenario: Packaged Au template requires an explicit topology cutoff

- **WHEN** 获批 single-bond covalent radius 在不改变通用 connectivity multiplier 的情况下无法连接既有 packaged Au59 bulk template，且用户明确批准模板专用 cutoff
- **THEN** 系统 MUST 仅为 packaged Au59 template 使用 `2.89 angstrom` 内部 Au–Au cutoff，并保持通用分子 connectivity、covalent-radius resource 和 Au electrode coordinates 不变
