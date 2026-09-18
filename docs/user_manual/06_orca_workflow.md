# 15. ORCA Optimization, Frequency Verification, and WBL Transmission

[Back to the manual index](README.md)

## 15.1 What the ORCA workflow does

Moltage treats ORCA as a workflow independent of FHI-aims. An ORCA project has
two progress indicators in Project Manager:

```text
Step 1 — Optimization
    ↓ recover and verify the optimized geometry and wavefunction
Step 2 — WBL Transmission
```

Frequency verification is an optional analysis launched after geometry recovery.
It is deliberately not a third project indicator. ORCA stages neither require nor
read `control.in`, `geometry.in`, FHI-aims species definitions, or AITRANSS files.

Before starting, configure and verify both the scheduler and ORCA runtime on the
selected server. See [Servers and Schedulers](03_servers_and_schedulers.md).

## 15.2 Start an ORCA optimization

Open a molecular Geometry tab, then choose **Calculation → ORCA → Step 1 —
Optimization...**. The dialog shows the server name, exact ORCA executable, and
verified version before any scientific settings.

### ORCA input

| Setting | Purpose | Initial behavior |
|---|---|---|
| **Method** | Electronic-structure method. | No method is selected automatically. |
| **Basis** | Orbital basis for methods that require one. | No basis is selected automatically. Composite methods disable this field. |
| **Dispersion** | `NONE`, `D3ZERO`, `D3BJ`, or `D4` where the method permits it. | Must be reviewed by the user. |
| **Optimization** | Geometry convergence: `LOOSEOPT`, `OPT`, `TIGHTOPT`, or `VERYTIGHTOPT`. | `OPT` |
| **Coordinates** | Redundant internal or Cartesian optimization coordinates. | `REDUNDANT` |
| **SCF convergence** | `DEFAULT`, `STRONGSCF`, `TIGHTSCF`, or `VERYTIGHTSCF`. | `DEFAULT` |
| **Charge** | Molecular net charge. | `0`; this is only an editable starting value. |
| **Multiplicity** | Spin multiplicity, 2S + 1. | `1`; the user must set the correct electronic state. |

Moltage checks basic consistency, including electron-count/multiplicity parity,
but that check cannot determine the physically correct charge or spin state.

The maintained method list includes BP86, BLYP, PBE, TPSS, B3LYP, PBE0, M062X,
TPSSH, B97-3C, PBEh-3c, r2SCAN-3c, and ωB97M-D4Rev. Only methods supported by
the verified ORCA version are offered. The exposed def2 basis choices are
def2-SVP, def2-TZVP, def2-TZVPP, and def2-QZVP for supported elements. Moltage
does not silently substitute another method or basis.

### Job resources

**Nodes**, **ORCA processes**, **Maximum runtime**, and **Scheduler memory** start
from the selected server profile and can be changed for this job. **Write
`%maxcore`** is optional. If it is enabled, the value is memory in MB *per ORCA
process* and is advisory rather than a scheduler memory limit. If it is disabled,
Moltage omits `%maxcore`; ORCA then uses its own documented default. Omitting it is
valid and does not by itself make a calculation fail.

Moltage displays a memory advisory when the requested process memory conflicts
with the scheduler allocation. The user remains responsible for selecting values
appropriate to the molecule, method, and cluster.

### Confirm and submit

Choose **Continue**, review the generated `orca_opt.inp` and scheduler script,
then explicitly submit. The confirmation is for a real remote job. Moltage invokes
the verified absolute ORCA driver; it does not rely on a bare `orca`, `srun`, or
`mpirun` found by chance in `PATH`.

Typical project-root files include:

- `orca_opt.inp` — submitted ORCA input;
- `submit.orca.sh` — scheduler script;
- `orca_opt.out` — ORCA output;
- `orca_opt.scheduler.out` — scheduler output;
- `orca_opt.gbw` — wavefunction used by Step 2;
- `orca_opt.xyz` — optimized Cartesian geometry when produced by ORCA.

The actual set of auxiliary ORCA files depends on the selected method and ORCA
version.

## 15.3 Monitor, recover, and resubmit Step 1

Use **Projects → Project Manager...** and **Refresh Status**. A green Step-1
indicator requires more than a scheduler `COMPLETED` state: Moltage verifies ORCA
normal termination, optimization convergence, the hash-bound submitted input, and
a valid ordered final geometry.

Choose **Open / Recover** to open the best supported evidence:

- a verified optimized geometry when recovery is complete;
- otherwise, the submitted input geometry read-only when available.

Once a verified optimized Geometry tab is active, the ORCA Step-2 and Frequency
entries become available under the **Calculation → ORCA** submenu.

**Resubmit Optimization...** opens a new project initialized from the prior
structured settings. If the preceding job is still active, Moltage first refreshes
that exact scheduler job and cancels only a verified active job. If its state is
ambiguous, resubmission is stopped rather than risking a duplicate calculation.

## 15.4 Optional frequency verification

After recovering a verified optimized structure, choose **Calculation → ORCA →
Run Frequency...**. Frequency analysis is separate from WBL and is never started
automatically.

The method, basis, dispersion, charge, and multiplicity are inherited read-only
from optimization. Select one mode:

- **FREQ — analytical Hessian** when the maintained method/runtime evidence
  supports it;
- **NUMFREQ — numerical Hessian**, which performs many displaced-gradient
  calculations and can be substantially more expensive.

Set the frequency job's resources and optionally enable `%maxcore`. Completion
reports ORCA-reported imaginary-mode annotations. A calculation with no reported
imaginary mode is useful local-minimum evidence, but it does not prove that the
structure is the global minimum.

## 15.5 Prepare ORCA WBL transmission

Choose **Calculation → ORCA → Step 2 — WBL Transmission...** from a recovered
ORCA Geometry tab. This stage analyzes the existing wavefunction. It does **not**
rerun SCF or geometry optimization and does not use Multiwfn.

Prerequisites are:

- verified Step-1 completion and geometry;
- the exact recorded `orca_opt.gbw`;
- a compatible `orca_2json` adjacent to the verified ORCA executable;
- supported complete basis, MO, spin, and overlap evidence.

Moltage runs the conversion utility against the existing GBW, reads the resulting
wavefunction evidence, computes the WBL model locally, and uploads deterministic
result/provenance artifacts to the project's `wbl/` directory. No scheduler job is
submitted for this step.

## 15.6 WBL contact settings

The basic view keeps only inputs needed for the current calculation.

### Contact atoms and linker detection

When exactly two unambiguous supported linker contacts are detected, Moltage fills
the left and right contact atoms automatically. Changing a contact updates its
detected linker. Supported linker kinds are SH, SMe, NH2, and pyridine.

To select manually, choose **Manual select in viewer...**. The settings dialog is
temporarily hidden, atom numbers are shown in the molecule viewer, and clicking a
supported S or N atom fills the field. The stable atom index is used; arbitrary
mouse-space coordinates are not accepted.

### Γ₀ coupling

Enter a positive **Γ₀** in eV. Moltage intentionally supplies no numerical Γ₀
default because this is a model coupling, not a universal linker constant. The
right value initially has **Same as left** enabled; clear it only when separate
couplings are intended. The numeric editor displays the `eV` suffix and rejects
letters.

### Energy window and sampling

| Setting | Initial value | Meaning |
|---|---:|---|
| **Au Fermi level, E<sub>F</sub>** | −5.1 eV | Editable generic Au/vacuum-reference hypothesis, not a universal surface value. |
| **Lower energy, E − E<sub>F</sub>** | −5 eV | Start of the relative energy window. |
| **Upper energy, E − E<sub>F</sub>** | +5 eV | End of the relative energy window. |
| **Sampling interval** | 0.01 eV | Energy spacing used for the calculated curve. |

Use the same energy reference, window, sampling, and coupling definitions when
comparing multiple molecules or charge/spin states.

## 15.7 Advanced WBL contact settings

Choose **Advanced contact settings...** only when the automatic interpretation is
inadequate.

- **Linker override** changes an unavailable or ambiguous automatic linker
  assignment.
- **Γ₀ evidence** records whether the entered value is a user-supplied
  `HYPOTHESIS` or is `CALIBRATED` against external data. It changes the provenance
  label, not the WBL formula.
- **Contact orbital projection** controls the Löwdin contact subspace. `Auto`
  expands to the actual selected model after the contact is known.
- **Projection direction override** accepts an optional molecular-XYZ unit
  direction (`x, y, z`). Leave it empty to use the geometry-derived direction.
- **Manual AO numbers** accepts 1-based, comma-separated AO indices only in the
  manual mode.

Automatic sulfur projection uses valence S 3p functions along the contact
direction; it does not mix in inner-shell S 2p functions. Automatic nitrogen
projection uses the N 2s/2p valence space along the lone-pair/contact direction,
or the N 2p direction normal to a detected local plane when applicable. These are
directional orbital projectors, not a bond-energy calculation.

## 15.8 Start and monitor Step 2

Choose **Start Step 2**. The application reports conversion, wavefunction reading,
calculation, upload, and verification progress. Project Manager immediately shows
the second ORCA indicator as active; success turns it green and enables **View WBL
Transmission**. A failure turns it red and preserves an explicit diagnostic.

The generated result set is:

- `orca_wbl_transmission.csv` — complete energy/transmission table;
- `orca_wbl_result.json` — settings, contact definitions, contributions, tool
  evidence, and artifact provenance;
- `orca_wbl_transmission.svg` — deterministic plot artifact.

All molecular orbitals in the verified evidence contribute to the curve. The JSON
also records the leading orbital contributions at E<sub>F</sub>. Result hashes are stored
in the project manifest and verified before later display.

![ORCA WBL result view showing logarithmic transmission curves](../images/readme/orca-wbl-transmission-synthetic.png)

**Figure 10.** ORCA WBL result view with logarithmic transmission curves. The
displayed data are illustrative rather than measured or production-HPC results.

## 15.9 Interpret the WBL result

For a verified restricted multiplicity-1 wavefunction, Moltage calculates one
closed-shell total transmission. Conventional spin degeneracy is included through
the conductance quantum G<sub>0</sub> = 2e²/h, so one orbital channel is bounded by T = 1;
duplicate Alpha and Beta curves are not shown.

For verified unrestricted open-shell evidence, Moltage shows Alpha, Beta, and the
raw spin sum. Alpha and Beta are per-spin transmissions in an e²/h convention;
when reporting conductance in units of G<sub>0</sub>, use
G/G<sub>0</sub> = (T<sub>α</sub> + T<sub>β</sub>) / 2.

The vertical axis is base-10 logarithmic and uses typographic powers such as
10<sup>−3</sup>.
Non-positive samples are not replaced with an artificial floor. The model remains
a linker-parameterized independent-resonance WBL **HYPOTHESIS**. It is not an
explicit Au–molecule–Au DFT-NEGF calculation and should not be described as one.

[Next: Analysis and results](07_analysis_and_results.md)
