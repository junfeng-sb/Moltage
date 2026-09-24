## Why

Moltage 目前只能对**自己提交**的 ORCA optimization 继续执行 Step 2 WBL transmission。用户在服务器上往往已经有大量成功完成、但不是由当前 Moltage project 提交的 ORCA 优化目录。这些结果目前无法进入 WBL 流程：唯一的办法是重新提交一次优化，重复消耗机时并产生与已有结果不一致的第二份数据。

需要一个独立入口，把一次**已经成功**的外部 ORCA 优化登记为 Moltage managed project，并从“优化已成功”这一状态直接进入现有 WBL pipeline。导入本身不重新运行 SCF、几何优化或 frequency，也不修改源目录。

## What Changes

- 在主菜单增加 `Projects > Import Existing Calculation... > ORCA Optimization...`。该入口始终可用，不依赖当前 viewer 内容、已加载分子或已存在 project。
- 新增 `Import Existing ORCA Optimization` 对话框：选择已配置 server profile、输入或浏览远端 ORCA 优化目录、编辑 managed project 名称、只读显示 destination preview、显示面向用户的验证结果。验证成功前 `Import` 保持禁用。
- 远端目录以**只读**方式验证。候选结果按 exact stem 配对 `<stem>.inp` / `<stem>.out` / `<stem>.xyz` / `<stem>.gbw`；恰好一组时自动选择，多组时要求用户显式选择，无法唯一确定时拒绝并说明原因。
- 成功判定复用现有 ORCA parser：`parse_orca_optimization_output` 证明 normal termination 与 optimization convergence，`parse_rendered_orca_structure` 与 `parse_orca_final_xyz` 证明 submitted 原子身份与最终优化结构一致。charge/multiplicity 从源 `.inp` 的字面 `* xyz <charge> <multiplicity>` 块读回（Moltage 自身已有的 rendering grammar），method/basis/dispersion 只在 `!` 行严格命中既有 catalog token 时记录，否则留空。
- 源 `.inp` 若以唯一的 `*xyzfile <charge> <multiplicity> <path>` 头从外部文件读取起始坐标（绝对路径原样使用，相对路径按源目录解析），Moltage 以既有 strict XYZ reader 读取该文件，只把这一行替换为 Moltage 自身 `* xyz` grammar 的内联坐标块，得到 managed `orca_opt.inp`；上述 parser 与交叉校验在该内联输入上原样运行。
- WBL wavefunction readiness 通过复用既有的 sibling `orca_2json` 能力校验确定，结果为 `READY` 或 `CONFIGURATION_REQUIRED`；后者仍可完成 optimization import。
- 导入在 server profile 已配置的 remote project workspace 中创建**独立** managed ORCA project，复用既有 dated-directory allocation。只把进入 WBL 所必需的四个文件以 server 端 copy 方式复制为 `orca_opt.inp` / `orca_opt.out` / `orca_opt.xyz` / `orca_opt.gbw`，并逐个校验 SHA256。`*xyzfile` 输入是唯一例外：`orca_opt.inp` 为内联坐标后的输入，以既有 new-file verified upload 写入并在服务器端校验 SHA256，源 `.inp` 与坐标文件的路径和 SHA256 记入 import provenance。源目录与坐标文件不被写入、重命名或删除。
- 导入后的 project 使用既有 ORCA managed-project architecture：Step 1 `SUCCEEDED` 且 origin 为 `IMPORTED_EXTERNAL`，无 scheduler job ID / scheduler kind / submit script；Step 2 WBL 不存在，因此第二盏灯保持 `NOT_STARTED`。
- 用户随后通过既有路径运行 Step 2：Project Manager → 选中导入的 project → `Refresh Status` → 点击第一盏状态灯打开 Output Geometry → `Calculation > ORCA > Step 2 — WBL Transmission...`。WBL pipeline、科学模型、Γ₀ 语义、spin 处理、结果展示与 `View WBL Transmission` 完全不变。
- Project manifest 升级到 schema 11，新增 ORCA optimization origin 与 import provenance；schema 1–10 继续可读且一律视为 `MOLTAGE_SUBMITTED`，不为旧 project 伪造 imported metadata。
- 对 imported project 禁用 `Resubmit Optimization...`，因为 Moltage 从未提交过该 Step 1，也不持有完整的可复现提交设置。

## Capabilities

### New Capabilities

- `import-existing-orca-optimization`: 定义外部 ORCA 优化结果的发现、配对、只读验证、managed workspace 创建、imported project state、provenance 与 WBL 衔接。

### Modified Capabilities

- None. 仓库当前没有 main OpenSpec specs；既有 ORCA/WBL 行为以 current code 与 tests 为 evidence，本 change 不修改其科学语义。

## Impact

- `PROJECT_SCHEMA_VERSION` 10 → 11。ORCA optimization step 新增可选 `orca_import_provenance`，`OrcaOptimizationResultEvidence` 新增 `origin`。旧 manifest 迁移为 `origin = MOLTAGE_SUBMITTED`、`orca_import_provenance = None`。
- `OrcaOptimizationResultEvidence.succeeded` 只在 `origin` 为 `MOLTAGE_SUBMITTED` 时要求 `scheduler_succeeded`；imported evidence 以 `scheduler_succeeded = False` 如实记录“Moltage 未观察到任何 scheduler 结果”，成功由 ORCA 自身输出证据构成。
- `app/project_recovery.py` 的 ORCA optimization 复核保留 imported origin，不再无条件写入 `scheduler_succeeded = True`，因此 Refresh 与重启后第一盏灯保持绿色、Step 2 保持可用。
- 复用而非新增基础设施：remote 目录浏览复用 `gui/remote_directory_dialog.py`，异步与取消复用既有 `QRunnable` worker/stop-token 模式，managed project 创建复用 `app/project_planning.py` 与 `app/project_submission.allocate_remote_project_directory`，checksum provenance 复用既有 `input_hashes` 机制。
- 验证与导入全部在后台线程执行，有界、可取消，且对话框关闭后不回调已销毁的 Qt widget；导入进行中 `Import` 被禁用以防重复创建 project。

## Out of scope / explicit non-goals

- 本地 ORCA 结果导入、Gaussian/FHI-aims import、通用量化 import framework。
- 整台服务器的自动搜索、导入整个 ORCA 工作目录。
- 从失败或未收敛的 ORCA 结果继续。
- 修改 ORCA Step 1、WBL 科学公式、transmission 定义、Gamma 默认值、singlet/open-shell 行为或图的坐标与样式。
- 重新设计 managed project architecture、remote subsystem、server profile 或 scheduler 支持。
- 真实服务器验收。

## Known scope deviation (user-approved)

原始需求把 `.out + .molden.input` 列为与 `.out + .gbw` 并列的有效 wavefunction 来源，并要求“已存在 `.molden.input` 时不重复转换”。**该分支不予实现。** 依据：`app/orca_wbl.py` 只消费 `orca_opt.gbw`，在分析时通过 `orca_2json` 生成 `orca_wavefunction.json`（basis / MO coefficients / AO overlap `S-Matrix`）并由 `orca/wavefunction.py` 解析；`orca_2mkl -molden` 产出的 `.molden.input` 是**只写 provenance artifact**，仓库中不存在任何 Molden reader。支持 Molden 作为输入需要新增一条科学证据管线并修改 WBL 输入契约，明确超出本 change 范围。导入因此要求 `.gbw`，并在对话框中解释被忽略的 `.molden.input`。

同样经用户确认的两项收窄：源 `.inp` 为必需文件（否则 Refresh 会把第一盏灯从绿降为黄）；method/basis 只在严格命中既有 catalog token 时记录，识别不到时留空并由 WBL 的既有 manual AO 模式承担。

后续经用户确认的修订（2026-09-22）：真实外部输入 `*xyzfile 0 5 /absolute/path/start.xyz` 被原设计拒绝，导致成功完成的优化无法导入。用户选择在导入时内联坐标，而不是保留逐字节相同的 `orca_opt.inp` 并在 recovery、geometry view 与 WBL 三处增加 imported 分支。代价是此类 project 的 managed `orca_opt.inp` 与源 `.inp` 不再逐字节相同；该差异只限于坐标行，并由 provenance 中源 `.inp` 与坐标文件各自的 SHA256 如实记录。
