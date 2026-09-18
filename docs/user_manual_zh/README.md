# Moltage v0.2.1 用户手册

本手册从使用者角度介绍 Moltage v0.2.1，包括各项命令的位置、
操作产生的影响、必须填写的设置，以及如何区分本地显示操作、远程计算
和具有破坏性的项目操作。

## 手册目录

1. [软件简介、安装、界面与打开结构](01_getting_started.md)
2. [Linker、接触 Au、电极构筑与 Au(111) 扩展](02_molecular_preparation.md)
3. [服务器配置、调度器、FHI-aims、AITRANSS 与 ORCA](03_servers_and_schedulers.md)
4. [Project Manager、状态指示、恢复、重启与删除](04_project_manager.md)
5. [FHI-aims 与 AITRANSS 四阶段工作流](05_fhi_aims_workflow.md)
6. [ORCA 优化、频率与 WBL 工作流](06_orca_workflow.md)
7. [Transmission、电子密度差与本地紧束缚模型](07_analysis_and_results.md)
8. [文件、故障排查、支持、按钮速查与术语](08_files_troubleshooting_reference.md)

## 目标和对应路径

| 目标 | 路径 | 计算软件 | 运行环境 |
| --- | --- | --- | --- |
| 打开并查看结构 | `File > New Geometry...` | 无 | 本地 |
| 构建接触 Au 或分子结 | Electrode Builder | 无 | 本地 |
| 优化分子并计算显式输运 | `Calculation > FHI-aims` | FHI-aims 和 AITRANSS | 已配置 Slurm 或 LSF 的服务器 |
| 使用 ORCA 优化分子 | `Calculation > ORCA > Step 1 — Optimization...` | ORCA | 已配置 Slurm 或 LSF 的服务器 |
| 估算 linker 参数化 WBL transmission | `Calculation > ORCA > Step 2 — WBL Transmission...` | 与优化阶段一致、同目录含 `orca_2json` 的 ORCA 安装 | 现有 ORCA 项目；不提交 Slurm 或 LSF 作业 |
| 运行本地单轨道模型 | `Calculation > Local Tight-Binding Transmission...` | 无 | 本地 |
| 计算电子密度重分布 | `Calculation > FHI-aims > Electron Density Difference...` | FHI-aims | 已配置 Slurm 或 LSF 的服务器 |

## 五分钟本地操作示例

1. 启动 Moltage。
2. 选择 **File > New Geometry...**，打开一个受支持的分子文件。
3. 在查看器中拖动以旋转，滚动鼠标滚轮以缩放；将指针停留在原子上，可查看
   原子编号。
4. 如果 Electrode Builder 未显示，请单击工具栏最右侧的按钮将其打开。
5. 如果结构含有受支持的 linker，选择一个已识别位点以预览接触 Au 的放置。
   只有按下 **Done** 后，原子才会真正加入工作结构。
6. 尝试 **Measure Distance** 或 **Measure Angle**。测量只改变显示，不修改分子。
7. 打开 **Settings > View...**，预览键粗细、原子颜色、标签或氢原子可见性。
   这些是显示设置，不是科学计算输入。

本示例在任何远程提交之前结束。只有需要运行外部程序时，才继续配置
[服务器](03_servers_and_schedulers.md)。

## 本手册中的重要术语

- **Geometry workspace（几何结构工作区）**：包含原子、坐标、Connectivity、
  查看器状态和可选编辑历史的标签页。
- **Project Manager（项目管理器）**：管理已经提交的计算，查看当前状态以及
  进行后续操作。
- **Current working geometry（当前工作结构）**：后续 Moltage 操作所使用的
  会话副本；最初打开的文件不会被自动覆盖。
- **Presentation-only（仅影响显示）**：只改变像素或标注，不改变科学数据、
  坐标、项目状态或远程文件。

## 版本范围

本手册对应 **Moltage v0.2.1**。后续版本可能新增或移动控件。可以使用软件
右上角的 **Update Log** 按钮查看当前安装版本内置的更新摘要。

> **文档与科学使用说明：** 截图中的结构、服务器、项目、Job 和结果数据均为
> 演示内容；灰色控件通常表示前置条件尚未满足。Moltage 会验证受支持的工作流
> 证据，但不认证特定外部程序或 HPC 安装环境；科学参数仍需根据目标体系复核。
