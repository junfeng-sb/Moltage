## Context

动机见 `proposal.md`。现有 `tools/run_tests.ps1` 已支持显式 `-Tests` 和 `-Full`，无需修改 runner。开发规则目前缺少统一的 risk-based 边界；`README.md` 的 full-suite 条件也需要同步。

等待代码位于六个 test modules：cluster/server profiles、projects、status refresh、molecule-viewer shutdown、transport-convergence viewer。原 timeout 分别为 3、3、3、2、5、5 秒；后两处 teardown 允许 `fail_on_timeout=False` 并返回条件结果。

当前安装为 PySide6 `6.10.1`、VTK `9.7.0`。shared viewer 的 `closeEvent` 仅调用 QVTK `Finalize`；上游 QVTK 另在 parent `destroyed` 时调用 `close`，其 interactor 有内部 Qt timer，scene 有 renderer `StartEvent` observer。普通 parent close/delete、reparent、native destroy 和 pending hover 的最小 probes 尚未复现所报 Windows 错误；这不是已确认根因。

进一步的最小 probe：`UseTimersOn -> StartRotate -> viewer.close` 后内部 timer 仍 active、interactor 仍 enabled；随后 `EndRotate` 触发 `failed to get valid pixel format` / `Failed to initialize OpenGL functions` 并以 exit code 1 退出。这证明存在 finalize 后仍可运行交互清理的局部 lifecycle 缺口，但尚未复现完全相同的 `wglMakeCurrent error: 6`。

实施后的 native subprocess regression 能稳定验证上述局部缺口已关闭，focused/direct integration tests 也未输出该错误；但唯一一次 full offline suite 在多个测试阶段仍复现 exact `wglMakeCurrent ... code 6`。这说明局部关闭顺序不是完整根因，剩余触发依赖更广的 viewer ownership、跨测试顺序或 native window lifecycle。进一步处理将需要超出本 change 的系统性 lifecycle 调查，因此本轮保留可证实的局部修复并明确记录未解决证据。

## Goals / Non-Goals

**Goals:** 直接复用现有 runner 和 test-support 组织，统一验证规则与等待逻辑；对 VTK 只实施有可重复因果证据的局部 cleanup。

**Non-Goals:** 不改变产品行为、科学参数或 schema；不重构 GUI tests、cross-test imports、markers、viewer lifecycle 或 packaging，不关闭 VTK error output。

## Decisions

1. `AGENTS.md` 记录完整的最小 validation policy；development skill 对应 Validate section 使用同一规则，`docs/MAINTENANCE.md` 加简短 OpenSpec development 说明，README 同步 full-suite 启动条件。无需新增 config 或 framework。
2. 新增 `tests/unit/qt_test_support.py` 的一个 `wait_until`：monotonic deadline、timeout 秒、Qt event processing、短间隔 polling；明确 timeout failure 或 `fail_on_timeout=False` 返回 `False`。六处仅保留 thin adapters，以保留现有 test call sites、错误信息和默认等待时长。不引入 mixin 或重组 imports。
3. tight-binding hover 等待实际 highlight/label 状态；toolbar 等待 native-window/control/layout 条件。固定时间不是成功条件；helper 保留 bounded failure。
4. VTK cleanup 局限于现有关闭路径：先停止 viewer timers 和 interaction state，再 disable style/interactor、停止 QVTK timer、移除 scene 自有 renderer observer，最后 finalize。main-window 和 tight-binding owner 在 native parent 销毁前显式关闭其 viewers。不建立新的 lifecycle manager，也不处理重新打开已关闭 viewer。使用真实 native subprocess验证 active-rotation close 和多 viewer/deferred delete；full-suite exact `error: 6` 仍存在时，按实际 evidence 报告并停止，不以屏蔽输出或扩大重构代替修复。
5. strict validation 在 proposal 完成后一次、apply 完成后一次；本轮不 archive，因此不执行 archive validation。最后仅一次 full offline suite 是用户明确要求的 acceptance，而非重新设为日常默认。

## Risks / Trade-offs

- wall-clock deadline 比原累计 qWait 计数更准确，在繁忙环境可能更早发现真正 timeout → 保留各测试原秒数，错误明确失败，不增加无界等待。
- toolbar 条件可能尚未处理 resize event → polling 每次先 processEvents，再检查完整可见状态，而非仅检查尺寸。
- Windows driver/Qt/VTK 组合或跨测试顺序可能影响复现 → 保留版本、触发步骤及实际 stderr 证据，不把“本轮未出现”写成根因已修复。
- 当前 working tree 有已验收的其他功能修改 → 仅叠加本 change，不 stage/commit，也不修复无关 full-suite 失败。
