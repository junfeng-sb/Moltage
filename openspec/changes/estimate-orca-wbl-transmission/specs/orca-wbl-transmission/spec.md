## ADDED Requirements

### Requirement: WBL is an explicit post-optimization ORCA stage
Moltage SHALL offer ORCA WBL transmission only after verified optimization success and `.gbw` readiness, SHALL require a separate user action, and SHALL NOT run a new SCF or optimization calculation.

#### Scenario: User starts WBL after optimization
- **WHEN** an ORCA optimization is `SUCCEEDED`, its `.gbw` hash is present, and the user confirms complete WBL settings
- **THEN** Moltage persists the WBL stage as `RUNNING`, extracts existing wavefunction evidence, and records `SUCCEEDED` only after deterministic analysis and artifact persistence succeed

#### Scenario: WBL processing fails
- **WHEN** a started WBL analysis encounters invalid evidence or another definite processing failure
- **THEN** Moltage records the second stage as `FAILED` with an explicit diagnostic and leaves optimization success unchanged

#### Scenario: WBL is not ready
- **WHEN** optimization or `.gbw` evidence is incomplete
- **THEN** Moltage disables or rejects WBL calculation with an explicit evidence reason and leaves optimization state unchanged

### Requirement: Löwdin analysis requires authoritative overlap evidence
Moltage SHALL obtain basis, MO coefficients, spin-specific energies/occupancies and AO overlap from a verified ORCA evidence-conversion utility, and SHALL reject missing, malformed or dimensionally inconsistent evidence.

#### Scenario: Molden lacks overlap
- **WHEN** only a Molden file is available and no compatible AO overlap artifact is supplied
- **THEN** Moltage reports that Löwdin analysis evidence is incomplete and SHALL NOT substitute coefficient-square populations

#### Scenario: Utility capability is unavailable
- **WHEN** the sibling `orca_2json` executable cannot be canonically verified
- **THEN** Moltage reports the missing capability and SHALL NOT search arbitrary filesystem paths or invoke a bare command

#### Scenario: Converted spin identity differs from submitted optimization
- **WHEN** converted wavefunction charge or multiplicity differs from the persisted optimization settings
- **THEN** Moltage rejects the WBL analysis explicitly before selecting closed-shell or spin-resolved treatment

### Requirement: Contact projections are explicit and valence-only
Moltage SHALL calculate contact weights through the approved direction-aware N/S valence subspaces, persist the resulting direction/subspace provenance, and require manual configuration when automatic evidence is unsupported or ambiguous.

#### Scenario: Sulfur valence projection
- **WHEN** a reviewed def2 basis and SH/SMe contact are selected
- **THEN** the automatic projector includes directional S `3p` valence functions and excludes S core `2p`

#### Scenario: Unsupported basis
- **WHEN** automatic projection is requested for a composite, custom or unknown basis identity
- **THEN** Moltage rejects automatic projection and requests a manual AO subspace without exponent-based guessing

### Requirement: Gamma parameters have no Moltage numeric default
Moltage SHALL require finite positive left/right `Gamma_0` values in eV for calculation and SHALL persist whether each value is user `HYPOTHESIS` or `CALIBRATED`; unresolved library entries SHALL remain `DATA_NEEDED` and not run.

#### Scenario: Gamma is missing
- **WHEN** either contact has no numeric `Gamma_0`
- **THEN** the WBL calculation cannot start and the UI identifies the missing parameter

#### Scenario: Right coupling mirrors user input
- **WHEN** `Same as left` is enabled and the user changes the left `Gamma_0`
- **THEN** the right value mirrors that user-entered value and remains read-only until the option is disabled
- **AND** Moltage does not supply a numeric linker coupling value by itself

### Requirement: WBL contact setup reuses molecular linker evidence
Moltage SHALL reuse the existing domain linker detector and recovered connectivity for contact/linker suggestions, SHALL keep ambiguous or unsupported detection explicit, and SHALL allow a contact atom to be selected from the numbered molecular viewer without duplicating chemical recognition in the GUI.

#### Scenario: Exactly two supported linkers are detected
- **WHEN** the recovered optimized structure contains exactly two unambiguous supported WBL linker candidates
- **THEN** the dialog prefills the two contact atoms in deterministic atom order and displays each detected linker

#### Scenario: User selects a contact in the viewer
- **WHEN** the user chooses `Manual select in viewer` for a contact
- **THEN** Moltage temporarily hides the settings dialog, labels all displayed atoms with one-based numbers, and fills the contact from a clicked supported S/N atom
- **AND** leaving or cancelling selection restores the dialog without altering the structure

#### Scenario: Automatic projection is shown
- **WHEN** a contact/linker has sufficient geometry evidence for automatic projection
- **THEN** the Advanced control identifies the resolved valence subspace and orientation model rather than displaying only `AUTO`

### Requirement: WBL energy defaults are explicit editable model inputs
Moltage SHALL initialize new WBL settings with a versioned editable Au `E_F` hypothesis of `-5.1 eV`, a relative energy window of `-5.0` to `+5.0 eV`, and a `0.01 eV` sampling interval, SHALL display eV units in numeric controls, and SHALL persist the accepted numeric values exactly through the existing settings model.

#### Scenario: New WBL dialog opens
- **WHEN** the user opens WBL settings for an eligible ORCA optimization
- **THEN** the form displays the versioned energy defaults with formal `E_F` notation and accepts numeric input without requiring typed unit text
- **AND** explanatory text identifies the Au value as an editable model starting point rather than a universal physical constant

### Requirement: Every MO contributes under an explicit spin treatment
Moltage SHALL evaluate the approved independent-resonance formula for every parsed MO on the exact user energy grid, SHALL select closed-shell or spin-resolved output only from verified ORCA multiplicity/spin evidence, and SHALL persist that treatment with the result.

#### Scenario: Complete open-shell calculation
- **WHEN** valid unrestricted evidence and WBL settings are supplied
- **THEN** every alpha and beta MO is included, raw `T_total = T_alpha + T_beta` is retained in the `e^2/h`-per-spin convention, and CSV/JSON/SVG plus per-spin top-two summaries are generated

#### Scenario: Restricted multiplicity-1 wavefunction
- **WHEN** valid restricted evidence with multiplicity 1 is supplied
- **THEN** Moltage evaluates every spatial MO exactly once and emits only spin-degenerate `T_total` in the conventional `G_0 = 2e^2/h` normalization
- **AND** no Alpha/Beta curves or summaries are fabricated, and each individual orbital-channel formula remains bounded by `T_n <= 1` without clipping the multi-orbital sum

#### Scenario: Unrestricted multiplicity-1 evidence
- **WHEN** ORCA identifies multiplicity 1 but supplies unrestricted Alpha/Beta evidence
- **THEN** Moltage rejects closed-shell treatment explicitly rather than discarding Beta evidence or guessing that the electrons are fully paired

### Requirement: Logarithmic decades use typographic power notation
Moltage SHALL render base-10 logarithmic-axis decade labels as `10ⁿ` and SHALL NOT expose E notation such as `1E-03` on transmission displays or their current-view exports.

#### Scenario: WBL result is displayed or exported
- **WHEN** a verified WBL result uses the default logarithmic transmission axis
- **THEN** visible decade labels use powers of ten with superscript exponents

### Requirement: Results remain a traceable WBL hypothesis
Moltage SHALL hash its source/output artifacts and persist model, contact, basis mapping, tool and parameter provenance. Product text SHALL identify the result as linker-parameterized WBL `HYPOTHESIS`, not explicit Au-molecule-Au DFT-NEGF.

#### Scenario: Result is reopened
- **WHEN** a completed WBL project is refreshed or viewed
- **THEN** Moltage reconstructs the summary/curves from persisted artifacts without reinterpreting current GUI settings or changing previous ORCA stages

### Requirement: ORCA project interaction is stage-specific
Moltage SHALL display exactly two ORCA project status indicators for optimization and WBL, SHALL expose WBL/frequency submission from the recovered ORCA Geometry workspace rather than the Project Manager footer, and SHALL expose completed WBL transmission from the second indicator and `Calculation > ORCA`.

#### Scenario: Optional frequency exists
- **WHEN** an ORCA project contains a frequency stage but no WBL stage
- **THEN** Project Manager still displays exactly two indicators and the second WBL indicator remains `NOT_STARTED`

#### Scenario: WBL completes
- **WHEN** verified WBL artifacts are persisted
- **THEN** the second indicator becomes successful and both supported View Transmission actions open the same verified alpha/beta/total presentation

#### Scenario: WBL is launched from a Geometry workspace
- **WHEN** WBL preparation, processing, success, or failure occurs
- **THEN** the originating ORCA Geometry workspace displays the latest explicit operation status without requiring the user to reopen Project Manager

#### Scenario: Result presentation fails after WBL succeeds
- **WHEN** WBL success has been persisted but assembling its local result presentation fails
- **THEN** the second indicator remains successful, Moltage reports the presentation error, and the optimization/WBL states are not reverted

#### Scenario: ORCA project is selected
- **WHEN** the selected project uses the ORCA workflow
- **THEN** the Project Manager footer hides FHI-only Step-3 resubmission, Step-4 self-energy retry, and FHI transmission actions rather than displaying them disabled
- **AND** selecting an FHI project restores those actions with their existing eligibility rules

### Requirement: ORCA geometry recovery has no FHI file dependency
Moltage SHALL recover/view ORCA submitted and optimized structures only from hash-bound ORCA-native artifacts and SHALL NOT request FHI-aims `control.in`, `geometry.in` or `geometry.in.next_step` for an ORCA workflow.

#### Scenario: Optimization is still running
- **WHEN** a managed ORCA optimization has accepted `orca_opt.inp` but no verified final geometry
- **THEN** Moltage can reopen the submitted molecular structure read-only without reading any FHI-aims input file

### Requirement: ORCA optimization can be safely resubmitted
Moltage SHALL allow a recovered incomplete ORCA optimization to prefill its persisted settings for a new managed-project submission and SHALL revalidate the source Job before that new submission.

#### Scenario: Prior Job is active
- **WHEN** the exact persisted source Job ID is authoritatively queued or running
- **THEN** Moltage cancels that exact Job through the verified configured scheduler command, persists `CANCEL_REQUESTED`, and only then submits the new optimization project

#### Scenario: Prior Job state is uncertain
- **WHEN** the source Job identity, scheduler state or cancellation outcome cannot be verified authoritatively
- **THEN** Moltage does not submit the replacement Job and reports the unresolved condition explicitly
