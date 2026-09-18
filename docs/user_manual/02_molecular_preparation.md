# 5–8. Linkers, Contacts, and Au Electrodes

[Back to the manual index](README.md)

## 5. Linker and contact recognition

### 5.1 Purpose

Moltage uses the molecular element identities and current Connectivity to find
supported linker termini. Recognition supplies stable candidate identities for
contact placement and later workflow checks. It does not prove a chemical
structure from appearance or distance alone.

### 5.2 Supported linker/contact kinds

| Displayed kind | Binding atom used by Moltage | Initial Au-placement parameters |
| --- | --- | --- |
| `NCS` | S | S–Au `2.34 Å`; C–S–Au reference angle `170°` |
| `SMe` | S | S–Au `2.40 Å`; reference angle `100°` |
| `Pyridine-N` | N | N–Au `2.15 Å`; equal-angle target `119°` |
| `NH2` | N | N–Au `2.42 Å`; reference angle `120°` |
| `SH` | S | S–Au `2.35 Å`; reference angle `105°` |
| `Alkynyl-C` | C | C–Au `2.05 Å`; reference angle `179°` |

These values are **Moltage project heuristic starting parameters** for initial
geometry construction. They are not universal equilibrium geometries,
experimental constants, or an assertion that the resulting structure is
scientifically optimized.

### 5.3 Electrode Builder site cards

Open the left panel with the **Electrode Builder** toolbar button. Each detected
site card reports:

- linker kind;
- binding-atom number;
- relevant group atoms;
- whether an Au atom is already attached;
- whether the site is available, selected, or already applied.

The panel allows at most two selected sites at one time. This models left and
right contacts; it does not require a particular visual left/right orientation
on the screen.

### 5.4 Existing contact Au

Recognition ignores direct Au neighbors while matching the linker's structural
pattern, then records a directly attached Au separately. A site card therefore
can report an existing contact Au without mistaking that Au for part of the
organic linker.

Exactly two supported linker termini with one attached Au each and no other Au
may be eligible to start directly at FHI-aims Step 2. Direct Step 3 has stricter
provenance requirements; see [the FHI-aims workflow](05_fhi_aims_workflow.md).

## 6. Building molecular contacts

### 6.1 Preview a contact

1. Open a normal editable Geometry workspace.
2. Show the Electrode Builder.
3. Select one available site card.
4. Review the distance and angle fields that appear in the card.
5. Moltage displays a translucent proposed Au atom and geometric annotations.

<p align="center">
  <img src="../images/readme/electrode-builder-contacts.png" alt="Detected linker contacts in the Electrode Builder" width="100%">
</p>

**Figure 2.** The two `SH` contacts in the example molecule after contact Au is
present. Magenta marks linker binding atoms; cyan marks existing attached Au.

The preview is not an atom in the working structure. Changing a field replaces
the proposal. The displayed proposal coordinates are the exact coordinates that
will be applied; **Done** does not perform another placement search.

### 6.2 Distance and angle fields

The labels depend on linker kind. They define the selected linker's supported
initial Au geometry, not a global bond constraint. Values must be finite and
valid for the corresponding placement model. Moltage performs the existing
steric screening and reports an explicit error when no acceptable proposal can
be formed.

For an `SH` linker, the card exposes Au–S distance and R–S–Au angle. Their
starting values are `2.35 Å` and `105°`.

### 6.3 Apply contact Au

Press the upper **Done** button to apply the currently selected contact
proposal(s):

- proposal coordinates become real Au atoms;
- scheduled terminal H atoms are removed when required by the accepted linker
  rule;
- existing atom order is preserved for surviving source atoms;
- new Au atoms are appended deterministically;
- Connectivity is updated explicitly;
- the opened source file remains unchanged;
- one Geometry Undo state is created.

The operation is atomic: Moltage does not partially apply a two-site request.
Applied sites become unavailable until the geometry is restored through Undo or
reloaded.

### 6.4 Save geometry.in

After an eligible application, **Save geometry.in** writes the current working
geometry to a user-selected local file. It does not create a remote calculation project,
submit a calculation, or produce `control.in`.

### 6.5 Direct-start implications

- A source structure that already contains exactly two valid contact Au atoms,
  or a current structure produced by the accepted contact-placement operation,
  can be offered as direct Step-2 input.
- Viewer-added contacts still require Step-2 molecule–Au optimization before
  Step 3.
- A source with additional Au atoms, shared/ambiguous contacts, or changed
  post-placement topology does not receive direct-start eligibility.

## 7. Building canonical Au pyramids

### 7.1 When the controls appear

Canonical-pyramid controls are intended for:

- a successfully recovered FHI-aims Step-2 molecule–Au geometry; or
- an eligible imported structure whose two contact Au atoms are explicitly
  confirmed as already appropriately optimized.

Both paths use the same generator. There is no separate imported-structure
electrode algorithm.

### 7.2 Pyramid layers

`Pyramid layers` accepts integer values from 2 to 10.

- Default: `6` layers.
- Recommended normal range: 2–6.
- Values 7–10 remain available but show a non-blocking computational-cost
  warning.
- Both sides use the same selected layer count and canonical shape.
- Left and right placement orientations/rolls remain independent.

Changing the layer count invalidates stale side previews.

### 7.3 Geometry model

`MoltageAuPyramidV1` generates a deterministic triangular-lattice tetrahedral
pyramid with nearest-neighbor spacing `2.88372 Å`. This is a Moltage geometry
parameter for the generator, not a universal bulk-Au equilibrium distance or
experimental constant.

The contact Au is reused exactly as the pyramid apex. It is not moved,
duplicated, or replaced. At six layers, each side is Au56, so Moltage appends 55
new Au atoms per side.

### 7.4 Preview and apply

1. Choose the layer count.
2. Select one side to inspect its preview, or select both sides to enable
   application.
3. Check that both generated pyramids point away from the molecule.
4. Press the pyramid section's **Done** button.

Moltage uses a deterministic joint roll search to improve opposite-cluster
separation. It does not require mirror symmetry or equal roll.

<p align="center">
  <img src="../images/readme/electrode-builder-junction.png" alt="Completed two-sided canonical Au junction" width="100%">
</p>

**Figure 3.** The example structure after applying two six-layer canonical Au
pyramids. The generated junction contains 120 working atoms in this example.

Application consumes the exact visible immutable preview and persists:

- layer count and lattice spacing;
- standard lattice identities and atom mappings;
- apex identity;
- three immutable outer-layer reference corners on each side;
- independent accepted rolls/orientations.

These records are later used by Step 3, Step 4, retry, rotation, and lattice
extension. Moltage does not recover them by guessing from final XYZ coordinates.

## 8. Extending the Au(111) lattice

### 8.1 Entry and purpose

After both canonical pyramids have been applied, press
**Extend Au(111) lattice**. This mode allows individual Au atoms to be added at
legal same-layer sites supplied by the canonical lattice backend.

The mouse never creates an arbitrary XYZ site. It only chooses among cached
Phase-2 candidates identified by:

`side + layer index + signed lattice key`

### 8.2 Candidate preview

While the pointer moves near a cached candidate, the viewer shows at most one
ghost Au atom:

| Preview | Meaning | Click result |
| --- | --- | --- |
| Normal translucent Au | `AVAILABLE`: valid lattice site with acceptable current clearance | Fresh validation, then add if still valid |
| Red translucent Au | `BLOCKED`: valid lattice site but overlaps the current full geometry | No atom is added |

Both forms also show:

- dashed guides to the exact Au neighbors the new atom would connect to; and
- a finite triangular grid in the candidate's current layer plane, fading
  outward from the candidate.

These guides are presentation only. They are not read back as lattice or bond
authority.

### 8.3 Add a site

Clicking an available ghost sends only its canonical identity to the backend.
Moltage re-enumerates/revalidates against the current state and collision rule;
a stale or newly blocked request is rejected. A successful addition:

- appends one Au atom;
- updates current Connectivity;
- records extension provenance separately from the standard pyramid;
- invalidates and refreshes the candidate cache;
- enters the active Geometry workspace's Undo/Redo history.

### 8.4 Rotation and identity

An added extension atom becomes a normal member of its left or right electrode.
Later rigid electrode motion moves the standard pyramid and its extension atoms
together. Rotation changes current coordinates but not the extension's side,
layer, lattice key, standard apex, or immutable reference corners.

### 8.5 Cache invalidation and mode exit

Candidates are recomputed after:

- a successful addition;
- Undo or Redo;
- pyramid/structure replacement;
- electrode rotation or another coordinate mutation;
- workspace switching.

Camera-only motion does not recompute the lattice. Leaving the canvas, exiting
the mode, or switching workspace clears the ghost and guides. Extension is
disabled on read-only, coordinate-only, or submitted immutable geometries.

### 8.6 What extension does not change

Extension does not change:

- `MoltageAuPyramidV1` standard geometry;
- `pyramid_layers`;
- the apex or the three standard reference corners;
- AITRANSS `$nlayers`;
- species data, radii, or connectivity multipliers;
- any server profile or remote project by itself.

[Next: servers and external programs](03_servers_and_schedulers.md)
