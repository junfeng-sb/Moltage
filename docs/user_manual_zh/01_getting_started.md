# 1–4. 入门

[返回手册目录](README.md)

## 1. Moltage 简介

Moltage 是一款 Windows 桌面工作台，可用于准备分子结构、构建 Au–分子结
几何结构、在已配置的 Linux 服务器上运行结构化计算，以及查看部分电子结构
和输运结果。

v0.2.1 包含三类相互区分的计算流程：

- **FHI-aims + AITRANSS**：从分子优化到非自旋 transmission 查看的一套
  由 Moltage 管理的四阶段流程。
- **ORCA**：分子优化、可选的频率分析，以及由用户明确启动的优化后 WBL 模型。
- **独立分析**：本地 tight-binding transmission，以及远程 FHI-aims 电子密度差任务。

Moltage 不附带 FHI-aims、AITRANSS 或 ORCA。用户必须合法取得这些程序的
使用权，并配置对应的服务器路径。

> **科学输入说明：** Moltage 不会判断哪一种泛函、基组、电荷、多重度、自旋态、
> 耦合强度、收敛标准、调度资源或服务器环境在科学上正确。所有起始值都必须根据
> 目标体系复核。

## 2. 安装与首次启动

### 2.1 系统要求

- 64 位 Windows。
- 能够运行 Qt/VTK 分子查看器的图形环境。
- 本地查看和本地 tight binding 不需要服务器账户。
- 远程计算需要已保存的服务器配置。

### 2.2 安装 Moltage v0.2.1

1. 打开 [v0.2.1 release 页面](https://github.com/junfeng-sb/Moltage/releases/tag/v0.2.1)。
2. 下载 `Moltage-Setup-0.2.1.exe`。
3. 运行安装程序，选择安装目录，并决定是否创建桌面快捷方式。

Windows 可能显示权限或安全提示。继续前请确认安装包来自官方 release 页面。

### 2.3 用户数据与卸载

安装程序只管理应用程序文件，不管理用户的 Moltage 状态。Profile、已知项目、
host key、所选主题和诊断状态存放在安装目录之外的 Windows 用户应用数据目录中。
保存的密码由 Windows Credential Manager 管理。卸载程序会有意保留这些用户状态。

### 2.4 首次启动

Moltage 启动时不会自动连接服务器。空的 Geometry 工作区包含 **Open...** 按钮
和受支持文件类型提示。只有用户明确发起服务器相关操作后，程序才会建立远程连接。

## 3. 界面概览

<p align="center">
  <img src="../images/readme/main-workspace.png" alt="Moltage 主分子工作区" width="100%">
</p>

**图 1.** 包含演示结构的主工作区。只有在优化分子时勾选了输出前线轨道，
并且相应文件可用时，右上角的轨道按钮才会出现。

### 3.1 主窗口区域

| 区域 | 用途 |
| --- | --- |
| Menu bar | File、Projects、Calculation、Server、Settings 和 Help 命令 |
| Main toolbar | 直接作用于当前标签页的几何结构与查看工具 |
| Workspace tabs | 相互独立的 Geometry、Transmission、Density 或本地分析工作区 |
| Electrode Builder | Linker 识别、接触 Au 放置、金字塔构建和晶格扩展 |
| Scientific canvas | 显示分子、Cube 数据或 transmission 图的主显示区 |
| Bottom status area | 所选原子、操作结果、测量表或当前工作流消息 |
| Upper-right controls | 主题选择器和内置 Update Log |

切换标签页会改变当前可用命令。Transmission、Density 结果、只读工作区及其他
不兼容工作区会禁用几何编辑命令。

### 3.2 工具栏按钮

主工具栏从左到右排列如下：

| 按钮 | 用途 | 是否改变结构？ |
| --- | --- | --- |
| **Undo** | 在当前 Geometry 工作区最多五级的编辑历史中恢复上一个状态 | 是，恢复旧状态 |
| **Redo** | 重新应用已撤销的几何编辑 | 是 |
| **Reset View** | 重置当前分子相机或 transmission 坐标范围 | 否 |
| **Element Labels** | 显示或隐藏元素符号标签 | 否 |
| **Measure Distance** | 反复选择两个不同原子并显示距离（Å） | 否 |
| **Measure Angle** | 反复选择 A–B–C 并显示以 B 为顶点的角度 | 否 |
| **Rotate Bond** | 选择满足条件的 Connectivity 边，并刚性旋转其中一个连通分量 | 是 |
| **Delete Atom** | 在退出模式前连续删除所选原子 | 是 |
| **Replace Atom** | 选择受支持元素后，连续替换所选原子的元素身份 | 是 |
| **Electrode Builder** | 显示或隐藏左侧 Electrode Builder 面板 | 单独点击不会 |

需要选择原子的工具彼此互斥。按 **Escape** 或再次关闭当前工具可退出选择模式。
如果没有活动 Geometry 标签页、结构只读，或当前工作流只允许改坐标，命令会被禁用。

### 3.3 菜单入口

| 菜单 | 命令 | 用途 |
| --- | --- | --- |
| File | **New Geometry...** | 打开受支持的本地结构或 Cube 文件 |
| File | **Export Current View...** | 以 1×–8× 分辨率将当前科学画布导出为 PNG、JPG/JPEG 或栅格 PDF |
| Projects | **Project Manager...** | 管理已经提交的计算，查看当前状态以及进行后续操作 |
| Calculation > FHI-aims | **Step 1 — Molecule Optimization...** | 启动由 Moltage 管理的分子优化 |
| Calculation > FHI-aims | **Step 2 — Molecule–Au Optimization...** | 继续已有项目，或从满足条件的双接触结构直接开始 |
| Calculation > FHI-aims | **Step 3 — Transport Convergence...** | 提交固定几何结构的输运收敛计算 |
| Calculation > FHI-aims | **Step 4 — Transmission...** | 在 Step 3 通过验证后准备并提交 AITRANSS |
| Calculation > FHI-aims | **Electron Density Difference...** | 打开独立的三组分电子密度差任务 |
| Calculation > ORCA | **Step 1 — Optimization...** | 启动 ORCA 分子优化 |
| Calculation > ORCA | **Step 2 — WBL Transmission...** | 使用已验证 ORCA 波函数进行分析，不重新运行 SCF/优化 |
| Calculation > ORCA | **Run Frequency...** | 为已完成的 ORCA 优化提交可选频率验证 |
| Calculation | **Local Tight-Binding Transmission...** | 打开会话内的单轨道模型 |
| Server | **Manage Servers...** | 新建和编辑服务器连接配置 |
| Settings | **View...** | 编辑分子、Cube 或 transmission 的显示设置 |
| Settings | **Bond Detection...** | 预览推断 Connectivity 所使用的阈值 |
| Settings | **Email Notifications...** | 为一个服务器 profile 配置调度器原生的终态邮件通知 |
| Help | **License and Third-Party Notices...** | 查看内置许可证和第三方声明 |
| Help | **About Moltage** | 显示应用程序和版本信息 |
| 右上角 Theme 控件 | Theme choices | 选择已注册的 Light 或 Dark 主题 |
| 右上角 Updates 控件 | **Update Log...** | 查看内置离线更新日志 |

命令会根据上下文启用。例如，只有当前 Geometry 工作区来自已经验证成功、
由 Moltage 管理的 ORCA 优化，并且具备所需 `.gbw` 文件时，ORCA WBL 才可用。

### 3.4 工作区标签页

- 同一个本地文件路径只会打开一个 Geometry 工作区。
- 如果拖入或打开已经处于打开状态的文件，程序会聚焦现有编辑工作区，而不会
  从磁盘重新读取。
- 关闭标签页只会丢弃本地显示和会话状态，不会取消远程计算。
- 同一项目的结果不会重复打开；再次打开同一成功 transmission 结果时，会聚焦已有标签页。
- 工作区上的关闭按钮不是远程删除命令。

### 3.5 主题选择

使用右上角 Theme 按钮，可在数种 Light 和 Dark 主题中切换。下面各展示一个示例：

<p align="center">
  <img src="../images/manual/theme-future-light.png" alt="Light 主题示例" width="100%">
</p>

**Light 主题示例。**

<p align="center">
  <img src="../images/manual/theme-event-horizon.png" alt="Dark 主题示例" width="100%">
</p>

**Dark 主题示例。**

软件会记住当前用户选择的主题。主题只改变界面外观和原子反馈颜色，不会改变坐标、
科学数值、服务器设置或生成的输入文件。

## 4. 打开和查看分子结构

### 4.1 支持的本地格式

| 扩展名 | 支持范围 |
| --- | --- |
| `.xyz` | 严格四列分子 XYZ；Connectivity 由几何关系推断 |
| `.mol` | 单个 MOL V2000 记录，支持 1、2、3 级键（type 1、2、3） |
| `.in` | 本地 FHI-aims 分子 geometry |
| `.next_step` | 完整独立的 FHI-aims next-step 分子 geometry |
| `.cube`, `.cub` | 单个有符号标量数据集；支持 ORCA/Gaussian 轨道格式，或由用户明确确认坐标单位的 FHI-aims/未知格式 |

v0.2.1 不支持 V3000、SDF、芳香/type-4 键、多数据集 Cube、形式电荷可视化和
键级编辑。格式错误会明确失败，Moltage 不会静默改用其他解析器。

### 4.2 打开文件

可使用以下任一方法：

1. 选择 **File > New Geometry...**，然后选择一个或多个受支持文件。
2. 将本地受支持文件拖入主工作区。

一次拖入多个文件时，程序会按源文件顺序打开。目录、非本地 URL 和不受支持的
扩展名会被报告，不会交给解析器。

### 4.3 查看器操作

- **Rotate**：使用正常轨迹球手势在分子画布中拖动。
- **Zoom**：滚动鼠标滚轮。
- **Inspect atom**：将指针停在原子上，显示空心圆环及其原子编号。
- **Select atom**：当当前工具请求选择原子时单击。
- **Reset camera**：按 **Reset View**。

查看器只渲染输入提供的原子中心和 Connectivity。它不会仅根据画面推断化学身份、
化合价、芳香性或电极角色。

### 4.4 元素标签与氢原子可见性

工具栏 **Element Labels** 按钮可立即切换元素符号。更多控件位于
**Settings > View... > Molecule**：

| 设置 | 默认值 | 用途 |
| --- | --- | --- |
| Bond thickness | `1.00×` | 只缩放显示的键线条 |
| Element labels | Off | 显示不参与拾取的元素符号标签 |
| Hide hydrogen atoms | Off | 隐藏 H 图形、H 标签、与 H 相连的键和 H 反馈，但不删除原子 |
| Atom colors | 内置配色 | 允许在当前会话中覆盖各元素的显示颜色 |

修改会立即在所有打开的 Geometry 标签页中预览。**OK** 保留当前会话状态；
**Cancel** 恢复打开对话框时的状态。只有已批准的 Cube 材质/光照参数和主题 ID
会跨应用程序启动保存；上述普通分子显示选项不会持久化。

### 4.5 Bond Detection

**入口：** `Settings > Bond Detection...`

对于推断得到的图，Moltage 使用：

`distance(i,j) <= factor × (covalent radius(i) + covalent radius(j))`

Factor 默认为 `1.10`，允许范围为 `0.01`–`10.00`，显示两位小数，每次改变 `0.10`。
每次数值变化都会预览重新生成的推断边。**OK** 保留预览的 factor/图；
**Cancel** 精确恢复打开对话框前的图。显式 MOL V2000 Connectivity 不受影响。

这种 Connectivity 只是供应用程序操作使用的近似结果，不代表键级、化合价或
化学结构鉴定。

### 4.6 测量

**Distance** 选择两个不同原子并报告 Å；**Angle** 依次选择 A、B、C，报告
以 B 为顶点的 A–B–C 角度。

- 当前工具会保持启用，可连续进行多次测量。
- 未完成选择使用琥珀色高亮，并显示 1/2/3 标签。
- 按 **Escape** 只清除当前未完成的选择序列。
- 已完成测量显示在底部测量面板中。
- 右键单击一行并选择 **Delete measurement** 可删除该结果。
- **Clear** 清除当前 Geometry 工作区的全部已完成和未完成测量，但保持所选
  测量工具处于启用状态。
- 测量不会写入 geometry 或计算输入。

替换原子会保留已完成测量；删除原子只会删除引用该原子的测量，并重新映射其余
原子编号。

### 4.7 Rotate Bond

1. 按 **Rotate Bond**。
2. 单击一条已渲染的 Connectivity 边。
3. 如果移除该边会将其连通分量分成两部分，Moltage 会显示扭转控件。环内边和
   端点之间仍存在其他路径的边会被拒绝。
4. 拖动旋转手柄，或单击显示的角度输入精确的有符号数值。
5. 使用切换旋转侧的控件固定另一连通分量；当前坐标成为新的 `0.0°` 基线。

程序会刚性移动端点较小一侧的分量，不相关的断开分量保持不动。第一次真实旋转
会修改工作坐标、进入 Undo/Redo，并清除依赖坐标的测量；它不会重写最初打开的
源文件，也不会重新推断键。

### 4.8 Delete Atom 与 Replace Atom

**Delete Atom** 会删除单击的原子及其关联边，并压缩编号。该模式会保持启用，
直到再次关闭或按 Escape。

**Replace Atom** 会先打开周期表。选择一个可用元素后，单击原子即可只改变
元素身份；坐标和 Connectivity 保持不变。两种操作都进入同一个五级 Geometry
Undo/Redo 历史。

只读或只允许改坐标的工作流标签页不能使用这些工具。改变原子身份或拓扑可能使
已应用电极的来源记录失效。

### 4.9 保存和导出

- Electrode Builder 中的 **Save geometry.in** 会在条件满足时，将当前已应用的
  分子结构写为 FHI-aims geometry 格式。
- **File > Export Current View...** 导出当前可见科学画布。选择输出文件、格式
  （`PNG`、`JPG`/`JPEG` 或栅格 `PDF`）和分辨率（`1×`–`8×`）。输出的是像素，
  不是新的科学数据集。
- 导出分子/Cube 视图时会保留当前相机和 overlay。
- 导出 transmission 视图时会包含当前显示设置和标注。

[下一章：分子准备与电极构筑](02_molecular_preparation.md)
