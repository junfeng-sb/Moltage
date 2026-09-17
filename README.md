# Moltage

**Single-Molecule Quantum Transport & Analysis Workbench**

Moltage is a portable Windows desktop workbench for molecular-junction transport calculations and related electronic-structure analysis. Its current remote workflow uses FHI-aims on Linux with Slurm or IBM Spectrum LSF, followed by AITRANSS; local molecular preparation, orbital and density visualization, density-difference analysis, and tight-binding transmission are integrated in the same application.

> **Development release:** `v0.2.1` is an early public-development checkpoint. Use it with independently reviewed scientific inputs and retain the generated provenance records. Synthetic offline tests validate Moltage logic; they do not certify a particular ORCA, FHI-aims, AITRANSS, SSH, scheduler, or HPC installation.

The product, Python distribution/import namespace, executable, installer, resources, and development documentation now use the Moltage name. New local state is written under `%APPDATA%\Moltage`, new credentials use the `Moltage` Windows Credential Manager service, and new remote manifests use `.moltage`. On first launch, known non-secret files from the former application-data directory are copied only when the corresponding Moltage file is absent; former credentials and remote manifests remain readable for compatibility and are never silently overwritten or deleted.

The Moltage identity and compatibility contract is **fully accepted and frozen**. Product-facing code must use `Moltage`/`moltage`; former identifiers may remain only at the explicit compatibility boundaries above and in their regression tests.

The managed workflow keeps scheduler completion, program completion, and scientific success as distinct states. Step 4 uses a separately configured AITRANSS runtime and records success only when the authoritative scheduler result, completion markers, active `tcontrol`, and a finite ordered transmission grid agree. Known interface-overlap and self-energy reader failures remain typed outcomes, and prior attempts remain immutable. The Project Manager opens a validated non-spin result in the tabbed workspace with logarithmic `T(E)` presentation, raw-linear `T(EF)` interpolation, session-local styling, and literal current-view image export. Optional Slurm terminal mail uses the fixed `END,FAIL` policy without SMTP credentials. These contracts are covered by deterministic synthetic offline fixtures; that validation is not evidence of acceptance on any external cluster.

Project guidance:

- `docs/PROJECT_SCOPE.md`
- `docs/ARCHITECTURE.md`
- `docs/WORKFLOW.md`
- `docs/CONFIGURATION.md`

Moltage does not bundle FHI-aims species definitions. Each saved server profile identifies a canonical remote root whose immediate `light`, `tight`, and `really_tight` children contain exact `<NN>_<Element>_default` files. New `control.in` generation reads and validates only the required files from that server before any remote project mutation; missing, ambiguous, malformed, or oversized definitions fail explicitly without another-accuracy fallback. Existing `control.in` files remain self-contained and readable without that profile field.

## ORCA optimization development status

The current working tree adds a separate ORCA molecular-optimization workflow while leaving the established FHI-aims/AITRANSS workflow unchanged. Server settings are divided into shared `General / Cluster` settings and top-level `FHI-aims` and `ORCA` program pages; AITRANSS remains a subtab of FHI-aims. An ORCA profile needs only one remotely verified absolute `orca` executable, its selected environment preparation, and literal version evidence. Slurm offers bounded discovery through the login/configured environment and ORCA-named module candidates; LSF intentionally provides manual validation only. Neither path scans the filesystem or assumes a site-specific installation layout.

Input generation has reviewed support for ORCA 5.0.x, 6.0.x, and 6.1.x. Method and basis have no Moltage default and must be selected explicitly; charge starts at `0`, multiplicity at `1`, and both remain user decisions subject only to an electron-parity consistency check. `%MaxCore` is optional and, when present, is expressed in MB per process. Leaving it blank is valid and renders no `%maxcore`; ORCA then uses its own documented default behavior (4096 MB per process), which is a planning value rather than a hard memory limit.

Optimization success requires separate agreement between scheduler success, ORCA normal termination, the documented optimization-converged marker, and an ordered finite `orca_opt.xyz`. A missing `.gbw` does not negate a verified optimization but prevents the optional WBL stage. Frequency is a separate, user-started `FREQ` or `NUMFREQ` stage that inherits the optimized scientific settings; its evidence records ORCA-reported imaginary-mode annotations without claiming that a global minimum has been proved.

ORCA projects display two status indicators: optimization and WBL transmission. After opening the ORCA structure, `Calculation > ORCA` contains optimization resubmission, WBL, frequency and completed-WBL viewing; WBL/frequency do not occupy the Project Manager footer. A queued, running or failed optimization can reopen its hash-bound submitted `orca_opt.inp` without any FHI-aims project file, and resubmission preloads its structured settings. Before a replacement is submitted, Moltage revalidates the exact prior scheduler Job and cancels it when it is still active; an uncertain status or cancellation outcome blocks the new submission.

After a verified optimization with hash-bound `.gbw` evidence, the user may explicitly start an ORCA WBL post-processing stage. Moltage records WBL progress in the second indicator and the originating Geometry workspace, verifies the sibling absolute `orca_2json`, exports MO/basis/overlap evidence from the existing `.gbw`, and performs no new SCF or optimization. Existing linker detection prefills exactly two unambiguous supported contacts; either contact can instead be chosen from numbered atoms in the read-only viewer. The user supplies positive coupling values, while `Same as left` can mirror that user input; Moltage provides no numeric coupling default. Projection and parameter-evidence overrides remain available under Advanced. New dialogs start from an editable, explicitly hypothetical generic Au `E_F = -5.1 eV`, `E-E_F = [-5, 5] eV`, and `0.01 eV` sampling. For verified restricted multiplicity-1 evidence, the result evaluates each spatial MO once and emits only the spin-degenerate total transmission in the conventional `G_0 = 2e^2/h` normalization; it does not duplicate identical Alpha/Beta curves. Verified unrestricted open-shell evidence retains Alpha, Beta, and raw spin-sum outputs with per-spin top-two contributions. CSV/JSON/SVG artifacts and hashes record the selected spin treatment, and logarithmic plot decades use typographic `10ⁿ` labels rather than E notation. Every result remains a linker-parameterized WBL `HYPOTHESIS`, not explicit Au-molecule-Au DFT-NEGF. Completed results open in the dedicated View WBL Transmission workspace from the Calculation menu or the second status indicator. Unsupported basis/spin/overlap evidence fails explicitly rather than falling back to raw coefficient populations. Repository validation remains synthetic and offline and does not claim compatibility with a particular external ORCA/HPC installation.

## Windows distribution

The Windows x64 distribution uses the existing `moltage_gui.py` entry, a
PyInstaller one-folder application, and a UAC-elevated Inno Setup installer. Build
dependencies are isolated in `.venv-packaging` and pinned in
`packaging/requirements-build.txt`. Run `packaging/build_windows.ps1` from
PowerShell after installing those dependencies and Inno Setup 6.

The generated application is windowed and does not open a console window. The
installer contains immutable application resources only. Server profiles,
known projects, known hosts, credentials, and lifecycle logs are created later
under their existing per-user owners and are neither copied into the installer
nor removed by uninstall.

Official release assets are published through GitHub Releases. Verify the
adjacent SHA-256 checksum before running the installer. The Windows development
installer is currently unsigned, so Windows SmartScreen may show an
unrecognized-publisher warning; a checksum verifies file integrity but is not a
substitute for a future code-signing certificate.

Runtime discovery is per server profile. It supports bounded terse queries for
Lmod and Tcl Environment Modules, plus Lmod spider queries for hierarchical
module trees; all reported modules remain candidates until the exact accepted
FHI-aims and AITRANSS executable identities are verified.

## Validation

From the repository root, install the application and the declared test
dependency in a clean development environment:

```powershell
py -m pip install -e ".[test]"
```

The documented default suite is `tests/unit`. It uses synthetic fixtures and
injected fake or in-memory boundaries; it does not require a configured SSH/HPC
server, scheduler account, FHI-aims, AITRANSS, species definitions, saved
credentials, or normal Moltage application state. The test process rejects
unmocked access through the production network, SSH, and native credential
boundaries and redirects application data to temporary test-owned storage.

Use focused validation after a small, locally owned change. The test paths are
explicit so the runner never guesses dependency impact or silently skips a
relevant test:

```powershell
.\tools\run_tests.ps1 -Tests tests\unit\test_orbital_cube.py,tests\unit\test_project_orbital_cube.py
```

For a completed feature, run its subsystem and direct integration tests with
explicit `-Tests` paths. Run the complete offline suite only for schema/persistence,
scientific behavior, high-risk scheduler/security/cross-layer architecture
changes, or a formal pre-commit snapshot; it is not the default for every change:

```powershell
.\tools\run_tests.ps1 -Full
```

The scoped OpenSpec validation and audit policy is in `AGENTS.md` and
[`docs/MAINTENANCE.md`](docs/MAINTENANCE.md). Full pre-public audits are separate
from the ordinary feature loop.

Both modes first compile the source and use the Windows software OpenGL path.
VTK GUI tests should not be forced through Qt's offscreen platform plugin,
which is not equivalent to the supported Windows desktop runtime.

Passing this synthetic offline suite validates project behavior only within its
documented test boundary. It is not evidence that a real SSH/HPC site,
scheduler configuration, FHI-aims or AITRANSS installation, MPI environment, or
scientific calculation has been accepted. Any such validation is a separate,
explicitly authorized operation and is never started by the default test
command.

## License and third-party software

Copyright (C) 2026 Junfeng Lin.

Moltage is free software distributed under the
[GNU General Public License version 3 only](LICENSE). The Windows distribution
uses the GPLv3 option for Qt/PySide and Qt Charts. Third-party components retain
their own copyrights and license terms; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and [`LICENSES/`](LICENSES/).

Each binary release must publish the exact Moltage source and the corresponding
third-party source bundle identified by
[`packaging/third_party_sources.toml`](packaging/third_party_sources.toml) at the
same GitHub Release. An upstream hyperlink alone is not used as a substitute for
those release assets. FHI-aims, AITRANSS, and ORCA are user-supplied external
programs and are not included in Moltage.

Security issues should be reported privately as described in
[`SECURITY.md`](SECURITY.md).
