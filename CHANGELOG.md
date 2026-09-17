# Changelog

## 0.1.13 to 0.2.1

- Added separate ORCA runtime configuration and a two-stage Optimization → WBL workflow, with optional frequency analysis.
- Added automatic or manual contact selection, explicit WBL parameters and provenance, and a dedicated Transmission viewer.
- Reorganized calculation menus and project actions by workflow; project refresh is bounded, stoppable, and non-blocking during shutdown.
- Singlet calculations now report one spin-degenerate total transmission, and logarithmic axes use superscript powers of ten.
- Fixed ORCA submission, recovery, WBL status, optimization resubmission, and Qt/VTK shutdown failures.

## 0.1.12 to 0.1.13

- Added per-server FHI-aims species-definition roots; new `control.in` files use definitions from the selected server.
- Added optional Slurm `account`/`partition`/`qos` and LSF `queue`/`project` settings, plus verified absolute `srun` launchers and selectable LSF resource policy.
- Replaced fixed Au59 templates with the 2–10 layer `MoltageAuPyramidV1` generator and conservative historical-project migration.
- Added canonical Au(111) lattice extension with stable lattice identity, Undo/Redo, rigid electrode rotation, collision previews, predicted bonds, and lattice-plane previews.
- Clarified Au-placement defaults and updated covalent and van der Waals radius tables.

## 0.1.11 to 0.1.12

- Renamed the application and Python package to Moltage.
- Migrated existing local profiles and projects to Moltage-owned namespaces; new remote projects use `.moltage` metadata and ambiguous duplicates are rejected.

## 0.1.10 to 0.1.11

- Added PNG/JPEG/PDF export for the current molecular, orbital, or Transmission view.
- Added configurable Transmission axes, curves, legends, ticks, and direct plot interaction.
- Added stoppable full-server refresh and improved atom-hover highlighting.
- Fixed completed LSF Step 4 results remaining unresolved when valid AITRANSS endpoint conventions were used.

## 0.1.9 to 0.1.10

- Improved the theme menu and corrected multicolor toolbar icon semantics.
- Preserved access to Step 1 orbital Cubes after adding contact Au atoms or starting later workflow stages.

## 0.1.8 to 0.1.9

- Added six selectable light/dark themes while retaining Future Light as the default.
- Unified theme styling across windows, panels, menus, tabs, controls, and icons.
- Standardized compact progress bars and fixed high-DPI toolbar icon scaling after theme changes.

## 0.1.7 to 0.1.8

- Added configurable Step 1 and Step 3 orbital-Cube output and on-demand display of recovered Step 1 orbitals.
- Fixed LSF terminal-state recovery after event-log rotation with bounded exact-Job-ID checks.

## 0.1.6 to 0.1.7

- Added detailed density-result retrieval progress and Full/Medium/Low display resolutions without changing full-resolution scientific data.
- Added verified streaming downloads and cached physical-grid downsampling for large Cube files.
- Improved LSF output/status recovery, density-task cancellation/recovery, and direct Step 2 submission from structures with two valid contact Au atoms.
- Server connection testing and cluster settings can now be used independently.

## 0.1.5 to 0.1.6

- Added a local tight-binding Transmission workspace with bulk parameters and per-atom/per-bond overrides.
- Added IBM Spectrum LSF submission, monitoring, cancellation, and scheduler-specific resource settings.
- Reworked runtime discovery as bounded evidence-based search with explicit partial-candidate diagnostics and manual configuration.
- Fixed LSF environment discovery, output recovery, terminal-state mapping, and unsafe remote `PATH` candidates.

## 0.1.4 to 0.1.5

- Added paired `R–C(CN)2` linker detection and supported Au-placement geometries.
- Added remote electron-density-difference calculations with fragment selection, Hirshfeld changes, interpolation, and report export.
- Added improved orbital-surface materials, measurement clearing, and manual FHI-aims/AITRANSS runtime configuration.
- Improved density interaction, result recovery, isosurface layout, server discovery, and task deletion handling.
- Fixed direct `.next_step` loading, Step 4 validation after `tcontrol` changes, `$ecp off` validation, and density-task resubmission.

## 0.1.3 to 0.1.4

- Added continuous Delete Atom and Replace Atom tools with Geometry Undo/Redo.
- Added `.cube`/`.cub` loading and orbital isosurfaces for ORCA, Gaussian, and FHI-aims files.
- Added configurable isosurface colors, opacity, lighting, and a built-in update log.
- Improved atom selection, orbital rendering, Au Tool highlighting, settings layouts, and measurement behavior.
