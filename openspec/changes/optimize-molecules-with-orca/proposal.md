## Why

Moltage 当前的远程计算链路只建模 FHI-aims/AITRANSS，服务器设置也把不同程序的运行时配置放在同一界面，无法清楚地配置和执行 ORCA。0.2.x 的第一步需要在保持既有 FHI-aims workflow 不变的前提下，建立从已导入分子结构到可验证 ORCA 优化结果的独立链路，并为后续用户主动选择的 WBL transmission 阶段提供可信输入证据。

## What Changes

- 将 Server Settings 组织为 shared `General / Cluster` 页面，以及并列的 FHI-aims、ORCA program 页面；AITRANSS 作为 FHI-aims toolchain 的子标签置于 FHI-aims 页面内。各 program/runtime 独立配置和发现，未配置无关 runtime 不阻止保存或使用当前 server profile；未来 Gaussian 属于与 FHI-aims、ORCA 同级的独立 program，但不在本 change 中创建占位或功能。
- 为 server profile 增加可选 ORCA runtime configuration，最少只保存经验证的绝对 `orca` executable、其环境准备方式和实际识别到的版本证据；旧 profile 继续兼容且 ORCA 保持未配置。
- 在 Slurm profile 上提供通用、有界、版本无关的 ORCA 自动发现：检查当前/已配置环境的 `PATH` 和匹配 ORCA 的 module catalog，验证 canonical regular executable；多个候选必须由用户选择，不扫描整个 filesystem、不依赖站点路径或特定版本。LSF 本阶段只支持手动配置和验证，不提供自动发现。
- 增加结构化 ORCA optimization settings：method、basis、可选 dispersion、charge、multiplicity、optimization convergence、coordinate system、SCF convergence、parallel ranks、walltime、scheduler memory，以及可选 `%MaxCore`。Method 和 basis 没有默认值；charge 默认为 `0`、multiplicity 默认为 `1`，但必须由用户确认适用于当前体系。
- 以确定性方式生成 `orca_opt.inp` 和 Slurm/LSF submission script。ORCA 必须通过已配置的绝对 executable 直接启动，parallelism 由 `%pal nprocs` 与 scheduler task slots 一致表达；不使用 `srun`、`mpirun`、运行时 `which` 或 raw ORCA keyword 输入。
- 将 ORCA optimization 建模为独立 workflow，并保守迁移既有 project：旧项目保持原 FHI-aims/AITRANSS 四步语义；新 ORCA 项目只创建自身的 optimization 状态，不预先声称 WBL stage 已存在或成功。
- 分别保存 scheduler、ORCA program termination 和 optimization convergence 证据。只有 scheduler 成功、ORCA normal termination、明确 convergence marker 和有效 `orca_opt.xyz` 全部满足时才报告 optimization success；`.gbw` 缺失只影响后续 WBL readiness，不否定已证实的优化成功。
- 支持用户在优化完成后另行选择 ORCA frequency calculation，使用与优化一致的 method、basis、charge 和 multiplicity，并选择 `FREQ` 或 `NUMFREQ`。频率是可选独立任务，其失败、未验证或 imaginary-mode 结果不覆盖 optimization success。
- 使用 synthetic fixtures 覆盖 Slurm/LSF rendering、runtime discovery/validation、profile/project migration、ORCA input/output parsing、提交幂等性、恢复、GUI 和既有 FHI workflow regression；不在默认测试中连接真实 HPC 或运行 ORCA。
- 只有实现、focused tests、full offline suite 和另行获批的真实 Slurm acceptance 全部通过后，才把产品版本提升为 `0.2.0`；本 proposal 不修改版本。

## Capabilities

### New Capabilities

- `orca-runtime-configuration`: 定义 server settings 的程序级隔离、ORCA runtime persistence、Slurm bounded discovery、LSF manual configuration、候选验证和 profile migration。
- `orca-molecule-optimization`: 定义 ORCA optimization 的结构化输入、scheduler submission、状态证据、结果恢复和既有 workflow compatibility。
- `orca-frequency-verification`: 定义优化后可选的独立 `FREQ`/`NUMFREQ` 任务、继承字段、结果证据和 imaginary-mode classification。

### Modified Capabilities

- None. 当前尚无 main OpenSpec specs。

## Impact

- Affected domain/persistence: `ServerProfile` runtime data、`server_profiles.json` migration、`CalculationProject`/remote manifest 的 workflow discriminator 与 ORCA stage records；既有 profile 和 FHI-aims/AITRANSS project behavior 保持兼容。
- Affected GUI: Server Settings/runtime configuration、项目创建/提交、优化参数、项目状态/恢复，以及复用的 molecule viewer；不同程序的必填项保持相互独立。
- New implementation surfaces: ORCA-specific settings models、version-aware allowlists、input/script renderers、runtime discovery/verification、output/XYZ/frequency parsers、submission and recovery orchestration；不得通过重命名 FHI-specific code 假装复用。
- External systems: SSH + Slurm/LSF submission；本 change 的自动 ORCA discovery 仅支持 Slurm，LSF 仅手动配置。真实集群 compatibility 只有在用户单独授权并实际验收后才能声称。
- Scientific semantics: optimization convergence、frequency evidence 和 WBL readiness 分开记录；未运行或缺失的 evidence 必须标记为 `UNVERIFIED`，不得 silent fallback 或推断成功。
- Non-goals: 不实现 WBL transmission、ORCA wavefunction analysis、ORCA installation、raw input/directive editors、其他 scheduler、remote subsystem 重构，或与本链路无关的 cleanup。
