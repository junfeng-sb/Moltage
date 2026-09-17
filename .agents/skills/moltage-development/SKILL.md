---
name: moltage-development
description: Develop or review code, configuration, tests, and architecture documentation in the Moltage repository. Use for any scoped change to its molecular-structure, junction, FHI-aims, remote/Slurm, notification, AITRANSS, application-state, or GUI workflow while enforcing deterministic science, explicit failure, and separation of concerns.
---

# Moltage Development

## Prepare

1. Read `AGENTS.md` first.
2. Read the documents relevant to the task: `docs/PROJECT_SCOPE.md`, `docs/ARCHITECTURE.md`, `docs/WORKFLOW.md`, and `docs/CONFIGURATION.md`.
3. Confirm the user's explicit scope and identify the owning conceptual area before editing.
4. Inspect existing code, tests, configuration, templates, and resources in that area completely enough to preserve its invariants.
5. Check for required tools and validated scientific inputs. If a required template, reference file, example, or documented rule is missing, stop the affected implementation step and report the missing input.

## Implement

1. Make the smallest correct change that completes the requested scope.
2. Keep GUI logic separate from application, domain/scientific, and infrastructure logic.
3. Keep values that vary by environment, laboratory, server, installation, or scientific definition in configuration, resources, or approved templates rather than source code.
4. Prefer explicit validation and failure for missing or invalid prerequisites.
5. Fix the violated model or invariant when debugging; do not stack special-case patches.
6. Add or change modules only where a clear responsibility exists. Avoid god files, generic dumping grounds, unnecessary tiny modules, speculative interfaces, registries, dependency-injection systems, and plugin frameworks.
7. Refactor adjacent code only when required for the requested change to be correct.
8. Any interactive selection control implemented as a combo/dropdown must retain a visible right-side dropdown indicator unless there is a documented UI reason not to.

## Guardrails

- Do not expand task scope or add useful-looking features without an explicit request.
- Do not add silent fallback, automatic recovery, or implicit substitution of values, files, algorithms, hosts, or paths.
- Do not invent scientific parameters, geometry, FHI-aims/AITRANSS syntax, species defaults, convergence criteria, charge/spin values, or electrode data.
- Do not treat Slurm completion as FHI-aims success or scientific convergence.
- Do not place credentials or secrets in tracked files.
- Do not turn this skill into an FHI-aims tutorial or use it as authority for computational chemistry settings.

## Validate

1. Follow `AGENTS.md`'s validation policy: small edits use affected focused tests; completed features use the subsystem plus direct integration tests through explicit `tools/run_tests.ps1 -Tests` paths. Do not pursue arbitrary coverage targets or build unnecessary test infrastructure.
2. The full offline suite is required only for schema/persistence, scientific behavior, high-risk scheduler/security/cross-layer architecture changes, or a formal pre-commit snapshot; do not add it to every OpenSpec change by default. Independent Claude audit is limited to those high-risk changes and public-release blockers, not ordinary GUI, documentation, or local features.
3. Run OpenSpec strict validation once after proposal completion, once after apply completion, and once before archive; reuse evidence if the artifacts have not changed. Full privacy/provenance/secret/AI-attribution/repository-hierarchy audits belong to pre-public validation, not the ordinary feature loop.
4. Match validation to the change and distinguish:
   - unit-tested behavior;
   - local execution;
   - remote submission;
   - scheduler completion;
   - technical program success; and
   - scientific convergence.
5. Never claim a validation level that was not actually performed. Mark unavailable required validation as unverified with its reason; do not suppress warnings or errors to hide missing or failed evidence.

## Report

State what changed, what was validated, what remains deliberately unimplemented, and which required inputs are still missing. Report every tool failure or omitted validation explicitly; never hide a fallback.
