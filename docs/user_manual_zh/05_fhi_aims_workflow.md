# 14. FHI-aims/AITRANSS 工作流与分子轨道

[返回手册目录](README.md)

## 14.1 工作流概览

由 Moltage 管理的 FHI-aims/AITRANSS 工作流包含四个阶段：

```text
Step 1 — Molecule Optimization
    ↓ 恢复并验证优化后的分子
Step 2 — Molecule–Au Optimization
    ↓ 恢复结构，然后添加标准 Au 金字塔
Step 3 — Transport Convergence
    ↓ 验证固定几何和 AITRANSS 前置条件
Step 4 — Transmission
    ↓ 验证 TE.dat 并查看 T(E)
```

每个项目始终绑定一个服务器配置和一个调度器。Moltage 不会在后台轮询服务器；必须显式执行 Refresh 才会核对远程状态。

开始前请配置：

- SSH 服务器配置和远程项目工作区；
- 已验证的 Slurm 或 LSF 客户端配置；
- FHI-aims 可执行文件、环境准备方式、启动器和元素定义根目录；
- AITRANSS（只需在 Step 4 前完成）；
- 经科学审核的计算参数和资源设置。

## 14.2 项目启动对话框

**入口：** `Calculation > FHI-aims > Step 1 — Molecule Optimization...`

当存在符合条件的直接起点时，可以通过相应的 Calculation 命令打开 Step 2 或 Step 3。

启动对话框包含：

| 字段/控件 | 用途 |
| --- | --- |
| **Server** | 选择拥有该项目的已保存配置 |
| **Calculation engine** | 此工作流为 FHI-aims/AITRANSS |
| **Project name** | 安全且可编辑的基本名称；Moltage 会追加日期和冲突后缀 |
| **Detected structure** | 已识别 linker/接触状态的摘要 |
| **Starting stage** | 确认第一个符合条件的真实计算阶段 |
| **Remote project preview** | 计划创建的远程项目目录名称 |
| **Cluster** | 来自配置的调度器/资源摘要 |
| **Email notification** | 当前调度器原生邮件设置 |
| **Cluster Settings...** | 返回尚未完成的调度器/程序配置 |
| **Continue** | 进入科学参数设置；此时尚不提交 |
| **Cancel** | 关闭窗口，不进行远程操作 |

项目目录候选依次为不带后缀的日期名称、`_02`、`_03` 等。只有确认远程目录已经存在时，Moltage 才会尝试下一个后缀。

## 14.3 Step 1 — Molecule Optimization

### 入口与前置条件

**入口：** `Calculation > FHI-aims > Step 1 — Molecule Optimization...`

当前可编辑的 Geometry 工作区必须包含受支持的分子结构。如果服务器执行配置、计算软件运行环境、元素定义根目录或输出文件名缺失，系统会在创建项目目录前阻止提交。

### FHI-aims 优化设置

**FHI-aims Optimization Settings** 对话框把通用、spin、charge、output 和原子覆盖设置分开。

| 设置 | v0.2.1 初始值 | 用途 |
| --- | --- | --- |
| **Functional** | PBE | 选择受支持的 XC token |
| **vdW** | TS-Hirshfeld | 选择 none、TS-Hirshfeld 或 TS-libMBD |
| **Relativity** | atomic ZORA scalar | 选择标量相对论处理或 none |
| **Species accuracy** | tight | 从配置的服务器根目录选择 `light`、`tight` 或 `really_tight` 定义 |
| **Force threshold** | `1.e-2 eV/Å` | 正的 BFGS 几何收敛阈值 |
| **Dipole** | On | 请求输出偶极矩 |
| **Total charge** | `0` | 系统总电荷，与每原子初始电荷猜测相互独立 |
| **Spin polarization** | Off | 启用受支持的共线 spin 初始化 |

受支持的 XC 选项是 PBE、PBE0、BLYP、B3LYP、revPBE 和 AM05。PBE0/B3LYP 杂化泛函优化会使用经审核的 `RI_method LVL_fast` 指令。某个选项在 Moltage 中可用，并不代表科学上的推荐。

### Spin 控件

启用 spin polarization 后，请选择一种初始化方式：

- **Per atom**：添加原子覆盖设置，并为至少一个原子提供显式非零初始磁矩。
- **Uniform default**：为所有原子提供相同的非零初始磁矩。

可选的 fixed spin moment 是单独的总自旋约束。不要混淆系统总电荷、每原子初始电荷、初始 spin density 和固定总自旋。

### 轨道 Cube 输出

HOMO−2、HOMO−1、HOMO、LUMO、LUMO+1 和 LUMO+2 六个前沿轨道默认全部选中。还可添加正的、从 1 开始编号的绝对态。Cube 网格间距可选：

- 留空：使用 FHI-aims 默认网格；
- 输入明确的 Å 数值：Moltage 会根据分子边界生成经审核的传统 `cube origin` 和 `cube edge` 记录。

### 原子覆盖设置

使用 **Add atom override**、**Edit** 和 **Remove** 配置某个原子的：

- species accuracy 覆盖值；
- initial moment；
- initial charge guess。

覆盖设置会随原子身份/顺序写入输入文件。它们属于计算设置，不会改变显示的化学元素。

### 最终提交确认

最终对话框会显示服务器、拟创建项目、阶段、资源、邮件策略和科学参数摘要。如果配置中没有可用的已保存凭据，才需要输入密码。**Submit** 是授权修改远程状态的确认点；**Cancel** 不会创建任何内容。

随后 Moltage 会：

1. 在提交前检查调度器和计算软件运行环境；
2. 只读取所需的服务器端 species definitions；
3. 在内存中生成完整的 `geometry.in`、`control.in` 和调度器脚本；
4. 分配一个新项目目录；
5. 通过临时文件上传并验证 SHA256；
6. 向调度器提交一次；
7. 保存返回的 Job ID 和 `QUEUED` 状态。

如果提交后连接中断且结果无法确认，状态会成为 `UNKNOWN`，系统绝不会自动再次提交。

### Step 1 成功与恢复

调度器完成本身并不足以判定成功。必须同时存在经审核的 FHI-aims 正常终止证据，以及原子数量和按顺序解析的元素均与提交分子一致的有效 `geometry.in.next_step`。

使用 Project Manager 的 **Refresh Status** 或 **Open / Recover**。恢复成功后，优化几何会在新的 Geometry 工作区中打开。

## 14.4 分子轨道可视化

如果 Step 1 请求了正则分子轨道 Cube 文件，且相应远程文件存在且非空，恢复后的工作区右上角会出现轨道按钮。

<p align="center">
  <img src="../images/readme/molecular-orbital-view.png" alt="恢复后的 HOMO 等值面" width="100%">
</p>

**图 9.** 示例分子的 HOMO 叠加显示。该表面仅用于展示。

### 加载轨道

1. 单击 HOMO−2、HOMO−1、HOMO、LUMO、LUMO+1、LUMO+2 或一个可用的绝对态。
2. Moltage 会短暂重新连接，只下载记录在当前项目中的对应 Cube 文件。
3. Cube 原子头信息必须与已经验证的 Step 1 恢复几何一致。
4. 带符号的标量场会叠加到分子骨架上。

添加已接受的接触 Au（包括记录中的末端 H 删除）后，绑定仍可保持有效。其他不相关的原子或坐标修改会使其失效。

### 等值面设置

**入口：** 当 Cube/轨道表面处于活动状态时，打开 `Settings > View... > Isosurface`。

| 设置 | 初始值 | 效果 |
| --- | --- | --- |
| Display style | Opaque | 不透明或固定 50% 半透明表面 |
| Display resolution | 取决于工作区 | 在支持时可选 Full/Medium/Low；仅影响显示 |
| Isovalue | 新的独立表面为 `0.02` | 改变提取的正/负等值面；零会隐藏表面 |
| Positive lobe | Red | 仅改变显示颜色 |
| Negative lobe | Blue | 仅改变显示颜色 |
| Ambient light | `0.90` | 环境光与方向光的平衡 |
| Light intensity | `0.82` | 轨道叶瓣的有效亮度 |
| Specular | `0.30` | 白色高光强度 |
| Shininess | `32` | 镜面反射指数 |

**Apply Recommended Material** 会预览 ambient `0.55`、intensity `0.95`、specular `0.30` 和 shininess `32`，不会改变 isovalue、颜色或透明度。**OK** 会按用户持久化四项光照/材质值；**Cancel** 恢复打开对话框时的值。

轨道叠加显示不会改变计算输入、Au 放置坐标或后续阶段的可用性。

## 14.5 Step 2 — Molecule–Au Optimization

### 两种入口

1. **正常继续：** 恢复成功的 Step 1 结构，应用两个已接受的接触 Au 提议，然后使用 `Calculation > FHI-aims > Step 2 — Molecule–Au Optimization...`。
2. **直接 Step 2：** 打开一个符合条件的本地结构。该结构必须恰有两个已识别的 linker 末端，每个末端连接一个接触 Au，且没有其他 Au；然后确认 Step 1 将被标记为 `SKIPPED`。

通过查看器添加的接触 Au 是有效的 Step 2 输入，并且在优化中不会被固定。

### 设置与提交

Step 2 会收集一套新的 FHI-aims 优化设置，不会静默继承 Step 1 的科学参数。正常继续会复用现有项目 UUID 和根目录，只创建 `molecule_Au`；直接 Step 2 会创建新项目，并把 Step 1 标记为 `SKIPPED`。

直接起点不会伪造 Step 1 结果。上传、哈希验证、提交前调度器检查、至多一次提交和科学结果恢复均遵循与 Step 1 相同的边界。

### 恢复之后

Step 2 恢复成功后，会加载优化后的分子–接触 Au 结构。使用 Electrode Builder 添加两侧标准金字塔。只有当前工作区包含已经确认的双侧电极结果时，Step 3 才可用。

## 14.6 Step 3 — Transport Convergence

### 入口与前置条件

**入口：** `Calculation > FHI-aims > Step 3 — Transport Convergence...`

正常情况下，必须已成功完成 Step 2、恢复经过验证的优化结构，并添加两侧金字塔。直接导入的 Step 3 起点还要求源结构中已有优化得到的接触 Au，并由用户明确确认分子–Au 几何已经适当优化。通过查看器添加的接触 Au 不能跳过 Step 2。

### Step 3 设置

Step 3 使用固定几何，因此没有 relaxation、force threshold、dispersion 或 optimization output。

| 字段 | 初始值 | 用途 |
| --- | --- | --- |
| **XC** | PBE | 受支持的 XC 选择 |
| **Basis preset** | tight | 服务器端 species accuracy |
| **Spin** | None | 可选的受支持共线均匀 spin 初始化 |
| **Initial moment / atom** | Spin 为 None 时禁用 | 启用 Step 3 spin 模式时需要非零值 |
| **Total charge** | `0` | 系统总电荷 |
| **Gaussian width** | `0.01` | 占据展宽 |
| **n_max_pulay** | `10` | Pulay 历史长度 |
| **charge_mix_param** | `0.2` | 电荷混合参数 |
| **sc_accuracy_rho** | `1E-5` | 密度收敛阈值 |
| **sc_accuracy_eev** | `1E-3` | 本征值总和阈值 |
| **sc_accuracy_etot** | `1E-6` | 总能量阈值 |
| **sc_iter_limit** | `500` | 最大 SCF 迭代数 |
| **Orbital state numbers** | 留空 | 仅可输入可选的正绝对态编号 |
| **Cube grid spacing** | 留空 | 除非明确设置，否则使用 FHI-aims 默认值 |

固定生成的指令为 `output aitranss`、`KS_method serial` 和 `restart aims.restart`。

### 提交与结果

确认窗口会显示当前 Step 2/电极状态、服务器、调度器资源、邮件设置和科学参数摘要。提交时会创建或复用固定的 `molecule_Au/transport` 目录，上传三个准备好的 Step 3 输入文件，提交一次，然后立即显示 Job ID 和路径而不等待输出。

科学成功要求：

- 调度器终态为成功；
- 存在 FHI-aims 正常终止证据；
- 提交几何的哈希匹配，且输出中的原子数为正并与提交结构一致；
- AITRANSS 矩阵/轨道前置文件非空且彼此一致；
- NSAOS 证据为正且一致。

输出暂时缺失时，状态可保持为 `SCHEDULER_COMPLETED`，等待之后的显式刷新。

### 超时/OOM 重试

经审核的超时或内存不足失败可在 Project Manager 中显示 **Resubmit Step 3...**。只能编辑资源。几何、`control.in`、restart 文件和以前的提交记录都会保留；系统不会自动重试。

## 14.7 Step 4 — AITRANSS Transmission

### 入口与前置条件

**入口：** 打开/恢复成功的 Step 3 Geometry 工作区，然后选择 `Calculation > FHI-aims > Step 4 — Transmission...`。

Moltage 会验证 Step 3 几何和前置条件，从已保存的电极元数据中读取六个表面参考原子，并检查 AITRANSS 运行环境。运行环境过期或缺失时仍可查看编辑器，但不能提交。

### 系统与表面字段

| 字段 | 含义 |
| --- | --- |
| `$natoms` | 从已验证 Step 3 证据得出的原子数 |
| `$nsaos` | 从矩阵/轨道文件头得出的 AO 数 |
| `$lsurc`, `$lsurx`, `$lsury` | 三个互不相同的 Left 参考平面 Au 原子编号 |
| `$rsurc`, `$rsurx`, `$rsury` | 三个互不相同的 Right 参考平面 Au 原子编号 |
| `$nlayers` | 独立的 AITRANSS 电极层参数 |
| **Source** | `AIMS_RECOMMENDED`、`USER_SPECIFIED` 或缺失证据说明 |

v0.2.1 中 `$nlayers` 的初始证据：

| 金字塔层数 | 初始 `$nlayers` | 分类 |
| --- | --- | --- |
| 4 | 2 | `AIMS_RECOMMENDED` |
| 5 | 3 | `AIMS_RECOMMENDED` |
| 6 | 4 | `USER_SPECIFIED` |
| 2、3、7–10 | 未配置 | 用户必须输入正值 |

编辑任何初始值后，来源会标记为用户指定。Moltage 不会推导公式，晶格扩展也永远不会改变 `$nlayers`。

### 输运与输出字段

| 字段 | 用途 |
| --- | --- |
| `$s1i`, `$s2i`, `$s3i` | AITRANSS 使用的经审核输运控制参数 |
| `$ener` | 起始能量 |
| `$estep` | 能量步长 |
| `$eend` | 请求的能量上限 |
| `$output file` | 活动 `tcontrol` 引用的安全结果文件名 |
| `$testing` | 显式开/关的 AITRANSS 选项 |
| `$ecp` | 显式开/关的 AITRANSS 选项 |

对话框会实时显示 **Generated tcontrol preview** 并报告验证错误。不要把可编辑值当作推荐值；请依据相应 AITRANSS 文档和你已验证的工作流设置参数。

### Step 4 资源

- Slurm：AITRANSS CPU 线程数、最长运行时间、每节点内存和当前邮件策略。
- LSF：作业槽位/AITRANSS 线程数、最长运行时间，以及结构化内存预留或明确使用站点默认值。

Step 4 运行一个 AITRANSS 程序实例。Slurm 使用配置的 Direct 或已验证绝对路径 srun 策略；Moltage 绝不会生成裸 `srun`。

### 提交与成功判定

单击 **Submit** 后，Moltage 会重新验证前置条件，只上传 `tcontrol` 和 Step 4 调度器脚本。不会覆盖已有且冲突的活动文件。调度器提交至多发生一次。

只有当前尝试的调度器状态、经审核的程序成功标记、可解析的活动 `tcontrol` 和完整、有限、严格递增的结果网格彼此一致时，Step 4 才算成功。即使调度器报告成功，已知的 interface-overlap 或 self-energy 格式错误仍会保持为具有具体类型的失败。

### 显式 self-energy 重试

只有经审核的电极界面重叠失败会提供 **Retry with explicit self-energy...**。Moltage 会预览界面计数和当前层数/输运控制参数，然后保留旧输入，并使用新的尝试专用 self-energy、`tcontrol`、脚本和输出文件名。该重试绝不会自动进行，也不会改变 Step 3 几何或矩阵。

## 14.8 查看 AITRANSS transmission

Step 4 通过科学成功判定后，在 Project Manager 中使用 **View Transmission**。Moltage 会从可解析的活动 `tcontrol` 中确定结果文件，不会通过通配符猜测文件，也不会选择最新文件。

结果查看器及其显示控件见[分析与结果](07_analysis_and_results.md)。

[下一章：ORCA 工作流](06_orca_workflow.md)
