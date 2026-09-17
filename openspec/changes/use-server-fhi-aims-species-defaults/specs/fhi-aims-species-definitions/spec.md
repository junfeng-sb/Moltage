## Purpose

定义 Moltage 如何为每个服务器定位、保存和读取其实际 FHI-aims species definitions，并确保所有新 `control.in` 使用该来源，同时保持既有任务和既有输入文件可继续使用。

## ADDED Requirements

### Requirement: Product contains no bundled FHI-aims species definitions

Moltage SHALL NOT 在 repository、source distribution 或 installer 中包含 FHI-aims `species_defaults` 文件，并且 SHALL NOT 在运行时使用 packaged species-definition fallback。

#### Scenario: Installed application has no packaged species library

- **WHEN** Moltage 被构建或安装
- **THEN** application resources 不包含 FHI-aims `*_default` species-definition files
- **AND** 新 `control.in` generation 不读取 application resource directory 中的 species definitions

### Requirement: Species root belongs to the selected server configuration

Moltage SHALL 为每个 server execution configuration 保存一个 FHI-aims species root。该 root MUST 是服务器上的 canonical absolute POSIX directory，并且其直接子目录 SHALL 对应 Moltage 支持的 accuracy families，包括 `light`、`tight` 和 `really_tight`。

#### Scenario: Save a valid configured root

- **WHEN** 用户为一个 server profile 保存满足路径格式要求的 species root
- **THEN** 该路径与该 server execution configuration 一起持久化
- **AND** 选择其他 server profile 不会复用该路径

#### Scenario: Load a legacy profile

- **WHEN** Moltage 读取一个在本 capability 之前保存且没有 species root 的合法 profile
- **THEN** profile 的既有 scheduler、runtime 和 connection values 保持不变
- **AND** species root 被视为 unresolved，而不是由 packaged data 或其他 profile 静默补齐

### Requirement: Runtime discovery performs targeted species-root detection

用户明确启动 FHI-aims runtime discovery 时，Moltage SHALL 在同一次 read-only discovery 中，根据已验证 FHI-aims executable 及其 installation evidence 检查有界的 candidate locations。Discovery MUST NOT 递归扫描 home directory 或整个 filesystem，也 MUST NOT 运行 FHI-aims calculation executable。

#### Scenario: A unique species root is discovered

- **WHEN** 一个已验证的 FHI-aims executable 对应唯一且可读的 species root
- **THEN** discovery result 包含该 canonical species-root path
- **AND** runtime configuration 界面自动填写该路径

#### Scenario: No species root is discovered

- **WHEN** 所有有界、evidence-derived candidate locations 均未通过验证
- **THEN** discovery 明确报告 `FHI-aims species root` 仍缺失
- **AND** 提示用户在现有 FHI-aims manual configuration 中输入或选择该目录
- **AND** 不启动 filesystem-wide fallback scan

#### Scenario: Multiple valid roots are discovered

- **WHEN** discovery 找到多个不能可靠自动区分的有效 species roots
- **THEN** Moltage 要求用户明确选择一个 candidate
- **AND** 不静默选择搜索顺序中的第一个路径

### Requirement: Manual species-root configuration is available with runtime settings

Moltage SHALL 在现有 FHI-aims runtime configuration 界面中同时提供 species root 的文本输入和 remote folder selection。自动发现与手动配置 SHALL 写入同一配置字段。

#### Scenario: User enters a path manually

- **WHEN** 用户输入一个 syntactically valid canonical absolute POSIX directory 并保存 server settings
- **THEN** Moltage 保存该 path
- **AND** 在下一次需要生成新 `control.in` 时对其远程可用性和内容进行验证

#### Scenario: User selects a remote folder

- **WHEN** 用户点击 species-root folder selector、完成 SSH authentication 并选择一个现有 remote directory
- **THEN** 选择器只返回该服务器上的 folder path
- **AND** 该 path 被填入 species-root field，不改变 Remote Project Workspace

### Requirement: New control.in uses definitions from the selected server

Moltage SHALL 仅从当前选择的 server profile 所配置 species root 读取生成新 `control.in` 所需的 definitions。所需文件 SHALL 按 selected accuracy、element atomic number 和 element symbol 在对应 immediate accuracy subdirectory 中确定，且不得用其他 accuracy 或 element 文件替代。

#### Scenario: Create a new Step 1 or Step 2 input

- **WHEN** 用户确认在已配置服务器上创建新的 Step 1 或 Step 2 task
- **THEN** Moltage 从该服务器读取结构实际需要的每个 element/accuracy definition
- **AND** 将经过验证的完整 blocks 写入新 `control.in`

#### Scenario: Create a new Step 3 input

- **WHEN** 用户确认创建新的或重新收敛的 Step 3 task
- **THEN** 新 `control.in` 使用当前 server profile 的 remote species definitions

#### Scenario: Create a new density-difference input

- **WHEN** 用户确认创建新的 density-difference task
- **THEN** 三个 component `control.in` 使用当前 server profile 的 remote species definitions

#### Scenario: Per-atom accuracy overrides are present

- **WHEN** 一个新 optimization input 对同一 element 请求多个 supported accuracy values
- **THEN** Moltage 分别读取每个所需 accuracy 文件
- **AND** 保持现有 calculation-only species alias semantics

### Requirement: Remote definitions are validated before remote mutation

Moltage SHALL 在用户明确授权相关 submission/export operation 后建立 connection，并在创建 remote project/task directory 或提交 scheduler job 之前完成所需 species files 的读取和验证。每个文件 MUST 可读、大小受限、可按 UTF-8 解码，并包含唯一且与 requested element 匹配的 active `species` declaration。

#### Scenario: All required files are valid

- **WHEN** 配置路径和本次所需 definitions 全部通过验证
- **THEN** Moltage 在 memory 中生成完整 input bundle
- **AND** 后续 remote directory creation、upload 和 scheduler submission 继续遵循既有 workflow

#### Scenario: A required file is unavailable or malformed

- **WHEN** species root 不可用，或任一 required element/accuracy file 缺失、过大、不可解码或 declaration 不匹配
- **THEN** Moltage 显式报告失败的 path、element 和 accuracy
- **AND** 不生成或提交不完整的 task
- **AND** 不创建该次 operation 的 remote project/task directory
- **AND** 不使用 cached、packaged、其他服务器或其他 accuracy 的 definition 作为 fallback

### Requirement: Existing control.in remains self-contained

已经生成的 `control.in` SHALL 被视为包含其自身完整 species-definition content。仅查看、解析、恢复或原样重试既有 inputs 时，Moltage MUST NOT 要求当前 server species root 与历史 definitions 相同，也 MUST NOT 因 species path 尚未配置而拒绝这些非生成操作。

#### Scenario: Recover an existing task with a legacy profile

- **WHEN** 用户读取或恢复一个已有 `control.in`，而其 profile 尚未配置 species root
- **THEN** Moltage 使用该 `control.in` 中已有的 species blocks 完成支持的解析和完整性检查
- **AND** 不连接服务器读取新的 definitions 作为解析前提

#### Scenario: Retry without regenerating control.in

- **WHEN** 既有 workflow 按原始 verified bytes 重试一个 task，且不生成新 `control.in`
- **THEN** retry 继续使用历史 `control.in`
- **AND** 不替换其中的 species blocks

#### Scenario: Restart generates a new control.in

- **WHEN** 用户从历史 task 恢复 settings 后确认创建一个新的 restart task
- **THEN** 历史 settings recovery 使用原文件中已嵌入的 species blocks
- **AND** 新 `control.in` 使用当前选择 server profile 的 remote definitions

### Requirement: New control.in generation requires a configured server

Moltage SHALL NOT 提供独立的 local species-directory configuration。包括 standalone local input export 在内，任何新 `control.in` generation MUST 使用一个已保存且可连接的 server profile。

#### Scenario: Standalone export uses a saved server

- **WHEN** 用户请求把新 FHI-aims inputs 导出到本机目录
- **THEN** Moltage 要求选择一个已保存的 server profile
- **AND** 经用户确认后连接该服务器、读取所需 definitions，再写入本地目标目录

#### Scenario: No usable server configuration exists

- **WHEN** 用户请求生成新 `control.in`，但没有可用 server profile 或该 profile 的 species root unresolved
- **THEN** Moltage 阻止 generation
- **AND** 引导用户完成 server/runtime/species-root configuration
