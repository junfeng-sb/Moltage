## Why

用户已明确要求直接实施两项修改：允许成功的 ORCA WBL Step 2 用新参数重新运行并覆盖旧结果；本机记住确认后的 Bond Detection 系数。当前代码分别在状态判断/结果目录存在检查和窗口初始化常量处阻止这两种行为。

## What Changes

- 成功或失败的 Step 2 均可重新设置并运行；运行中的 Step 2 仍禁止重复启动。
- 提交界面预填先前参数并明确提示成功后替换结果。只复用既有优化，不提交新的 ORCA 优化。
- 新结果经校验后替换 `wbl/`；正常失败恢复旧结果，无法确认远端状态时明确停止，不盲目清理或覆盖。
- OK 后在现有 per-user preferences 文件保存成键系数；启动恢复，Cancel 不保存。

## Capabilities

### New Capabilities
- `wbl-rerun-and-local-bond-preference`: WBL 重新计算与本机成键系数保存。

### Modified Capabilities

无额外 scientific formula、scheduler 或支持平台变更。

## Impact

ORCA WBL domain/application/GUI、专用结果发布边界、现有用户设置 repository、相关 synthetic tests 和双语手册。用户设置 schema 2 -> 3；老文件继续使用 1.1 作为未保存该项时的默认值，不修改 project/server schema。无真实 HPC 操作、提交或打包。
