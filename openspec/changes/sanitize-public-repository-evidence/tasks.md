## 1. Preserve the Existing Working Tree

- [x] 1.1 记录 apply 开始时的 branch、index、unstaged/untracked 状态和上一 completed change 的 affected files，验证未使用 reset/checkout/clean 且后续 audit 能区分 pre-existing changes。
- [x] 1.2 将本次已确认的 private evidence inventory 仅保留为 one-time local validation input，不写入 tracked artifact；验证 change files 中只出现 evidence categories 而不复制具体 private values。

## 2. Remove Environment-Bound Production Identifiers

- [x] 2.1 将 production code 中 cluster-branded timeout/task-OOM helpers、regex constants、imports 和 diagnostics 改为 server-generic Slurm terminology，验证 exact grammar、bounded-read behavior 和 scheduler/program/scientific state distinction 的 targeted tests 仍通过。
- [x] 2.2 移除 production defaults、runtime discovery 与 GUI examples 中绑定单一真实安装的 module/version/executable identifiers，保留 existing profile explicit values、bounded generic discovery 和 manual fallback；运行 profile migration、runtime discovery/search、submission 与 dialog targeted tests 验证兼容性。

## 3. Replace Real-Derived Fixtures with Synthetic Evidence

- [x] 3.1 以新 generic filenames 和独立构造的 coordinates/topology 替换 `tests/fixtures/phase1b` 与 `tests/fixtures/phase2c` 中的真实 molecule/optimized-output evidence，更新所有引用和 assertions；验证 anchor、connectivity、Au placement、geometry import/recovery、Cube 和 viewer targeted tests。
- [x] 3.2 独立构造 synthetic junction geometry 与 matching transport-control fixture，保留 self-energy selection 所需 atom-count、surface-membership 和 plane-group invariants但不复用真实坐标；验证 self-energy、transport submission/recovery 和 interface-partition targeted tests。
- [x] 3.3 将真实 scientific-program output 和 transmission fixtures 改为最小 synthetic grammar samples，移除 real provenance、timestamps、compile host、hash 与 scientific values；验证 AITRANSS output/transmission parser、endpoint、recovery、plot/interpolation tests。
- [x] 3.4 将 scheduler accounting fixtures 和 unit-test literals 中的真实 Job IDs、UID、node、timestamps、email 与 project/system names 替换为集中、明确的 synthetic values；验证 Slurm status/cancel/OOM/timeout、submission lifecycle、project/dialog 和 mail tests。

## 4. Sanitize Documentation and Planning Evidence

- [x] 4.1 将 `README.md` 与 directly affected maintained docs 中的 real-environment chronology、server incident、runtime identity 和 unpublished project references 改写为 generic observable behavior 与 synthetic-validation statements；验证不把 offline/synthetic tests 表述为 real external acceptance。
- [x] 4.2 清理既有 OpenSpec change artifacts 中残留的 real profile/environment references，同时保持其 approved species-definition behavior 和 acceptance limits；运行 `openspec validate` 验证两个 changes 均有效。

## 5. Validate the Sanitized Current Tree

- [x] 5.1 运行所有直接受 identifiers、runtime configuration、fixtures、scheduler evidence、project workflow 和 documentation changes 影响的 targeted offline tests，并记录 collected/passed/failed/skipped 与 exit code；失败时不得继续声称 content sanitization 已验证。
  - Targeted groups completed with 225, 222, 346, and 95 passed respectively; an additional focused remote-project rerun completed with 6 passed. All had 0 failed and exit code 0.
- [x] 5.2 运行 `./tools/run_tests.ps1 -Full`，记录 collected/passed/failed/skipped 与 exit code，且不得修复 scope 外 diagnostics。
  - Final full offline run: collected 1239, passed 1239, failed 0, skipped 0, exit code 0.
- [x] 5.3 对 tracked files、candidate staging set 和 tracked binary assets 执行 one-time private inventory 与 generic pattern 的 byte-aware scan，验证没有 real environment/contact/project/runtime-capture evidence，并明确记录实际扫描范围。
  - Scanned the 337-file intended current-tree candidate (315 existing tracked files plus 22 non-ignored untracked files), including all 3 binary assets, with the one-time private inventory and generic environment/contact/job/path/secret/capture patterns. Remaining matches were synthetic test values, example-domain contacts, generic software terminology, or version-like false positives; no real operational or unpublished-research evidence was found.
- [x] 5.4 确认 ignored local calculation/cache/build directories 的 tracked count 为零，审计 Git status/diff 并验证上一 change 的有效修改未丢失、没有 local profile/credential/runtime data 进入 candidate set。
  - Tracked forbidden/local-state count is zero. `tmp/`, the UUID runtime directory, build/dist/cache paths, and local profile/state paths remain ignored; the prior species-definition implementation and its 381 staged resource deletions remain present.

## 6. Create an Isolated Public Git Baseline

- [x] 6.1 汇报 current-tree tests、privacy scan 和 exact intended public file set；在未获得用户对 history replacement 的单独明确授权时停止，不创建 commit、branch、tag、remote 或 backup。
- [x] 6.2 从审核通过的 337-file sanitized exact file set 逐文件建立独立 publication directory，验证 source/target hashes 一致、未复制 `.git` 或 ignored data，并在 full offline suite 通过后创建新的 clean root baseline。
  - Exactly 337 files were copied, source/target SHA-256 comparison reported zero differences, the copied tree contained no `.git`, and its full offline suite passed 1239/1239 before a single clean root commit was created.
- [x] 6.3 保留原 private repository/history 不变，验证其 refs、reflogs 和 objects 未被修改，且未为其添加 remote、tag 或 publication package。
  - The private source repository retained its original branch, HEAD/reflog tip, two-commit history, index state, and sole local ref; it still has no remote or tag, and no history-mutating command was run there.
- [x] 6.4 对独立 publication repository 的完整 reachable Git object graph、checkout、index 和 binary assets 重跑 privacy scan，确认 working tree clean、只有新的 sanitized history 且未添加 remote/tag/push；记录任何未验证范围。
  - The publication repository has one root commit and 337 tracked files. Exact-inventory and generic scans of reachable blobs, object names, commit metadata, checkout, index, and all three binary assets found no private evidence; `git fsck` passed, the working tree was clean, and no remote, tag, or push was added.
