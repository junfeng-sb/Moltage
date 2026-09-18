# 16–18. Analysis and Result Views

[Back to the manual index](README.md)

## 16. AITRANSS transmission results

After a successful FHI-aims/AITRANSS Step 4, select the project and choose
**View Transmission** in Project Manager. Moltage verifies the authoritative
result identified by the active `tcontrol`; it does not choose a file merely
because it is the newest match.

![AITRANSS transmission result view with example data](../images/readme/aitranss-transmission-synthetic.png)

**Figure 11.** AITRANSS transmission result view with illustrative data.

### Reading the plot

- The horizontal axis is energy relative to E<sub>F</sub> in eV.
- The vertical axis is unchanged raw transmission T(E) on a base-10 logarithmic
  scale.
- Decades use typographic labels such as 10<sup>−3</sup> rather than `1E-3`.
- A dashed vertical reference marks E<sub>F</sub>.
- T(E<sub>F</sub>) is evaluated from the two surrounding raw samples when interpolation
  is supported; the status text says whether the value is exact, interpolated, or
  unavailable.
- Non-positive data are not replaced with an artificial positive floor.

Move the pointer near the curve to inspect the nearest sample. Click near a point
to pin the readout, click blank plot space to release it, and use the keyboard to
step through visible positive samples after a point is pinned.

### Adjusting presentation

With the result tab active, choose **Settings → View...**. The Transmission View
Settings dialog has five pages:

| Page | Controls |
|---|---|
| **X Axis** | Range, title, fonts, axis/grid appearance, and optional mirrored axis. |
| **Y Axis** | Positive logarithmic range, title, fonts, axis/grid appearance, and optional mirrored axis. |
| **Ticks** | Direction, major/minor tick lengths and widths, visibility, and intervals. |
| **Curve** | Curve color, width, line style, and legend visibility. |
| **Canvas** | Export dimensions, background/frame, and plot margins. |

Double-clicking an axis opens the corresponding axis page. Double-clicking the
curve opens **Curve**; double-clicking the canvas opens **Canvas**. **Apply** updates
presentation only and never rewrites the scientific result. The toolbar **Reset
View** action restores the frozen default ranges. Use **File → Export Current
View...** to save the styled plot at the requested scale.

## 17. Electron density difference

Choose **Calculation → FHI-aims → Electron Density Difference...** from a mutable
Geometry workspace. This is an independent fixed-geometry calculation, not an
AITRANSS stage and not a four-step transport project.

The scientific definition is:

```text
Δρ = ρ(total) − ρ(subset 1) − ρ(subset 2)
```

Positive values show electron accumulation and negative values show depletion.
The result includes interaction-induced redistribution and polarization; it is not
by itself a unique measure of interfragment transferred charge.

### 17.1 Fragments page

Every atom must belong to exactly one of two nonempty fragments.

- **Active fragment** selects Subset 1 or Subset 2.
- **Mouse** selects point selection, rectangle selection, or camera rotation.
- **Remove from active fragment** makes subsequent picks remove atoms.
- **Subset 1 / Subset 2** accept atom-number ranges such as `1-20,25,31-40`.
- **Add all remaining atoms** assigns every currently unassigned atom to the
  active fragment.

Point and rectangle selection are continuous. Assigning an atom to one subset
removes it from the other. Rectangle selection uses projected atom centers,
including centers hidden behind other atoms. Open rings and atom numbers identify
the two subsets without changing coordinates.

### 17.2 States page

**XC** and **Species** apply to all three SCFs. Total, Subset 1, and Subset 2 each
have an independent charge and optional collinear-spin initial moment. **Derive
Subset 2 charge = Total − Subset 1** is enabled initially.

Fragment charge and spin describe independent reference calculations. In
particular, cutting a covalent bond may require an open-shell fragment. Neutral
and non-spin initial values are not a claim about the correct physical state.

### 17.3 SCF & Grid page

The page exposes occupation width, Pulay/mixing and convergence fields, SCF
iteration limit, **Grid spacing**, and **Boundary padding**. Initial grid settings
are 0.1 Å spacing and a 14-bohr boundary margin expressed in Å. They are starting
settings, not convergence guarantees. Moltage creates one explicit common grid
for all components and never silently resamples mismatched scientific inputs.

### 17.4 Server & Resources page

Select the server and task name, then review resources for the profile's scheduler.
One scheduler job runs Total → Subset 1 → Subset 2 sequentially. The maximum
runtime must therefore cover all three SCFs. FHI-aims runtime and species settings
come from the server profile; AITRANSS is not required.

Choose **Submit** for a new task. **Refresh Status**, **Recover Results**, and
**Retry Failed Components** become available according to task state. Three lamps
show Total, Subset 1, and Subset 2. They describe sequential components inside one
job, not three independent submissions. Right-clicking an active lamp offers
cancellation of the exact shared scheduler job.

Retry preserves successful validated components and reuses the original input
bytes for unsuccessful ones. A stopped or failed task can instead be reopened and
**Resubmit with Changes** as a new task. The original task remains unchanged.

### 17.5 Result view

Recovery verifies converged/terminated outputs, ordered fixed geometry, Hirshfeld
charges, and common-grid Cube data before showing a green component. Scheduler
success alone is insufficient.

The result view contains:

- a table of total and fragment Hirshfeld charges and electron gains;
- positive/negative density-difference isosurfaces;
- **Display resolution** choices Full, Medium, and Low;
- **Density difference** or **Total/reference density** display mode;
- **Density Interpolation** λ from 0 to 1;
- **View...** for isosurface appearance;
- **Export report...** for a reproducible result directory.

Medium and Low resolution affect only display mesh extraction. Validated Cube
fields, subtraction, Hirshfeld values, integrals, and exported data remain at full
source resolution. The interpolation is
`ρλ = ρsubset1 + ρsubset2 + λΔρ`; it is a visualization device, not time evolution,
physical current, or an electron trajectory.

Export creates a new directory and never overwrites an existing report. It
contains `difference.cube`, `atom_charges.csv`, `fragment_charges.csv`,
`analysis_manifest.json`, and `report.html`.

## 18. Local tight-binding transmission

Choose **Calculation → Local Tight-Binding Transmission...** from a Geometry
workspace. This is a session-local exploratory model. It does not connect to a
server, create a remote calculation project, or supply chemistry-dependent parameters.

The model is orthogonal, one-orbital, real-valued bond-coupling, spinless, and coherent. All
energies are relative to E<sub>F</sub> = 0 eV. Its output is a model result, not an ab
initio transport calculation.

### 18.1 Contacts

- **Linker candidate** lists detected anchor candidates.
- **Use as Left / Use as Right** copies the selected candidate.
- **Left atom / Right atom** accept atom numbers.
- **Pick Left / Pick Right** let the user choose directly in the viewer.
- **Γ Left / Γ Right** are required positive couplings in eV.

Detection supplies candidates only. If explicit electrodes are present, select
the atoms actually coupled to the idealized leads.

### 18.2 Atom Energies

Enter one required onsite energy ε for every element type. Click an atom to give
it an exact override; clear the override to inherit its element value again.

### 18.3 Bond Couplings

Enter one required hopping value t for every unordered element pair present in
the displayed connectivity. Click a displayed bond to override it exactly. MOL
bond order is visual reference only and does not generate a coupling value.

### 18.4 Energy Grid

Enter the included start, excluded end, positive step, and positive numerical η,
all in eV. η is explicit broadening and changes the calculated curve.

The movable **Hamiltonian H (eV)** dock displays the effective matrix after all
type values and exact overrides. **Calculate** remains disabled until every
required value is valid. After the first calculation, edits trigger a bounded
recalculation of the current session. Closing the tab discards this local state.

## 18.5 What these result types do not prove

- AITRANSS T(E) is meaningful only with validated inputs and the assumptions of
  that FHI-aims/AITRANSS workflow.
- ORCA WBL is a linker-parameterized hypothesis, not explicit electrode DFT-NEGF.
- Local tight-binding is an explicitly user-parameterized illustrative model.
- Density difference visualizes a chosen fragment decomposition; it is not a
  unique charge-transfer observable.

Keep these boundaries in figures, captions, reports, and comparisons.

[Next: Files, troubleshooting, and reference](08_files_troubleshooting_reference.md)
