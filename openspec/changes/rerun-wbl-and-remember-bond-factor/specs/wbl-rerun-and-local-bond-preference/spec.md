## ADDED Requirements

### Requirement: Explicit WBL replacement
Moltage SHALL allow a completed WBL Step 2 to run again with edited parameters in the same project, while preserving the verified optimization.

#### Scenario: Successful replacement
- **WHEN** a user confirms new settings for the current successful WBL result
- **THEN** Step 2 runs once, publishes verified replacement artifacts and settings, and displays the replacement curve/export
- **AND** optimization and frequency evidence remain unchanged

#### Scenario: Cancel, failure or conflicting state
- **WHEN** setup is cancelled, the run fails, or current result evidence no longer matches the confirmed result
- **THEN** no unconfirmed result replaces the accepted result
- **AND** failures are explicit; known failures restore prior results, unknown remote outcomes retain evidence and stop instead of guessing

#### Scenario: Active calculation
- **WHEN** Step 2 is already running
- **THEN** a duplicate WBL run is rejected

### Requirement: Local bond threshold persistence
Moltage SHALL remember only the accepted Bond Detection factor per user, preserving the existing formula, value limits, theme and lighting.

#### Scenario: Restart and migration
- **WHEN** the user confirms a valid factor and restarts Moltage
- **THEN** the restored factor is used for local inferred bonds and subsequent WBL setup
- **AND** older preference files without this setting retain 1.1 until explicitly changed

#### Scenario: Preview, cancel and invalid storage
- **WHEN** the dialog is cancelled, storage fails, or a saved factor is malformed
- **THEN** preview is not silently committed; Cancel preserves saved values, write failure restores the original factor/graph, and malformed data is reported
