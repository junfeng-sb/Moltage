# 19–22. 文件、故障排除与参考

[返回手册目录](README.md)

## 19. 文件、状态与来源记录

### 19.1 用户拥有的内容

Moltage 将已安装程序文件、当前用户的应用数据、本地源文件和远程计算项目分开管理。

- 安装目录包含应用程序和随程序再分发的运行时组件。
- 非敏感偏好设置、服务器配置、已知项目缓存、known-hosts 数据和回收站记录位于 `%APPDATA%\Moltage\`。
- 已保存的服务器密码使用 Windows Credential Manager，不会写入服务器配置文件。
- 电子密度结果缓存位于 `%APPDATA%\Moltage\density_results`。
- 输入分子文件保留在用户放置的位置，除非用户明确保存或导出到别处。
- 由 Moltage 管理的计算数据位于所选服务器配置的 **Remote project workspace** 下。

卸载程序会删除由安装器管理的应用文件和快捷方式，但保留当前用户的应用数据。请按照自己的数据管理策略备份输入文件和远程项目数据。

### 19.2 项目记录

每个由 Moltage 管理的远程项目都包含 `.moltage` 项目清单。该记录保存工作流、项目身份、设置、提交记录、文件、哈希、调度器信息和阶段状态。Project Manager 以远程项目清单为准；本地项目列表只是缓存。

项目活动期间，请勿手动编辑项目清单或重命名计算文件。如果外部编辑改变了由哈希校验的文件，Moltage 会报告不匹配，而不会静默接受不同结果。

### 19.3 典型 FHI-aims 项目结构

```text
ProjectName.YYYYMMDD/
  .moltage/project.json
  geometry.in
  control.in
  submit.sh
  aims.out
  molecule_Au/
    geometry.in
    control.in
    submit.sh
    aims.out
    transport/
      geometry.in
      control.in
      submit.sh
      aims.out
      tcontrol
      submit.aitranss.sh
      aitranss.out
      TE.dat
```

输出文件名来自已保存的提交记录。直接从 Step 2 或 Step 3 开始时，前面阶段会被标记为 `SKIPPED`，不会伪造相应输出。

### 19.4 典型 ORCA 项目结构

```text
ProjectName.YYYYMMDD/
  .moltage/project.json
  orca_opt.inp
  submit.orca.sh
  orca_opt.out
  orca_opt.scheduler.out
  orca_opt.gbw
  orca_opt.xyz
  frequency/                 # 仅在明确请求频率分析后出现
  wbl/                       # 仅在明确请求 WBL 后出现
    orca_wbl_transmission.csv
    orca_wbl_result.json
    orca_wbl_transmission.svg
```

ORCA 可能生成额外的原生辅助文件。Moltage 不会仅因为某个未识别文件存在，就把它作为判断成功的依据。

### 19.5 用户应保留的来源信息

- 把对应的输入、输出、调度器脚本和项目清单保存在一起。
- 在报告中记录用户选择的 charge、multiplicity、method、basis、species accuracy、coupling value 和 energy reference。
- 区分 `HYPOTHESIS` 参数和根据外部证据标定的数值。
- 不要把 ORCA WBL 或本地紧束缚输出称为显式 DFT-NEGF。
- 将导出图片与其源项目和设置一起保存。
- 比较计算时使用一致的模型定义，并明确记录有意设置的差异。

## 20. 故障排除

### 20.1 菜单项被禁用

Moltage 根据当前活动工作区和已验证项目状态启用操作。请检查：

1. 当前是否为 Geometry 标签页，而不是 Transmission、Density 或 Tight-Binding 标签页？
2. 几何是否可编辑？已恢复的提交几何可能是只读的。
3. 所选操作是否要求前一阶段已验证完成？
4. 服务器上相关 FHI-aims、AITRANSS 或 ORCA 运行环境是否已经配置并验证？
5. 对 ORCA Step 2 和 Frequency，优化几何是否已恢复到当前 ORCA Geometry 标签页？

切换标签页会让主操作重新绑定到活动工作区，但不会改变远程项目。

### 20.2 自动查找运行环境未找到结果或只找到不完整候选

自动查找有明确范围，刻意不会遍历整台服务器。只有可执行文件、所需环境和相关路径通过检查时，系统才能确认程序。

使用 **Server → Manage Servers... → Edit → Cluster Execution Settings...**，打开相应程序页面，然后：

- 提供安全的安装目录提示并重新查找；或
- 选择 **Manual Configuration...**，输入该服务器上已知的绝对路径和环境。

对于 FHI-aims，还需验证 MPI 启动器，以及直接子目录含 `light`、`tight` 和 `really_tight` 的元素定义根目录。对于 ORCA，需要验证主程序路径和环境。找到可执行文件并不能证明 MPI ABI、计算节点可用性、许可资格或科学输入兼容性。

### 20.3 SSH 连接或 host key 失败

- 重新检查 hostname、port、username 和网络/VPN 访问。
- 首次连接需要明确接受显示的 host-key fingerprint。
- host key 改变时会被阻止。在替换可信证据前，请先向服务器管理员确认变化。
- v0.2.1 支持密码认证；尚未实现 SSH key、jump host 或 ssh-agent 工作流。

切勿把密码、私钥、访问 token 或未经去敏的内部服务器记录粘贴到公开 issue。

### 20.4 提交结果显示 unknown

Moltage 至多执行一次调度器提交。如果提交后连接中断且无法确认结果，项目会变为 `UNKNOWN`，应用不会自动再次提交。重新连接并使用 **Refresh Status**。在确认对应提交记录的调度器状态前，不要创建重复作业。

### 20.5 刷新时间过长

刷新有明确超时。刷新期间按钮会变为 **Stop Refresh**；停止会立即与刷新分离，而不取消远程计算。关闭 Moltage 也不会等待仅用于状态查询的刷新结束。提交、取消、删除或结果传输则不同，退出前可能需要明确的完成/拒绝消息。

### 20.6 调度器显示 completed，但指示灯不是绿色

绿色代表科学证据已通过验证，而不仅是调度器成功退出。请查看界面诊断，并使用 **Open / Recover** 或 **Refresh Status**。常见原因包括：

- 缺少正常终止或收敛证据；
- 结果文件缺失、为空、格式错误或哈希不匹配；
- 输出属于不同几何或不同提交记录；
- AITRANSS 结果网格与 `tcontrol` 不一致；
- WBL 结果文件集合不完整或创建后被更改。

证据不足时，Moltage 会保留 `SCHEDULER_COMPLETED` 或失败状态，而不会虚构科学成功。

### 20.7 ORCA Step 2 无法启动

确认以下所有条件：

- Step 1 已验证正常终止和优化收敛；
- `orca_opt.gbw` 与记录中的 Step 1 提交匹配；
- 已验证 ORCA 安装提供兼容且同目录的 `orca_2json`；
- 已选择两个受支持的 S/N 接触和 linker 类型；
- 左侧 Γ₀ 以及在独立设置时的右侧 Γ₀ 都为正值；
- 能量窗口和步长有效。

WBL 阶段不以创建 Molden 文件作为成功标准。它优先使用完整的 `orca_2json` 证据路径，并创建前文列出的三个持久化 WBL 结果文件。

### 20.8 对数坐标中的 transmission 曲线为空

对数轴无法显示零或负值。Moltage 刻意不使用人为正下限替换这些数据。检查结果在当前 X 范围内是否存在正样本，然后使用 **Reset View**，或在 **Settings → View...** 中调整 X/Y 范围。显示问题不会改变持久化科学结果。

### 20.9 电极扩展位点显示为红色

红色表示该位置在标准 Au(111) 晶格上有效，但被当前完整几何阻挡，单击不会添加原子。移动或旋转相应电极/分子后重新评估；不要把红色当作强制添加重叠原子的提示。正常半透明金色表示可用位点。

### 20.10 电子密度任务无法提交

两个 subset 必须非空、互不重叠，并且共同覆盖所有原子。Total charge 必须等于两个 fragment charge 之和。所选服务器必须具有已验证的 FHI-aims 运行环境和元素定义根目录。请检查片段自旋状态，并确保单个作业的最长运行时间足以覆盖三次连续 SCF。

### 20.11 图形或渲染问题

更新显卡驱动后重试。VTK/Qt 在虚拟机或远程桌面下的初始化行为可能不同；Windows 发行版包含 Qt 的软件 OpenGL 后备组件。如果可复现的渲染错误仍存在，请报告 Moltage 版本、Windows 版本、GPU/驱动程序、具体操作和经去敏的截图。

## 21. 精简 UI 参考

### 21.1 主工具栏（从左到右）

| 操作 | 用途 |
|---|---|
| **Undo / Redo** | 在当前几何结构工作区的有限编辑历史中前进或后退 |
| **Reset View** | 重新适配相机，或恢复结果图的默认范围 |
| **Element Labels** | 在当前几何结构工作区中切换原子标签 |
| **Measure Distance** | 选择两个原子并显示距离 |
| **Measure Angle** | 选择三个原子并显示角度 |
| **Rotate Bond** | 选择一个键，通过交互或数值方式旋转一侧 |
| **Delete Atom** | 在可编辑的几何结构工作区中连续删除所选原子 |
| **Replace Atom** | 在可编辑的几何结构工作区中连续替换所选原子 |
| **Electrode Builder** | 打开或关闭锚点/接触/电极工具 |

使用 `Esc` 退出当前选择/编辑模式。坐标发生变化后，已完成的测量叠加会被清除，因为旧值已不再描述新几何。

### 21.2 主菜单

| 菜单 | 重要入口 |
|---|---|
| **File** | New Geometry；Export Current View |
| **Projects** | Project Manager |
| **Calculation → FHI-aims** | Step 1–4 和 Electron Density Difference |
| **Calculation → ORCA** | Optimization、WBL Transmission、Frequency |
| **Calculation** | Local Tight-Binding Transmission |
| **Server** | Manage Servers |
| **Settings** | View、Bond Detection、Email Notifications |
| **Help** | License and Third-Party Notices；About Moltage |
| **右上角按钮** | Theme 和 Update Log |

详细操作见[开始使用](01_getting_started.md)。

### 21.3 项目指示灯颜色

| 外观 | 含义 |
|---|---|
| 绿色实心 | 科学状态已验证成功 |
| 黄色实心 | 排队中、运行中、输出已生成，或其他尚未结束的中间状态 |
| 红色实心 | 验证或计算失败 |
| 灰色实心 | 已跳过；部分明确取消或停止的工作流也使用灰色终态 |
| 灰色空心 | 尚未开始 |

请同时阅读附带文字。颜色只概括状态，不能替代已保存的诊断。

### 21.4 常见键盘/鼠标操作

- 相应模式活动时，左键可选取原子、键或图表数据点。
- 除非某工具已接管交互，在分子查看器中拖动会旋转相机。
- 滚轮缩放当前 3D 或图表视图。
- `Esc` 取消未完成的选取模式。
- 标准 Undo/Redo 快捷键只作用于可编辑的几何结构工作区。
- 结果标签页和已恢复的提交标签页为只读，除非明确打开为新的可编辑草稿。

## 22. 更新、许可与支持

**Update Log** 会以当前应用语言显示所安装版本的更新说明。**Help → License and Third-Party Notices...** 显示 Moltage 的 GPL-3.0-only 许可和再分发组件的声明。**Help → About Moltage** 显示安装版本。

有效的问题报告应包括：

- Moltage 版本和安装类型；
- Windows 版本；
- 工作流和具体阶段；
- 预期与实际行为；
- 尽可能使用非敏感示例给出的可复现步骤；
- 界面显示的完整诊断；
- 经去敏的截图或最小合成输入。

分享前，请移除密码、主机名、用户名、内部路径、调度器账户名、作业编号、未公开的分子/项目名称和真实科研输出。不要上传专有的 FHI-aims 或 ORCA 软件发行文件。

项目主页、当前源代码、问题追踪和版本下载位于 [github.com/junfeng-sb/Moltage](https://github.com/junfeng-sb/Moltage)。

---

至此，v0.2.1 中文用户手册结束。可返回[手册目录](README.md)或[项目 README](../../README.md)。
