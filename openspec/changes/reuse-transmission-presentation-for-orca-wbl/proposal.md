## Why

ORCA WBL transmission 结果视图当前使用一套独立的固定样式图表：坐标轴范围、刻度模型、曲线样式、画布与导出尺寸、镜像坐标轴和曲线读数全部不可调整，`Settings > View...` 只弹出一条 "styling is fixed" 提示。同一应用内的 AITRANSS transmission 视图早已具备经过评审的完整呈现控制。用户明确要求 WBL 复用 AITRANSS 的全部图像显示功能（含坐标轴调整），并要求 `E_F` 以 E 加下标 F 的排版形式显示，而不是 ASCII 写法。

## What Changes

- 把现有 AITRANSS transmission 的 Qt Charts 呈现层抽取为共享 GUI presentation 模块，并从单曲线扩展为曲线序列；AITRANSS 视图的可观察行为保持不变。
- ORCA WBL 结果视图改为使用同一呈现层：五页 View Settings（X Axis / Y Axis / Ticks / Curve / Canvas）、双击坐标轴/曲线/画布打开对应页、可选镜像上/右坐标轴、显式刻度模型、`10ⁿ` 对数刻度标签、曲线读数探针（悬停、固定、键盘步进、空白处释放）、Reset View 以及按画布尺寸导出当前视图。
- Curve 设置页按曲线列出：closed-shell 结果一条 Total 曲线；unrestricted 结果 Alpha / Beta / Spin sum 三条曲线，各自可设颜色、线宽、线型与图例标签。
- WBL 图中的 Fermi 参考线不再以 `E_F` 文本出现在图例中；WBL SVG artifact 的横轴标题改为 `E − E` 加排版下标 `F`；GUI 中 `E_F` 继续使用 `<sub>` 排版。
- 不改变任何 WBL 科学计算、结果 JSON/CSV schema、持久化状态、remote 行为或 AITRANSS 结果语义。

## Capabilities

### Modified Capabilities

- `orca-wbl-transmission`: 结果呈现由固定样式改为共享的可调 transmission 呈现层，并统一 `E_F` 排版。

## Impact

- 代码：新增 `src/moltage/gui/transmission_canvas.py`；修改 `src/moltage/gui/transmission_view.py`、`src/moltage/gui/transmission_settings.py`、`src/moltage/gui/orca_wbl_view.py`、`src/moltage/orca/wbl_artifacts.py`。
- GUI session 内部的 `TransmissionVisualSettings.curve` 单曲线字段改为 `curves` 曲线序列；该记录不持久化，不影响任何 schema。
- 新生成的 `orca_wbl_transmission.svg` 字节因排版改变而改变；既有已持久化 artifact 及其 hash 不被重写或重新校验。
- 文档：`docs/ARCHITECTURE.md`、`docs/user_manual/06_orca_workflow.md`、`docs/user_manual_zh/06_orca_workflow.md`、`CHANGELOG.md`。
