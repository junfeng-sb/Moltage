## Purpose

确保 Moltage 首次公开的 repository snapshot、测试证据和可达 Git history 不暴露真实 HPC 环境、个人身份或未公开科研运行信息，同时保留由 synthetic evidence 验证的既有软件行为。

## ADDED Requirements

### Requirement: Publication candidate excludes private operational evidence
准备公开的 repository 内容 MUST 不包含已识别的真实 server/profile、host/node、account/contact、remote path、scheduler Job ID/UID、project/system name、runtime version/configuration incident、timestamp 或真实运行 provenance。

#### Scenario: Tracked publication content is scanned
- **WHEN** 对拟公开的 tracked files 和 candidate staging set 执行已识别 private inventory 与通用 environment-evidence patterns 的 text/binary scan
- **THEN** 扫描不得发现真实 operational 或 unpublished-research evidence
- **THEN** validation report MUST 区分实际扫描范围与未验证范围

#### Scenario: Ignored local state remains present
- **WHEN** repository 同目录仍存在 ignored calculation state、cache 或 build output
- **THEN** 这些内容 MUST 保持 untracked，且不得被复制到 publication candidate
- **THEN** cleanup MUST NOT 以 repository sanitization 为由删除这些本地数据

### Requirement: Behavioral fixtures use synthetic evidence
用于验证 scheduler、remote workflow、scientific-program parsing 和 molecular UI 的 tracked fixtures MUST 是明确的 deterministic synthetic data，不得保留真实 output capture、真实计算数值、真实结构坐标或其精确 provenance。

#### Scenario: Real-derived fixture is replaced
- **WHEN** 一个 existing fixture 被识别为真实 scheduler/program output、molecular geometry 或 transmission result
- **THEN** 它 MUST 被独立构造的 synthetic fixture 替换或移除
- **THEN** replacement MUST 仅保留对应 test 所需的 grammar、topology、state transition 或 failure boundary
- **THEN** replacement MUST NOT 被描述为 scientific validation evidence

#### Scenario: Existing behavior is validated with synthetic fixtures
- **WHEN** synthetic replacements 完成
- **THEN** 受影响的 targeted tests 和项目 full offline test suite MUST 实际通过，或未通过/未执行项 MUST 明确报告
- **THEN** scheduler state、program completion 和 scientific success 的既有区分 MUST 保持不变

### Requirement: Runtime configuration remains server-generic
Repository 中的 runtime discovery、examples 和 tests MUST 使用 server-generic terminology 与 synthetic configuration。用户 profile 中显式保存的合法 executable、module、version 和 path MUST 继续作为 per-server data 被读取和使用，不得由清理过程重写。

#### Scenario: Existing profile contains environment-specific values
- **WHEN** 用户加载一个包含合法 explicit runtime configuration 的 existing profile
- **THEN** application MUST 保持这些值及其既有 validation semantics
- **THEN** repository 自身 MUST NOT 将该用户环境作为 global default 或 public fixture

#### Scenario: Generic discovery cannot resolve a runtime
- **WHEN** bounded server-generic discovery 无法确定需要的 executable 或 path
- **THEN** application MUST 保持现有 explicit manual-configuration failure path
- **THEN** application MUST NOT 通过 private cluster-specific fallback、invented value 或 filesystem-wide scan 掩盖缺失信息

### Requirement: Maintained documentation contains no private acceptance narrative
Maintained documentation MUST 描述通用 observable behavior、synthetic examples 和明确 validation level，不得保留可关联到真实服务器、个人或未公开项目的运行事件时间线。

#### Scenario: Historical real-environment narrative is sanitized
- **WHEN** documentation 中的段落依赖真实 job、host、version、project 或 incident 才能成立
- **THEN** 该段落 MUST 被改写为通用 behavior contract 或移除
- **THEN** 不得把 synthetic replacement 表述为 real external acceptance

### Requirement: Public Git history is sanitized
拟公开 branch 的所有 reachable Git objects MUST 不包含已识别 private operational 或 unpublished-research evidence。若当前 history 已包含此类内容，新增 cleanup commit 本身不构成合格清理。

#### Scenario: Existing commits contain private evidence
- **WHEN** privacy validation 发现拟公开 branch 的 earlier commits 可恢复已清理信息
- **THEN** 首次公开前 MUST 建立不引用这些 commits 或其 repository metadata 的独立 clean root publication repository
- **THEN** 原 private repository MAY 保留其 history，但 MUST NOT 连接到 public remote、加入 publication package 或被上传

#### Scenario: Clean history is prepared for publication
- **WHEN** clean root history 已获授权并建立
- **THEN** intended public refs 及其 reachable objects MUST 通过同一 private inventory 与通用 pattern scan
- **THEN** 原 private repository 的 refs、objects 与 `.git` metadata MUST NOT 被加入 remote、tag 或 publication package
