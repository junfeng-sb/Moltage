## 1. Signed Lattice Domain

- [x] 1.1 Add typed signed same-layer site/candidate identities and an identity-anchored per-side lattice frame without changing `MoltageAuPyramidV1`; verify focused domain tests cover key-sum/layer validation, the six fixed neighbor deltas, deterministic coordinate generation, independent LEFT/RIGHT rigid transforms, and explicit rejection of non-rigid or ambiguous standard mappings.
- [x] 1.2 Implement apex one-site and adjacent two-site seed generation plus generic boundary-edge generation from occupied-key sets; verify synthetic tests assert exactly 6 apex candidates, exactly 2 triangle-completion candidates, rejection of a nonadjacent two-site state, seed-to-boundary transition after the first triangle, no interior-edge candidates, deduplication, stable ordering, and candidate generation on every layer of supported 2–10 layer pyramids.
- [x] 1.3 Apply the shared occupancy, side, overlap and sub-spacing validation pipeline to seed and boundary candidates; verify tests cover occupied standard/extension keys, cross-layer collision, opposite-side collision, legal same-side same/cross-layer neighbors, and prove the pipeline does not consult covalent radii, vdW radii, anchor defaults or global connectivity settings.

## 2. Immutable Add Behavior

- [x] 2.1 Evolve `AppliedElectrodePlacement` with a default-empty ordered extension collection while preserving the accepted standard preview as its immutable base; verify current zero-extension placement tests remain valid and new tests reject any changed standard atom, standard mapping or original bond.
- [x] 2.2 Implement the pure add operation that revalidates an exact candidate, appends one Au, records its side/layer/signed key, connects every and only same-side lattice-distance-one neighbor, updates `added_au_indices`, and returns recomputed candidates; verify repeated-add tests cover apex growth, ordinary boundary growth, multiple layers, asymmetric sides and no artificial extension count limit.
- [x] 2.3 Make invalid, forged, occupied, side-mismatched and stale candidate additions atomic no-ops; verify tests compare the complete pre/post immutable structure, connectivity, standard metadata, extension metadata and reference identities after each rejected operation.

## 3. Project Schema and Provenance

- [x] 3.1 Add a separate `ProjectElectrodeLatticeExtension` record and default-empty per-cluster collection, then update `provenance_from_applied_electrodes` to preserve ordered extension identities/mappings; verify focused domain tests keep standard/legacy atom records distinct and preserve different extension counts on LEFT and RIGHT.
- [x] 3.2 Advance the project manifest to schema 8 and serialize/parse explicit `LATTICE_EXTENSION` origin, layer, signed key and global atom index deterministically; verify round-trip tests cover interleaved global append order and reject duplicate keys, standard-key collisions, invalid sums/layers, duplicate/cross-side mappings, non-Au mappings and missing fields.
- [x] 3.3 Migrate valid schema 7 records with empty extension collections while leaving schemas 1–6 on the existing bounded legacy path with no inferred extensions; verify migration regression tests preserve all current workflow/provenance fields and exact existing calculation-file fixtures without geometry-, suffix- or count-based extension inference.

## 4. Downstream Mapping Invariants

- [x] 4.1 Update authoritative surface validation so standard plus extension mappings exactly cover the generated suffix, each extension coordinate matches its identity-derived side frame, and generated full electrode membership includes extensions; verify zero-extension regressions plus asymmetric/interleaved extension tests pass while legacy self-energy membership remains unchanged.
- [x] 4.2 Keep `lsurc/lsurx/lsury` and `rsurc/rsurx/rsury` bound only to the original three standard reference corners; verify synthetic Step-4 and self-energy tests add Au on multiple layers/sides yet produce identical corner indices before and after extension and explicitly fail on altered extension mappings.
- [x] 4.3 Preserve complete schema-8 provenance through normal/direct Step 3, rejected/unknown submission outcomes, resource-only resubmission, restart, recovery and self-energy retry; verify existing workflow test modules assert no extension identity is lost, reordered or regenerated and no scheduler/program/scientific state is changed by provenance validation failure.
- [x] 4.4 Prove same-layer extension leaves `pyramid_layers` and `$nlayers` value/source semantics untouched; verify focused `tcontrol`/retry tests cover existing configured values and missing values before/after arbitrary additions without invoking or changing the recommendation table.

## 5. Documentation and Offline Validation

- [x] 5.1 Update only maintained documentation that directly describes standard-electrode immutability, project schema, full electrode membership or Phase-2 extension provenance; verify it explicitly keeps GUI hover/click in future `interact-with-au111-extension-sites`, retains the `2.88372 Å` project-parameter qualification, and makes no real-environment scientific claim.
- [x] 5.2 Run focused synthetic tests for lattice generation, accepted placement, manifest migration, Step-3/Step-4, restart/recovery/retry, self-energy and `$nlayers`; report exact selected/pass/fail counts and verify no SSH, scheduler, FHI-aims or AITRANSS process is contacted.
- [x] 5.3 Run `./tools/run_tests.ps1 -Full` and verify the full offline suite passes with no unexpected repository artifacts; report exact collected/passed/failed/skipped counts and do not claim GUI Phase 3 or real HPC acceptance.
