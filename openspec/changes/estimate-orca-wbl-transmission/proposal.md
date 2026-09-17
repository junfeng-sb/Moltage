## Why

Moltage 已能验证 ORCA 分子优化及 `.gbw` 可用性，但尚不能把这些既有电子结构证据转化为独立、可追溯的 wide-band-limit (WBL) transmission 估算。用户需要在不重新运行 SCF/优化、也不依赖 Multiwfn 的前提下，主动启动一个与优化成功状态分离的 ORCA workflow 第二阶段。

## What Changes

- 在已验证 ORCA optimization 后增加用户主动选择的 `ORCA_WBL_TRANSMISSION` 第二阶段；它不自动启动、不提交新的量子化学计算，也不改变 optimization/frequency 结果。
- 从远程 `.gbw` 使用同一 ORCA 安装中的、经验证的绝对 `orca_2json` 提取 MO、basis 和 overlap matrix 证据；可选保存 `orca_2mkl` 生成的 Molden provenance artifact，但 Molden 不能替代 Löwdin 所需的 overlap matrix。
- 使用 canonical MO、symmetric Löwdin orthogonalization 和 direction-aware contact subspaces 计算全部 MO 的独立-resonance WBL transmission；restricted multiplicity-1 结果只计算一次 spatial-MO total，unrestricted open-shell 结果保留 spin resolution。
- 首版支持 `SH`、`SMe`、`NH2`、`Pyridine`。Moltage 不提供任何数值 `Gamma_0` 默认值；左右 `Gamma_0` 必须由用户提供，并明确标记 `HYPOTHESIS` 或 `CALIBRATED` provenance。
- WBL 设置界面复用既有 linker detector：恰好识别出两个受支持 linker 时预填 contact/linker；用户也可暂时隐藏设置窗并在结构视图按原子序号点选 contact。主界面只保留 contact、自动识别结果、`Gamma_0` 与能量窗口，projection/provenance override 收入 Advanced。
- Au `E_F` 使用明确标为可编辑 `HYPOTHESIS` 的 `-5.1 eV` 模型起始值，默认扫描窗口为 `E-E_F = [-5, 5] eV`、步长 `0.01 eV`；这些 UI defaults 不改变既有结果或用户提交参数。
- 对 reviewed def2 basis 使用版本化 valence-shell mapping；S 只使用 directional valence `3p`，不得混入 core `2p`。未知/custom/composite basis 不做猜测，要求用户提供明确 manual AO subspace。
- 按已验证的 spin treatment 输出 total-only 或 alpha/beta/raw-sum CSV/图、structured JSON、适用的 top-two MO 摘要和输入/工具/参数 hashes。
- 使用 synthetic fixtures 验证 parsing、projection、全部-MO transmission、持久化、remote orchestration 和 GUI；不连接真实 HPC 或运行 ORCA。

## Capabilities

### New Capabilities

- `orca-wbl-transmission`: 定义 optimization 后的 ORCA wavefunction evidence extraction、directional Löwdin contact projection、spin-resolved WBL calculation、provenance、project state 和 visualization。

### Modified Capabilities

- None. 当前尚无 main OpenSpec specs。

## Impact

- Project manifest schema 增加可选 ORCA WBL stage 及其 immutable submitted/result evidence；旧 schema 与既有 optimization/frequency project 保持兼容。
- ORCA project 固定显示 optimization 与 WBL 两盏状态灯；WBL/frequency 提交位于已取回 ORCA structure 的 `Calculation > ORCA` 子菜单，完成的 WBL 可从该菜单或第二盏灯打开 transmission view。
- WBL 准备、运行、成功和失败信息同时传播到发起操作的 ORCA Geometry workspace，而不只显示在 Project Manager。
- 未完成 ORCA optimization 也可仅从 `orca_opt.inp` 取回 submitted structure，并以持久化 settings 重新提交；新提交前只对经 scheduler 重新验证仍 active 的旧 Job 发出 exact-ID cancellation，状态不确定时不提交新 Job。
- ORCA structure recovery/view 只读取 ORCA-native artifacts，不读取 `control.in`、`geometry.in` 或其他 FHI-aims project files。
- Remote operation 只运行 ORCA distribution 的 evidence-conversion utilities，不运行新的 electronic-structure job。
- 结果必须标为 linker-parameterized WBL `HYPOTHESIS`，不得称为 explicit Au-molecule-Au DFT-NEGF。
