## Context

See `proposal.md` for motivation and `specs/fhi-aims-species-definitions/spec.md` for observable requirements.

当前 `OfficialSpeciesLibrary` 只能从 local `Path` 读取 packaged files，Step 1/2、Step 3 和 density input builders 在 SSH connection 之前调用它。Submission request 因而携带已经完成的 input bundle。历史 restart parsing 也用同一 library 重建 `control.in` 以完成 exact round-trip。

现有 runtime search 已提供 canonical executable resolution、bounded command budget、module/environment handling、evidence-derived installation roots 和禁止 filesystem-wide scan 的边界；现有 `RemoteExecutor` 已提供 `stat`、`list_directory`、`read_bytes` 和 bounded head reads。Server profile schema 9 仍保留 legacy Slurm `modules + launch_command` 路径，该路径可能没有 structured `FhiAimsRuntimeConfiguration`，不能因本 change 被隐式转换。

## Goals / Non-Goals

**Goals:**

- 在不让 Domain/input writers 依赖 SSH 的前提下，从 selected server 获取经过验证的、仅本次需要的 species blocks。
- 保持新 task 的 pre-mutation failure boundary：definitions 完整后才允许 remote `mkdir`、upload 或 scheduler dispatch。
- 在 structured 和 legacy FHI-aims launch configuration 中使用同一个 per-profile species root。
- 让历史 `control.in` 保持 self-contained，并保留旧 profile 的读取能力。

**Non-Goals:**

- 不建立 local species-directory configuration、persistent species cache 或 bundled fallback。
- 不执行 FHI-aims、未知 setup script 或 filesystem-wide discovery。
- 不改变 supported accuracy families、species alias semantics、scheduler resource model、project layout 或 manifest schemas。
- 不借此整理现有 remote/application architecture 或其他 packaged resources。

## Decisions

### 1. Persist the authoritative path on the execution preset

在 `SlurmExecutionPreset` 增加 optional `fhi_species_defaults_path`，并将它作为生成新 input 时的 authoritative path。虽然 class 名称保留历史上的 `Slurm`，该 preset 已同时承载 Slurm/LSF execution policy；字段放在这里能够覆盖 structured `fhi_runtime` 和 legacy Slurm `modules + launch_command`，而不会改变任一 launch renderer。

`RuntimeDiscoveryHints` 增加可为空的 species-root hint，`RuntimeCandidate` 增加 discovered species-root result。Hint 用于保留用户输入和约束 Find Missing；只有 execution preset 中的字段用于 generation。自动发现得到的 path 和手动输入最终写入同一个 preset field。

Alternatives considered:

- 放入 `FhiAimsRuntimeConfiguration`：语义集中，但会迫使 legacy Slurm profile 转换 launch model，可能改变现有 `srun` options，因此拒绝。
- 只放入 `RuntimeDiscoveryHints`：hint 不是 verified/runnable configuration，不能成为 submission authority，因此拒绝。
- 新建完整 `FhiAimsInstallationConfiguration` 聚合层：结构更整齐，但超出本 change 的最小范围，因此拒绝。

### 2. Migrate server profiles without inventing a path

`server_profiles.json` schema 从 9 增加一个版本。Schema 1–9 继续按现有 migration 读取，新增 path 为 `None`；load 本身不改写文件。下次保存时写出新 schema。缺失 path 不影响浏览、恢复或原样 retry 历史 task，但所有新 `control.in` generation 在连接前置检查中被明确标为 incomplete。

Project manifest schema 6、density manifest schema 1 和 local project index 不增加字段。它们已保存生成后 `control.in` 的 immutable hashes；server profile 负责未来 generation source，避免把可变 local connection configuration复制到 historical project state。

### 3. Discover by structure near executable evidence, not by broad search

Species discovery 作为 FHI-aims candidate completion 的一个步骤运行在同一 selected runtime environment 中：

1. 手动 species-root hint 优先，只验证该 exact directory。
2. 对 canonical FHI-aims executable 复用现有 installation-root evidence，并只保留 FHI-aims-derived anchors。
3. 在固定数量的 anchors 和 ancestor levels 内，检查 anchor 本身、其 `species_defaults` child，以及 immediate child 下的 `species_defaults`；version-like children 只在已经进入 `species_defaults` 后列举。所有 directory listings 和 candidates 都设定明确上限。
4. Candidate 仅在其 immediate children 包含所有 supported accuracy directories 时被识别为 species root。Search 不递归进入任意目录，不调用 `find /`、`locate` 或 FHI-aims executable。
5. 无 candidate 时返回 missing requirement；多个 distinct canonical candidates 时要求用户选择。

该结构性策略不固定某个 species-root basename 或 installation prefix，同时允许单独授权的 external read-only acceptance 确认实际 relative layout。未被这些 evidence paths 覆盖的安装由 manual root 处理，而不是扩大扫描范围。

### 4. Validate the root twice at different strengths

保存手动文本时只做 canonical absolute POSIX syntax validation；remote folder chooser 证明目录在当次 connection 中存在。Find Missing/automatic discovery 验证 root directory 和 supported accuracy subdirectories。每次生成新 input 时再次验证 root，并逐一验证本次 required files，防止配置后发生删除、替换或 permission change。

每个 required filename 由现有 element-to-atomic-number mapping、selected accuracy 和 exact `<NN>_<Element>_default` grammar 组成。Remote acquisition 先 `stat` 并应用明确的 per-file byte limit，再读取完整 bytes、严格 UTF-8 decode、normalize line endings，并复用现有 unique/matching `species` declaration validation。任何失败携带 path、element 和 accuracy context，且不尝试其他文件或 accuracy。

### 5. Keep remote I/O in Application and rendering pure

将 filesystem-only `OfficialSpeciesLibrary` 的 production role 分为两个边界：

- 一个 pure/in-memory species-block provider，继续满足现有 builders 所需的 `load(element, accuracy, species_name)` 行为；
- 一个 application-layer remote acquisition service，通过已连接的 `RemoteExecutor` 读取、验证并去重 `(element, accuracy)` requests，然后构造 in-memory provider。

Input builders 和 control writers 仍只处理 validated `SpeciesDefaultBlock`，不接触 credentials、SSH 或 server profile。`SpeciesDefaultBlock` 的 source identity 改为能表达 canonical remote POSIX path 的值，而不把 remote path 伪装成 Windows local `Path`。Atomic-number/element mapping 和 block validation 保留；仅 packaged-root loader 被删除。

不采用 temporary local mirror，因为它会产生新的 cache/lifecycle 问题；不让 Domain provider 持有 `RemoteExecutor`，因为这会反转 preferred dependency direction。

### 6. Move new-input materialization inside the authorized operation

GUI/controller 在 confirmation 前保留 structure、settings、selected profile 和 provenance，但不再构造包含 species blocks 的最终 bundle。Application submission request 改为携带 immutable input plan。

用户确认后，submission service 执行：connect → verify configured FHI-aims/scheduler inputs → acquire required species blocks → materialize and validate complete input bundle → remote allocation/upload/dispatch。Step 1/2、direct/restarted Step 3 和 existing-project continuation 使用同一 boundary。Density service 在其现有 connection 内、任何 task directory allocation 前完成相同步骤；三个 components 共享一次去重后的 block set。

Standalone local export 使用已保存 server profile 和同一 acquisition service。用户确认 connection 后，只有在全部 remote definitions 与最终 bundle 已准备完成时才写入 local destination；它不执行 scheduler preflight、remote write 或 job submission。

不采用 GUI 预取，因为它会在 final operation 之外建立额外 connection，并可能让 confirmation 后使用 stale bytes。

### 7. Parse historical control.in from its embedded blocks

History/restart parser 从已校验的 historical `control.in` 提取完整 species blocks及其 species-to-element mapping，用这些原始 blocks执行既有 settings round-trip。仅解析、查看、recover 和 byte-preserving retry 不读取当前 server species root。

当用户基于 recovered settings 创建新的 restart task 时，该操作转入 Decision 6 的 new-input path，并从当前 selected server 读取 definitions。这样不会要求历史和当前 FHI-aims installation 的 definitions 逐字相同，也不会改变已有 input bytes。

### 8. Extend the existing runtime UI and folder browser

FHI-aims tab 在 Remote path、MPI launcher 和 Environment 附近增加一个 Species definitions root field、resolved status 和 folder button。`RemoteDirectoryDialog` 参数化 title/explanation/result label 后复用，目录 worker 继续只列 existing folders。Species selector 的结果只写入 species field，不能修改 Remote Project Workspace。

Runtime summary 同时显示 executable 和 species root。Missing diagnostics 明确指出 path contract：用户应选择其 immediate children 为 supported accuracy directories 的 root。

### 9. Remove data only after production and tests no longer depend on it

先完成 provider、orchestration、migration 与 synthetic/fake-remote tests，再删除 `resources/fhi_aims/species_defaults/`。PyInstaller 仍可打包整个其余 `resources/`；packaging validation 增加明确的 absence assertion，防止 definitions 以另一路径重新进入 installer。

Test fixtures 只包含用于 parser/validation 的最小 synthetic text，不复制官方 FHI-aims basis/default content。Maintained documentation 只更新本 capability 直接改变的配置、generation 和 packaging 行为。

## Risks / Trade-offs

- [不同 FHI-aims distributions 的目录布局不同，automatic discovery 可能遗漏] → 使用 executable-derived bounded structural search，保留同界面的 exact manual path，并仅在单独授权时使用 external environment 做 read-only acceptance。
- [Species files 在 discovery 后发生变化] → 每次新 generation 重新 stat/read/validate；最终 `control.in` 和既有 manifest hash 仍记录实际提交 bytes。
- [SSH latency 增加 input generation 时间] → 只读取当前 structure 实际需要且按 `(element, accuracy)` 去重的 small files，不预取整个 library，也不建立 persistent cache。
- [旧 profile 可以加载但不能立刻创建新任务] → UI 显示明确 incomplete state，并直接链接到 existing runtime configuration；绝不回退到 bundled data。
- [Historical exact round-trip 因 block extraction 边界错误而回归] → 使用既有 generated Step 1/2/3 fixtures覆盖 aliases、per-atom accuracy 和 exact bytes，并保持 unsupported/ambiguous input 显式失败。
- [Schema rollback] → Schema 1–9 读取保持只读兼容且仅在用户保存时升级；若需回退旧 application，必须恢复升级前的 profile-store backup，不能让旧 binary 猜测新 schema。
- [Remote read failure 被误认为 scheduler/program failure] → Species acquisition 使用独立 configuration/input-preparation error；它发生在 submission 前，不生成 job ID，也不改变 scheduler or scientific status。

## Migration Plan

1. 增加 profile schema migration、new fields 和 validation，不改变旧 launch rendering。
2. 扩展 runtime candidate discovery、missing diagnostics、manual field 和 remote folder chooser。
3. 实现 bounded remote block acquisition 与 in-memory provider，并用 fake executor 验证安全/错误边界。
4. 将 Step 1/2、Step 3、density 和 standalone export 改为 connection 内 materialization；保持 remote mutation 在后。
5. 将 historical parser/restart recovery 改为使用 embedded blocks，并验证 byte-preserving retry。
6. 删除 packaged species files和 production loader，更新直接相关 docs/packaging checks。
7. 运行 targeted offline tests，再运行现有 full offline suite。
8. 在单独获得 external-operation authorization 后，使用用户选择的 saved Slurm profile 做 read-only acceptance：定位 root、读取并验证 representative synthetic files；不运行 FHI-aims、不创建 remote directory、不提交 job。未验证的其他 installation layouts 保持明确 unverified，且不在 repository 保留环境身份、路径或 capture。

Rollback 通过 revert 本 change 恢复 code/resources。若 profile store 已由新版本保存为新 schema，回退旧 binary 前使用升级前备份；remote projects 和既有 `control.in` 不需要迁移或回滚。

## Open Questions

- 一次经单独授权的 read-only acceptance 未从 executable evidence 得到 unique species root，但验证了 temporary manual-root fallback 的路径 contract 与 in-memory materialization。其他 installation layouts 仍为 unverified；该限制不改变 manual fallback、path contract 或 generation architecture。
