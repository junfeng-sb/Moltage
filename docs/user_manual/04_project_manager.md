# 13. Project Manager

[Back to the manual index](README.md)

## 13.1 Purpose and entry

**Entry:** `Projects > Project Manager...`

Project Manager manages submitted calculations, shows their current status, and
provides the valid next actions for each workflow. It reads projects below the
selected server configuration and checks scheduler/program status only when the
user explicitly requests a refresh.

<p align="center">
  <img src="../images/readme/project-manager-synthetic.png" alt="Project Manager with example ORCA and FHI-aims projects" width="90%">
</p>

**Figure 8.** Project Manager showing example ORCA and FHI-aims projects. The
displayed names and Job IDs are illustrative.

## 13.2 Window controls

| Control | Purpose |
| --- | --- |
| **Server** | Select the saved profile whose remote workspace will be inspected |
| **Refresh / Stop** | Start a full discovery/reconciliation, or stop ownership of the current refresh immediately |
| **Sort** | Sort by File name, Submission date, or Step type |
| Direction control | Reverse the complete deterministic sort order |
| View selector | Switch among Details, Compact, and Tiles without remote work |
| Recycle Bin control | Show locally recycled items for the selected profile |
| Project list | Select one project/task by stable UUID |
| Message area | Show the selected operation/status explanation |
| Contextual action rows | Present only actions valid for the selected workflow/state |
| **Close** | Hide the modeless window; calculations continue |

Project Manager is modeless. Hiding it does not stop status refresh or a remote
job. Closing the application detaches status-only refresh sessions but does not
cancel calculations.

## 13.3 Refresh behavior

Full refresh and single-project **Refresh Status** each have a 60-second
whole-operation UI deadline, including connection and remote reads.

- While refreshing, **Refresh** becomes **Stop**.
- **Stop** immediately restores local controls and ignores late results from
  that refresh session.
- Stop and timeout do not send `scancel` or `bkill`.
- Timeout means the refresh did not complete; it does not mean the calculation
  timed out.
- The previously displayed states remain visible.
- A new explicit refresh can start without an old late result overwriting it.
- Project state already written before refresh stops is not rolled back.

## 13.4 Project presentation modes

All three views use the same underlying authoritative snapshots.

| View | Contents |
| --- | --- |
| **Details** | Multiline workflow state, scheduler/Job evidence, program/scientific messages, and indicators |
| **Compact** | One row containing remote project name and stage indicators |
| **Tiles** | Fixed tile containing date-free display name, project submission date, and indicators |

Switching views or sort order is local only. Selection remains bound to the
project UUID and no SSH query or manifest write is performed.

Sort definitions:

- **File name**: natural case-insensitive remote-directory order.
- **Submission date**: earliest real stage-submission timestamp for the project;
  retries do not make an old project appear new.
- **Step type**: authoritative highest active/reached real stage, not filename
  text.

The fresh-window default is Submission date, newest first.

## 13.5 Status indicators

### FHI-aims/AITRANSS projects

Four indicators are always ordered:

1. Molecule optimization
2. Molecule–Au optimization
3. Transport convergence
4. Transmission

### ORCA projects

Exactly two indicators are shown:

1. ORCA optimization
2. WBL transmission

Optional frequency analysis does not create a third indicator.

### Density-difference tasks

Three indicators represent sequential components of one shared scheduler job:

1. Total
2. Subset 1
3. Subset 2

### Colors and symbols

| Appearance | State |
| --- | --- |
| Green filled | `SUCCEEDED` / scientifically recovered component |
| Gray filled | `SKIPPED`; density cancellation also uses a distinct gray terminal presentation |
| Red filled | `FAILED` |
| Hollow | `NOT_STARTED` |
| Yellow filled | `QUEUED`, `RUNNING`, `SCHEDULER_COMPLETED`, or `UNKNOWN` |
| Yellow with `?` | Unresolved terminal/program evidence; tooltip preserves the exact state |

Scheduler `COMPLETED` is not automatically green. The relevant program output,
hashes, geometry/result evidence, and workflow-specific success conditions must
also pass.

## 13.6 Left-click and right-click on indicators

- **Left-click** opens the available read-only input/output geometry menu for
  that exact stage or opens the associated completed result where supported.
- **Right-click** provides task actions only when that exact stage is the current
  active task and its state permits the operation.

For a current `QUEUED` or `RUNNING` Step 1–4 FHI task with a stored decimal Job
ID, right-click can expose **Abort Task and Create Restart Draft...**. Moltage
revalidates the exact project, stage, Job, input hashes, and scheduler state
before dispatching one exact cancellation. It never guesses a task by name.

## 13.7 Common contextual buttons

| Button | Purpose and eligibility |
| --- | --- |
| **Open / Recover** | Refresh/recover the selected project's applicable geometry or result evidence |
| **Refresh Status** | Reconcile only the selected item |
| **Kill Job** | Explicitly request cancellation for the narrowly supported active Step-3 OOM case after fresh exact-Job validation |
| **Cancel ORCA Job...** | Cancel the exact verified active ORCA optimization Job |
| **Resubmit Optimization...** | Open structured settings from an ORCA project and create a new project; an active old Job is cancelled first |
| **Resubmit Step 3...** | Retry reviewed Step-3 timeout/OOM failures with editable resources and immutable scientific inputs |
| **Retry with explicit self-energy...** | Prepare the narrowly supported AITRANSS interface-overlap retry |
| **View Transmission** | Open a scientifically successful, currently parseable FHI/AITRANSS result |
| **View WBL Transmission** | Open a completed ORCA WBL result |
| **Delete Project...** | Move an eligible terminal item to local recycle, or explicitly request guarded permanent remote deletion |
| **Restore** | Remove the selected local recycle tombstone; normal Refresh is required to rediscover the remote item |
| **Back to Projects** | Leave the Recycle Bin view |
| **Close** | Hide Project Manager |

Buttons unrelated to the selected workflow are hidden rather than presented as
misleading disabled controls. In particular, ORCA projects do not show Step-3
retry, explicit self-energy retry, or FHI transmission actions.

## 13.8 Open / Recover

Recovery keeps several outcomes separate:

1. connection/query success;
2. scheduler state;
3. external-program termination;
4. scientific convergence/completeness;
5. availability of a displayable geometry/result.

Examples:

- A finished Step-1 FHI job becomes successful only with the reviewed normal
  termination evidence and a valid chemistry-consistent
  `geometry.in.next_step`.
- An ORCA optimization requires scheduler success, ORCA normal termination,
  optimization convergence, hash-bound input, and a valid ordered final XYZ.
- AITRANSS Step 4 requires its positive markers, parseable active `tcontrol`,
  and a complete valid transmission grid.

A plot-rendering error does not retroactively change a scientifically successful
project state.

## 13.9 Restart drafts

After an exact cancellation is accepted, Moltage opens the pre-run geometry as a
session-local editable draft. Submission remains locked until explicit
**Refresh Status** establishes that the source Job is terminal.

- Step 1/2 drafts recover structured optimization settings.
- Step 3 drafts recover fixed-geometry convergence settings.
- Step 4 drafts recover `tcontrol`, one-process resources, and the exact Step-3
  transport geometry.
- Step-3/4 drafts are coordinate-only; atom order, elements, and electrode
  membership must remain unchanged.

An edited Step 1, 2, or 3 draft creates a new project and records restart
provenance. It never overwrites the source project. A coordinate-changed Step-4
draft must return through a new Step-3 project because old matrices are no
longer valid.

## 13.10 Step-3 resource retry

**Resubmit Step 3...** is offered only for reviewed timeout or memory failures.
The dialog recovers resource values from the hash-verified current attempt
script. Depending on scheduler it exposes the applicable nodes/tasks/CPUs,
runtime, memory, OpenMP threads, and execution-model summary.

- Geometry and `control.in` hashes remain unchanged.
- `aims.restart` is retained if it exists.
- Previous scripts, outputs, and Job records are preserved.
- The retry receives new `retryNN` script/output names.
- Moltage does not calculate a memory increase automatically.
- Submission is still at most once; an ambiguous dispatch outcome becomes
  `UNKNOWN` and is not automatically retried.

## 13.11 Exact cancellation boundary

Cancellation is always explicit. A normal successful scheduler command means
only that the cancellation request was accepted. Run **Refresh Status** later to
obtain the terminal scheduler state.

If the connection is lost after dispatch, Moltage reports an unknown outcome and
does not issue a second cancellation automatically. A terminal race sends no
cancellation and is reconciled normally.

## 13.12 Recycle and permanent deletion

### Move to Recycle Bin

The default **Delete Project...** operation creates a local tombstone:

- no SSH connection;
- no scheduler cancellation;
- no remote move or deletion;
- the authoritative remote project remains intact;
- the project is suppressed from the normal list for that profile.

**Restore** removes only the tombstone. Refresh then rediscovers the project if
it still exists remotely.

### Delete Permanently

Checking the permanent server-file option changes the operation to an
irreversible exact-path deletion. It is available only when:

- no stage/task is queued, running, unknown, or unresolved;
- no matching live submission is active;
- no local managed workspace for the item remains open;
- profile, project UUID, manifest revision, and path still match;
- the directory is one direct child below the configured remote workspace;
- the target is not a symlink and has a nonempty basename.

Moltage dispatches one safely quoted exact-root delete with no wildcard. An
ambiguous transport loss is reported as **Deletion outcome unknown. Reconnect
and Refresh.** It is never repeated automatically.

## 13.13 Cross-machine project binding

A project records the server-configuration UUID used when it was created. When
opening that project from another Moltage installation, Moltage asks whether to
associate the exact remote project UUID/path with the currently selected local
configuration. The approval is stored only on the current computer for that
exact combination and does not modify the remote project. Another configuration
or path requires separate confirmation.

[Next: FHI-aims and AITRANSS workflow](05_fhi_aims_workflow.md)
