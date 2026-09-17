## Context

参见 [proposal.md](proposal.md) 的动机和 [specs/default-test-isolation/spec.md](specs/default-test-isolation/spec.md) 的行为要求。当前 `tools/run_tests.ps1 -Full` 编译 `src/`、`tools/` 后只运行 `tests/unit/`；现有 SSH/SFTP、Slurm/LSF、FHI-aims 和 AITRANSS 测试已经主要依赖 in-memory/scripted executors 与 synthetic fixtures，未发现默认测试主动连接真实基础设施。

当前缺口是这些边界没有集中强制执行：`APPDATA` 默认仍可指向用户目录，production `Paramiko`/credential adapters 在误用时仍可能接触真实环境，`pytest` 未由 project metadata 声明，且两个 runtime-configuration tests 使用固定的 Git Bash 绝对路径。Repository 当前没有真实环境 integration-test suite，本 change 不创建一个。

## Goals / Non-Goals

**Goals:**

- 在 `tests/unit/` 的共同入口建立 fail-closed guard，使完整默认测试自动继承相同隔离，而不要求逐个测试自律。
- 保持现有 fake/mock injection 方式和 `tools/run_tests.ps1` 的使用习惯。
- 让 test dependency、可选 shell discovery、fixture provenance 和 validation boundary 对新 contributor 明确可见。

**Non-Goals:**

- 不修改 production SSH、scheduler、scientific runtime 或 application-state behavior。
- 不创建或执行真实 HPC/FHI-aims/AITRANSS acceptance tests，也不声称 synthetic tests 证明真实环境兼容性。
- 不重写 test architecture、引入 CI、整理所有 fixtures，或修复与本需求无关的 Qt hover/timer 偶发测试问题。

## Decisions

### 1. Keep `tests/unit/` as the default suite and add one central isolation guard

在 `tests/unit/conftest.py` 中建立 default-suite guard，并确保它在 test modules 使用 application state 或 external adapters 前生效。Guard 将为测试进程创建 temporary application-data root、设置 `APPDATA`，并在测试结束时恢复 process environment 和清理 temporary state。

同时在当前 production external boundaries 阻止未注入的真实操作：至少覆盖 Python socket connection entry points、`Paramiko` 的真实 client connection，以及 native keyring backend 的取得/使用。拒绝信息应明确指出 default tests 禁止真实 external access。现有 tests 通过 constructor/factory injection 使用的 fake clients、memory credential backends 和 scripted executors不受影响。

选择 central guard 而不是逐个 test patch，是因为新增测试如果遗漏 mock 也必须 fail closed。选择覆盖当前已使用的 Python boundaries，而不是引入 OS-level sandbox 或新的第三方 network-blocking dependency，是为了保持实现最小；source audit 和 regression tests 将验证当前 production remote path 仍位于这些 boundaries 内。

### 2. Declare a narrow test dependency extra

在 `pyproject.toml` 增加 `test` optional dependency group，当前只声明 default runner 必需且 runtime dependencies 未包含的 `pytest`。README 使用该 extra 给出 clean-environment installation command。

不新增完整 dev-tool bundle，因为 formatter、linter、type checker 和 packaging tools 不属于本 change，也不是 default suite 的必要条件。

### 3. Discover optional Bash through `PATH`

将 `tests/unit/test_runtime_configuration.py` 中固定的 `C:/Program Files/Git/bin/bash.exe` 改为通过标准 executable discovery 查找 `bash`。发现后运行原有 syntax validation；未发现时保留明确的 `skip`/`unverified` 结果。

不把 Bash 变成 mandatory dependency：这些测试已有 deterministic renderer assertions，shell syntax check 是平台相关的附加 validation。也不搜索常见安装目录，因为那仍然会重新引入 machine-specific knowledge。

### 4. Document fixture provenance without changing scientific semantics

新增 `tests/fixtures/README.md`，统一说明 fixture collection 是 independently constructed synthetic data、各目录只模拟被测试的最小格式特征，并禁止加入 distribution files、real server captures、real research outputs、accounts 或 credentials。现有有独立 README 的 fixture 可以保留更具体说明。

不修改 fixture numerical content，也不对其作新的 scientific interpretation；本 change 只明确 provenance 和 validation boundary。

### 5. Document the default/real-environment boundary at the existing test entry point

README 将给出安装 test extra 和运行 `tools/run_tests.ps1 -Full` 的命令，并明确该命令只运行 synthetic/offline default tests。若未来增加真实环境验收，它必须采用不同的显式入口并另行获得授权；本 change 不预先设计 marker、directory 或 credentials workflow。

## Risks / Trade-offs

- [Guard patch 过宽，影响 library 内部无关行为] → 只拦截 repository 当前实际使用的 connection/credential entry points，并用 focused tests 证明 injected fakes 继续工作。
- [Guard 加载太晚，module import 已读取用户 state] → 在 `tests/unit` 的 collection boundary 设置 temporary `APPDATA`，并增加 regression test 验证 product module 使用 test-owned path。
- [新增 remote implementation 绕过现有 boundary] → 在 guard 的 focused tests 和 maintained fixture/test policy 中明确 fail-closed 约束；未来新增 external adapter 必须同时纳入 default-test boundary。
- [Bash 缺失减少了一项 syntax evidence] → 明确 skip 原因且不声称该 validation 通过；已有 renderer assertions 仍执行。
- [Full suite 中既有 Qt timing flake 偶发失败] → 如实记录实际 validation 结果；该失败不在本 change 中修复，也不得被 isolation guard 掩盖。

## Migration Plan

1. 增加 test dependency extra 与 central isolation guard，并先运行 focused isolation tests。
2. 迁移 Bash discovery，补充 fixture/README 说明。
3. 运行完整 offline suite；任何失败均按实际结果报告，不连接真实服务作为补救。
4. 若 guard 导致现有 fake-based test 回归，回退本 change 的 test-harness edits；production behavior 和 persisted data 无需迁移。
