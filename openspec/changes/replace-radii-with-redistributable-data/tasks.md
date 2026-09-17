## 1. Source and License Approval Gate

- [x] 1.1 Identify a covalent-radii source with authoritative provenance, explicit radius definition and units, coverage of H–Bi, and license terms permitting public and commercial redistribution; verify every claim against primary source and authoritative license evidence.
- [x] 1.2 Identify a van-der-Waals-radii source with authoritative provenance, explicit radius definition and units, coverage of H–Bi excluding Pm, and license terms permitting public and commercial redistribution; verify every claim against primary source and authoritative license evidence.
- [x] 1.3 Present both source assessments, proposed attribution obligations, coverage, and any transformation to the user and obtain explicit approval; verify that no radius resource, loader, algorithm, or expected value is modified before this approval is recorded.

## 2. Resource and Loader Replacement

- [x] 2.1 Replace `covalent_radii.toml` with the approved coherent dataset and complete provenance metadata, preserving H–Bi coverage; verify symbols, source transcription or documented conversion, Å units, finite positive values, and coverage with focused tests.
- [x] 2.2 Replace `vdw_radii.toml` with the approved coherent dataset and complete provenance metadata, preserving H–Bi coverage excluding Pm; verify symbols, source transcription or documented conversion, Å units, finite positive values, and coverage with focused tests.
- [x] 2.3 Replace source-specific loader checks with required source-agnostic provenance, license, units, definition, coverage, and value validation; verify malformed or incomplete synthetic resources fail explicitly without fallback.
- [x] 2.4 Preserve existing packaged-resource lookup and downstream loader interfaces; verify a clean offline test context loads both approved tables without network access or developer-local files.

## 3. Provenance and Compatibility Evidence

- [x] 3.1 Produce a reviewable old-versus-approved-data summary covering element sets and per-element absolute/relative differences without committing a duplicate of the old tables; verify every changed value is traceable to the approved source or documented unit conversion.
- [x] 3.2 Update maintained documentation with each dataset's purpose, radius definition, citation, license, attribution, coverage, and Moltage curation/transformation; verify it does not overstate scientific meaning or imply unsupported provenance.
- [x] 3.3 Add or update synthetic tests for current reference connectivity, cutoff-boundary connectivity, steric placement/selection, and default-radii display loading; verify algorithms, thresholds, and multipliers remain unchanged and any altered expected behavior is individually explained.
- [x] 3.4 If regression evidence shows a meaningful unexplained behavior change, stop and obtain an explicit user decision; verify no algorithm tuning, silent fallback, or blanket fixture rewrite is used to force acceptance.

## 4. Validation and Scope Audit

- [x] 4.1 Run the focused radius, connectivity, steric-placement, packaging, and display-related offline tests and report exact collected/passed/failed/skipped results.
- [x] 4.2 Run `./tools/run_tests.ps1 -Full` and report the actual full offline suite result without claiming unrun validation.
- [x] 4.3 Audit the final diff against the approved artifacts; verify no Au placement/electrode data, unrelated scientific algorithms, scheduler/runtime code, project LICENSE, Git history, online dependency, or persisted schema entered the change.
