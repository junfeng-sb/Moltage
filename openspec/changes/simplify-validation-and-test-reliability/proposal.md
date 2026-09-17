## Why

日常局部修改不应反复承担 full offline suite 和全量发布审计；现有 GUI tests 的固定延时与重复等待实现也降低了验证的稳定性。本 change 仅统一验证规则和 bounded polling，并调查 Windows Qt/VTK teardown 的 `wglMakeCurrent failed ... error: 6`。

## What Changes

- 在 `AGENTS.md`、`moltage-development` skill、现有开发说明中明确 focused / subsystem + direct integration / high-risk full offline validation 的边界。
- 限定 independent Claude audit 和 pre-public 全量审计的适用范围；strict validation 仅在 proposal、apply、archive 边界执行，artifacts 未变化时不重复。
- 将指定的两个裸 `QTest.qWait` 改成 bounded condition polling；现有六处 `_wait_until` 共用一个 test-support helper，timeout 使用秒并保留调用方等待时长和失败行为。
- 定位 Qt/VTK teardown 最小触发路径；仅在有明确局部修复及回归证据时实施。不屏蔽错误；若需要 lifecycle 重构，记录结论并停止该部分。
- 本轮最终只运行一次 full offline suite。

## Capabilities

### New Capabilities

None。属于 development policy、test-support 和局部资源清理，不新增产品能力；使用 `skip_specs: true`。

### Modified Capabilities

None。不改变科学行为、persisted schemas、scheduler、文件格式或产品功能。

## Impact

- `AGENTS.md`、`.agents/skills/moltage-development/SKILL.md`、`docs/MAINTENANCE.md` 和 `README.md` 的 validation wording。
- `tests/unit` 中指定的两处固定等待、六处等待 helper 及最小 helper/teardown regression tests。
- 如证据充分，仅 shared molecular viewer 的资源释放顺序和必要的直接 owner cleanup。
- 不修改 generated OpenSpec skills、`openspec/config.yaml`、其他 active changes、packaging、pytest markers 或 cross-test imports；不安装依赖、不连接真实 HPC、不 commit/archive。

用户已明确授权创建并实施本最小 change；本轮完成 planning 后进入 apply，无需额外授权普通离线验证。
