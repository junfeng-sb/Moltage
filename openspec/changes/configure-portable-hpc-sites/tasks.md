## 1. Profile Model and Migration

- [x] 1.1 在现有 domain model 中增加 scheduler selector normalization、`LsfResourceRequirementMode`、Slurm AITRANSS `DIRECT/SRUN` mode，以及批准的 `SlurmExecutionPreset` optional fields；用 unit tests 验证 scheduler-specific field isolation、blank-to-`None` normalization、unsafe token rejection、LSF mode constraints 和 absolute `srun` path validation。
- [x] 1.2 将 `server_profiles.json` 提升到 schema 11 并扩展 serialization/round-trip；用 `tests/unit/test_server_profiles.py` 覆盖 schema 1–10 admission fields 迁移为空、旧 LSF 迁移为 `SPAN_RUSAGE`、可复用的 existing absolute `srun` 和 unresolved legacy Slurm Step 4 configuration，确认 load 不自动 rewrite。

## 2. Minimal Configuration UI and Verification

- [x] 2.1 在现有 Cluster Execution Settings 的 Slurm page 增加 optional Account、Partition、QoS，在 LSF page 增加 optional Queue、Project；用 `tests/unit/test_cluster_execution_dialog.py` 验证 blank 表示 site default、scheduler 切换不串用字段、save/cancel 保持现有其他配置。
- [x] 2.2 在 LSF page 增加 `Scheduler/site default` 与 `Structured span + rusage` selector，并按 mode 禁用或标注不生效的 host/memory controls；同步 Step 4 resource presentation，用 GUI tests 验证用户不会把未生成的 `-R` requirement 误认为已提交。
- [x] 2.3 在现有 AITRANSS manual runtime page 增加 Slurm-only `direct/srun` selector 和 conditional absolute `srun` field；仅复用已验证 FHI `srun` 或 bounded exact `<scheduler-bin>/srun` verification，使用 fake executor tests 证明不做 filesystem scan、不自动选择 mode，且 unresolved `SRUN` 会在 remote mutation/submission 前失败。

## 3. Batch Script Rendering

- [x] 3.1 增加小型 pure renderer helpers，并让 FHI-aims 与 AITRANSS scripts 对非空 Slurm `account/partition/qos` 和 LSF `queue/project` 生成对应 directives；用 `tests/unit/test_slurm.py` 与 `tests/unit/test_lsf_scheduler.py` 验证 configured/blank/cross-scheduler cases，确认无 raw directive passthrough。
- [x] 3.2 让 FHI-aims 与 AITRANSS LSF renderers 根据 `SITE_DEFAULT` omit `#BSUB -R`，根据 `SPAN_RUSAGE` 保持当前 structured expression；验证两种 mode 的 exact synthetic scripts、host/rank constraints 和 memory presentation。
- [x] 3.3 将 Slurm Step 4 AITRANSS renderer 改为 exact `direct` 或 verified absolute `srun` command；用 `tests/unit/test_aitranss_dialog.py` 和现有 AITRANSS/script tests 验证 environment order、one-task invocation、unsafe/missing path failure，并断言 production rendering 不再包含裸 `srun --ntasks=1`。

## 4. Existing Workflow Integration

- [x] 4.1 验证 Step 1/2/3、restart/retry、density 和 Step 4 的现有 orchestration 通过已有 preset/renderers 获取新字段，resource-only retry 不丢失 site configuration，且 failure 不改变 scheduler/program/scientific status；运行相关 `test_project_submission.py`、`test_phase3b_submission.py`、`test_transport_convergence_submission.py` 和 `test_density_workflow.py` targeted cases。
- [x] 4.2 仅更新本 capability 直接涉及的 maintained configuration documentation 和 `config/server.example.yaml`，删除“later account/partition”与生产 schema 不一致的表述，并核对没有引入 server name、real path、credential 或 raw directive example。

## 5. Offline Validation

- [x] 5.1 运行 profile migration、Cluster Execution Settings、runtime verification、Slurm/LSF rendering、AITRANSS 和 submission integration 的 targeted synthetic test set，记录实际 collected/passed/failed 与 command；任何未运行项明确标记为 unverified。
- [x] 5.2 targeted tests 通过后运行 `./tools/run_tests.ps1 -Full`，记录实际结果并确认 `git status --short` 没有非预期项目变化；不连接 SSH/HPC，不声称 external-cluster acceptance。
