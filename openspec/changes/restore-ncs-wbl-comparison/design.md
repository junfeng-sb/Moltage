## Context

已只读核对最初独立脚本：完整 Alpha MO 系数通过 SVD 得到对称 Löwdin 分量，并从同一分解恢复作用于 Beta 的 overlap 平方根，然后在所选 S 上对全部 p 行的模平方求和。当前实现用已有 AO overlap 的正平方根计算 Löwdin 量；NCS 新增实现却套用了径向筛选与方向投影。旧图来自独立报告脚本，不是 Git 中丢失的 GUI。

## Decisions

1. 增加明确的 `S_ALL_P_LEGACY` mode，仅作为 NCS Auto 的解析结果，并允许显式选用。向量为末端 S 的每个 p AO 的单位基向量，direction 为 None，不要求 def2 径向映射；完整 p triplet 必须可验证。已有显式 directional/manual 模式按原定义执行。Auto 下同时提供 direction 属于矛盾参数，明确要求用户选择 directional 模式，不静默忽略。
   自动 linker 检测仍通过既有 topology 识别末端 S；用户在 Advanced 显式覆盖为 NCS 时，all-p 数学投影只验证所选原子为 S 与完整 p AO，不再要求 connectivity 提供方向。该手动标签不是额外的结构识别证明。legacy population 保留任意小的正权重，不使用 directional projector 的既有数值截断。
2. 保持 `Gamma_L/R,n = Gamma_0,L/R * w_L/R,n` 和全 MO 独立共振求和不变。旧模型会包含紧缩 p 基函数，不宣称它是纯 3p 或已校准的接触模型。用 synthetic 非正交 AO basis 与完整、S-正交归一的 MO 矩阵比较旧 SVD 与当前 overlap 路径。
   这验证了该条件下的数学等价，不是更换当前 Löwdin 实现或承诺任意输入逐位相同。舍入或不满足 S-正交归一的系数可能导致两条路径不同；当前 overlap 最小特征值必须大于 1e-10 的原有门槛不变，近线性相关的大/弥散基组可能明确失败，而旧 SVD 脚本可能仍返回数值。此类真实输入尚未验收。provenance 使用 `LEGACY_SELECTED_S_ALL_P_NO_RADIAL_OR_DIRECTION_FILTER`，不将手动 NCS 标签包装成已证实的 terminal topology。
3. 只从已有 JSON 的 settings、summary 和 orbital contributions 补齐 presentation 的只读模型/轨道详情；不从曲线反推轨道，不要求迁移已存 artifact。详情缺失时明确显示 unavailable，不伪造参数。
   v1/v2 继续按原有 CSV/summary 契约恢复结果；缺失、不完整或不一致的可选 report detail 只使该侧栏明确显示原因，不将原可读项目降级。v3 的 report metadata 缺失或不一致明确失败。SVG 用固定 report viewBox 随请求像素尺寸整体缩放，避免裁切侧栏。
4. GUI 使用现有 transmission canvas，添加 WBL 专属参数/贡献报告与轨道位置标记；共享 AITRANSS 呈现行为不变。每个 spin 的标记按 T_n(E_F) 排名，位置是该 MO 的 epsilon-E_F 和对应全 MO 曲线。超出显示范围的标记隐藏但侧栏仍列出。图像导出包含侧栏，TXT 保持全部原始样本。
5. 图表恢复红 Alpha、蓝 Beta，raw spin sum 保留为较细虚线以避免遮挡；closed-shell 仍为一条总曲线。默认对数轴、排版指数/下标不变；用户设置仍可覆盖样式和尺寸。

## Non-Goals

不承诺恢复不同输入/参数的曲线数值，不做真实服务器重算，不改其他 linker、科学常数、自旋归一化、几何或计算架构，不把旧研究数据带入仓库。
