## Why

Moltage 已能按 server profile 保存 SSH、scheduler command、runtime 和 remote workspace，但仍依赖若干未显式配置的站点行为：Slurm/LSF admission selectors 缺失、Step 4 AITRANSS 固定调用 PATH 中的裸 `srun`，且 LSF 总是生成同一组 `span/rusage` requirement。这些假设会使相同 workflow 迁移到采用不同 scheduler defaults 或 launch policy 的服务器时失败。

## What Changes

- 在现有 `SlurmExecutionPreset` 中增加 scheduler-specific optional profile fields：Slurm `account`、`partition`、`qos`，以及 LSF `queue`、`project`。空值明确表示使用 scheduler/site default，不自动探测或猜测。
- 在现有 Slurm/LSF 配置页面加入对应的最少输入项；不创建新的通用 scheduler model，也不增加 raw directive 输入。
- 为 Slurm Step 4 AITRANSS 增加 `direct` 与 `srun` 两种 launch mode。`srun` mode 只使用已配置并经 remote verification 的绝对 `srun` path；不再生成裸 `srun`。
- 为 LSF 增加 `site-default` 与现有 `span-rusage` 两种 structured resource-requirement mode。`site-default` 不生成 `#BSUB -R`；`span-rusage` 保持当前受支持的 `span[...] rusage[mem=...G]` rendering。
- 让 FHI-aims、AITRANSS 和直接复用这些 renderers 的现有提交路径一致应用 scheduler-specific profile fields，并保持 scheduler/program/scientific state 的现有区分。
- 将 `server_profiles.json` schema 向前提升并兼容 schema 1–10：旧 optional admission fields 迁移为空；旧 LSF profile 保留 `span-rusage` 行为；旧 Slurm AITRANSS profile 仅在可形成并验证绝对 `srun` candidate 时保留原 launch behavior，否则明确要求用户补全 launch configuration。

## Capabilities

### New Capabilities

- `hpc-site-configuration`: 规定 scheduler-specific site fields、Slurm AITRANSS launch policy、LSF structured resource-requirement policy，以及 profile migration 和 fail-closed behavior。

### Modified Capabilities

- None. 当前尚无 main OpenSpec specs。

## Impact

- Affected code: `ServerProfile`/`SlurmExecutionPreset` validation and persistence；Cluster Execution Settings 和 AITRANSS runtime configuration UI；Slurm/LSF FHI-aims 与 Step 4 script rendering；scheduler/runtime preflight。
- Affected persisted compatibility surface: `server_profiles.json` schema migration；remote project layout、project manifest schema、scientific inputs 和 result semantics 不变。
- Affected validation: synthetic profile migration/round-trip、GUI field isolation、directive rendering/omission、AITRANSS direct/absolute-srun launching、LSF resource modes，以及现有 submission/retry/density paths 的 regression tests。
- External operations: proposal 和 offline implementation validation 不连接真实 SSH/HPC；任何真实服务器 acceptance 需要用户另行明确授权。
