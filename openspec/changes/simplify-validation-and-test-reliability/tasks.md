## 1. Validation Policy

- [x] 1.1 更新 `AGENTS.md`、development skill 和现有开发说明的 risk-based validation / audit / strict-validation 边界；通过只读对照确认一致、不改变 runner 或 generated skills。

## 2. Bounded Qt Waiting

- [x] 2.1 新增单一秒制 bounded polling helper，覆盖 immediate success、event-driven success、明确 timeout failure、non-failing teardown 返回值和 predicate exception；运行 helper focused tests。
- [x] 2.2 六处重复 `_wait_until` 仅委托 shared helper，保持原 2/3/5 秒及失败信息；运行对应 GUI focused tests。
- [x] 2.3 将 tight-binding hover 和 toolbar 的两个裸 `qWait` 替换为 observable condition polling；运行这两个模块并检查不再存在指定裸等待。

## 3. Local Qt/VTK Teardown

- [x] 3.1 记录最小 probe 的实际 timer/interactor/GL evidence，明确 exact `wglMakeCurrent error: 6` 是否复现；证据不足的部分停止，不作推测性重构。
- [x] 3.2 实施明确的局部 cleanup：停止 timers/interaction、disable interactor、detach scene observer、native parent 销毁前关闭 viewers；增加 native subprocess 和 cleanup-order regression，保持错误输出可见并运行 focused tests。

验证记录：shared helper、六个 adapters、两个指定等待测试和 native teardown/direct owner tests 合计 `178 passed`（37.60 秒）。skill `quick_validate.py` 通过。新增 teardown tests 首轮的两处测试编写错误已修正，不是未解决产品失败。

## 4. Final Acceptance

- [x] 4.1 运行 subsystem / direct integration focused tests，报告实际结果和任何未验证的 VTK evidence。
- [x] 4.2 最终只运行一次 `tools/run_tests.ps1 -Full`；记录 passed/failed/skipped/exit code，不修复无关失败、不重复 full suite。
- [x] 4.3 更新本 change 的实际验证记录后执行一次 apply-completion `openspec validate simplify-validation-and-test-reliability --strict`，并报告 scope deviation；不 archive/commit。

补充 direct integration：Cube、measurement、atom editing、bond torsion、preview picking、density、view settings 和 native teardown 为 `103 passed`（30.90 秒）。使用 `--capture=tee-sys` 保持 native stderr 可见，未输出所报 wgl 错误。

唯一一次 full offline suite 为 `1446 passed, 2 skipped`（141.41 秒，exit code 0）。同一运行在多个阶段仍输出 `wglMakeCurrent failed ... code 6`，共记录 165 条 `wglMakeCurrent failed` 诊断；因此 exact error 已复现，但未被本轮局部 cleanup 全面消除。已确认的 active-timer/finalize 顺序缺口有局部回归保护；剩余问题具有跨测试顺序或更广 viewer lifecycle 特征，需要超出本 change 的调查/重构，按批准边界停止，不隐藏 stderr、不推测根因。
