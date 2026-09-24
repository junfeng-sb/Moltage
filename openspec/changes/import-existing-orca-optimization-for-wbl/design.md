## Context

现有 ORCA workflow 的每一段都绑定在 managed project root 下四个固定文件名和 manifest 中的 hash 上：

| 消费者 | 读取 |
| --- | --- |
| `app/project_recovery.py::_recover_orca_optimization` | `orca_opt.inp`（hash-bound）、`orca_opt.out`、`orca_opt.xyz`、`orca_opt.gbw` |
| `app/project_recovery.py::_orca_context_snapshot` | `orca_opt.inp`（hash-bound） |
| `app/project_geometry.py::_load_orca_geometry` | `orca_opt.inp`（hash-bound）、`orca_opt.xyz` |
| `app/orca_wbl.py::OrcaWblService.calculate` | `orca_opt.gbw`（hash-bound）、`orca_opt.inp`、`orca_opt.xyz`、`orca_optimization_settings.{charge,multiplicity,basis}`、`orca_submitted_elements` |

由此得到本设计的核心选择：**imported project 在 managed workspace 中呈现为一个结构上普通的 ORCA project**。只要这四个文件存在且 hash 与 manifest 一致，recovery、geometry view 和 WBL 三条既有路径**完全不需要修改**。这是复用既有 pipeline 而非复制它的最小方式。

唯一必须改动既有行为的地方是 origin 的真实性：Moltage 从未为 imported project 观察过任何 scheduler 结果，因此不能记录 `scheduler_succeeded = True`。

## Goals / Non-Goals

Goals：
- 让一次已成功的外部 ORCA 优化成为可进入现有 WBL pipeline 的 managed project。
- 只用既有 reviewed parser 与既有 conversion utility 判定成功；不新增 ORCA 输出语法。
- 源目录严格只读。
- 失败时不留下 managed project、不留下空远端目录、不伪造成功状态。

Non-Goals：
- 不新增 Molden 读取路径（见 proposal 的 scope deviation）。
- 不改变 WBL 科学模型、Γ₀ 语义、spin 处理或结果呈现。
- 不新增线程框架、remote subsystem、scheduler 或 workspace 配置。

## Decisions

### D1 — 源结果组按 exact stem 配对，内容交叉校验只使用 reviewed parser

一个候选结果组是目录中同时存在且非空的 `<stem>.inp`、`<stem>.out`、`<stem>.xyz`、`<stem>.gbw` 四个普通文件。

理由：ORCA 以输入文件 stem 命名 `.gbw` 与 `.xyz`；`orca x.inp > x.out` 是标准调用方式。仓库中不存在任何经审阅的 ORCA 输出 metadata 语法（`orca/evidence.py` 只认四个 marker），因此**不得**从 `.out` 内容推断配对关系——那会构成发明 ORCA 输出语法。

在 stem 配对之上，再用既有 parser 做一次内容交叉校验：`parse_orca_final_xyz(<stem>.xyz, parse_rendered_orca_structure(<managed input>))` 必须成功，其中 managed input 对内联坐标的 `.inp` 即源 `.inp`，对 `*xyzfile` 输入即 D6 所述的内联结果。它同时证明原子数与有序元素身份一致，也就证明 `.inp` 与 `.xyz` 属于同一次计算。若不一致，导入被拒绝并给出具体原因。

歧义处理：0 组 → 明确失败并列出最接近的 stem 及缺失扩展名；1 组 → 自动选择；≥2 组 → 在对话框中列出候选，必须由用户显式选择，不做任何猜测。

### D2 — charge / multiplicity 从源 `.inp` 的 `* xyz` 头读回

`orca/input_writer.py::_render` 写出的正是 `* xyz {charge} {multiplicity}`，而 `parse_rendered_orca_structure` 已经以该 grammar 读回坐标块。新增的 `parse_rendered_orca_scientific_identity` 只是从同一行再取回它丢弃的两个整数，并从 `! ...` 行按既有 catalog 严格识别 token。这是读回 Moltage 自己的既有格式，不是解析 ORCA 输出。对 `*xyzfile` 输入，两者读取的是 D6 的内联结果，其 `* xyz` 头由 `render_orca_xyz_block` 以同一 grammar 写出。

`!` 行规则：恰好命中一个 `OrcaMethod`、至多一个 `OrcaBasis`、至多一个 `OrcaDispersion` 时记录三者；任何歧义（多个 method、多个 basis、多个 dispersion）→ 三者全部留空。留空时导入仍然成功，`OrcaWblSettings` 的自动 contact projection 不可用，用户使用既有 manual AO 模式。这是既有行为：`orca/wbl.py` 在 `basis is None` 时已经明确要求 manual AO mode 而不做回退。

不从 `.out` 解析 charge、multiplicity、几何或版本——那需要未经审阅的 ORCA 输出语法。ORCA 版本证据沿用既有来源：server profile 的 `OrcaRuntimeConfiguration.version_evidence`（由 `remote/orca_runtime.py` 的版本探测产生），随 step 的 `orca_runtime` 一并持久化。

### D3 — origin 显式建模，`succeeded` 按 origin 解释 scheduler 维度

```python
class OrcaOptimizationOrigin(StrEnum):
    MOLTAGE_SUBMITTED = "MOLTAGE_SUBMITTED"
    IMPORTED_EXTERNAL = "IMPORTED_EXTERNAL"
```

`OrcaOptimizationResultEvidence` 增加 `origin`（默认 `MOLTAGE_SUBMITTED`，保持既有构造调用不变）。`succeeded` 改为：

- `MOLTAGE_SUBMITTED`：`scheduler_succeeded ∧ normal_termination ∧ optimization_converged ∧ final_xyz_valid`（与今天完全相同）。
- `IMPORTED_EXTERNAL`：`normal_termination ∧ optimization_converged ∧ final_xyz_valid`，且 `scheduler_succeeded` 必须为 `False`。

imported evidence 因此如实记录“没有 Moltage 观察到的 scheduler 结果”，而不是谎称有一次成功的调度。对应地，imported step 的 `job_id`、`scheduler_kind`、`scheduler_state`、`submit_script_filename`、`slurm_output_filename` 全为 `None`——没有伪造的 Slurm/LSF job ID。

`app/project_recovery.py::_recover_orca_optimization` 今天无条件写 `scheduler_succeeded=True` 与 `scheduler_state="COMPLETED"`。改为：从既有 `step.orca_optimization_result.origin` 继承 origin；imported 时 `scheduler_succeeded=False` 且不写 `scheduler_state`。这保证 Refresh 与应用重启后状态不被降级。

`app/orca_recovery.py::refresh_project` 在 `job_id is None` 时的早退消息对 imported project 改为说明这是导入的优化，而不是“ORCA stage has not been submitted”。

### D4 — schema 11 与 import provenance

`PROJECT_SCHEMA_VERSION = 11`，`LEGACY_PROJECT_SCHEMA_VERSIONS = (1..10)`。

`ProjectStepRecord` 增加 `orca_import_provenance: OrcaImportProvenance | None`，仅在 `ORCA_OPTIMIZATION` 上合法（与既有 ORCA 字段的 per-kind 校验同构）。

```python
@dataclass(frozen=True, slots=True)
class OrcaImportProvenance:
    source_directory: str                 # 绝对 POSIX，源目录
    source_stem: str
    source_input_filename: str
    source_output_filename: str
    source_geometry_filename: str
    source_wavefunction_filename: str
    source_input_sha256: str
    source_output_sha256: str
    source_geometry_sha256: str
    source_wavefunction_sha256: str
    imported_at: datetime
    wavefunction_readiness: OrcaImportWavefunctionReadiness  # READY | CONFIGURATION_REQUIRED
    orca_2json_path: str | None = None
    readiness_diagnostic: str | None = None
    source_coordinate_path: str | None = None    # 仅 *xyzfile 输入；绝对 POSIX
    source_coordinate_sha256: str | None = None  # 被内联的坐标字节的 SHA256
```

两个 coordinate 字段必须同时存在或同时缺省。它们加入时 schema 11/12 尚未发布，manifest 读取时缺省键解释为 `None`——这对此前写入的所有导入如实成立，因为那些导入只接受内联坐标的源 `.inp`。

manifest 解析对 `schema_version >= 11` 读取这两处新证据，否则 `origin = MOLTAGE_SUBMITTED`、`orca_import_provenance = None`。不为旧 project 伪造 imported metadata。

provenance 的四类明确区分：

| 类别 | 记录位置 |
| --- | --- |
| external source artifacts | `OrcaImportProvenance.source_*`（路径 + SHA256） |
| Moltage copied/managed inputs | `ProjectStepRecord.input_hashes`（既有机制，四个 `orca_opt.*`） |
| 后续生成的 Molden 文件 | 既有 `wbl/orca_wavefunction.molden.input` + `orca_wbl_result.artifact_hashes` |
| 用户输入的 WBL 参数 | 既有 `orca_wbl_settings` |
| WBL 输出 | 既有 `orca_wbl_result` + `wbl/` artifacts |

server profile identity、managed workspace identity 沿用既有 `project.server_profile_id` / `project.project_id` / `project.remote_project_path`，不重复存第二份。

### D5 — managed workspace 创建顺序与 fail-closed 清理

```
validate（只读，短连接）
  ↓ 用户点 Import（新的短连接）
1. 重算四个源文件（及 *xyzfile 坐标文件）SHA256；与 validate 阶段不一致 → 中止，未创建任何目录
2. allocate_remote_project_directory(root, base_name, today)   # mkdir 抢占，collision 自动退到 _02/_03
3. server 端 cp 四个源文件 → <root>/orca_opt.{inp,out,xyz,gbw}
   （*xyzfile 输入：orca_opt.inp 改由 upload_new_files_atomically 写入内联结果，其余三个照常 cp）
4. 逐个 sha256sum 校验 dest 摘要 == 源摘要
5. mkdir <root>/.moltage
6. repository.write_initial(project)      # manifest 最后写
7. local_index.mark_seen(project, bound_server_profile_id=profile.profile_id)
```

manifest 最后写，因此任何中途失败都不会留下一个可被 discovery 识别的半成品 managed project。此外在失败路径上执行有界清理：对**恰好这四个绝对路径**执行 `rm -f --`（无通配符、无递归），再对 `<root>/.moltage` 与 `<root>` 执行 `rmdir --`（仅在为空时成功）。清理结果不确定时如实报告并给出路径，绝不宣称成功。

destination 已存在的情况由 `allocate_remote_project_directory` 的 mkdir 语义天然处理：它只会占用一个尚不存在的名字，永不覆盖。

所有 remote 路径通过 `shlex.quote` 处理，正确承载空格；不拼接未转义的 shell 命令，不执行用户输入路径中的任何内容——路径始终作为参数而非命令。

命令构造归属 `remote/step_inputs.py`（已拥有 "exact initial upload / new-file-only verified upload / verified atomic replacement" 的 step-input 边界），新增 `remote_file_sha256`、`copy_remote_files_with_verified_digests`、`discard_imported_step_inputs` 三个窄函数。application 层不自行拼命令。

### D6 — 为什么导入需要源 `.inp`

若 managed workspace 缺少可解析且 hash 一致的 `orca_opt.inp`：

- `_recover_orca_optimization` 得到 `submitted = None` → `final_xyz_valid = False` → state 降为 `SCHEDULER_COMPLETED`，**第一盏灯在首次 Refresh 后由绿变黄**，Step 2 入口消失；
- `_load_orca_geometry` 无法打开 INPUT view；
- `OrcaWblService` 无法取得 `submitted` 以校验 `orca_opt.xyz`。

替代方案是在这三处各加一个 imported-origin 分支。本设计选择要求源 `.inp`，因为它把改动面压缩到零。

**修订（用户确认，2026-09-22）**：原设计以“复制进来的是真实的提交输入”为由拒绝 `*xyzfile` 输入，但真实外部优化常见 `*xyzfile 0 5 /abs/path/start.xyz`，拒绝使这些已成功的优化无法导入。现在：

- 源 `.inp` 中恰好一个 `*xyzfile <charge> <multiplicity> <path>` 头（`*` 与关键字之间允许空白）时，路径为绝对则原样使用，相对则按源目录解析（与 D1 已假定的 `orca <stem>.inp > <stem>.out` 调用目录一致）。含 `..`、多于一个 `*xyzfile`、与内联 `* xyz` 块并存、头字段数不为三、或路径恰为 `<stem>.xyz`（此时它已是最终几何，起始结构不可恢复）都明确拒绝。
- 坐标文件以既有 strict reader `structure/xyz.py::parse_xyz` 读取；只把 `*xyzfile` 这一行替换为 `render_orca_xyz_block` 写出的 `* xyz` 块，其余字节与行尾保持不变，得到 managed `orca_opt.inp`。
- recovery、geometry view 与 WBL 因此仍然零改动。代价是此类 project 的 managed `orca_opt.inp` 并非逐字节的源 `.inp`：provenance 同时保存源 `.inp` 的 SHA256 和坐标文件的路径与 SHA256，managed 输入的 SHA256 由既有 `input_hashes` 记录，三者区分如实。对话框明确显示坐标来源并说明已内联。

`* xyz` 之外的其他坐标来源（如 `%coords`、内坐标）仍被拒绝并给出明确原因。

### D7 — WBL readiness 是独立维度

`orca_2json` 的能力校验从 `app/orca_wbl.py` 中提升为公开函数 `verify_orca_wbl_utilities(executor, runtime)`，由 WBL service 与 import service **共用同一实现**（不复制）。

- profile 已配置 `orca_runtime` 且 sibling `orca_2json` 通过校验 → `READY`。
- 未配置 runtime，或校验失败 → `CONFIGURATION_REQUIRED`，附明确诊断（“在该服务器的 ORCA 设置中配置并验证 conversion utility”）。此时 optimization import 仍然完成，第一盏灯为绿。

readiness 不改变 `wbl_input_ready` 的既有含义（`.gbw` 存在且非空）。一个 `CONFIGURATION_REQUIRED` 的 project 允许用户启动 WBL，并在既有的 utility 校验处以明确诊断失败——这是显式失败而非静默回退。

### D8 — 为什么禁用 imported project 的 `Resubmit Optimization...`

Moltage 从未提交过该 Step 1，且 method/basis 可能为空。允许 Resubmit 会让 `render_orca_optimization_input` 在提交路径上抛错，或在 method 恰好可识别时让 Moltage 以自己重建的设置冒充原始提交。直接禁用并给出原因，与“不声称 Moltage 提交过 Step 1”一致。

### D9 — 异步与 Qt 生命周期

两个 `QRunnable`（validation / import）沿用 `gui/remote_directory_dialog.py::RemoteDirectoryWorker` 的既有模式：`setAutoDelete(False)`、`signals.succeeded/failed/finished`、`finished` 回传 worker 自身由对话框从 worker set 中移除、`finally` 中清空 password。连接使用 `connect_for_remote_operation(..., stop_token=...)`，`RemoteOperationStopToken` 支持用户取消并在后台线程关闭 session，不阻塞 Qt。

对话框在 busy 期间禁用 `Validate` / `Import` / `Browse…` / server 选择并拒绝 `reject()`（与 `RemoteDirectoryDialog` 相同）；worker 完成时先检查对话框是否仍存活再更新 UI。导入 worker 仅允许存在一个，防止双击产生重复 project。

## Risks / Trade-offs

- **要求源 `.inp`**：`*xyzfile` 输入经内联后可导入（D6 修订）；其他坐标来源仍被拒绝。缓解：对话框明确说明原因，不静默降级。
- **`*xyzfile` 内联使 managed `orca_opt.inp` 不再逐字节等于源 `.inp`**。缓解：只替换坐标行，并分别记录源 `.inp`、坐标文件和 managed 输入的 SHA256；坐标文件在 import 前复核，变化即中止。
- **`cp` / `sha256sum` / `rmdir` 依赖** 已有先例（`app/orca_wbl.py` 已依赖 `sha256sum` 与 `bash -lc`）。所有路径经 `shlex.quote`，无通配符、无递归删除。
- **大 `.gbw` 的复制时间**：全部在服务器端完成，不经过本地网络往返；操作在后台线程并可取消。
- **origin 影响 `succeeded` 语义**：改动被限制在 `OrcaOptimizationResultEvidence` 内部，默认值保持既有调用点行为不变，并由针对性测试锁定 `MOLTAGE_SUBMITTED` 路径未发生变化。

## Migration

schema 10 → 11 为纯增量：新字段可选，旧 manifest 读入后 `origin = MOLTAGE_SUBMITTED`、`orca_import_provenance = None`。既有 ORCA / FHI project 的状态、灯、Step 2 可用性与 geometry 视图不变。没有反向迁移需求，因为 schema 版本沿用既有单调升级策略。
