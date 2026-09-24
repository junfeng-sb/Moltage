## ADDED Requirements

### Requirement: NCS legacy all-p comparison
Moltage SHALL resolve NCS Auto to the terminal sulfur's complete p-type Löwdin subspace, without radial-shell filtering or directional projection, and SHALL record the resolved legacy mode and AO indices. The result SHALL remain a HYPOTHESIS with explicit user couplings.

#### Scenario: NCS automatic calculation
- **WHEN** a valid terminal S contact and complete p AO evidence are supplied
- **THEN** every p AO on that S contributes its squared Löwdin coefficient and no other atom or angular type contributes
- **AND** other linker defaults and explicit directional/manual overrides retain their behavior

#### Scenario: Conflicting direction override
- **WHEN** an all-p NCS contact also supplies a direction override
- **THEN** Moltage rejects the conflicting input and requests an explicit directional mode rather than silently ignoring it

### Requirement: Data-backed WBL report presentation
Moltage SHALL display a near-square WBL plot with red Alpha and blue Beta curves, a distinguishable raw spin sum, and a model/leading-orbital sidebar reconstructed from verified current results. Closed-shell results SHALL retain only the spin-degenerate total.

#### Scenario: Leading orbital markers
- **WHEN** a result supplies model settings and top-orbital evidence
- **THEN** the sidebar shows E_F, user coupling/status, all-MO counts and the two largest individual contributions per available spin
- **AND** triangle markers locate their MO energies on the corresponding full curve within the visible range
- **AND** numbers, units, MO indexing convention and model limitations are explicit

#### Scenario: Existing controls and exports
- **WHEN** the user adjusts axes, curves, canvas size or exports transmission data
- **THEN** existing controls and lossless full-grid TXT export remain available, image export includes the report sidebar, and source result artifacts are unchanged

#### Scenario: Older result without optional report evidence
- **WHEN** otherwise valid historical presentation data lacks model or orbital detail
- **THEN** Moltage still displays the verified curves and explicitly marks missing detail unavailable instead of guessing
