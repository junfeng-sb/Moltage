## Why

Moltage 的默认测试虽然目前主要使用 synthetic data 和 fake executors，但这种隔离依赖测试自身遵守约定，且测试依赖、用户配置目录和网络访问边界尚未被明确约束。项目公开后，需要让全新、离线且没有真实 SSH/HPC/FHI-aims 环境的开发环境能够可靠运行默认测试，并让意外外部访问明确失败，而不是受开发者机器状态影响。

## What Changes

- 将默认测试定义为离线、自包含的项目逻辑验证：不得读取真实用户配置或凭据，也不得连接真实 SSH/HPC、scheduler、remote filesystem、FHI-aims 或 AITRANSS 环境。
- 为默认测试建立 fail-closed 外部访问边界，并将 local application state 重定向到测试临时位置；测试需要外部行为时继续使用显式注入的 fake/mock 实现。
- 明确声明运行默认测试所需的 test dependency，使干净环境不依赖开发者机器上预装的 `pytest`。
- 移除测试对开发者机器绝对路径的依赖；可选 shell validation 仅使用可发现的本地工具，工具缺失时明确标记为未验证，而不伪造成功。
- 为 synthetic fixtures 补充统一 provenance 说明，明确它们不来自 FHI-aims distribution、真实服务器 capture 或真实科研任务。
- 在开发文档中明确默认测试的安装/运行方式及其 validation boundary：通过默认测试不代表已经验证真实 HPC 或 scientific runtime 兼容性。

## Capabilities

### New Capabilities

- `default-test-isolation`: 定义 Moltage 默认测试在无真实科研/HPC基础设施环境中的离线隔离、synthetic fixture、依赖声明和 validation boundary。

### Modified Capabilities

None.

## Impact

- 影响 test configuration、少量现有 test helpers、synthetic fixture documentation、`pyproject.toml` 的 test dependency 声明，以及默认测试说明。
- 不改变 production runtime、server profile schema、scheduler submission、scientific workflow 或用户数据格式。
- 不新增或执行真实环境 integration tests；已有与本 change 无关的 Qt hover/timer 偶发测试问题不在本范围内。
