## Why

Moltage 当前把 FHI-aims `species_defaults` 随项目和安装包分发，并在本地生成 `control.in` 时固定读取这些文件。这会使提交输入与用户实际选择的 FHI-aims 安装环境脱节；本 change 将 definitions 的唯一来源改为已配置服务器上的实际 FHI-aims installation。

## What Changes

- 移除 repository 和安装包内携带的 FHI-aims species-definition 文件，并取消所有 production packaged fallback。
- 扩展现有 FHI-aims runtime discovery：从已验证 executable 的 installation evidence 定向寻找直接包含 `light`、`tight`、`really_tight` 等 accuracy 子目录的 species root，并把验证后的路径保存到对应 server profile。
- 在现有 FHI-aims manual runtime configuration 中增加 species root 的手动输入和 remote folder selection；自动发现失败时提供明确的补全提示。
- 用户授权创建新任务后，通过现有 SSH/SFTP connection 从所选服务器只读取本次 `control.in` 所需的 element/accuracy 文件；完成路径、大小、编码和 species declaration 校验后才生成 inputs，并在任何 remote project creation 或 scheduler submission 前失败关闭。
- 保持旧 server profiles 可加载；旧 profile 在补齐 species root 前不能创建新的 `control.in`，但已有任务和已生成 `control.in` 仍可查看、恢复或复用。
- 历史 `control.in` 的解析以其已嵌入的 species blocks 为依据；重新生成的新 `control.in` 使用当前 server profile 配置的 remote definitions。
- **BREAKING**：standalone local FHI-aims input export 不再使用 bundled definitions，也不支持单独配置 local species directory；生成新 `control.in` 必须选择并连接一个已配置 server profile。

## Capabilities

### New Capabilities

- `fhi-aims-species-definitions`: 规定 per-server species-root discovery/manual configuration、远程 definitions 获取、新 `control.in` generation，以及旧 profile/历史 input 的兼容行为。

### Modified Capabilities

- None. 当前尚无 main OpenSpec specs。

## Impact

- Affected code: FHI-aims species resolver and input builders；Step 1/2、Step 3、density difference、restart submission orchestration；runtime discovery/preflight；server-profile persistence；runtime configuration GUI。
- Affected persisted compatibility surface: `server_profiles.json` 需要向后兼容的 schema migration；project and density manifests 可继续依赖既有 immutable input hashes，不要求为本 change 改变 schema。
- Affected packaging: `resources/fhi_aims/species_defaults/` 不再进入 source distribution 或 Windows installer；其他 packaged resources 不变。
- Affected validation: fake remote discovery/read tests、profile migration、GUI、input generation/recovery、submission ordering 和 packaging checks；真实 Slurm server 只用于后续经用户明确授权的 acceptance，不在 planning 或 offline validation 中连接。
