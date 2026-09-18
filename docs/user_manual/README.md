# Moltage v0.2.1 User Manual

This manual describes Moltage v0.2.1 from a user's point of view. It explains
where each command is located, what it changes, which settings are required,
and how to distinguish a local display operation from a remote calculation or
destructive project action.

## Manual contents

1. [About, installation, interface, and opening structures](01_getting_started.md)
2. [Linkers, contact Au, electrode construction, and Au(111) extension](02_molecular_preparation.md)
3. [Server profiles, schedulers, FHI-aims, AITRANSS, and ORCA](03_servers_and_schedulers.md)
4. [Project Manager, status indicators, recovery, restart, and deletion](04_project_manager.md)
5. [FHI-aims and AITRANSS four-stage workflow](05_fhi_aims_workflow.md)
6. [ORCA optimization, frequency, and WBL workflow](06_orca_workflow.md)
7. [Transmission, density difference, and local tight binding](07_analysis_and_results.md)
8. [Files, troubleshooting, support, button reference, and glossary](08_files_troubleshooting_reference.md)

## Goals and corresponding paths

| Goal | Path | Calculation software | Runtime environment |
| --- | --- | --- | --- |
| Open and inspect a structure | `File > New Geometry...` | None | Local |
| Prepare contact Au or a junction | Electrode Builder | None | Local |
| Optimize a molecule and calculate explicit transport | `Calculation > FHI-aims` | FHI-aims and AITRANSS | Configured Slurm or LSF server |
| Optimize a molecule with ORCA | `Calculation > ORCA > Step 1 — Optimization...` | ORCA | Configured Slurm or LSF server |
| Estimate linker-parameterized WBL transmission | `Calculation > ORCA > Step 2 — WBL Transmission...` | The same ORCA installation used for optimization, with adjacent `orca_2json` | Existing ORCA project; no Slurm or LSF job is submitted |
| Run a local one-orbital model | `Calculation > Local Tight-Binding Transmission...` | None | Local |
| Calculate density redistribution | `Calculation > FHI-aims > Electron Density Difference...` | FHI-aims | Configured Slurm or LSF server |

## Five-minute local walkthrough

1. Start Moltage.
2. Select **File > New Geometry...** and choose a supported molecular file.
3. Drag in the viewer to rotate, use the mouse wheel to zoom, and move the
   pointer over an atom to see its atom number.
4. Open the Electrode Builder with the last toolbar button if it is hidden.
5. If the structure contains a supported linker, select a detected site to
   preview its contact Au placement. No atom is committed until **Done** is
   pressed.
6. Try **Measure Distance** or **Measure Angle**. Measurements are visual and do
   not change the molecule.
7. Open **Settings > View...** to preview bond thickness, atom colors, labels,
   or hydrogen visibility. These are display settings, not scientific input.

The tutorial stops before any remote submission. Continue with
[server configuration](03_servers_and_schedulers.md) only if you intend to run
an external program.

## Important terms used throughout the manual

- **Geometry workspace**: a tab containing atoms, coordinates, Connectivity,
  viewer state, and optional editing history.
- **Project Manager**: manages submitted calculations, shows their current
  state, and provides the available follow-up actions.
- **Current working geometry**: the session copy used by subsequent Moltage
  actions. The originally opened file is not overwritten automatically.
- **Presentation-only**: changes pixels or annotations but not scientific data,
  coordinates, project state, or remote files.

## Version scope

This manual is versioned for **Moltage v0.2.1**. Later releases may add or move
controls. Use the upper-right **Update Log** button inside Moltage to check the
installed version's bundled change summary.

> **Documentation and scientific-use note:** Screenshots use demonstration
> structures and illustrative server, project, Job, and result data. Disabled
> controls normally await a prerequisite. Moltage validates supported workflow
> evidence but does not certify a particular external-program or HPC
> installation; review scientific settings for the intended system.
