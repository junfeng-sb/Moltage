## ADDED Requirements

### Requirement: WBL results use the shared transmission presentation
Moltage SHALL present a verified ORCA WBL result with the same reusable transmission presentation used by the AITRANSS result view, including editable X/Y axis ranges, titles and typography, the explicit tick model, optional mirrored top/right axes, per-curve style controls, canvas and export-size controls, curve probing, and Reset View. Presentation changes SHALL NOT alter persisted WBL artifacts, hashes, or the scientific result.

#### Scenario: View settings are opened for a WBL result
- **WHEN** a WBL result tab is active and the user chooses `Settings > View...`
- **THEN** Moltage opens the five-page transmission settings dialog for that result instead of reporting that styling is fixed

#### Scenario: Axis range is edited
- **WHEN** the user applies a new finite X range or a positive Y range
- **THEN** the WBL chart redraws within that range, the persisted energy and transmission values remain unchanged, and Reset View restores the frozen default ranges

#### Scenario: Spin-resolved curves are styled individually
- **WHEN** a verified unrestricted WBL result is displayed
- **THEN** the Curve page lists the Alpha, Beta, and spin-sum curves separately and applies each curve's own color, width, line style, and legend label

#### Scenario: Closed-shell result exposes one curve
- **WHEN** a verified restricted multiplicity-1 WBL result is displayed
- **THEN** the Curve page lists only the spin-degenerate total curve and no Alpha or Beta curve is fabricated

#### Scenario: Sample is probed on a WBL curve
- **WHEN** the pointer approaches a displayed positive sample
- **THEN** Moltage identifies the nearest sample of the nearest curve and reports that curve's name with its energy and transmission without changing the data

### Requirement: Fermi energy is displayed as a typographic subscript
Moltage SHALL display the Fermi reference as `E` with a subscript `F` on WBL transmission presentations and their exported plot artifact, and SHALL NOT display `E_F` as ASCII text in those presentations.

#### Scenario: WBL chart is displayed
- **WHEN** a verified WBL result is displayed
- **THEN** the energy axis title and the Fermi annotation use a subscript `F`, and the Fermi reference line contributes no `E_F` legend entry

#### Scenario: WBL plot artifact is rendered
- **WHEN** Moltage renders the deterministic WBL plot artifact
- **THEN** its energy axis title renders `F` as a typographic subscript rather than as `E_F`
