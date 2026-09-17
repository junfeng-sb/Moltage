## Context

See `proposal.md`. 当前 publication candidate 已通过早期 sanitization，但一个 root commit 与 current test files 仍保留少量 runtime/environment-derived evidence。现有 working tree 同时包含三个已完成、未提交 change，实施必须保留这些 changes，不能使用 reset、checkout、broad staging 或 history rewrite。

## Goals / Non-Goals

**Goals:**

- 只替换不影响 parser、runtime discovery 或 submission semantics 的 test identifiers/fixtures。
- 删除一个无引用且不属于 runtime 的旧 UI reference image。
- 验证 current candidate 而不声称已经清理 reachable Git history。

**Non-Goals:**

- 不修改 scheduler/runtime/scientific production behavior。
- 不修改 Au electrode coordinates、generation、placement defaults 或 radii data。
- 不执行 Git history rewrite、commit、remote mutation、public visibility change 或 license decision。

## Decisions

### 1. Preserve test grammar while replacing evidence values

LSF timeout fixture 保留 `Job <...>`、`Exited with exit code`、`Completed <exit>` 与 `TERM_RUNLIMIT` grammar，只把 Job ID、Job Name 和 timestamps 改成集中、明显 synthetic 的值。这样 parser boundary 不变，也不会保留 captured operational identity。

### 2. Generalize only test paths

Runtime tests 使用 `/srv/moltage-test/` 下的 generalized toolchain/install paths，并保持原来的 alias、PATH inventory、wrapper、launcher candidate 与 library association assertions。Production search tokens 和 accepted executable families不变。

### 3. Make test identities self-evidently synthetic

Project UUID 使用重复模式的 RFC 4122 test UUID；project name 使用 `ExampleMolecule`。不引入新的 identity abstraction，也不改变 schema。

### 4. Remove, do not replace, the orphan image

`docs/ui_reference/future_light.png` 无 textual reference 且包含 obsolete branding。删除该文件比创建新 artwork 更符合本 change 的 provenance scope。

### 5. Defer scientific-resource provenance explicitly

另建 draft follow-up change 记录 Au electrode coordinates、Au placement defaults 与 radii provenance 的 release blocker；本 change 不判断来源许可或修改科学值。

## Risks / Trade-offs

- [修改 fixture 导致 parser coverage 漂移] → 保留 exact grammar markers，并运行相关 LSF/runtime/submission tests。
- [existing uncommitted changes 被覆盖] → 仅对已确认行做小 patch，并在结束时审计 `git diff`/`git status`。
- [current tree clean 被误报为 history clean] → 报告明确限定为 current candidate；root-history replacement 仍为后续独立操作。
- [未安装专用 secret scanner] → 重跑已知 inventory、generic secret formats、high-entropy 与 binary-aware scan，并将 scanner coverage limitation 明示为 unverified。
