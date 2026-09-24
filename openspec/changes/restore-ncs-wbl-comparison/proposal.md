## Why

用户要求先恢复最初 NCS 计算方案用于对照，再讨论更好的科学模型；并把原独立脚本的 WBL 图表布局带回当前结果界面。此 change 记录该直接实施请求，不改其他 linker 的科学定义。

## What Changes

- NCS Auto 使用末端 S 的全部 p 型 AO Löwdin 权重，不做径向壳层筛选或方向投影；明确记录 `S_ALL_P_LEGACY`，保持 HYPOTHESIS。显式 directional/manual 覆盖保留。
- 恢复红 Alpha / 蓝 Beta、近方形绘图区、模型参数栏、每个 spin 在费米能级贡献最大的两个 MO 及其能量标记；保留可辨认的 raw spin sum、closed-shell 单曲线、TXT 导出和现有呈现设置。
- 展示信息取自当前结果，不固定旧图的 MO 数、耦合值、能量范围或项目名；旧 artifact 不重写。

## Capabilities

### Modified Capabilities

- `orca-wbl-transmission`: 补充既有 active change 的 NCS 对照模型与结果报告呈现。

## Impact

限于 ORCA WBL projector、结果呈现适配、相关 GUI、synthetic tests 与双语说明。复用当前 overlap/JSON 证据、全 MO 求和、导出及共享呈现控件；不变更 server/project schema、SCF、SSH、scheduler、其他 linker、Gamma_0 默认值或原始科学数据。不连接真实 HPC。
