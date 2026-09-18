# 19–22. Files, Troubleshooting, and Reference

[Back to the manual index](README.md)

## 19. Files, state, and provenance

### 19.1 What belongs to the user

Moltage separates installed program files, per-user application state, local
source files, and remote calculation projects.

- The installation directory contains the application and redistributed runtime
  components.
- Non-secret preferences, server profiles, known-project cache, known-hosts data,
  and recycle-bin records live under `%APPDATA%\Moltage\`.
- Saved server passwords use Windows Credential Manager; they are not written to
  profile JSON.
- Density-result caches live under `%APPDATA%\Moltage\density_results`.
- Input molecule files stay where the user placed them unless explicitly saved or
  exported elsewhere.
- Managed calculation data live below the selected server profile's **Remote
  project workspace**.

Uninstalling removes installer-owned application files and shortcuts but preserves
per-user state. Back up user input files and any remote project data according to
your own data-management policy.

### 19.2 Authoritative project records

Each managed remote project contains a `.moltage` manifest. This record binds the
workflow, stable project identity, settings, attempts, files, hashes, scheduler
evidence, and stage state. Project Manager treats the remote manifest as
authoritative; the local project list is only a cache.

Do not hand-edit manifests or rename calculation files while a project is active.
If an external edit changes a hash-bound artifact, Moltage reports the mismatch
instead of silently accepting a different result.

### 19.3 Typical FHI-aims project layout

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

The exact output names come from the stored attempt. Direct Step-2 or Step-3
starts mark earlier stages `SKIPPED` and do not fabricate their outputs.

### 19.4 Typical ORCA project layout

```text
ProjectName.YYYYMMDD/
  .moltage/project.json
  orca_opt.inp
  submit.orca.sh
  orca_opt.out
  orca_opt.scheduler.out
  orca_opt.gbw
  orca_opt.xyz
  frequency/                 # only after an explicit frequency request
  wbl/                       # only after an explicit WBL request
    orca_wbl_transmission.csv
    orca_wbl_result.json
    orca_wbl_transmission.svg
```

ORCA may create additional native auxiliary files. Moltage does not treat an
unrecognized auxiliary file as authoritative merely because it is present.

### 19.5 Provenance rules users should preserve

- Keep the exact input, output, scheduler script, and manifest together.
- Record user-chosen charge, multiplicity, method, basis, species accuracy,
  coupling values, and energy reference in reports.
- Distinguish `HYPOTHESIS` parameters from values calibrated against external
  evidence.
- Do not call ORCA WBL or local tight-binding output explicit DFT-NEGF.
- Keep exported figures with their source project and settings.
- When comparing calculations, use consistent model definitions and explicitly
  document intentional differences.

## 20. Troubleshooting

### 20.1 A menu item is disabled

Moltage enables actions from the active workspace and verified project state.
Check the following:

1. Is a Geometry tab active rather than a Transmission, Density, or Tight-Binding
   tab?
2. Is the geometry mutable? Recovered submitted geometries may be read-only.
3. Does the selected operation require a verified preceding stage?
4. Is the relevant FHI-aims, AITRANSS, or ORCA runtime configured and verified for
   the server?
5. For ORCA Step 2 and Frequency, was the optimized geometry recovered into the
   current ORCA Geometry tab?

Switching tabs causes the main actions to be rebound to the active workspace; it
does not change the remote project.

### 20.2 Runtime discovery finds nothing or only a partial candidate

Automatic discovery is bounded and deliberately does not crawl an entire server.
It can confirm a program only when the executable, required environment, and
related paths satisfy the maintained checks.

Use **Server → Manage Servers... → Edit → Cluster Execution Settings...**, open
the relevant program page, and either:

- supply a safe installation-directory hint and run discovery again; or
- choose **Manual Configuration...** and enter the exact absolute paths and
  environment already known for that server.

For FHI-aims, also verify the MPI launcher and a species root whose immediate
children include `light`, `tight`, and `really_tight`. For ORCA, verify the exact
driver path and environment. A located executable is not proof of MPI ABI,
compute-node availability, license eligibility, or scientific input compatibility.

### 20.3 SSH connection or host-key failure

- Recheck hostname, port, username, and network/VPN access.
- A first connection requires explicit acceptance of the displayed host-key
  fingerprint.
- A changed key is blocked. Confirm the change with the server administrator
  before replacing trusted evidence.
- Password authentication is the supported mode in v0.2.1; SSH key, jump-host,
  and ssh-agent workflows are not implemented.

Never paste a password, private key, access token, or unredacted internal server
record into a public issue.

### 20.4 Submission reports an unknown outcome

Moltage performs at most one scheduler dispatch. If the connection is lost after
dispatch and the outcome cannot be proven, the project becomes `UNKNOWN`; the
application does not submit again automatically. Reconnect and use **Refresh
Status**. Do not create a duplicate job until the scheduler state of the exact
attempt is known.

### 20.5 Refresh takes too long

Refresh has a bounded timeout. While it is active, the button changes to **Stop
Refresh**; stopping detaches the refresh immediately without cancelling a remote
calculation. Closing Moltage also does not wait for a status-only refresh. A
submission, cancellation, deletion, or result transfer is different and may
require an explicit completion/refusal message before shutdown.

### 20.6 Scheduler says completed but the indicator is not green

Green means validated scientific evidence, not merely scheduler exit success.
Use the displayed diagnostic and **Open / Recover** or **Refresh Status**. Common
causes include:

- missing normal-termination or convergence evidence;
- missing, empty, malformed, or hash-mismatched result files;
- output that belongs to a different geometry or attempt;
- an AITRANSS result grid inconsistent with `tcontrol`;
- a WBL artifact set that is incomplete or changed after creation.

Moltage preserves `SCHEDULER_COMPLETED` or a failure state when the evidence is
insufficient rather than inventing scientific success.

### 20.7 ORCA Step 2 cannot start

Confirm all of the following:

- Step 1 has verified normal termination and optimization convergence;
- `orca_opt.gbw` matches the recorded Step-1 attempt;
- the verified ORCA installation supplies a compatible adjacent `orca_2json`;
- two supported S/N contacts and linker types are selected;
- left and, when independent, right Γ₀ values are positive;
- the energy window and step are valid.

The WBL stage does not create a Molden file as its success criterion. It prefers
the complete `orca_2json` evidence path and creates the three persisted WBL result
artifacts listed above.

### 20.8 A transmission curve appears blank on a log axis

Logarithmic axes cannot display zero or negative values. Moltage intentionally
does not replace them with a positive floor. Check whether the result has positive
samples in the current X range, then use **Reset View** or adjust X/Y ranges in
**Settings → View...**. A presentation problem does not alter the persisted
scientific result.

### 20.9 An electrode-extension site is red

Red means the site is valid on the canonical Au(111) lattice but is blocked by the
current complete geometry. The click is rejected. Move or rotate the relevant
electrode/molecule and reevaluate; do not treat red as an invitation to force an
overlapping atom. Normal translucent gold indicates an available site.

### 20.10 A density task will not submit

Both subsets must be nonempty, disjoint, and together cover every atom. Total
charge must equal the sum of fragment charges. The selected server must have a
verified FHI-aims runtime and species root. Review fragment spin states and ensure
that the single job runtime can cover all three sequential SCFs.

### 20.11 Graphics or rendering trouble

Update the graphics driver and retry. VTK/Qt initialization may behave differently
under virtual machines or Remote Desktop; the Windows distribution includes Qt's
software-OpenGL fallback. If a reproducible rendering error remains, report the
Moltage version, Windows version, GPU/driver, exact operation, and a sanitized
screenshot.

## 21. Compact UI reference

### 21.1 Main toolbar, left to right

| Action | Purpose |
|---|---|
| **Undo / Redo** | Navigate the active Geometry workspace's bounded edit history. |
| **Reset View** | Refit the camera or restore a result plot's default ranges. |
| **Element Labels** | Toggle atom labels in the active Geometry workspace. |
| **Measure Distance** | Pick two atoms and display their distance. |
| **Measure Angle** | Pick three atoms and display their angle. |
| **Rotate Bond** | Select a bond and rotate one side interactively or numerically. |
| **Delete Atom** | Repeatedly delete selected atoms in a mutable Geometry workspace. |
| **Replace Atom** | Repeatedly replace selected atoms in a mutable Geometry workspace. |
| **Electrode Builder** | Open or close anchor/contact/electrode tools. |

Use `Esc` to leave an active picking/editing mode. A coordinate mutation clears
completed measurement overlays because their old values no longer describe the
new geometry.

### 21.2 Main menus

| Menu | Important entries |
|---|---|
| **File** | New Geometry; Export Current View. |
| **Projects** | Project Manager. |
| **Calculation → FHI-aims** | Steps 1–4 and Electron Density Difference. |
| **Calculation → ORCA** | Optimization, WBL Transmission, Frequency. |
| **Calculation** | Local Tight-Binding Transmission. |
| **Server** | Manage Servers. |
| **Settings** | View, Bond Detection, Email Notifications. |
| **Help** | License and Third-Party Notices; About Moltage. |
| **Upper-right buttons** | Theme and Update Log. |

For detailed behavior, see [Getting Started](01_getting_started.md).

### 21.3 Project indicator colors

| Appearance | Meaning |
|---|---|
| Filled green | Scientifically verified success. |
| Filled yellow | Queued, running, output-ready, or another nonterminal/intermediate state. |
| Filled red | Failed validation or calculation. |
| Filled gray | Skipped; some explicitly cancelled/stopped workflows also use a gray terminal state. |
| Outlined gray | Not started. |

Read the accompanying text. A color summarizes state but does not replace the
stored diagnostic.

### 21.4 Common keyboard/mouse behavior

- Left click picks atoms, bonds, or plot points when the corresponding mode is
  active.
- Drag in the molecule viewer rotates the camera unless a tool has captured the
  interaction.
- Wheel zooms the current 3D or chart view.
- `Esc` cancels an incomplete pick mode.
- Standard Undo/Redo shortcuts act only on a mutable Geometry workspace.
- Result and recovered submitted tabs are read-only unless explicitly opened as a
  new editable draft.

## 22. Updates, license, and support

The **Update Log** shows the installed release notes in the current application
language. **Help → License and Third-Party Notices...** identifies Moltage's
GPL-3.0-only license and the notices for redistributed components. **Help → About
Moltage** shows the installed version.

For a useful issue report, include:

- Moltage version and installation type;
- Windows version;
- workflow and exact stage;
- expected and observed behavior;
- reproducible steps using a non-sensitive example if possible;
- the exact on-screen diagnostic;
- a sanitized screenshot or minimal synthetic input.

Before sharing, remove passwords, hostnames, usernames, internal paths, scheduler
account names, job IDs, unpublished molecule/project names, and real research
outputs. Do not upload proprietary FHI-aims or ORCA distribution files.

The project homepage, current source, issue tracker, and release downloads are at
[github.com/junfeng-sb/Moltage](https://github.com/junfeng-sb/Moltage).

---

This completes the v0.2.1 user manual. Return to the
[manual index](README.md) or the [project README](../../README.md).
