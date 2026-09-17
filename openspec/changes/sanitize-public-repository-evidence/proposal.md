## Why

当前 repository 的 maintained documentation、内部命名、tests/fixtures 与现有 Git history 中仍包含可关联到真实 HPC 环境、个人身份和未公开计算的数据。首次发布前必须将这些证据移除或 synthetic 化，同时保持现有可验证行为不变，避免通过当前文件或历史 commit 恢复私人运行信息。

## What Changes

- 将 server/profile、host/node、remote path、email、scheduler Job ID/UID、project/system name、timestamp 与 environment-specific diagnostic 等真实标识替换为明确的 synthetic examples。
- 将来源于真实计算的 molecular geometry、scheduler output、program output、transmission data 和相关 provenance text 改为最小、确定性的 synthetic fixtures，仅保留相应 parser、state transition 和 workflow tests 所需的格式与边界条件。
- 移除 source/tests/docs 中的 cluster-branded identifiers 和与单一真实安装绑定的默认示例；runtime 仍通过 per-profile configuration 和通用、受约束的 discovery 处理用户实际 executable/module/path。
- 将 maintained documentation 改为描述通用 observable behavior 和 synthetic validation，不再记录真实服务器事件、运行编号或未公开科研对象。
- 对 tracked text、binary assets、candidate staging set 和 Git object graph 执行定向 privacy validation；不得把未扫描范围声称为已清理。
- 首次公开前，从审核通过的 sanitized exact file set 建立独立的 clean-root publication repository；原 private repository 及其 history 保持不变，但不得连接到 public remote、加入 publication package 或以其他方式上传。

## Capabilities

### New Capabilities

- `public-repository-sanitization`: 定义首次公开前 tracked content、test fixtures、documentation、implementation identifiers 与 Git history 不得暴露真实 HPC/个人/未公开科研证据的要求，以及 synthetic replacement 和验证边界。

### Modified Capabilities

None.

## Impact

直接影响包含环境或真实运行证据的 `README.md`、`docs/`、相关 `src/moltage/` identifiers/runtime examples、`tests/unit/`、`tests/fixtures/` 和已完成 change artifacts；tests 需要随 synthetic fixtures 同步更新。现有 persisted profile schema、用户已保存的 explicit runtime configuration、scheduler/program/scientific state semantics 和受保护的 file-format behavior 保持兼容。ignored local calculation data、build/dist artifacts、OS credential storage、真实 SSH/HPC operations 及外部 server state 均不在本 change 的修改范围内。
