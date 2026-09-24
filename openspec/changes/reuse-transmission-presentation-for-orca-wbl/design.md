## Context

`gui/transmission_view.py` 目前同时承担两件事：AITRANSS `TransmissionResult` 的适配，以及一整套可复用的 Qt Charts 呈现逻辑（自定义刻度绘制、镜像坐标轴、上标对数标签、左侧竖排 Y 标题、曲线探针读数、Fermi 注记、五页设置应用、按画布尺寸导出）。`gui/orca_wbl_view.py` 则完全独立地重建了一个固定样式图表。两者唯一共享的是 `gui/log_axis.py` 的 `10ⁿ` 标签工具。

## Goals / Non-Goals

- Goals: 让 ORCA WBL 与 AITRANSS 使用同一份呈现实现；支持多曲线；统一 `E_F` 排版。
- Non-Goals: 不改变 WBL 科学模型、artifact schema、持久化或 remote 行为；不改变 AITRANSS 的既有可观察呈现行为；不引入通用绘图框架或插件机制；不新增主导轨道标注等未被请求的内容。

## Decisions

### 1. 抽取中立的共享呈现模块而不是互相 import 视图模块

新增 `gui/transmission_canvas.py`，承载 `_VerticalAxisLabel`、`AxisRangeDialog`、`TransmissionChartView`、共享格式化助手，以及新的 `TransmissionCanvasView(QWidget)`。`gui/transmission_view.py` 与 `gui/orca_wbl_view.py` 各自只保留“把本工作流的已验证结果适配成曲线定义 + 本工作流特有的说明文字”。这样 ORCA 视图不会依赖 AITRANSS 视图模块，`gui/transmission_canvas.py` 也不依赖任何 workflow 数据类型。

### 2. `TransmissionCanvasView` 由子类继承而不是被组合

两个视图都需要在图上下方插入各自的 provenance/summary/limitation 文本，且既有测试直接访问视图上的 `_chart`、`_chart_view`、`_energy_axis` 等属性。继承使这些属性天然保留在最终视图对象上，避免为兼容而写转发属性。

### 3. 曲线序列取代单曲线

`TransmissionVisualSettings.curve: CurveVisualSettings` 改为 `curves: tuple[CurveVisualSettings, ...]`（非空）。Curve 设置页按曲线生成一组控件，第一条曲线保留既有 object name，后续曲线以序号后缀区分。探针以 `(curve_index, point_index)` 标识最近的正值样本；多曲线时读数首行显示曲线名。AITRANSS 仍然只提供一条曲线，其页面布局与行为不变。

### 4. `E_F` 排版

Qt Charts 图例只渲染纯文本，无法显示下标，因此 WBL 的 Fermi 参考线与 AITRANSS 一致地不进入图例（垂直虚线的含义已由 `Energy − E<sub>F</sub> (eV)` 轴标题给出）。SVG artifact 使用 `<tspan>` 下标渲染 `E − E_F (eV)` 的 `F`。Project Manager 详情面板的纯文本证据行不在本次 scope 内。

## Risks / Trade-offs

- 触及已冻结的 AITRANSS 呈现实现。缓解：保持公开行为、object name 与探针交互不变，并运行既有 transmission 视图与设置测试。
- 新 SVG 字节与旧 artifact 不同。缓解：只影响新生成的 artifact；读取路径只使用 JSON 与 CSV 重建呈现，不重新渲染或比较 SVG。
