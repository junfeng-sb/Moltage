## 1. Canonical Geometry and Placement

- [x] 1.1 Implement `MoltageAuPyramidV1` with `2.88372 Å` spacing, deterministic integer-lattice identities, tetrahedral counts, apex/base-corner identities, and 2–10 layer validation; verify focused domain tests cover all nine counts, neighbor distances, ordering, corner order, repeatability, and invalid inputs.
- [x] 1.2 Replace A/B-template input in the existing placement builder with generated canonical geometry while retaining independent outward alignment and per-side roll optimization; verify focused placement tests cover 2-, 6-, and 10-layer pyramids, different valid left/right rolls, apex reuse, and dynamic atom mappings without 59/58/116 assumptions.
- [x] 1.3 Update applied-electrode/workspace records to carry `pyramid_layers` and generated atom identities through preview and Done; verify preview-to-apply tests prove that the accepted structure and metadata are derived from the same proposal.

## 2. Project Provenance and Migration

- [x] 2.1 Evolve the project manifest from schema 6 to schema 7 with generic two-side generator provenance, ordered atom/lattice identities, local-to-global mappings, apex and immutable reference corners; verify serialization round-trip and malformed-record rejection tests pass.
- [x] 2.2 Persist identical schema-7 provenance for normal Step-3 continuation and direct Step-3 creation, and preserve it across authoritative submission outcomes and resource-only retries; verify synthetic workflow tests cover queued, unknown, rejected/retried, and reopened projects without losing either side's metadata.
- [x] 2.3 Implement the bounded schema 4–6 direct-Step-3 `LegacyAu59V1` decoder using existing complete mappings but no coordinate resources; verify regression tests recover unique contact/membership/corners and reject overlapping, incomplete, or invalid mappings.
- [x] 2.4 Implement conservative legacy normal-flow recovery only when the historical ordering and apex/contact evidence are unique; verify synthetic tests preserve non-mapping-dependent project access, allow a provably unique case, and explicitly block ambiguous Step 4/restart/retry without modifying historical files or `$nlayers`.

## 3. Au Tool Layer Selection

- [x] 3.1 Add the shared `Pyramid layers` selector with default 6 and range 2–10 to the existing Au Tool controls; verify GUI tests show 2–6 as recommended and a clear non-blocking high-cost warning for 7–10.
- [x] 3.2 Invalidate both stale side proposals when the layer selection changes while preserving one-side preview and two-side Done rules; verify GUI/workspace tests cannot apply metadata created for a previous layer count.
- [x] 3.3 Replace fixed Au59/Au116 user-facing counts and A/B wording with values derived from generated proposals; verify GUI tests report Au56 per side and 55 newly appended atoms per side at the default without coupling side orientation.

## 4. Authoritative Surface Reconstruction

- [x] 4.1 Refactor `electrode_surface.py` to build surface membership and ordered reference corners from schema-7/validated legacy provenance; verify unit tests cover dynamic counts, 1-based left/right AITRANSS indices, rigid coordinate transforms, non-collinear corners, and explicit failures for reordered or mismatched structures.
- [x] 4.2 Route Step-4 recovery/submission and overlap/self-energy retry through the authoritative provenance resolver with no template/suffix fallback; verify synthetic application tests prove every path uses the persisted mapping and preserves scheduler/program/scientific state when validation fails.
- [x] 4.3 Route Step-3/Step-4 restart drafts through the same provenance resolver and copy identities unchanged; verify restart tests cover both generated and recoverable legacy projects and reject ambiguous legacy mapping.
- [x] 4.4 Update self-energy plane membership to consume explicit layer/standard-membership identities instead of fixed local-index ranges; verify focused self-energy tests cover multiple pyramid sizes and exclude no atoms based on legacy A/B adatom positions.

## 5. Evidence-Gated `$nlayers`

- [x] 5.1 Remove geometry-derived/fixed `recommended_nlayers = 4` and add a typed initial-value interface whose exact table is `{4: (2, AIMS_RECOMMENDED), 5: (3, AIMS_RECOMMENDED), 6: (4, USER_SPECIFIED)}`; verify tests use no formula/fallback and leave 2, 3, and 7–10 unset.
- [x] 5.2 Allow `TControlProposal` to represent missing `$nlayers` and its optional source while keeping final `TControlSettings` positive and complete; verify Step-4 dialog tests display `AIMS recommended` for untouched 4/5 defaults, `User specified` for the 6-layer default and every edited/manual value, keep all defaults editable, and require input for uncovered sizes before new `tcontrol` materialization.
- [x] 5.3 Preserve `$nlayers` from historical `tcontrol`/attempt data across viewing, recovery and retries; verify byte-preservation and regression tests prove the new recommendation path never rewrites an existing value.
- [x] 5.4 Record only the reviewed AITRANSS `051414` fcc(111) Au20+2-adatom/`$nlayers=2` and Au35+2-adatom/`$nlayers=3` header provenance, explicitly record 6-layer/`4` as a user decision rather than AIMS evidence, and provide manual guidance for the six uncovered sizes; verify no real server identity/path, capture, or library coordinate file enters the repository.

## 6. Resource and Documentation Boundary

- [x] 6.1 Remove `au_6layer_variant_a.xyz`, `au_6layer_variant_b.xyz`, active A/B loaders/enums, and packaging references after all consumers use the generator/provenance path; verify repository and built-artifact/resource-manifest checks find neither coordinate file and clean-install generation tests do not access external files.
- [x] 6.2 Update only maintained documentation that describes fixed Au59/A/B/reference-index or automatic `$nlayers = 4` behavior, including the `2.88372 Å` project-parameter evidence and the new Au56 default; verify documentation searches contain no active-workflow claim that contradicts the new spec while clearly marking real AITRANSS acceptance unverified.

## 7. Integrated Offline Validation

- [x] 7.1 Run the focused generator, placement, GUI, manifest migration, Step-3/Step-4, restart, self-energy and `tcontrol` synthetic test selections; verify all selected tests pass without SSH, FHI-aims, AITRANSS or a real scheduler.
- [x] 7.2 Run `./tools/run_tests.ps1 -Full` and verify the full offline suite passes with no unexpected tracked/untracked runtime artifacts; report exact collected/passed/failed/skipped counts and do not claim real-environment scientific validation.
