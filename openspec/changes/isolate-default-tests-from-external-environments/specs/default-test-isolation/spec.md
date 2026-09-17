## Purpose

确保 Moltage 的默认测试能够在没有开发者个人配置、凭据、真实科研软件或 HPC 基础设施的普通离线开发环境中可靠验证项目自身逻辑，并明确其证据边界。

## ADDED Requirements

### Requirement: Default test suite is offline and environment-independent

项目的 documented default test suite MUST 能够在未配置真实 SSH/HPC server、scheduler、remote filesystem、FHI-aims、AITRANSS、`species_defaults`、用户凭据或开发者专用绝对路径的环境中运行。需要这些行为的测试 MUST 使用显式构造的 synthetic data 或注入的 fake/mock boundary，不得访问真实科研基础设施。

#### Scenario: Clean environment runs the default suite
- **WHEN** 用户在安装了 documented project/test dependencies 的干净环境中运行 default test command，且没有真实 HPC/FHI-aims 配置或凭据
- **THEN** default test suite 可以执行而不要求任何真实 external environment

#### Scenario: External behavior is exercised in a unit test
- **WHEN** default test 需要验证 SSH、remote filesystem、scheduler 或 scientific runtime 相关逻辑
- **THEN** 该测试使用显式 synthetic input 和 fake/mock boundary，并且不向真实 external resource 发起操作

### Requirement: Default tests fail closed on unintended external access

Default test process MUST 隔离当前已支持的真实 network/SSH 和 credential access boundary，并 MUST 将 Moltage local application state 指向 test-owned temporary location。未经测试显式注入的真实外部访问尝试 MUST 在操作发生前明确失败，不得 silent fallback 到开发者机器的 configuration、credentials 或 network。

#### Scenario: Test code attempts a real network or SSH connection
- **WHEN** default test process 中的代码尝试通过 production external-access boundary 建立未被 fake/mock 替代的 network 或 SSH connection
- **THEN** 测试立即以明确的 isolation failure 终止该访问，且不发送真实连接请求

#### Scenario: Test code requests persisted credentials
- **WHEN** default test process 中的代码尝试通过 production credential boundary 读取真实 persisted credential
- **THEN** 访问被明确拒绝，且不读取用户 credential store

#### Scenario: Test code reads or writes local application state
- **WHEN** default test 触发 server profile、known project、view preference 或其他 local application state 的读取或写入
- **THEN** 操作仅作用于本次测试拥有的 temporary location，不访问或修改用户正常的 Moltage state

#### Scenario: Default suite completes
- **WHEN** documented default test suite 执行结束
- **THEN** 除已明确忽略的 test cache/temporary artifacts 外，repository 不产生由测试造成的 tracked 或 untracked project content

### Requirement: Test fixtures are synthetic and provenance-labelled

Default tests 使用的 scientific input/output、scheduler output、remote filesystem layout 和类似 fixtures MUST 是为测试目标独立构造的最小 synthetic data，并 MUST 在 fixture documentation 中声明 provenance 和适用边界。不得将 FHI-aims distribution files、真实 server capture、真实 research task output、真实账户信息或 credentials 作为 default-test fixture 引入 repository。

#### Scenario: Contributor inspects fixture provenance
- **WHEN** contributor 查看 default-test fixture collection 的说明
- **THEN** 可以确认 fixtures 为 synthetic、了解其验证用途，并看到它们不构成真实 scientific/HPC compatibility evidence

#### Scenario: A new external-format fixture is added
- **WHEN** default test 需要新增模拟 scientific program 或 scheduler 格式的 fixture
- **THEN** fixture 只包含验证该行为所需的 synthetic content，且不会复制真实 distribution、server capture 或 research result

### Requirement: Default-test prerequisites are portable and declared

项目 MUST 声明运行 documented default test suite 所需的 test dependencies，且 tests MUST NOT 依赖开发者机器上的固定绝对路径。可选的本地 validation tool 只能通过当前环境的可移植 discovery mechanism 使用；不可用时 MUST 明确报告对应 validation 未执行或测试被跳过，不得将其表示为通过。

#### Scenario: Test dependencies are installed from project metadata
- **WHEN** 用户按照 maintained documentation 在干净环境中安装 project 及其 test dependencies
- **THEN** default test runner 所需的 Python test tooling 可由 project metadata 获得，无需依赖预先手工安装的开发者工具

#### Scenario: Optional shell validator is available
- **WHEN** 当前环境通过可移植 discovery mechanism 提供受支持的 shell validator
- **THEN** 相关测试可以使用该已发现工具进行 validation，而不引用开发者专用绝对路径

#### Scenario: Optional shell validator is unavailable
- **WHEN** 当前环境无法发现可选 shell validator
- **THEN** 相关 optional validation 被明确标记为 skipped 或 unverified，且不得声称 shell validation 已通过

### Requirement: Real-environment validation remains explicit and separate

任何连接真实 SSH/HPC、scheduler、FHI-aims、AITRANSS 或真实 remote filesystem 的 validation MUST 是明确 opt-in、与 default test command 分离的操作，并 MUST 需要用户明确授权。Maintained documentation MUST 明确 default test result 只证明项目在 synthetic/offline boundary 内的行为，不证明所有真实 HPC site 或 scientific runtime 的兼容性。

#### Scenario: User runs the default test command
- **WHEN** 用户运行 documented default test command
- **THEN** 不会自动启动任何 real-environment validation，也不要求 real-environment authorization

#### Scenario: Default suite passes
- **WHEN** default test suite 报告通过
- **THEN** 项目 documentation 和 test output 不会把该结果表述为真实 HPC site、FHI-aims、AITRANSS 或 scientific result 已完成验收
