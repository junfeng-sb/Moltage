# Synthetic offline test fixtures

Every file under this directory is independently constructed synthetic data for
Moltage's default offline tests. The collection is not copied from an FHI-aims
or AITRANSS distribution, a real SSH/HPC server capture, or an unpublished
research calculation. It contains no real account, credential, server identity,
project identity, or scheduler Job ID.

Some fixtures intentionally retain a program-identification line, parser marker,
or error phrase because the test exercises that external format. Such literal
grammar describes the format being simulated; it is not provenance evidence and
does not mean that the fixture was produced by that program.

| Directory | Synthetic purpose |
| --- | --- |
| `cube/` | Minimal signed scalar grid and molecular header for Cube parsing and display tests. The ORCA-identification header is simulated parser input, not ORCA output. |
| `phase1b/` | Minimal molecular geometry and XYZ structures for deterministic input and connectivity tests. |
| `phase2c/` | Minimal optimized-geometry grammar for recovery tests. |
| `phase4a/` | Minimal AITRANSS diagnostic grammar for typed failure classification. |
| `phase4b/` | Minimal AITRANSS retry, `tcontrol`, and self-energy reader grammar; see its more-specific README. |
| `phase4c/` | Minimal successful AITRANSS markers and non-spin transmission grid grammar. |
| `remote_r3_r2/` | Minimal synthetic scheduler accounting rows for cancellation and task-state parsing. |
| `ui_r5/` | Minimal MOL V2000 records, including deliberately invalid records, for structure parser and display tests. |

New fixtures must contain only the smallest independently constructed content
needed to verify one documented behavior. Do not add original scientific-software
distribution files, real server output, real research data, local configuration,
credentials, or private infrastructure identifiers.

Passing tests based on these fixtures validates parser, workflow, and presentation
behavior only within the synthetic offline boundary. It does not demonstrate
compatibility with a real HPC site, scheduler deployment, FHI-aims/AITRANSS build,
MPI stack, or scientific calculation.
