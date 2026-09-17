# Electron Density Difference

## Implemented scope

`Calculation → Electron Density Difference…` snapshots the active applied atomic
coordinates, including read-only imported/recovered geometries, Cube geometry and
any number of Au atoms. It does not change the source tab or require transport
Step 1–4 eligibility. Unapplied electrode previews are not atoms in this snapshot.
Finite molecular/cluster geometries use the existing supported element/species set.

The independent density calculation tab supports continuous point selection,
rectangle selection (projected atom centers, including occluded atoms) and
1-based ranges such as `1-20,25,31-40`. Clicking an atom already present in the
active subset removes it. The rectangle is a transparent black dashed outline.
Both subsets are displayed with camera-facing open rings and 1-based atom labels;
the active pointer atom temporarily uses the theme's separate hover ring. Each
registered theme supplies distinct high-saturation subset-1, subset-2 and hover
colors. These overlays do not replace atoms or mutate the partition.
Edited range text applies as soon as it is valid, and clicking either range field
makes that subset active; there is no separate Apply action. Assigning an atom
transfers it from the other subset; removal and “Add all remaining atoms” operate
on the active subset. Submission requires two nonempty, disjoint subsets covering
all atoms and names any unassigned atom numbers before remote work begins.
Original global IDs and fragment-local IDs are explicitly recorded, without
changing coordinates.

## Scientific definition and inputs

Three independent fixed-geometry SCFs produce Total, Subset 1 and Subset 2 electron
number densities. All share XC, official per-element species preset, scalar ZORA,
SCF conventions and one explicit Cube grid. No relaxation, capping, ghost atoms,
reorientation, AITRANSS output or restart from unrelated transport files is added.
The electronic state of each isolated fragment is a user choice: net charge and
collinear spin initialization are independent. Total charge must equal the sum of
fragment charges. Cutting a covalent bond may require a spin-polarized reference;
neutral/non-spin defaults are not a statement of the correct physical state.

`Δρ = ρtotal − ρsubset1 − ρsubset2`.
Positive values indicate electron accumulation; negative values indicate depletion.
This is interaction-induced density redistribution, including polarization, not a
unique measurement of interfragment transferred charge.

Native FHI-aims inputs are `output cube total_density`, `output hirshfeld`, and
`cube_content_unit bohr`. Generated `cube origin`/`cube edge` parameters are in Å;
controlled output Cube coordinates and density values are read in bohr and
electrons/bohr³. The initial grid uses the documented 0.1 Å spacing and 14 bohr
boundary margin; users can change these. These are starting settings, not grid
convergence guarantees. No automatic resampling or unit guessing is performed.

The original output parser requires an SCF convergence marker, normal termination
and the final complete ordered Hirshfeld section. Atom and fragment statistics are
`ΔNi = qfragment,i − qtotal,i`, reported as **Hirshfeld electron gains**, not as
integrals over fixed atomic regions of the difference Cube. Their interpretation
depends on the population partitioning. No Multiwfn code, executable or fchk reader
is used.

Format evidence:

- [FHI-aims documentation](https://fhi-aims.org/documentation), Cube and Hirshfeld output.
- [Current official output reference](https://fhi-aims.org/uploads/manual/Ch3/S52.html), explicit grid/units and total-density definition.
- [Official GSS2014 FHI-aims tutorial, printed page 5](https://helper.ipam.ucla.edu/publications/gss2014/gss2014_12171.pdf), Hirshfeld section/atom-row example.
- [Independent aimstools output-reader documentation](https://aims-tools.readthedocs.io/en/latest/_modules/aimstools/postprocessing/charge_analysis.html), corroborating the `Hirshfeld charge` output label; not a copied implementation or dependency.

## One task, one current job, three component lamps

New density projects have their own `.moltage/density.json`; existing tasks whose
only manifest uses the former metadata-directory name remain readable and are
updated in place, while duplicate current/former manifests fail explicitly as
ambiguous. Density tasks are not four-step CalculationProject records. Project Manager and the calculation/status
tab show three ordinary-size right-side lamps for Total, Subset 1 and Subset 2.
They report the three sequential component stages; they do not represent three
independently submitted Slurm jobs. Open exposes explicit Refresh Status, Recover
Results and Retry Failed Components. Successful Recover Results opens or focuses a
separate read-only Density Difference result view; no density surface or charge
table is embedded in the submission tab. There is no new background polling
service.

One Slurm batch runs Total → Subset 1 → Subset 2 sequentially in the allocation.
Each foreground launcher runs in a subshell; failure stops subsequent components.
Resources belong to the user/server profile; the UI accepts walltime in hours and
converts it to the existing whole-minute backend field, and walltime must cover all
three SCFs. Slurm native mail remains END,FAIL. Runtime configuration, scheduler
discovery, atomic upload and SHA256 verification reuse existing boundaries. No
AITRANSS executable is required for this task.

Right-clicking any active component lamp offers cancellation of the exact shared
Slurm Job ID. Before cancellation the job is authoritatively refreshed; a terminal
race does not issue `scancel`, and an ambiguous post-dispatch connection loss is
reported as UNKNOWN and is not retried in that UI session. Cancelling the shared
job stops the current component and prevents later components from starting.

Project Manager applies the ordinary deletion rules to density tasks. Queued,
running, unknown or unresolved tasks cannot be deleted. A terminal task can be
moved to the same restart-safe local Project Recycle Bin without SSH or remote
mutation, and Restore makes it eligible for read-only rediscovery again. Optional
permanent deletion additionally requires all calculation/result tabs for that task
to be closed, rereads the unchanged authoritative `density.json`, verifies the
exact direct-child non-symlink task directory and performs the existing single
exact-path delete operation. A recycled density task remains suppressed even if an
open local tab emits a later task update.

The manifest is set to UNKNOWN **before** the one sbatch dispatch. Transport loss
never triggers another dispatch. A locally recorded unambiguous receipt can repair
an interrupted manifest update, without resubmitting. A missing receipt remains
UNKNOWN and cannot be retried automatically. SSH cleanup warnings do not replace
known command outcomes and are reported without exception messages/secrets.

Queued/running/unknown and output-ready-but-unvalidated components are yellow.
Failed components are red, and components not yet reached by the sequential script
remain outlined gray. When Slurm or LSF authoritatively reports `CANCELLED`, the
task and the interrupted component are recorded as `CANCELLED` and shown filled
gray instead of being relabeled `FAILED`; earlier complete outputs remain
`OUTPUT_READY` and later components remain `NOT_STARTED`. This terminal state keeps
the same explicit resubmit, immutable-input retry, recovery and deletion actions as
other stopped tasks. Green requires a recovered component with converged/terminated
output, complete charges, matching fixed geometry and fully parsed common-grid Cube
data. Slurm COMPLETED/0 alone cannot turn all lamps green. Conversely, explicit
recovery may establish valid scientific results after a scheduler failure occurring
after output completion; the scheduler failure/exit evidence is retained in the
task message and report.

Routine Refresh reads bounded logs/status, not complete Cube files. Explicit
recovery streams each terminal output directly to a unique temporary local file,
then size-checks, hashes and atomically installs it in the per-user application-
data cache. The calculation tab shows the current file bytes and aggregate bytes
at the bottom while transfer totals are known, followed by explicit validation and
difference-building stages. Cached validated Cubes avoid repeated downloads.
The app never overwrites historical component inputs or results. A retry creates
`attempt02`, `attempt03`, etc., preserving successful validated components and
rerunning unsuccessful ones with the **original input bytes**, not regenerated
species data. After an authoritatively stopped/failed task reaches `FAILED`, opening
its calculation tab makes the fragment, electronic-state, SCF/grid and resource
fields editable. **Resubmit with Changes** snapshots those current values into a
new density task and remote directory; it does not rewrite the failed source task
or any historical attempt. The stored common grid is reused exactly unless the
spacing or padding field is changed. Active, ambiguous, output-ready and completed
tasks remain read-only. **Retry Failed Components** remains the distinct immutable-
science path: it reuses the original scientific input bytes, while current server
resource values may be changed. Both server-context mismatch and changed historical
input hashes block historical reuse.

Example layout:

```
Example_Density.YYYYMMDD/
  .moltage/density.json
  attempt01/
    submit.sh
    atom_map.csv
    slurm.out
    total/{geometry.in,control.in,aims.out,density.cube}
    subset1/{geometry.in,control.in,aims.out,density.cube}
    subset2/{geometry.in,control.in,aims.out,density.cube}
```

## Results and interpolation

The dedicated read-only Density Difference result view uses the existing VTK
surface/material controls to render positive and negative density differences.
View settings retain independent colors, isovalue, opacity style and lighting.
Its display grid defaults to **Medium** and can be changed from either the three
exclusive controls at the upper right or `View… → Isosurface`: **Full** uses the
source grid, **Medium** limits each axis to 120 points, and **Low** limits each axis
to 80 points. An axis already below the selected limit is not enlarged. Reduced
grids are generated by trilinear interpolation over the same physical extent and
cached per result view, so later slider changes reuse them. This is strictly a
rendering optimization: validated source fields, Hirshfeld values, subtraction,
integrals and exported `difference.cube` remain at full source resolution.
Camera movement does not rebuild scalar fields or meshes. Slider work is debounced
and stale requests are discarded; numeric subtraction/field preparation runs off
the GUI thread. The current VTK extraction/polygon limit is reused.

**Density Interpolation** uses `ρλ = ρsubset1 + ρsubset2 + λΔρ` and
`Δρλ = λΔρ`, for `0 ≤ λ ≤ 1`. At zero, the density mode shows the reference sum;
the difference mode is empty. This is not time evolution, physical current or an
electron trajectory.

Export creates a new directory containing `difference.cube`, `atom_charges.csv`,
`fragment_charges.csv`, `analysis_manifest.json` and `report.html`. Existing reports
are not overwritten. The report records sources, settings/hashes, partitioning
limitations and a finite-grid integral diagnostic. All-electron core peaks and
finite boundaries mean this quadrature is not an exact electron-count certificate.

## Validation boundary

Local fixtures are synthetic, explicitly labelled as such. Unit tests exercise
partitioning, exact geometry bytes, grids, native-output markers, subtraction,
Hirshfeld signs/mapping, interpolation, export, at-most-once submission, recovery,
cache behavior and immutable retry reuse. Qt tests exercise selection, Enter,
three-lamp Project Manager routing, exact-job cancellation and the Calculation
entry.

Implementation tests use local synthetic fixtures and fake executors; they perform
no SSH connection, scheduler submission, cancellation, upload or email delivery.
Installed-version compatibility, SCF and grid convergence, graphics-driver behavior,
and representative large-Cube performance require separately authorized external
acceptance. Installer/profile packaging is unchanged.

The offline suite covers component-state transitions, immutable retry input reuse,
terminal-state editability, density rendering, atom/subset feedback and workspace
interactions. Passing these tests establishes software behavior only; it is not
scientific validation or evidence from a production calculation.
