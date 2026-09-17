## 1. Server Profile and Runtime Model

- [x] 1.1 在 shared server execution preset 中增加 optional `fhi_species_defaults_path`，并在 runtime discovery hint/result 中承载同一 path；实现 canonical absolute POSIX path validation，同时保持 structured 与 legacy FHI-aims launch rendering 不变。为 valid、missing、relative、non-canonical 和跨 profile 隔离场景补充 unit tests。
- [x] 1.2 提升 `server_profiles.json` schema version，并为所有既有 schema 1–9 profile 将缺失字段迁移为 unresolved `None`，不得从其他 profile 或 packaged resource 推断值。验证旧 profile 的 scheduler、runtime、connection fields 原样加载，且只有显式保存才写出新 schema。

## 2. Targeted Discovery and Configuration UI

- [x] 2.1 在现有 FHI-aims runtime discovery 中实现 executable-evidence-derived、数量与深度均有上限的 species-root candidate 检查；验证 exact manual hint、unique result、no result 和 multiple results，并用 fake remote executor 断言不会运行 FHI-aims、`find /`、`locate`、home-directory recursion 或 filesystem-wide scan。
- [x] 2.2 扩展 discovery completeness、diagnostics 和 runtime summary，使 executable 与 species root 分别报告；只有 unique valid root 可以自动填入，missing result 必须列出 path contract，ambiguous result 必须要求用户选择。覆盖 partial candidate 不得被静默应用的 tests。
- [x] 2.3 在现有 FHI-aims manual/runtime configuration UI 增加 `Species definitions root` 文本字段、状态和 remote-folder button，并让自动发现与手动输入写入同一 preset field。补充 GUI tests，验证切换 profile、取消选择和保存失败时不会串用或丢失其他 runtime settings。
- [x] 2.4 参数化并复用 remote directory chooser，使 species selector 只返回 selected server 上的 folder，并明确提示其 immediate children 应包含 `light`、`tight`、`really_tight`。验证该选择不会读取或修改 `Remote Project Workspace`。

## 3. Remote Species Acquisition

- [x] 3.1 将 input builders 依赖的 species source 抽象为 pure/in-memory provider，保留 `load(element, accuracy, species_name)`、atomic-number mapping、calculation-only alias 和 block validation semantics；使用最小 synthetic species text 替换 tests 对官方 packaged definitions 的依赖。
- [x] 3.2 在 Application layer 实现基于已连接 `RemoteExecutor` 的 species acquisition service：按 `(element, accuracy)` 去重，使用 exact `<NN>_<Element>_default` filename，先 bounded `stat` 再完整读取，严格 UTF-8 decode，并验证唯一且匹配的 active `species` declaration。覆盖 unreadable、missing、oversized、invalid UTF-8、wrong element 和 duplicate declaration failures。
- [x] 3.3 为 acquisition error 建立明确的 configuration/input-preparation reporting，包含 remote path、element 与 accuracy；验证失败时不会尝试其他 accuracy、其他 server、cache 或 packaged fallback，也不会产生 scheduler/program/scientific status。

## 4. New Input Generation Paths

- [x] 4.1 将 Step 1/2 新建与 continuation submission request 改为携带 immutable input plan，并在用户确认后的 connection 内按 `connect -> runtime/scheduler preflight -> species acquisition -> bundle materialization -> remote mutation -> submission` 执行。用 ordering/failure tests 证明 definitions 未全部验证时不会创建 remote directory、upload 或 dispatch。
- [x] 4.2 将 direct Step 3、re-convergence 和 restart 的新 `control.in` generation 接入同一 remote acquisition boundary；验证当前 selected server/profile 的 definitions 被使用，既有 Step 4 result 不被无关重算或修改。
- [x] 4.3 将新 density-difference task 的三个 component inputs 接入同一 boundary，并在创建任何 component directory 前完成一次去重后的 species acquisition。验证三个 `control.in` 使用相同 validated block set，任一缺失会原子性阻止整个新 task。
- [x] 4.4 修改 standalone local input export，使其要求已保存且可连接的 server profile，并在用户确认后只读获取 definitions、完整 materialize bundle 后才写入 local destination。验证该路径不执行 scheduler preflight、remote write、remote directory creation 或 job submission。

## 5. Historical Input Compatibility

- [x] 5.1 让 historical `control.in` parser 从文件内提取并验证完整 species blocks 与 species-to-element mapping，用于 settings recovery 和 exact round-trip。覆盖 Step 1/2/3 fixtures、aliases、per-atom accuracy、ambiguous declarations 和 malformed blocks。
- [x] 5.2 验证旧 profile 在 species root unresolved 时仍可查看、解析、recover 和 byte-preserving retry 已有 task，且这些操作不建立 species-fetch connection；验证用户确认生成新 restart `control.in` 时必须改用当前 server definitions。

## 6. Remove Bundled Definitions and Update Direct Evidence

- [x] 6.1 在所有 production paths 与 tests 已迁移后，删除 `resources/fhi_aims/species_defaults/` 和 packaged-root production loader；检查 source、tests、packaging manifests/specs 和 installers 不再引用或包含官方 FHI-aims `*_default` files。
- [x] 6.2 仅更新本 capability 直接涉及的 maintained documentation、example configuration 和 user-facing diagnostics，说明 per-server species root、manual fallback、legacy-profile incomplete state 与 standalone export requirement；不得顺带修改其他 FHI-aims、scheduler 或 architecture 文档。

## 7. Validation and Acceptance

- [x] 7.1 运行与 profile migration、runtime discovery/UI、remote acquisition、input generation、historical parsing 和 packaging absence 直接相关的 targeted offline tests，并记录实际命令与结果；任何未执行项目必须标为 unverified。
  - 实际执行 4 组 `./tools/run_tests.ps1 -Tests ...`，分别为 59、121、214、50 项；合计 444 passed、0 failed、0 skipped，所有命令 exit code 0。
- [x] 7.2 运行项目现有 full offline test suite `./tools/run_tests.ps1 -Full`，记录 collected/passed/failed/skipped 与 exit code；不得把未运行或失败的 validation 声称为通过。
  - 最终干净复跑实际结果：collected 1239、passed 1239、failed 0、skipped 0、exit code 0；耗时 142.00 秒。
- [x] 7.3 **EXTERNAL OPERATION — separately authorized and completed read-only:** 使用用户选择的 saved Slurm profile 检查 executable-derived bounded discovery；未获得 unique species-root evidence 时明确返回 unresolved，且未启动 filesystem-wide fallback。随后使用用户明确提供但未持久化的 temporary manual root，验证其 immediate `light`、`tight`、`really_tight` contract，读取 representative synthetic element/accuracy definitions，并仅在 memory 中成功 materialize matching inputs。该 root 不被视为 official installation default，也不与 executable 建立可推导关系；未运行 FHI-aims、调用 scheduler、创建或修改 remote directory、写入或下载文件。环境身份、路径和运行 capture 不保留在 repository；其他 installation layouts 仍为 unverified。
