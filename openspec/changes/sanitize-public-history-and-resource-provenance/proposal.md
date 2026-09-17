## Why

拟公开的 current tree 与现有 sanitized root commit 仍包含少量可关联到真实 runtime、HPC capture、安装环境或未公开科研体系的测试证据。当前 GitHub repository 仍为 private，因此应在任何 public visibility 或 clean-root publication 操作前，以明确 synthetic evidence 替换这些内容。

## What Changes

- 将 LSF `bhist` 测试片段中的 plausible real Job ID、Job Name 和 timestamp 替换为明确的 synthetic values，同时保持 timeout parsing 语义不变。
- 将 runtime discovery tests 中保留的 deployment-derived installation names/versioned paths 替换为 generalized synthetic paths，不修改 production discovery behavior。
- 将不明确的 project UUID 和 research-associated test name 替换为明显 synthetic identifiers。
- 删除无引用、旧品牌且来源记录不明确的 `docs/ui_reference/future_light.png`。
- 保留 current tree 已完成的 `.gitignore` runtime UUID removal 和通用 runtime/cache ignore rules。
- 对受影响 tests、full offline suite 与 current-tree privacy/provenance patterns 执行验证。
- 将 Au electrode coordinates、Au placement defaults 与 covalent/van-der-Waals radii 的来源核验记录为独立的 public-release prerequisite follow-up change，本 change 不修改其内容。

## Capabilities

### New Capabilities

None. 本 change 只清理 publication candidate 中的 test/resource evidence，不改变 Moltage runtime observable behavior，因此 `.openspec.yaml` 使用 `skip_specs: true`。

### Modified Capabilities

None.

## Impact

直接影响 `.gitignore` 的既有未提交 removal、相关 unit-test literals、一个无引用 UI reference image，以及本 change 的 OpenSpec artifacts。Scheduler/runtime/scientific code、Au electrode generation、scientific parameters、Git history、remote repository、license 与最终 publication repository 均不在 scope 内。
