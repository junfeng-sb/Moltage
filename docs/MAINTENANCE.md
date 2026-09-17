# Maintenance Notes

## Development validation and OpenSpec

Use `tools/run_tests.ps1 -Tests` with explicit affected test paths for small edits;
on feature completion, include that subsystem and its direct integration tests.
The full offline suite is reserved for schema/persistence, scientific behavior,
high-risk scheduler/security/cross-layer architecture changes, and formal
pre-commit snapshots, not every OpenSpec change. Independent Claude audit applies
only to those high-risk changes and public-release blockers; ordinary GUI,
documentation, and local features do not require a default second audit.

Validate the completed proposal, completed apply, and pre-archive artifacts once
with `openspec validate <change> --strict`. If artifacts are unchanged, reuse the
existing evidence instead of repeating the command. Full privacy, provenance,
secret, AI-attribution, and repository-hierarchy audits run at pre-public, outside
the ordinary feature loop. Development still forbids introducing secrets or
unverified scientific data. Report only checks actually run and explicitly mark
unavailable required evidence as unverified. See `AGENTS.md` for the policy and
`README.md` for the existing offline test commands; no new test framework is used.

## Server-generic runtime module discovery

An earlier runtime action stopped after one nonzero `module -t avail fhi-aims` result and understood only one module-tree header shape. A successful SSH connection to a different cluster could therefore be mislabeled as a missing runtime before any executable was checked. The runtime owner is now server-generic: it first verifies the exact module sequence already entered in the profile, then uses bounded terse `avail` queries for Lmod and Tcl Environment Modules and bounded Lmod `spider` queries for hierarchical layouts. Multiple headers, case differences, and standard terse decorations are accepted only as candidate syntax.

Candidate syntax is never runtime proof. Every saved environment still has to load in isolation and expose an accepted FHI-aims or AITRANSS executable-family member through a canonical absolute, regular, executable path. Ordinary query/load failures only reject that strategy or candidate; SSH transport failures remain typed connection failures. Discovery has a hard candidate limit, does not crawl the remote filesystem, does not execute either scientific program, and changes no scheduler, mail, submission, or scientific behavior.

## Remote command output draining

Phase 2D-M2 drains ready standard-output and standard-error chunks fairly while polling command completion. It obtains the exit status only after Paramiko reports channel EOF/closure and both inbound buffers are empty; transient not-ready states therefore cannot discard late output, while progressive draining still prevents large output from blocking the channel window. Failures before that complete result exists retain the unknown-outcome, never-retry semantics, but explicit channel cleanup is best-effort and cannot replace an already-known result with an unknown outcome. No SSH tuning or command-timeout policy is introduced.

## FHI-aims MPI fail-fast and OOM propagation

Some Slurm installations permit a site default that does not terminate the remaining tasks when one task exits nonzero. Moltage therefore renders `srun --kill-on-bad-exit=1` for FHI-aims jobs so a task failure propagates through the final launcher. Independently, partial task-OOM output can appear while the scheduler parent remains active, so program output never substitutes for terminal scheduler evidence.

Future FHI-aims scripts receive exactly one `--kill-on-bad-exit=1` immediately after `srun`, while preserving CPU binding, resources, modules, executable, scientific inputs, and `END,FAIL` mail. The launch remains the final batch command so its exit status propagates naturally. This applies to Step 1, both Step-2 paths, initial Step 3, and Step-3 retry. It does not alter the frozen Step-4 AITRANSS launcher.

Recovery remains scheduler-authoritative. Active jobs remain active despite OOM text. Terminal `OUT_OF_MEMORY` is presented as `任务因内存不足终止`; after a terminal `FAILED` parent is established, only the paired exact cgroup/srun task-OOM signatures can produce `SLURM_TASK_OUT_OF_MEMORY` with the same user-facing reason. Deterministic synthetic fixtures cover these state distinctions without recording external jobs.

## Explicit active-OOM cancellation and resource retry

REMOTE-R3-R1 adds a display-only `TASK_OOM_DETECTED` observation when an explicit Refresh finds exact parent state `RUNNING` and both reviewed signatures in the bounded authoritative current-attempt output tail. It never persists `FAILED`, runs `scancel`, or submits by itself. `Kill Job` requires exact-ID confirmation and one connection that reloads the manifest, revalidates the current UUID/Step-3 Job/script/output/status/evidence tuple, and dispatches exactly one absolute `scancel <decimal-job-id>`. A normal nonzero or pre-dispatch race changes nothing; an ambiguous post-dispatch result is unknown and is not retried. A known zero means request accepted only, and a later explicit Refresh remains terminal authority.

After cancellation, the same surviving current output can reproduce the OOM origin without a schema change. Terminal `CANCELLED` remains the scheduler state while the application cause is `SLURM_TASK_OUT_OF_MEMORY`. Reviewed TIMEOUT/OOM retry uses the existing attempt-owned submission service: editable resources come from the hash-verified old script, while current fail-fast and `END,FAIL` rendering apply to the new script; geometry, control, restart, old Job/script/output, and science remain immutable.

## Decorated Slurm cancellation accounting

The production parser accepts the parent `sacct` state grammar `CANCELLED by <decimal UID>` and canonicalizes it to `CANCELLED`. The exact parent remains project-level authority; `.batch`, `.extern`, and task child rows do not replace it. If the current attempt also has the reviewed OOM evidence, recovery retains scheduler state `CANCELLED` while recording application cause `SLURM_TASK_OUT_OF_MEMORY`. Deterministic synthetic accounting rows validate this behavior without retaining a user ID, node, Job ID, timestamp, or captured scheduler output.

## Project Manager locality and permanent-delete safety

PM-R1 keeps its reversible path entirely local. Recycle and Restore atomically mutate only the `recycled_projects` tombstones in `%APPDATA%\Moltage\known_projects.json`; they make no connection, scheduler call, remote manifest write, or remote filesystem change. The `(selected server-profile UUID, project UUID)` key prevents an identically identified project bound to a different selected profile from being suppressed. Open workspaces are not closed for an otherwise terminal local recycle. PM-R1-R1 refuses `QUEUED`, `RUNNING`, `UNKNOWN`, typed accounting-pending/unresolved scheduler observations, and matching live submissions before a tombstone can be created; it never hides or cancels that work.

Permanent deletion is deliberately not a generic recursive-filesystem feature. It shares the same active/unresolved application predicate as local recycle, and the GUI additionally fails closed while a managed Geometry or Transmission workspace for the UUID is open. `SCHEDULER_COMPLETED` remains operation-terminal and deletion-eligible subject to those existing guards, although the shared visual mapper correctly presents its unresolved scientific status as yellow rather than green. One short-lived trusted connection then verifies that the selected profile and authoritative manifest still match the exact project, revision, and path; the target must be a normalized nonempty direct child below the configured workspace root, never `/` or the workspace root. A read-only remote check further requires the exact root to exist as a directory and not be a symlink.

The destructive boundary safely quotes that one lexical path and dispatches `test -d <path> && test ! -L <path> && rm -rf -- <path>` once, with no glob or name-based selection. It then uses a separate read-only check to prove the path and symlink entry absent. Only confirmed absence permits local-index cleanup and removal from the main list. A normal nonzero result is a definite failure and retains local state. `RemoteCommandOutcomeUnknown` during dispatch, or any transport loss before the absence check completes, becomes `Deletion outcome unknown. Reconnect and Refresh.`; no automatic second delete is permitted. Connection cleanup is best-effort and cannot replace the known destructive-operation outcome.

Keep this locality intact in later maintenance. Bulk deletion, server-side trash, empty-bin remote deletion, arbitrary remote browsing, rename, drag/drop ordering, tags, favorites, search/filter, and new global Project Manager preference persistence remain deferred rather than extensions of this exact-project boundary.

## Exact active-task restart drafts

The task-restart path is an explicit, exact-current-attempt operation rather than generic scheduler control. Right-click is offered only on the current Step 1–4 indicator while its durable state is `QUEUED` or `RUNNING` and it has a decimal Job ID. Before cancellation, one short-lived connection reloads and revalidates the selected profile, project UUID/revision, current step, exact Job ID, attempt filenames, input hashes, and exact scheduler parent. It then reads and strictly reconstructs all editable settings/resources from the hash-verified current input pair and submit script. Only after those checks succeed may it dispatch exactly one verified absolute `scancel <decimal-job-id>`. A normal nonzero produces no draft; ambiguous post-dispatch loss is unknown, opens no draft, and is never retried.

A known accepted cancellation request may open the exact pre-run `geometry.in` immediately in a session-local editable Geometry tab, but submission stays locked until explicit Refresh durably reconciles that same Job as terminal. This lock is not a new workflow state and no source manifest is rewritten merely to record the request. Step-3/4 restart workspaces remain coordinate-only so atom order, elements, and electrode membership cannot drift. Step 1, Step 2, Step 3, and geometry-changed Step 4 resubmit through the existing new-project path with schema-5 source provenance; they never overwrite the source calculation. Only geometry-unchanged Step 4 with durable scheduler state `CANCELLED` may create a new attempt in the same project, preserving the prior Job, tcontrol, script, output, hashes, current fail-fast policy, and `END,FAIL` mail semantics.

Implementation and automated validation of this feature used only fake/local executors. No SSH connection, `scancel`, `sbatch`, upload, manifest mutation, or email was performed, and no external-environment acceptance is claimed here.
