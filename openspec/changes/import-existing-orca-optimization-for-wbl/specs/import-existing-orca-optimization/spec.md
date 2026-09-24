## ADDED Requirements

### Requirement: Import is a discoverable entry point independent of viewer content
Moltage SHALL expose `Projects > Import Existing Calculation... > ORCA Optimization...` and SHALL keep it enabled without any loaded molecule, geometry workspace, or existing project. The action SHALL NOT be placed in Server Settings, SHALL NOT be presented as an ordinary file-open action, and SHALL NOT require creating an empty project first.

#### Scenario: No molecule is loaded
- **WHEN** the application has no open Geometry workspace and no loaded structure
- **THEN** the `ORCA Optimization...` import action is enabled and opens the import dialog

#### Scenario: Dialog states that no calculation is rerun
- **WHEN** the import dialog opens
- **THEN** it is titled `Import Existing ORCA Optimization` and states that Moltage will not rerun ORCA or modify the source directory

#### Scenario: No server profile exists
- **WHEN** no server profile is configured
- **THEN** Moltage explicitly reports that a server must be configured first and offers the existing Server Settings entry, and SHALL NOT use a hardcoded host or path

### Requirement: Remote source selection reuses existing remote browsing
Moltage SHALL let the user select one configured server profile and type, paste, or browse one absolute remote directory, SHALL treat that value as a remote directory rather than a local path, and SHALL reuse the existing remote directory browser rather than implementing a second SSH or browsing subsystem.

#### Scenario: Path is entered manually
- **WHEN** the user types or pastes an absolute remote directory
- **THEN** the value is accepted for validation without requiring the browser

#### Scenario: Path is chosen by browsing
- **WHEN** the user chooses a directory through the existing remote browser
- **THEN** the selected absolute directory is written back into the editable path field

#### Scenario: Managed project name defaults from the source
- **WHEN** a source directory is selected
- **THEN** the editable managed project name defaults to a name derived from that directory basename, and internal identity continues to use the existing project UUID mechanism

#### Scenario: Destination is previewed read-only
- **WHEN** a server profile and managed project name are present
- **THEN** the dialog shows a read-only preview of the managed project location under that profile's configured remote project workspace, and SHALL NOT introduce a second workspace configuration

### Requirement: Result pairing is deterministic and never guessed
Moltage SHALL identify one candidate ORCA result group as a stem whose `<stem>.inp`, `<stem>.out`, `<stem>.xyz`, and `<stem>.gbw` all exist in the source directory as non-empty regular files, SHALL additionally verify through the existing parsers that the final geometry matches the input atom identity, and SHALL NOT infer pairing from unreviewed ORCA output metadata or from directory and file naming alone.

#### Scenario: Exactly one candidate group
- **WHEN** the source directory contains exactly one complete candidate group
- **THEN** Moltage selects it automatically and reports the selected filenames

#### Scenario: Several candidate groups
- **WHEN** the source directory contains more than one complete candidate group
- **THEN** Moltage lists the candidates and requires an explicit user selection before validation can succeed

#### Scenario: No candidate group
- **WHEN** no stem provides all four required files
- **THEN** Moltage reports which files are missing for the closest stems and SHALL NOT import

#### Scenario: Geometry does not belong to the input
- **WHEN** `<stem>.xyz` atom count or ordered element identity differs from the submitted coordinates of `<stem>.inp`, whether inline or read through its `*xyzfile` reference
- **THEN** Moltage rejects the import and states that the selected artifacts are not from one calculation

### Requirement: Success is proven only by existing ORCA evidence rules
Moltage SHALL determine imported optimization success through the same reviewed ORCA parsers used by Step 1, SHALL require ORCA normal termination, optimization convergence, a verified final optimized geometry, and readable charge and multiplicity, and SHALL NOT introduce a second success rule or a new ORCA output grammar.

#### Scenario: Normal termination is absent
- **WHEN** the selected `.out` does not contain the reviewed normal-termination evidence
- **THEN** validation fails with that explicit reason and `Import` stays disabled

#### Scenario: Optimization did not converge
- **WHEN** the selected `.out` reports non-convergence or does not confirm convergence
- **THEN** validation fails with that explicit reason and `Import` stays disabled

#### Scenario: Charge and multiplicity are read from the submitted input
- **WHEN** the selected `.inp` contains the literal coordinate block header, or exactly one `*xyzfile` header, with charge and multiplicity
- **THEN** Moltage records those integers as the imported scientific identity

#### Scenario: Charge and multiplicity cannot be read
- **WHEN** the selected `.inp` has neither a parseable explicit coordinate block header nor exactly one parseable `*xyzfile` header
- **THEN** validation fails with an explicit reason and SHALL NOT substitute assumed values

#### Scenario: Starting coordinates are read through an xyzfile reference
- **WHEN** the selected `.inp` reads its coordinates through one `*xyzfile <charge> <multiplicity> <path>` header whose file is a readable XYZ file
- **THEN** Moltage resolves an absolute path as written and a relative path against the source directory, writes those coordinates inline in place of only that header to form the managed `orca_opt.inp`, validates that managed input with the same parsers, and states in the validation result which coordinate file was inlined

#### Scenario: The xyzfile reference cannot be used
- **WHEN** the referenced coordinate file is missing or not a readable XYZ file, the path contains `..`, the path is the result's own `<stem>.xyz`, or the input has several `*xyzfile` headers or also an inline coordinate block
- **THEN** validation fails with that explicit reason and `Import` stays disabled

#### Scenario: Method and basis are ambiguous
- **WHEN** the input keyword line does not resolve to exactly one reviewed method and at most one reviewed basis
- **THEN** Moltage leaves method, basis, and dispersion unset, still completes validation, and the later WBL stage requires its existing manual AO mode

### Requirement: Wavefunction readiness is reported separately from optimization success
Moltage SHALL determine WBL wavefunction readiness by reusing the existing verification of the `orca_2json` utility adjacent to the server profile's recorded ORCA executable, SHALL report `ready` or `configuration required` in user-facing terms, and SHALL NOT depend on a bare `orca_2mkl` or similar command resolved through `PATH`.

#### Scenario: Conversion utility is verified
- **WHEN** the profile records an ORCA runtime whose sibling `orca_2json` verifies
- **THEN** the dialog reports WBL readiness as ready and records the verified utility path in provenance

#### Scenario: Conversion utility is unavailable
- **WHEN** no ORCA runtime is configured for the profile, or the sibling utility cannot be verified
- **THEN** the dialog reports WBL readiness as configuration required, names the server's ORCA settings as the place to fix it, and the optimization import may still be completed

#### Scenario: A Molden artifact is present in the source
- **WHEN** the source directory also contains a `.molden.input` file
- **THEN** Moltage states that it is not used as a wavefunction source and still requires the `.gbw`

### Requirement: Validation results are user-facing and gate the Import action
Moltage SHALL present validation as readable statements covering ORCA output detection, normal termination, optimization convergence, wavefunction source, optimized geometry, and WBL readiness, SHALL NOT display raw parser objects, Python exceptions, or internal field names, and SHALL enable `Import` only after validation succeeds.

#### Scenario: Validation succeeds
- **WHEN** every required fact is verified
- **THEN** each result line is shown in user-facing wording and `Import` becomes enabled

#### Scenario: Validation fails
- **WHEN** any required fact cannot be verified
- **THEN** the specific reason is shown and `Import` remains disabled

#### Scenario: Parser failure is sanitized
- **WHEN** a selected artifact is corrupt and a parser raises
- **THEN** the dialog shows a sanitized explanation of which artifact is unreadable and SHALL NOT show a traceback or internal type name

### Requirement: The source directory is a read-only origin
Moltage SHALL treat the remote ORCA optimization directory as read-only, SHALL NOT create, modify, rename, or delete any file in it, SHALL NOT generate Molden, CSV, JSON, plot, or provenance artifacts inside it, and SHALL NOT cancel any scheduler job associated with it.

#### Scenario: Successful import leaves the source unchanged
- **WHEN** an import completes successfully
- **THEN** the source directory contains exactly the same file names, contents, and count as before the operation

#### Scenario: Failed import leaves the source unchanged
- **WHEN** an import fails at any stage
- **THEN** the source directory is still unchanged and no source file was overwritten

#### Scenario: Only required artifacts are copied
- **WHEN** an import completes successfully
- **THEN** only the selected input, output, optimized geometry, and wavefunction files are copied, an `*xyzfile` input being written in its inlined form instead of copied, and the rest of the source directory is not transferred

### Requirement: Import creates an independent managed workspace without overwriting anything
Moltage SHALL create the imported project as a separate managed project under the selected server profile's configured remote project workspace using the existing managed-project directory rules, SHALL NOT create it inside the source directory, and SHALL NOT overwrite an existing project because of a name collision.

#### Scenario: Destination name already exists
- **WHEN** the deterministic managed directory name is already taken
- **THEN** Moltage claims the next deterministic candidate and SHALL NOT write into the existing directory

#### Scenario: Copied artifacts are verified
- **WHEN** required artifacts are copied into the managed workspace
- **THEN** each copy's checksum is verified against its source, an inlined `orca_opt.inp` is verified against its own bytes, and a mismatch aborts the import with an explicit reason

#### Scenario: The coordinate file changed after validation
- **WHEN** the `*xyzfile` coordinate file no longer matches its validated checksum at import time
- **THEN** the import is aborted before any managed directory is created

#### Scenario: Import fails after the directory was claimed
- **WHEN** an import fails after the managed directory was created but before the manifest was written
- **THEN** no project manifest exists, the copied artifacts and the claimed directory are removed, and an uncertain cleanup outcome is reported explicitly with its path

#### Scenario: Remote paths contain spaces
- **WHEN** a source or destination path contains spaces or other shell-significant characters
- **THEN** the operation succeeds using the project's safe path handling, and no part of the path is executed as a command

### Requirement: Imported projects use the existing ORCA managed-project state
Moltage SHALL record an imported project in the existing ORCA workflow with optimization `SUCCEEDED`, an explicit imported origin, and no WBL stage, SHALL NOT fabricate a scheduler Job ID, scheduler state, or submit script, and SHALL NOT claim that Moltage submitted the optimization.

#### Scenario: Project Manager indicators after import
- **WHEN** an imported project is listed in Project Manager
- **THEN** the first ORCA indicator is `SUCCEEDED` and the second ORCA indicator is `NOT_STARTED`

#### Scenario: No fabricated scheduler identity
- **WHEN** the imported project manifest is read
- **THEN** the optimization step has no Job ID, no scheduler type, and no submit script filename, and its result evidence records that no Moltage scheduler outcome was observed

#### Scenario: No FHI-aims or AITRANSS operations appear
- **WHEN** an imported project is selected
- **THEN** no Step 3, Step 4, self-energy, or AITRANSS action is offered, and no `control.in` or FHI-aims artifact is read

#### Scenario: Resubmission is not offered
- **WHEN** an imported project is selected in Project Manager
- **THEN** `Resubmit Optimization...` is unavailable, because Moltage did not submit that optimization

#### Scenario: A valid historical optimization is never marked failed
- **WHEN** a verified imported optimization is refreshed
- **THEN** its state remains `SUCCEEDED` and is not downgraded because no scheduler record exists

### Requirement: Imported state survives refresh and restart
Moltage SHALL preserve imported optimization success, both status indicators, and Step 2 availability across explicit Refresh and application restart, and SHALL keep older project schemas readable without fabricating imported metadata for them.

#### Scenario: Refresh preserves imported success
- **WHEN** the user refreshes an imported project
- **THEN** the optimization stays `SUCCEEDED` with imported origin and the second indicator stays `NOT_STARTED`

#### Scenario: Restart preserves imported state
- **WHEN** Moltage is restarted and the imported project is discovered again
- **THEN** the persisted state, indicators, and Step 2 availability are identical

#### Scenario: Legacy ORCA project remains readable
- **WHEN** a project manifest written by an earlier schema is read
- **THEN** it parses successfully, its optimization origin is the submitted origin, and it carries no import provenance

### Requirement: Imported provenance distinguishes external, managed, and generated artifacts
Moltage SHALL persist the source server profile identity, source remote directory, the selected input, output, optimized-geometry and wavefunction filenames with their source checksums, the absolute path and checksum of an `*xyzfile` coordinate file when one was inlined, the import timestamp, charge, multiplicity, normal-termination evidence, optimization-convergence evidence, final-geometry evidence, the imported origin, the managed workspace identity, and the detected ORCA version when the existing runtime evidence provides one, using the project's existing checksum mechanism for managed copies.

#### Scenario: External and managed artifacts are separable
- **WHEN** imported provenance is read
- **THEN** external source artifacts are recorded with their own paths and checksums, and Moltage's managed copies are recorded through the existing step input-hash mechanism

#### Scenario: An inlined input is distinguishable from its sources
- **WHEN** the imported input read its coordinates through `*xyzfile`
- **THEN** the source `.inp` checksum, the coordinate file path and checksum, and the managed `orca_opt.inp` checksum are each recorded separately, and a provenance record written before coordinate files were recorded remains readable with no coordinate file

#### Scenario: Later WBL artifacts remain separate
- **WHEN** a WBL analysis later runs on an imported project
- **THEN** its generated Molden file, user-entered WBL parameters, and WBL outputs remain recorded in the existing WBL stage evidence and are not merged into the import provenance

#### Scenario: No credentials are persisted
- **WHEN** an imported project manifest is written
- **THEN** it contains no password or other server credential

### Requirement: Imported projects enter the existing WBL pipeline unchanged
Moltage SHALL let an imported project use the existing `Calculation > ORCA > Step 2 — WBL Transmission...` entry with the existing parameter dialog, linker and contact detection, coupling and energy-window semantics, spin treatment, validation rules, result presentation, and `View WBL Transmission`, and SHALL NOT duplicate the WBL pipeline or alter its scientific model.

#### Scenario: Step 2 opens for an imported project
- **WHEN** the recovered optimized geometry of an imported project is the active Geometry workspace and Project Manager is open
- **THEN** the existing Step 2 WBL action is enabled and opens the existing settings dialog

#### Scenario: Linker detection uses the imported optimized structure
- **WHEN** the Step 2 dialog opens for an imported project
- **THEN** contact and linker prefill are derived from the imported optimized geometry through the existing detector

#### Scenario: WBL uses the verified conversion utility
- **WHEN** WBL runs for an imported project
- **THEN** it converts the managed `.gbw` copy through the same verified adjacent ORCA utility used by ordinary projects and runs no ORCA SCF or optimization

#### Scenario: WBL results are written to the managed workspace
- **WHEN** a WBL analysis for an imported project succeeds
- **THEN** its artifacts and provenance are written under the managed project workspace, the second indicator becomes `SUCCEEDED`, `View WBL Transmission` becomes available, and nothing is written to the source directory

### Requirement: Import never starts a calculation and never blocks the interface
Moltage SHALL perform every remote listing, validation, verification, and copy operation off the GUI thread with a bounded timeout and explicit cancellation, SHALL NOT start WBL automatically after import, SHALL NOT rerun SCF, optimization, or frequency, and SHALL NOT wait for an unbounded remote operation at application exit.

#### Scenario: Import does not start WBL
- **WHEN** an import completes successfully
- **THEN** no WBL stage exists and no analysis was started

#### Scenario: Repeated activation cannot duplicate a project
- **WHEN** the user activates `Import` while an import is already running
- **THEN** the action is disabled, progress is shown, and exactly one managed project is created

#### Scenario: The user cancels a running operation
- **WHEN** the user cancels validation or import
- **THEN** the remote session is released without blocking the interface, and no partially created managed project remains

#### Scenario: The dialog is closed while work is in flight
- **WHEN** the dialog is destroyed before a worker finishes
- **THEN** the late result is discarded without calling into destroyed widgets

#### Scenario: The remote operation fails or times out
- **WHEN** the directory does not exist, is not readable, the connection fails, or the operation times out
- **THEN** Moltage reports that specific reason, creates no managed project, and leaves no empty remote directory
