# 9–12. 服务器、调度器与程序运行环境

[返回手册目录](README.md)

远程计算需要两类不同配置：

1. **Server Connections**：如何连接一台 SSH 服务器，以及 Moltage 项目目录存放的位置。
2. **Cluster Execution Settings**：该服务器的调度器、任务默认资源和外部科学程序。

只需配置当前工作流实际使用的外部程序。FHI-aims、AITRANSS 和 ORCA 字段
不需要同时填写。

## 9. 服务器配置

### 9.1 打开 Server Connections

**入口：** `Server > Manage Servers...`

仅打开此对话框不会建立网络连接。

### 9.2 连接字段

| 字段 | 用途 |
| --- | --- |
| **Server profile** | 选择已有的服务器配置 |
| **Profile name** | 本地显示名称，不作为远程主机标识 |
| **Host** | 由站点或用户提供的服务器主机名或地址 |
| **Port** | 连接端口；新配置默认为 `22` |
| **Username** | 远程登录用户名 |
| **Password** | 明确执行连接操作时使用的密码 |
| Eye button | 临时显示/隐藏密码字段 |
| **Save password securely** | 将密码保存到 Windows Credential Manager，而不是服务器配置文件 |
| **Auto connect** | 当明确的远程操作需要短连接时，自动解析已保存凭据 |
| **Remote project workspace** | Moltage 创建项目目录的现有绝对 POSIX 目录 |
| Folder button | 连接后打开只读远程目录选择器 |

`Auto connect` 不会在软件启动时自动连接，也不会保持永久 SSH session。

### 9.3 服务器配置按钮

| 按钮 | 操作 |
| --- | --- |
| **Test Connection** | 建立短期 SSH 连接并报告成功或失败 |
| **Cluster Settings...** | 编辑当前配置的调度资源和计算软件运行环境 |
| **New** | 清空表单以新建配置 |
| **Save** | 验证并保存当前配置 |
| **Save As** | 将当前数值另存为一项新配置 |
| **Delete** | 删除本地配置及其保存的凭据；不会删除远程项目 |
| **Close** | 等待尚未结束的连接操作完成后关闭对话框 |

首次连接未知 SSH host 时，程序会显示 host-key fingerprint。只有通过独立的站点
渠道核实该 fingerprint 后才应信任。接受的 key 存放在 Moltage 自己的 known-hosts
文件中。

### 9.4 远程项目工作目录选择器

Folder 按钮只列出现有远程目录。**Up** 移到父目录，**Refresh** 重新加载当前目录，
**Select Folder** 返回所选现有目录，**Cancel** 不做修改。它不会扫描服务器、
新建目录、选择文件或修改远程内容。

### 9.5 凭据与隐私

- 密码绝不会写入 `server_profiles.json`、项目清单、日志、截图或仓库文件。
- v0.2.1 仅支持密码 SSH；尚未实现 SSH key、ssh-agent 和 jump host。
- 可选邮件接收地址属于服务器配置数据，但不是密码。
- 提交公开 issue 时，不要包含真实 hostname、用户名、路径、项目名称或 capture，
  除非你有意公开这些信息。

## 10. 调度器设置

### 10.1 打开 Cluster Execution Settings

在 **Server Connections** 中选择一项服务器配置，然后按 **Cluster Settings...**。
该对话框包含三个顶级页面：

- **General / Cluster**
- **FHI-aims**，其中包含独立的 FHI-aims 和 AITRANSS 子标签
- **ORCA**

顶部显示的服务器名称和远程项目工作目录用于确认当前正在编辑的配置。
**Save** 保存完整工作副本；**Cancel** 不写入任何内容。

<p align="center">
  <img src="../images/manual/cluster-settings-general-synthetic.png" alt="使用示例值的 General 与 Slurm 设置" width="100%">
</p>

**图 4.** 配置为 Slurm 的 General / Cluster 页面。图中名称、路径和资源均为
示例值，不代表对真实站点的建议。

### 10.2 调度器类型与命令位置

Moltage v0.2.1 支持 **Slurm** 和 **IBM Spectrum LSF**。

| 控件 | 含义 |
| --- | --- |
| **Scheduler** | 当前检测或保存的调度器身份 |
| **Manual scheduler type** | 使用手动配置时选择 Slurm 或 LSF |
| **Automatic detection (recommended)** | 在限定范围内查找并验证所需的调度器客户端命令 |
| **Manual** | 使用明确输入的调度器命令目录 |
| **Scheduler command directory** | 包含调度器客户端命令的远程绝对目录 |
| **Detect Scheduler / Verify** | 执行只读验证，不提交任务 |
| **Status** | 位置是否已检测/验证 |
| **Submit command** | 解析得到的 `sbatch` 或 `bsub` 路径 |
| **Version** | 可用时显示调度器版本证据 |
| **Detection method** | 已保存命令位置的取得方式 |

自动检测只在限定范围内进行，不会递归扫描服务器，也不会执行任意站点初始化脚本。

### 10.3 Slurm 资源

| 字段 | 是否必填 | 含义 |
| --- | --- | --- |
| **Account** | 可选 | `--account`；留空使用站点默认值 |
| **Partition** | 可选 | `--partition`；留空使用站点默认值 |
| **QoS** | 可选 | `--qos`；留空使用站点默认值 |
| **Nodes** | 任务必填 | 请求的节点数 |
| **MPI tasks** | 必填 | 调度器任务数；FHI-aims 和 ORCA 按各自工作流使用 |
| **CPUs per task** | 必填 | 每个调度器任务分配的 CPU 数 |
| **Maximum runtime** | 必填 | 作业最长运行时间；以整数分钟保存 |
| **Memory limit per node** | 必填 | 每节点请求的整数 GB |
| **OpenMP threads** | 必填 | 计算软件使用的 OpenMP 线程数 |

Moltage 不会猜测 account、partition 或 QoS。留空明确表示交由调度器/站点默认值处理。

### 10.4 Slurm 行为

| 选项 | 用途 |
| --- | --- |
| **Do not automatically requeue the job** | 请求不要自动 requeue |
| **Do not export the submission environment** | 防止意外继承桌面/login 提交环境 |
| **Clear inherited Slurm environment** | 在准备计算软件运行环境前清除继承的 Slurm 变量 |

这些是服务器/任务执行设置，不是科学参数。

### 10.5 LSF 资源与客户端目录

手动配置 LSF 时还需要：

- **LSF configuration directory**（`LSF_ENVDIR`，其中含有 `lsf.conf`）；
- **LSF library directory**（`LSF_LIBDIR`）；
- **LSF server directory**（`LSF_SERVERDIR`）；
- 作为 `LSF_BINDIR` 使用的调度器命令目录。

只有当 `bsub`、`bjobs`、`bhist`、`bkill` 和 `lsid` 均可用，并且精确环境能够
通过只读 `lsid` 初始化时，配置才会被接受。

LSF admission 字段均为可选：

- **Queue**：留空使用站点默认值。
- **Project**：留空使用站点默认值。

LSF 资源策略只有两个受支持选项：

| Policy | 结果 |
| --- | --- |
| **Scheduler/site default** | 不生成结构化 `#BSUB -R`；放置和 reservation 使用站点默认值 |
| **Structured span + rusage** | 生成结构化的主机、进程和内存要求 |

Moltage 不接受原始 `#BSUB`、原始 `-R` 或任意调度器指令。

### 10.6 输出文件名

FHI-aims 页面包含 **Output file name**。它必须是相对于固定任务工作目录的一个
安全文件名，不能是绝对路径。

- Slurm 将其映射到 `--output`。
- LSF 在执行开始时将 job shell 重定向到该文件，并把自己的报告写入
  `<output file name>.lsf.log`。
- 如果留空，只有经过确认才能保存有意不完整的服务器配置；使用该不完整配置的
  FHI-aims 提交会被阻止。

### 10.7 邮件通知

**入口：** `Settings > Email Notifications...`

选择服务器配置，启用 **Enable completion email**，并输入一个接收地址。
对话框会显示调度器原生邮件的发送路径。

对 Slurm，受支持的生成脚本固定使用 `END,FAIL`。邮件只报告调度器终态，不代表
Moltage 科学验证成功。邮件由调度器在提交后发送，无需保持 Moltage 运行。
离线验证不能证明某个站点一定会发送邮件。

## 11. FHI-aims 与 AITRANSS 设置

### 11.1 程序页面的划分

FHI-aims 是顶级外部程序页面。AITRANSS 是其中的子标签，因为它用于 FHI-aims
输运工作流。ORCA 是独立顶级页面。配置一个程序不会让其他程序变成必填项。

### 11.2 FHI-aims 页面

<p align="center">
  <img src="../images/manual/cluster-settings-fhi-aims-synthetic.png" alt="使用示例路径的 FHI-aims 运行环境设置" width="100%">
</p>

**图 5.** 使用示例路径的 FHI-aims 运行环境与元素定义根目录配置。

| 控件 | 用途 |
| --- | --- |
| **Clear previously loaded environment modules** | 在按顺序加载已配置 modules 前执行 module purge |
| **Environment modules** | 所选 FHI-aims 运行环境需要按顺序加载的 module 列表 |
| **Add module** | 追加一个已验证 module 名称 |
| **Edit** | 修改选中的 module 名称 |
| **Remove** | 删除选中的 module |
| **FHI-aims executable** | FHI-aims 可执行文件的远程绝对路径 |
| **Species definitions root** | 直接含有 `light`、`tight` 和 `really_tight` 子目录的元素定义根目录 |
| **FHI-aims status** | 发现/配置与验证摘要 |
| **Discover Runtime...** | 在限定范围内查找并验证运行环境 |
| **Manual Configuration...** | 输入安装位置或可执行文件、环境准备方式、启动器和元素定义根目录 |
| **FHI-aims launch command** | 最终环境准备和启动命令的只读摘要 |
| **Output file name** | 上述应用程序输出文件名 |

### 11.3 元素定义根目录

Moltage 不附带 FHI-aims `species_defaults`。每次新生成 Step 1、Step 2、Step 3、
density 或独立导出的 `control.in` 时，只读取所选服务器已配置根目录中实际需要的
`<NN>_<Element>_default` 文件。

配置的根目录应当直接包含 `tight` 等精度子目录，而不是某一个元素目录。
找到可执行文件并不能证明存在唯一匹配的元素定义根目录。零个或多个候选时，
都需要用户选择。

已经包含元素定义区块的 `control.in` 文件本身是完整的，不需要再次读取服务器上的元素定义文件。

### 11.4 手动配置运行环境

该对话框包含独立的 FHI-aims 和 AITRANSS 页面。对每个程序先填写已知信息，
再使用 **Find Missing** 进行有界补全。

环境准备方式包括：

| 模式 | 含义 |
| --- | --- |
| **Auto** | 尚未解析的搜索请求，不是可运行环境 |
| **No setup** | 使用正常登录环境，不运行 module 命令 |
| **Modules** | 加载输入的有序 module 名称 |
| **Setup script** | 使用用户明确选择且可信的环境脚本 |

只有 **Modules** 会显示 module 名称；只有 **Setup script** 会显示其路径。

其他 FHI-aims 字段包括安装目录或可执行文件、兼容的绝对 `srun`
或 `mpirun` 启动器，以及元素定义根目录。部分候选会被报告，但不会被静默保存为
可运行配置。

按钮：

- **Find Missing**：在限定范围内搜索，并将已填写值视为约束；执行用户选择的环境
  脚本前需要明确同意。
- **Apply**：将完整结果复制到外层 Cluster Settings 工作副本。
- **Cancel**：丢弃手动配置对话框中的修改。

仍需按外层 **Save** 才会保存到服务器配置。

### 11.5 自动查找的边界

查找流程会检查已配置或当前环境、限定范围内的 PATH 信息、相邻安装目录、受支持的
module 信息，以及可用时已有的文件名索引。它不会：

- 执行科学计算；
- 递归遍历用户主目录或整个文件系统；
- 自动执行查找到的环境脚本；
- 证明 MPI ABI 兼容性、计算节点可用性或科学收敛。

如果结果不完整，程序会指出仍未解析的手动字段。

### 11.6 AITRANSS 子标签

<p align="center">
  <img src="../images/manual/cluster-settings-aitranss-synthetic.png" alt="使用示例路径的 AITRANSS 运行环境设置" width="100%">
</p>

**图 6.** 使用示例路径配置的 AITRANSS 运行环境。该页面与 FHI-aims
可执行文件字段相互独立。

| 控件 | 用途 |
| --- | --- |
| **AITRANSS executable** | AITRANSS 可执行文件的远程绝对路径 |
| **AITRANSS status** | 已保存/已发现/已验证状态 |
| **Discover Runtime...** | 在限定范围内查找并验证 AITRANSS |
| **Manual Configuration...** | 输入 AITRANSS 位置和环境准备方式 |

对于 Slurm Step 4，在运行环境配置中选择一种启动方式：

- **Direct**：直接调用已配置的可执行文件。
- **srun**：使用已配置且经过远程验证的 `srun` 绝对路径调用，文件名
  必须是 `srun`。

Moltage 绝不会退回使用 `PATH` 中的裸 `srun`。LSF 不显示这些 Slurm-only
AITRANSS launch 控件。

## 12. ORCA 环境设置

<p align="center">
  <img src="../images/manual/cluster-settings-orca-synthetic.png" alt="使用示例路径的 ORCA 运行环境设置" width="100%">
</p>

**图 7.** 使用示例路径的 ORCA 运行环境配置。ORCA 与 FHI-aims/AITRANSS
字段相互独立。

### 12.1 最低配置要求

ORCA 优化需要：

- 一个以 `orca` 结尾的绝对 POSIX 路径；
- 一种明确的环境准备方式：No setup、Modules 或可信 Setup script；
- Moltage 支持范围内的版本信息；
- 所选服务器的调度器设置。

不需要另外配置 MPI 启动器、基组路径、频率计算可执行文件或 WBL 工具路径。

### 12.2 字段和按钮

| 控件 | 用途 |
| --- | --- |
| **ORCA executable** | ORCA 主程序的远程绝对路径 |
| **Environment** | No setup、Modules、Setup script 或尚未解析的 Auto |
| **Modules** | 选择 Modules 时按顺序加载的 ORCA module |
| **Setup script** | 选择 Setup script 时明确指定的可信脚本 |
| **Version evidence** | 解析后的 ORCA 版本输出/证据 |
| **Status** | 配置的 supported/unsupported/unverified 状态 |
| **Discover ORCA** | 有界自动发现，仅 Slurm 可用 |
| **Validate Manual Path** | 验证明确输入的路径和环境；Slurm 和 LSF 均可用 |

发现流程不会自动选择最新 ORCA。存在多个完整候选时，需要用户选择。v0.2.1
已审查的结构化支持范围为 ORCA 5.0.x、6.0.x 和 6.1.x。

### 12.3 WBL conversion utility

ORCA WBL 需要与完成优化时记录的 ORCA 可执行文件位于同一目录的 `orca_2json`。
只有用户明确启动 WBL 时，Moltage 才进行检查；没有独立服务器配置字段，也不从裸
`PATH` 回退查找。相邻的 `orca_2mkl` 可选，可以额外生成用于来源记录的 Molden 文件，
但不能替代 `orca_2json` 提供的 overlap/MO 证据。

### 12.4 保存与验证

查找或验证运行环境只会修改对话框中的工作副本，直到按下 **Save**。
**Cancel** 会丢弃修改。站点改变后，已保存路径仍可能过期；相应工作流会在进行
远程修改前重新验证前置条件。

[下一章：Project Manager](04_project_manager.md)
