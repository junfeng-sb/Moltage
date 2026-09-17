## 1. Runtime Path Ownership

- [x] 1.1 在 `src/moltage/app/paths.py` 增加 `density_results_path()`，复用既有 safe `%APPDATA%\Moltage` resolution，并以 focused unit test 验证 safe path 与缺失 `APPDATA` 时的 explicit failure。
- [x] 1.2 将 production density-service composition point 改为使用 `density_results_path()`，保留 `DensityWorkflowService` 的 explicit cache injection，并验证 service construction 在不同 current working directory 下既解析到 per-user root 又不创建 working-directory entry。

## 2. Repository Boundary Metadata

- [x] 2.1 更新 `.gitignore`，保留 `/tmp/`、`/density_results/`、cache、bytecode、virtual-environment、build/dist 与 root copied application-state category rules，只移除已迁出数据对应的 exact UUID rule；使用 `git check-ignore --no-index -v` 验证类别规则生效且任意 UUID root、`.in`、`.out`、`.dat`、`.cube`、`.xyz`、`.mol` 不会被宽泛排除。
- [x] 2.2 仅在 `docs/ARCHITECTURE.md` 与 `docs/CONFIGURATION.md` 的现有 local-state sections 中记录 density cache ownership 和 current-working-directory independence，并通过 diff inspection 确认没有改写无关 workflow/scientific documentation。

## 3. Offline Validation

- [x] 3.1 运行 `tests/unit/test_app_paths.py` 及 production composition point 对应的 focused test，记录 collected/passed/failed 与 exit code，并确认测试未产生 non-ignored repository artifact。
- [x] 3.2 运行 `tools/run_tests.ps1 -Full`，记录完整 collected/passed/failed 与 exit code；若无法执行则明确标记 unverified，不得以其他检查替代。
- [x] 3.3 使用 `git status --short`、targeted write-path search 与 repository-root inventory 确认 tracked implementation 之外仅存在预期 ignored developer artifacts，且没有新的 UUID runtime directory、local application state 或 scientific calculation output。

## 4. Explicitly Authorized Local Cleanup

- [x] 4.1 在任何 destructive action 前，只读验证已调查的两个 exact source paths、UUID manifest identity/state、resolved absolute paths、destination absence、file count/byte length/SHA-256，并确认 targets 均与预期 historical repository 或 `%APPDATA%\Moltage\density_results` boundary 一致；任何差异均停止 cleanup 并报告。
- [x] 4.2 在 apply 已获用户明确授权的前提下，将 exact orphaned density manifest 复制到 `%APPDATA%\Moltage\density_results/<task-id>/`，比较 source/destination byte length 与 SHA-256 后才删除 source file 和 empty UUID directory，并通过 post-operation existence/hash checks 验证；不得覆盖 destination 或处理其他 UUID。
- [x] 4.3 在 apply 已获用户明确授权且重新确认无 repository references 后，删除 exact historical `tmp/` directory，报告删除的 file count/bytes 与不可恢复性，并验证未删除 tracked content 或其他 ignored directories。
- [x] 4.4 确认 former `%APPDATA%\AIMS-Transport\density_results` caches 未被复制、修改或删除，并以 final `git status --short --ignored` 区分 clean tracked state 与仍存在的正常 ignored developer caches。
