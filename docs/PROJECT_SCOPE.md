# Project Scope

## Current status

The managed workflow is implemented through Step-4 recovery and basic transmission inspection. Environment-specific deployment and scientific acceptance remain the responsibility of each configured server and are not asserted by the repository's offline validation.

Version 0.2.1 also implements a separate ORCA molecular-optimization workflow, an explicit post-optimization linker-parameterized WBL stage, and an optional frequency stage. It supports reviewed structured choices for ORCA 5.0.x, 6.0.x and 6.1.x, Slurm bounded runtime discovery, and Slurm/LSF manual runtime validation and direct-driver submission. WBL reuses a verified optimization `.gbw`, requires sibling `orca_2json` overlap/MO evidence and explicit user coupling assumptions, runs no new SCF/optimization, and remains labelled `HYPOTHESIS` rather than DFT-NEGF. The default suite is synthetic/offline and does not claim external-cluster compatibility.

The implemented workflow reaches:

```text
Step 1 optimization
    -> Step 1 recovery
    -> Step 2 contact-Au optimization
    -> Step 2 recovery
    -> parameter-selected canonical Au pyramids (six-layer Au56 by default)
    -> Step 3 transport-convergence input
    -> Step-3 submission and recovery
    -> Step 4 AITRANSS submission and recovery
    -> validated non-spin TE.dat
    -> basic T(E) preview
```

Step 3 uses deterministic non-relaxing input generation and submit-once delivery under the existing project UUID/root. Recovery distinguishes active, scheduler-terminal, program-complete, and scientifically successful outcomes. Explicit retry preserves `geometry.in`, `control.in`, prior scripts/logs, and `aims.restart`, assigns unique retry script/output names, and retains minimal attempt provenance; no retry is automatic.

After a technically successful Step 3, recovery validates exact geometry, bounded NATOMS/NSAOS evidence, and required AITRANSS files before preparing editable deterministic `tcontrol`. Step 4 uses a separately configured runtime and one-task submission. Scheduler `COMPLETED/0:0` is necessary but insufficient: known electrode-interface overlap and self-energy file-format failures remain typed scientific failures. Success requires the authoritative current-attempt output, reviewed completion markers, a parseable active `tcontrol`, and a finite increasing transmission grid to agree. Both supported complete-grid upper-bound conventions are accepted, and a post-run `tcontrol` SHA256 difference is diagnostic only. The result presentation uses raw `T(E)` on a base-10 logarithmic axis, raw-linear `T(EF)` interpolation, session-local styling, and literal current-view export. These state transitions, parser boundaries, and presentation contracts are validated with deterministic synthetic fixtures rather than captured external runs.

The Step-4 AITRANSS endpoint-compatibility correction is **fully accepted and frozen**. Its scope is limited to accepting the two verified complete-grid upper-bound conventions during scientific recovery; it does not weaken scheduler authority, completion-marker, Fermi-energy, ordering, spacing, truncation, or overrun checks.

The Slurm-native terminal-mail policy is fixed to `END,FAIL`. Each server profile may enable one validated recipient for Steps 1–4 and Step-3 retries. This reports scheduler termination metadata rather than scientific success and requires neither an open application nor user SMTP credentials. Offline renderer tests verify the generated directives; delivery behavior remains external-environment acceptance.

UI-M1/UI-M1-R1 native manual acceptance is **passed and frozen**. UI-M2 adds only session-local connectivity-edge rotation of the current working Cartesian geometry. UI-R5 adds strict MOL V2000 import and display-only single/double/triple strands. UI-R6 adds one session-global presentation state for relative bond thickness, per-element colors, element-symbol labels, and visual-only H filtering. UI-R6-R1 previews thickness and colors immediately, and previews the existing Bond Detection factor by replacing inferred edges in place; OK commits the preview and Cancel restores the dialog-open snapshot. Cube MVP adds read-only `.cube`/`.cub` single-field signed-isosurface visualization with only two fixed opacity modes, independent red/blue lobe colors, direct/stepped isovalue control, and adjustable ambient/brightness plus specular/shininess controls with smooth Phong shading. Accepted Cube lighting and material values persist per user across application restarts. It changes no MOL order, geometry writing, scientific settings, project state, workflow eligibility, or remote behavior.

The desktop-theme surface is **fully accepted and frozen**. It retains Future Light as its compatible default and adds the approved Arctic Circuit, Pearl Quantum, Aurora Glass, Solar Paper, Event Horizon, and Ember Lab themes. The upper-right Theme button sits immediately left of Updates; its chooser presents separated Light and Dark columns, with a hollow-circle indicator for an unselected choice and a circle-plus-center-dot indicator for the active choice. Runtime repolishing explicitly preserves the normal main-window geometry. The selected palette/style and contrast-safe neutral SVG tint apply at runtime, and only the registered theme ID is remembered per user. Multicolor editing icons retain their semantic colors through paired light/dark resources. Theme choice changes no scientific renderer state, workflow input, project, server profile, or remote operation.

The current-view image exporter is a presentation-only extension. `File -> Export Current View...` targets the active scientific canvas rather than menus or editing controls: the current molecular/Cube camera, visible geometry and active isovalue/material state, or the styled Transmission plot. It writes PNG, JPG, JPEG or raster-backed PDF at an explicit integer 1×–8× screen multiplier. Export does not alter scalar fields, plotted values, coordinates, project state, or remote files.

The atom-feedback refinement is **fully accepted and frozen**. Every molecular canvas, including Cube overlays, marks the atom under the pointer with a camera-facing open ring and its 1-based atom number. Electron-density fragment selection uses the same open-ring/number language for both subsets, with separate high-saturation hover, subset-1 and subset-2 colors supplied by each registered application theme. It changes no atom identity, coordinates, Connectivity, Cube values, density partition, project state, or submission input.

The completed Step-1 orbital recovery and display surface is **fully accepted and frozen**. Recovery performs one bounded listing of that exact managed working directory and advertises only non-empty canonical orbital Cube files requested by the generated `control.in`. The recovered editable geometry shows those orbital labels at the upper right and downloads only the selected Cube on demand. The same catalog remains available when Step-1 output geometry is reopened from its status indicator after later steps have started. Its signed isosurface is a presentation-only overlay: the recovered structure, Au Tool state, continuation eligibility, and later submission inputs remain unchanged. Contact-Au application, including traceable terminal-H removal, does not detach the original Step-1 field; unrelated atom or coordinate edits still invalidate the overlay request. The existing View isosurface controls remain available.

Local anchor preparation additionally recognizes a paired `R–C(CN)2` topology only when exactly two qualifying groups occur in one structure. The four existing Cyano-N sites remain intact, while the two center C atoms become additional Dicyano-C sites with user-approved center-C and contextual Cyano-N placement geometry. This changes no remote calculation behavior or accepted electrode geometry.

Accepted two-side Au pyramids can be extended interactively at Phase-2 canonical Au(111) lattice sites. The viewer caches the backend candidate snapshot and shows at most one normal translucent Au preview for an available site or red preview for a blocked site. That ghost includes dashed guides to the exact Au neighbors that unchanged Phase-2 Connectivity would add and a finite triangular grid in its current layer plane, fading outward from the candidate; the blocked form uses red guides as well. Addition still occurs only after fresh validation of the stable side/layer/lattice-key identity. Successful additions participate in the existing Geometry Undo/Redo and same-side rigid electrode rotation. The interaction adds no scientific threshold, project/profile field, or server operation.

Manual local FHI-aims geometry import is **fully accepted and frozen**. A selected `.in` or `.next_step` file is parsed through the established molecular-geometry reader; a standalone `.next_step` contains the complete structure and therefore never requires a companion `.in`. Server-backed optimization recovery remains a separate path and retains its submitted/optimized atom-count and ordered-element checks.

The Windows distribution stage now produces a versioned x64 windowed application and UAC-elevated installer with an explicit destination-directory page, without embedding build-machine settings or credentials. Local build validation has passed; UAC installation, overwrite, and uninstall acceptance on Windows remains pending.

The dual-scheduler execution and targeted runtime-discovery release is **fully accepted and frozen**. It supports only Slurm and IBM Spectrum LSF, keeps their resource semantics and submission pages separate, and preserves the selected scheduler across normal calculations, retries, AITRANSS, and density-difference jobs. LSF automatic preflight requires the complete command set and an `lsid`-validated `LSF_ENVDIR`/`LSF_BINDIR`/`LSF_LIBDIR`/`LSF_SERVERDIR` client environment. Runtime discovery remains bounded and evidence-directed: it neither sources a discovered environment script nor recursively scans the filesystem. All server-specific paths remain per-profile data and are excluded from the installer.

WF-R1 adds only local-start eligibility and truthful provenance. An active editable local molecular geometry is eligible for direct Step 2 when the frozen detector finds exactly two recognized linker termini, each with exactly one directly attached contact Au, and those two atoms are the complete Au population. The contacts may either already be present in the imported source or belong to a current state produced by accepted viewer placement, whether the two sites were applied together or sequentially; the latest applied-placement atom and topology proof must still match the working state. The existing Step-2 action may then create a normal managed project with Step 1 `SKIPPED`. Direct Step 3 is deliberately narrower: only a source that already contained the appropriately optimized contact Au may enter the frozen Phase-2D Electrode Builder, reuse those contact atoms as the two apices, and proceed after its exact two-cluster Done result with explicit pre-optimized-geometry confirmation and persisted electrode mapping. Viewer-added contacts never skip their required Step-2 optimization. No skipped calculation is represented as `SUCCEEDED`, and later recovery/progression uses the normal workflow.

The local Tight-Binding Transmission MVP, including its separate on-demand result window and hover-only parameter feedback, is **fully accepted and frozen**. It provides an independent session-local tight-binding transmission workspace for a loaded molecular graph. It uses one orthogonal orbital per atom, explicit real bond couplings and onsite energies, two explicit single-atom wide-band contacts, and a user-defined energy grid. Linker detection supplies contact candidates only; no scientific parameter is invented. The Hamiltonian, hover-only 3D parameter feedback, and on-demand local transmission result window are presentation/calculation state only and do not enter projects, remote execution, AITRANSS, saved settings, or the installer.

The current Project Manager refinement bounds full server Refresh, single-project status refresh, and density-task refresh to 60 seconds each. Stop restores local controls immediately and interrupts only the refresh session, not the calculation job. Active status refresh does not delay application shutdown, and late stopped/timed-out results cannot overwrite a newer refresh. The Transmission presentation editor includes a separate tab-local Ticks page for major/minor length and width, inside/outside direction, minor visibility, and linear-X/logarithmic-Y interval counts. These controls change only rendered presentation and exported pixels.

## Overall goal

Build a portable Windows desktop application that guides a molecular-junction calculation from imported structure through:

1. local structure and contact preparation;
2. FHI-aims geometry optimization on one configured Linux/Slurm server;
3. server-side monitoring and scientific/technical completion checks;
4. retrieval of the optimized structure;
5. Au electrode construction;
6. a single-core FHI-aims convergence calculation;
7. AITRANSS execution; and
8. a basic transmission-versus-energy preview.

The application is a deterministic scientific workflow tool, not a numerical data-analysis platform. It now includes literal current-view figure styling/export, but performs no derived scientific analysis or automatic publication-layout workflow. Its MVP must not require Codex or another AI assistant at runtime.

## MVP strategy

Build and validate one thin vertical workflow before adding breadth. The intended first later functional path is approximately:

`one imported XYZ/MOL V2000 geometry -> one anchor type -> two Au -> one server -> FHI-aims optimization -> server monitor -> optimized structure -> electrode -> one-core convergence -> AITRANSS -> T(E)`

The first proof path will likely use one simple anchor type, probably `-NH2`, but implementation requires an explicit later task and approved scientific definitions. The MVP uses a single workflow per project; alternative calculations are separate projects rather than branches within a project.

## Approved MVP boundaries

- Distribute the Windows x64 desktop application as a windowed executable through a UAC-elevated installer with a user-selectable local destination, without bundling per-user configuration or credentials.
- Support strict four-column XYZ, single-record MOL V2000, manually selected local FHI-aims molecular geometry files ending in `.in` or `.next_step`, and positive-grid-count single-dataset Cube/CUB, including one-identifier ORCA/Gaussian orbital records with negative `NATOMS`. ORCA coordinates use Bohr; FHI-aims and unidentified headers require an explicit workspace-local Bohr/Å choice. Plain element species require no `control.in`; an adjacent `control.in` is used only when present so calculation-specific aliases can be resolved. A manually opened next-step file is parsed directly from its own complete molecular section and does not require a companion `.in`; server-backed optimized-structure recovery retains its submitted/optimized chemistry-order validation. True multi-dataset Cube, negative grid counts, V3000, SDF, aromatic/type-4 bonds, wedge/dash, formal-charge visualization, and bond-order editing remain excluded.
- Bind each calculation project to one selected Linux server profile using SSH username/password authentication, Slurm or LSF, and an existing FHI-aims installation. A project created by another application installation may be explicitly bound to an exact current-machine profile/project UUID/path tuple without rewriting its remote historical profile identity.
- Keep server-side monitoring alive independently of the Windows GUI.
- Determine FHI-aims success from scheduler and program output evidence; leaving `squeue` is not success.
- Optionally request one terminal email through the selected server's verified Slurm-native mail path, using the fixed `END,FAIL` policy and no user SMTP credentials.
- Keep environment-specific and scientific variation in configuration, templates, and resources.
- Let Step 1 request the six independently selectable frontier-orbital Cube files by default plus optional absolute states; let Step 3 request optional absolute states. Preserve the FHI-aims default Cube grid unless the user explicitly supplies a spacing.
- Display the authoritative Energy/Transmission `T(E)` data in the reviewed interactive logarithmic plot, with tab-local presentation controls and literal current-view image export; do not derive conductance, current, or new scientific data products from that presentation layer.

## Explicit MVP exclusions

Do not add the following unless a later task explicitly changes scope:

- simultaneous cross-server project orchestration;
- SSH keys, SSH agents, or jump hosts;
- PBS or SGE;
- automatic retries or automatic scientific error recovery;
- AI assistants, parameter recommendation, or other runtime AI;
- project comparison or branching workflow histories;
- automated publication-layout workflows or advanced data processing;
- databases, telemetry, or cloud services;
- generic plugin systems.

## Phase 0 exclusions

Phase 0 does not implement parsers, connectivity, anchor recognition, Au placement, visualization, GUI, FHI-aims input generation, species-default handling, remote transfer or execution, Slurm, monitoring, SMTP, project naming, persisted state, output parsing, electrode construction, convergence, AITRANSS, transmission parsing, plotting, or executable packaging.

It also does not create fake implementations, placeholder APIs, speculative signatures, or a forest of empty future modules.

## Non-goals

- Interpret scientific results or recommend scientific parameters.
- Prove chemical structure from a transmission peak.
- Interpret or transform numerical results into derived publication analyses.
- Compare projects or manage a generic HPC platform.
- Provide an AI assistant as an MVP runtime dependency.
- Replace FHI-aims, Slurm, AITRANSS, or laboratory-approved scientific resources.
