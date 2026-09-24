## Context

用户本轮批准覆盖旧 WBL 结果，取代上一 change 为对照计算要求另建项目的限制。`orca_wbl_stage_blocks_new_run` 和 `wbl/` absent preflight 必须一起更新；旧图表 workspace 按 identity 缓存，也必须随新结果失效。Bond Detection 当前有 preview/OK/Cancel 和现成原子图快照，用户偏好已有原子写入的 JSON repository。

## Goals / Non-Goals

允许明确的原项目 WBL 重新计算，保持结果与 manifest 一致；只保存用户确认的成键系数。不修改 WBL 数学、radii、connectivity formula、优化输入、认证、scheduler、项目模型架构或其他偏好。

## Decisions

1. 成功重算必须携带用户确认的旧 WBL evidence，服务重新加载后匹配，防止陈旧界面静默覆盖更新的结果；RUNNING 阶段仍拒绝。
2. 沿用一个 Step 2 record 和 `wbl/`。先生成并校验独立 staging 文件，以服务器端 SHA256 验证既有结果和新上传，不下载旧波函数做 checksum。发布时只把已验证的旧目录临时移开、新目录切入，再保存 manifest；明确失败时恢复旧目录与旧 record。断线或 revision 变化无法确定时保留证据并报错，不猜测。成功后仅清理本次已验证的旧文件集合；清理失败明确提示，不撤销成功计算。
3. 重算界面预填旧 settings，显示替换提示；Cancel 无动作。新成功结果使旧 transmission tab 失效，重新打开显示新曲线及导出数据。旧优化/频率阶段不变。
4. 在现有 `view_preferences.json` 加入 `bond_threshold_factor`，schema 3 接受现有 0.01–10.00 范围内有限数值。schema 1/2 不改动已有 theme/lighting，未保存 factor 使用既有 1.1；新文件缺失/非法数值显式失败。各项保存保持其他设置。
5. 启动、加载结构和 WBL 使用恢复后的系数；只在 Bond Detection 的 OK 后保存，保存失败回滚图和数值并提示，Cancel 不写文件。

独立审查后的边界补齐：在将成功 record 写为 RUNNING 之前，以服务器端 SHA256 验证并保存 `wbl-previous-<token>.project.json`，保留旧参数与结果哈希；已验证的成功/回滚后清理，未知结果保留并报告供人工核查。该文件只是恢复证据，不增加自动恢复、解锁或 attempt manager。已验证的失败暂存文件可清理，无法验证的部分上传保留并明确报告。新的成功 record 不继承旧图表；本地索引保存失败只提示，不把已提交成功的结果降为失败。

## Risks / Trade-offs

多文件远端发布不是跨文件系统数据库事务；使用有限的 staging/备份与 manifest revision 检查，未知结果 fail closed，禁止无证据恢复。无新的持久化 attempt manager。真实网络中断和服务器接受情况不由 synthetic tests 证明。
