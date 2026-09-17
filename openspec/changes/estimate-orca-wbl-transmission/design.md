## Context

ORCA optimization projects persist the exact runtime, settings, optimized geometry hashes and optional `.gbw` readiness. Standard Molden contains MO coefficients but does not provide the AO overlap matrix required by the approved symmetric Löwdin definition, while ORCA 6 `orca_2json` can export basis, MO coefficients and one-electron integral `S`. This change therefore treats verified JSON plus overlap as the authoritative analysis input and treats any Molden output only as an auxiliary provenance artifact.

## Goals / Non-Goals

**Goals:**

- Add a user-authorized ORCA second stage that uses existing optimization evidence without new SCF/optimization.
- Keep linker assumptions, numeric user parameters, basis mapping and every generated artifact explicit and reproducible.
- Calculate one spin-degenerate total from each spatial MO for restricted multiplicity-1 evidence; retain alpha/beta/raw-sum transmission for unrestricted open-shell evidence.
- Preserve optimization/frequency state and all existing FHI transmission behavior.

**Non-Goals:**

- Do not implement explicit Au surface Green functions, DFT-NEGF, self-energy extraction, calibration, interference between nonorthogonal resonances, or a general wavefunction-analysis workbench.
- Do not invent `Gamma_0`, basis shell classifications, chemical planarity thresholds or fallback projections.
- Do not install ORCA, connect to real HPC during apply, or claim real-installation compatibility from offline tests.

## Decisions

### 1. Evidence extraction is capability-verified and overlap-gated

Resolve `orca_2json` as a sibling of the persisted absolute ORCA executable, require an absolute canonical executable file, and generate a fixed config requesting basis, MO coefficients and `S`. Run it only against the existing `.gbw`; hash every downloaded input/output and require its charge/multiplicity evidence to match the submitted optimization settings before selecting a spin treatment. Unknown JSON layout, missing/invalid overlap, unsupported spin layout, charge/multiplicity mismatch or dimension mismatch fails explicitly. Never fall back to raw Molden coefficient squares.

If a sibling `orca_2mkl` is present it may create a Molden artifact for inspection; absence does not invalidate the authoritative JSON route. A user-supplied Molden file requires a separately supplied compatible overlap artifact before calculation.

### 2. Use one explicit, versioned projection model

For each represented orbital set, calculate `d = S^(1/2) C` from a validated symmetric positive-definite overlap matrix. For contact subspace projector `P_X`, use `w_X,n = d_n^T P_X d_n` and `Gamma_X,n = Gamma_0,X w_X,n`.

Automatic contact modes are:

- `SH`: contact direction follows the terminal S-H bond; use directional valence S `3p`.
- `SMe`: contact direction is the normalized outward bisector opposite the two S-C unit vectors; use directional valence S `3p`; degenerate geometry requires manual direction.
- pyramidal `NH2`: direction is opposite the sum of the three N-neighbor unit vectors; use valence N `2s` plus directional N `2p`.
- numerically planar `NH2`: use the local neighbor-plane normal and directional N `2p` only. Ambiguous near-planar chemistry is not classified through an invented chemical threshold and must be manually overridden.
- `Pyridine`: use the outward in-plane bisector opposite the two adjacent ring-carbon unit vectors; use valence N `2s` plus directional N `2p`, excluding the ring-normal pi component.

An explicit linker-Au bond may supply direction, but does not supply `Gamma_0`. Manual mode stores the selected AO indices and direction explicitly.

Automatic shell mapping is supported only for the reviewed `DEF2-SVP`, `DEF2-TZVP`, `DEF2-TZVPP`, and `DEF2-QZVP` identities. It selects the reviewed outer N `2s/2p` or S `3p` contractions and never selects S core `2p`. Composite/custom/unknown basis requires a manual subspace; there is no exponent-threshold fallback.

### 3. Use an independent-resonance WBL hypothesis without clipping

For every parsed MO in the applicable orbital set:

`T_n(E) = Gamma_L,n Gamma_R,n / ((E - epsilon_n)^2 + ((Gamma_L,n + Gamma_R,n)/2)^2)`

For restricted multiplicity-1 evidence, calculate the spatial-orbital set once and expose that curve directly as spin-degenerate `T_total` in the conventional `G_0 = 2e^2/h` normalization. Do not create duplicate Alpha/Beta curves or double the result. The formula bounds each individual orbital channel by `T_n <= 1`; the independent sum is not globally clipped because multiple overlapping orbital channels may legitimately sum above one within this hypothesis.

For verified unrestricted open-shell evidence, sum every MO separately to form `T_alpha` and `T_beta`, and retain the raw spin sum `T_total = T_alpha + T_beta` in the `e^2/h`-per-spin convention. A multiplicity-1 result with unrestricted evidence fails explicitly rather than silently discarding Beta information. Report the largest two total spatial-MO contributions for closed shell or the largest two contributions per spin for open shell.

Energy values and `Gamma_0` are eV. The scan is user-provided as `E-E_F` minimum, maximum and step plus absolute `E_F`; validation requires finite values and an exact bounded deterministic grid.

### 4. Persist parameters, progress and summaries; store arrays as artifacts

Raise the project manifest schema and add optional `ORCA_WBL_TRANSMISSION` after optimization and before optional frequency. Existing ORCA projects with optimization/frequency remain valid and are not rewritten merely by loading. A confirmed calculation first persists the WBL stage as `RUNNING`, then changes it to `SUCCEEDED` with settings, source evidence hashes, model identity/status, tool evidence and result summary/hashes; a definite failure becomes `FAILED` with an explicit diagnostic. CSV/JSON/SVG arrays remain artifacts in `wbl/`.

WBL remains a local/remote post-processing operation rather than a scheduler Job. Its state never rewrites optimization success, and recovery does not attempt scheduler queries for the WBL stage.

### 5. Keep GUI orchestration thin

ORCA projects expose exactly two Project Manager indicators: optimization and WBL. Frequency is an optional analysis action, not a third project lamp. WBL and frequency start only from a recovered ORCA Geometry workspace under `Calculation > ORCA`; they do not occupy the Project Manager footer. The WBL dialog is enabled only when optimization is `SUCCEEDED` and `.gbw` evidence is ready.

The dialog reuses the existing domain linker detector and recovered connectivity: exactly two unambiguous supported linkers prefill the two contacts in deterministic atom order, and changing a contact refreshes its detected linker. A `Manual select in viewer` choice temporarily hides the dialog, labels every atom with its one-based number, and returns only a supported S/N atom selected in the recovered read-only viewer. The GUI does not duplicate chemical detection logic.

The main form contains only contact, detected linker, explicit `Gamma_0`, and energy-window controls. A checked `Same as left` control mirrors the user-entered left `Gamma_0`; it is convenience, not a Moltage numeric coupling default. Parameter provenance, projection override, optional direction, and manual AO indices live under Advanced with descriptive labels. Automatic projection text shows the actual derived valence model (for example S valence `3p` or N valence `2s/2p`) instead of only `AUTO`.

The editable Au `E_F = -5.1 eV` starting value is a versioned Moltage `HYPOTHESIS`, using the vacuum-zero sign convention and the commonly tabulated elemental/polycrystalline Au work function as background rather than claiming a universal surface value. The UI also starts with `E-E_F = [-5, 5] eV` and `0.01 eV` sampling. Every accepted value remains persisted in the existing settings object; reopening results never reinterprets these defaults.

The application service extracts remote evidence, performs the deterministic local analysis, uploads artifacts atomically and persists the stage. A separate ORCA WBL viewer displays the applicable total-only or alpha/beta/raw-sum curves and summaries without changing the FHI transmission view. Base-10 logarithmic decades use typographic `10ⁿ` labels rather than E notation, including current-view export. Existing current-view export captures this widget unchanged. The completed view is reachable from `Calculation > ORCA` and the WBL status indicator.

Preparation, worker progress, persisted `RUNNING`/terminal state, and pre-stage failures are also routed to the originating ORCA Geometry workspace status area. This is presentation-only propagation; the persisted project stage remains authoritative.

### 6. Keep ORCA recovery and resubmission independent from FHI artifacts

ORCA geometry recovery/view parses only hash-bound `orca_opt.inp` and, when verified, `orca_opt.xyz`; it never requests `control.in`, `geometry.in` or `geometry.in.next_step`. A queued, running, failed or otherwise incomplete optimization may therefore reopen its submitted structure read-only and prefill its persisted ORCA settings for a new managed-project submission.

Before that replacement submission is created, Moltage reloads the exact source project and asks its configured scheduler about the exact persisted Job ID. A confirmed queued/running Job is cancelled through the verified absolute scheduler command and recorded as `CANCEL_REQUESTED`; a terminal Job needs no cancellation. Missing identity, accounting lag, unrecognized status or unknown cancellation outcome blocks the new submission rather than guessing.

## Risks / Trade-offs

- ORCA utility JSON layouts can differ by version → support only tested explicit layouts and report unknown layouts as unverified.
- Directional linker models are hypotheses rather than observable couplings → persist exact model ID, vectors, subspaces and provenance; never label results DFT-NEGF.
- A local numerical planarity test does not define chemical near-planarity → only classify numerically coplanar input automatically and require an explicit override for ambiguous cases.
- Independent resonance sums can exceed a single-channel bound and omit interference → do not conceal this through clipping; state the model limitation in output/UI.
