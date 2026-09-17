## 1. Current-Geometry Interaction Evaluation

- [x] 1.1 Add immutable Phase-3 interaction records and one-way current-side frame mapping from Phase-2 identities; verify synthetic tests cover independent LEFT/RIGHT rigid transforms, signed keys, existing extensions, and explicit rejection of non-rigid or mismatched mappings.
- [x] 1.2 Add full-working-geometry `AVAILABLE`/`BLOCKED` classification that reuses existing Au lattice clearance and non-Au vdW radius-sum semantics without exclusions or new parameters; verify synthetic tests cover clear sites, Au coincidence/sub-spacing, non-Au overlap, exact-boundary clearance, candidate retention, invalid coordinates, and missing radii.
- [x] 1.3 Add the narrow current-world add adapter that resolves a fresh canonical candidate by identity, invokes the unchanged Phase-2 add operation, and appends the mapped working coordinate atomically; verify tests cover unrotated and rotated sides, stale/blocked no-op behavior, Connectivity, applied provenance, and unchanged reference/`pyramid_layers`/`$nlayers` semantics.

## 2. Viewer Projection and Preview

- [x] 2.1 Add opaque stable-token projection targets to the existing molecular scene/viewer using the current atom-pick envelope, render scale, depth ordering, and hover throttle; verify viewer tests cover deterministic nearest selection, overlapping projections, no-hit, camera reprojection, and that pointer coordinates never create a candidate.
- [x] 2.2 Route extension hover/click/leave events without making the ghost actor pickable; verify tests demonstrate that hover and click return the stable token rather than XYZ and that leave, drag, mode exit, or cleared targets end the interaction safely.
- [x] 2.3 Reuse the single `PreviewAtom` pipeline for one normal translucent Au ghost or one red translucent `BLOCKED` ghost; verify scene/presentation tests cover both colors, one-ghost maximum, unchanged base atoms/Connectivity, cleanup, and acceptable light/dark theme contrast.

## 3. Geometry Workspace Integration

- [x] 3.1 Add the minimal checkable lattice-extension control to the existing Electrode Builder and its mutually exclusive viewer pick mode; verify GUI tests cover accepted editable electrodes and disabled behavior for missing, read-only, coordinate-only, submitted, and non-Geometry workspaces.
- [x] 3.2 Store the transient candidate snapshot per Geometry workspace and centralize invalidation for add, Undo/Redo, pyramid/structure replacement, coordinate mutation, and workspace switch; verify tests count one enumeration per unchanged state, no enumeration per mouse move or camera-only change, and no cross-workspace cache reuse.
- [x] 3.3 Connect mouse-nearest records to normal/red preview state and operation feedback; verify GUI tests cover `AVAILABLE`, `BLOCKED`, no-hit, mouse leave, workspace switch, and the guarantee that a blocked candidate remains discoverable but cannot be added.
- [x] 3.4 Implement click-time editability, fresh-identity, current-frame, and full-geometry revalidation before committing one add; verify GUI tests cover successful refresh of structure/Connectivity/provenance/candidates and atomic no-op for blocked, stale, invalid-frame, backend, or viewer-presentation failure.
- [x] 3.5 Integrate each successful add with the existing five-level Geometry Undo/Redo history; verify Undo/Redo restores matching atom count, Connectivity, applied extension provenance, calculation eligibility, cache, and preview, while rejected clicks create no history entry.
- [x] 3.6 Ensure existing electrode rotation moves newly added extension Au with its same-side Connectivity component and invalidates the interaction cache; verify regression tests cover add-then-rotate and rotate-then-add while preserving side/layer/key, standard provenance, apex and reference corners.

## 4. Compatibility and Validation

- [x] 4.1 Add focused synthetic workflow regressions for recovered Step-2 and eligible imported direct-Step-3 paths, asymmetric sides/layers, repeated additions, save/submission use of exact current coordinates, and schema-8 provenance pass-through; verify no test connects SSH/HPC or runs FHI-aims/AITRANSS.
- [x] 4.2 Run existing Phase-2 lattice, Step-3, Step-4 surface/restart/retry, geometry-writer precision, viewer, history, and theme focused tests; verify Phase-2 generation, reference corners, manifest schema, `$nlayers`, scientific inputs, and numeric output precision remain unchanged.
- [x] 4.3 Update only the maintained `PROJECT_SCOPE`, `ARCHITECTURE`, `WORKFLOW`, and `CONFIGURATION` passages that currently reserve or describe the Phase-3 interaction; verify documentation states normal/red preview, canonical identity, cache boundaries, rigid side behavior, and no scientific/persistence expansion.
- [x] 4.4 Run `openspec validate interact-with-au111-extension-sites --strict` and the full offline suite `./tools/run_tests.ps1 -Full`; record exact collected/passed/failed/skipped counts and confirm `git status --short` contains no test-generated non-ignored artifacts.

## 5. Canonical Bond and Layer Hover Guides

- [x] 5.1 Extend only the Phase-3 interaction record with predicted current-world bond targets and independent same-layer basis vectors derived from canonical identity/current rigid frame; verify synthetic tests compare every predicted neighbor with the unchanged Phase-2 post-add Connectivity for multiple sides/layers and rotated geometry.
- [x] 5.2 Add dedicated unpickable scene actors for dashed predicted bonds and one finite triangular-grid plane whose per-vertex opacity fades from the candidate outward; verify scene tests cover exact supplied endpoints/basis, three lattice directions, monotonic fade, single-guide replacement, colors, and complete cleanup without changing base geometry.
- [x] 5.3 Wire the visual guide to the existing cached hover lifecycle for both `AVAILABLE` and red `BLOCKED` candidates; verify repeated hover/camera-only motion performs no new enumeration or collision evaluation, and no-hit, drag, mode exit, workspace switch, add, Undo/Redo, rotation, and cache invalidation clear or refresh all ghost guides consistently.
- [x] 5.4 Update only the directly affected maintained documentation, run focused synthetic/viewer/workflow regressions and the full offline suite, then run `openspec validate interact-with-au111-extension-sites --strict`; report exact results and any scope deviation.
