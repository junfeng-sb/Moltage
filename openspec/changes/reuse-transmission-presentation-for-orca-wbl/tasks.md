## 1. Shared Presentation

- [x] 1.1 新增 `gui/transmission_canvas.py`，迁移竖排轴标题、轴范围对话框、chart view 与共享格式化助手，并支持曲线序列探针与读数。
- [x] 1.2 在 `gui/transmission_settings.py` 中把单曲线设置改为非空曲线序列，Curve 页按曲线分组，保留第一条曲线的既有控件 object name。
- [x] 1.3 新增 `TransmissionCanvasView`，集中 chart/坐标轴/镜像轴/Fermi 参考与标记/探针/五页设置应用/Reset View/画布导出。

## 2. Workflow Views

- [x] 2.1 `gui/transmission_view.py` 改为基于共享呈现层的 AITRANSS 适配，保持既有可观察行为与 object name。
- [x] 2.2 `gui/orca_wbl_view.py` 改为基于共享呈现层，按 spin treatment 提供一条或三条曲线，保留 WBL provenance/summary/limitation 文本与 `HYPOTHESIS` 标注。
- [x] 2.3 WBL Fermi 参考线退出图例，并在图内以排版下标显示 `T(E_F)` 注记。

## 3. Typography

- [x] 3.1 `orca/wbl_artifacts.py` 的 SVG 横轴标题改用 `<tspan>` 下标 `F`，保持其余确定性输出不变。

## 4. Validation and Documentation

- [x] 4.1 扩充 `tests/unit/test_orca_wbl_view.py`：五页设置可打开并应用、坐标轴范围可改、Reset View 恢复默认、多曲线探针读数、图例不含 `E_F`。
- [x] 4.2 更新 `tests/unit/test_transmission_view.py` 与 `tests/unit/test_transmission_settings.py` 以适配曲线序列，并确认 AITRANSS 行为不变。
- [x] 4.3 在 `tests/unit/test_orca_wbl.py` 中断言 SVG 横轴使用排版下标。
- [x] 4.4 运行受影响模块的 focused tests（`tools/run_tests.ps1 -Tests`）。
- [x] 4.5 更新 `docs/ARCHITECTURE.md` 与中英双语用户手册的 WBL 结果视图说明，并更新 `CHANGELOG.md`。
- [x] 4.6 对本 change 运行一次 `openspec validate --strict`。
