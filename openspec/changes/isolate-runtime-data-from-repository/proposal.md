## Why

历史开发过程中留下的 `tmp/` 与一个直接位于 repository root 的 density-task UUID 目录混淆了 source tree、开发产物和用户运行数据。当前生产入口已将新的 density cache 放入 `%APPDATA%\Moltage\density_results`，但 repository 仍保留针对单个 UUID 的临时 ignore 规则，且缺少防止 current working directory 再次成为运行时存储位置的明确契约和回归验证。

## What Changes

- 将 per-user persistent state、system temporary data、user-selected output、remote calculation workspace、packaged static resources 与 developer artifacts 的边界定义为可验证行为。
- 为 density results cache 提供单一的 application-data path owner，并确保生产入口不依赖 current working directory。
- 增加 targeted regression coverage，证明正常构造 density workflow service 不会在 current working directory 创建运行时文件。
- 保留按类别划分的 `/tmp/`、cache、build、dist 与 copied application-state ignore 规则，移除只匹配一个历史 UUID 的规则；不添加会排除合法 scientific fixtures 的全局扩展名规则，也不以宽泛 UUID pattern 隐藏路径回归。
- 在单独授权的 apply 阶段处理已识别的本机遗留：删除无 repository 引用的开发期 `tmp/`，并在校验无冲突及内容 hash 后将 orphaned density manifest 移出 repository root、保存在 per-user application-data boundary 内，而不是直接删除。
- 仅更新与上述边界直接相关的 maintained documentation；不改变 scientific inputs、remote manifests、scheduler behavior 或用户明确选择的 export destinations。

## Capabilities

### New Capabilities

- `runtime-data-boundary`: 定义 Moltage 本地持久状态、临时文件、用户输出、开发产物与 repository/package resources 之间的可验证存储边界。

### Modified Capabilities

None. 当前不存在 main OpenSpec specs。

## Impact

- Affected code: `src/moltage/app/paths.py` 与生产 density-service composition point。
- Affected tests: application-data path 与 density-service construction 的 focused unit tests。
- Affected repository metadata: `.gitignore` 中的历史 UUID 例外及相关分类注释。
- Affected documentation: 仅补充本地 runtime/development data ownership。
- Compatibility: 不修改 persisted schema、remote project layout、existing calculation data、scientific/file formats 或 workflow states；旧 `%APPDATA%\AIMS-Transport\density_results` 保持原位且不在本 change 中自动复制或删除。
- Operations: 本机遗留目录的移动/删除是独立、可审计的 destructive step，必须在 apply 获得明确授权后执行；不连接 SSH/HPC，也不扫描其他目录。
