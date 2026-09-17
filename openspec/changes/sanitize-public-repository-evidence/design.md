## Context

See `proposal.md` for motivation. 当前 repository 只有一个 local branch、两个 commits、没有 remote 或 tag，但这两个 commits 已包含 environment-bound documentation、test evidence 和已计划移除的 packaged scientific definitions。当前 working tree 还包含上一项已完成 change 的未提交实现，因此 sanitization 必须保留这些 existing changes，不能通过 checkout/reset 重建内容。

Private evidence 横跨 documentation、production identifiers、shared test profiles、unit-test literals 和 scientific/scheduler fixtures。单纯删除当前文件或追加 cleanup commit 不能阻止从 earlier commits 恢复数据。

## Goals / Non-Goals

**Goals:**

- 产生不含已识别真实 HPC、个人和未公开科研证据的 publication candidate。
- 用独立构造的 synthetic fixtures 保持 parser、workflow/state、remote-path validation 和 GUI tests 的既有语义。
- 保持 persisted server profiles 与用户显式 runtime configuration 的兼容性。
- 在内容和 tests 验证完成后，从 exact sanitized file set 建立与原 history 隔离的 clean-root publication repository。

**Non-Goals:**

- 不删除 ignored local calculations、cache、build/dist output 或 OS credential storage。
- 不连接真实 SSH/HPC、不运行 scientific programs、不修改 external server state。
- 不重构 remote subsystem、scheduler model 或 scientific algorithms。
- 不把 packaged product resources 无差别认定为 private；只处理调查已直接识别的真实运行/科研 evidence 及其依赖。
- 不修改或删除原 private repository 的 refs、reflogs 或 objects，也不为其添加 public remote、tag 或 publication package。

## Decisions

### 1. Private inventory remains outside tracked artifacts

OpenSpec artifacts 只记录 evidence categories、affected areas 和验证规则，不复制具体 private identifiers。Apply validation 使用本次对话中确认的 one-time private inventory，加上针对 email、host/path、scheduler accounting、runtime capture 和 provenance language 的通用 pattern scan。

选择这一方式而不是提交 denylist 文件，是为了避免“用于检测秘密的文件本身再次公开秘密”。通用 pattern 不能替代已知值扫描，两者必须同时执行并分别报告范围。

### 2. Synthetic fixtures are independently constructed

真实 output files 将缩减为仅包含 parser 所需 markers/fields 的 synthetic snippets；scheduler rows 使用明确 synthetic 的 decimal identifiers、UID、node 和 timestamp。Molecular and junction fixtures 将以新的 synthetic names 和独立坐标重建，保留 tests 所需 atom types、connectivity、contact topology、surface membership 和 count invariants，但不通过整体平移、轻微扰动或直接截取真实坐标生成。

Transmission fixtures 使用人工确定的 finite monotonic grids 和 synthetic values，只验证 file grammar、endpoint convention、interpolation 与 plotting。Fixture documentation 明确声明 synthetic/non-scientific provenance，且不保存 real-output hashes。

### 3. Cluster branding is removed without weakening validation

Production helper/function/regex identifiers 改为描述其实际 grammar（例如 generic Slurm timeout/task-OOM evidence），保持 exact-line matching 和 bounded-read behavior，不扩大接受范围。Test helpers 统一使用 `ExampleCluster`、`compute001`、`scientist@example.org`、`/srv/moltage-test/projects` 以及集中定义的 synthetic IDs。

Cluster-derived module sequences、version strings、executable examples 和 scheduler configuration incidents 不再作为 global defaults 或 compatibility proof。Existing profiles 中的 explicit values 仍按原 schema 读取；generic discovery 保持 bounded、evidence-directed 和 explicit manual fallback，不加入新的 scan 或 silent substitution。

### 4. Documentation records capability rather than private chronology

`README.md` 与 maintained docs 保留用户需要理解的 workflow/state semantics 和已由 offline tests 支持的 behavior，删除 real acceptance chronology、job-level incident history 和 server-specific diagnosis。对只能由真实环境证明但本 change 不重新执行的内容，改为 `unverified in public synthetic validation`，不得把 synthetic fixture 结果提升为 real external evidence。

### 5. Public history is created in an isolated repository

Content sanitization、targeted tests、full offline suite 和 current-tree privacy scan 先在现有 working tree 完成。用户确认 publication strategy 后，从已审核的 exact sanitized file set 逐文件复制到全新目录，不复制 `.git` 或 ignored local data；复制结果通过 file-count 和 content-hash verification 后才建立新的 root history。

原 private repository 继续保留其 history，且不得连接或 push 到 public remote。独立 publication repository 只包含 clean root commit，因此无需破坏性删除原 refs、reflogs 或 unreachable objects；最终仍必须扫描 publication repository 的 intended refs、完整 reachable object graph、index、checkout 和 binary assets，而不只扫描 checkout。

### 6. Validation is layered and evidence-accurate

Validation 顺序为：affected targeted tests、full offline suite、current tracked/candidate binary-aware scan、exact-copy hash verification、publication-repository full test、reachable-history scan、Git status/index audit。原 private history 不属于 publication candidate；任何未执行范围明确标为 unverified。

## Risks / Trade-offs

- [Synthetic fixture 不再触发原边界] → 从每个现有 test 的实际 parser/state assertion 反推最小字段，并先运行 targeted tests，再运行 full suite。
- [新结构仍可反推出真实结构] → 坐标与 topology fixture 独立构造并使用新名称，不采用对真实坐标的小扰动。
- [删除 version-specific default 影响旧 profile] → 保持 persisted explicit runtime fields/schema，不修改用户本地 profile；为旧 profile 和 manual configuration 保留 regression coverage。
- [通用 scan 漏掉已知 private value] → one-time private inventory 与 generic patterns 双重验证，并对 tracked binary assets 进行 byte scan。
- [working tree 中上一 change 被覆盖] → apply 前记录 existing diff，逐文件合并且禁止 reset/checkout；validation 区分已有变更与本 change。
- [publication snapshot 漏项或混入 ignored data] → 仅复制审核清单中的 existing files，禁止 broad copy；对 source/target 执行逐文件 hash comparison，并在新 root commit 后扫描 reachable objects。

## Migration Plan

1. 在当前 working tree 上完成 identifiers、fixtures、tests 和 maintained docs 的 synthetic replacement，同时保留上一 change。
2. 运行 targeted 与 full offline validation，再对 current tracked/candidate content 做 private-inventory 和 generic-pattern scan。
3. 向用户报告 sanitization 结果与 exact intended public file set；在未获单独授权时停止，不改写 refs/history。
4. 获授权后在独立目录从审核文件集建立新的 clean root baseline，并验证 branch/index/working tree 与 source snapshot 一致。
5. 保留原 private repository/history 且不为其添加 public remote；扫描新 publication repository 的全部 intended reachable objects，在另有明确目标 remote 前不 push、不 tag。
