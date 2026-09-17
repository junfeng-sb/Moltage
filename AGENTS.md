# Moltage Development Guide

Moltage is a portable Windows desktop workbench for single-molecule quantum transport and related analysis. Current workflows include FHI-aims/AITRANSS transport and ORCA optimization/WBL analysis; the application must not depend on Codex or another AI assistant at runtime.

Before editing, read the documents relevant to the task:

- `docs/PROJECT_SCOPE.md`
- `docs/ARCHITECTURE.md`
- `docs/WORKFLOW.md`
- `docs/CONFIGURATION.md`
- `.agents/skills/moltage-development/SKILL.md`

Stay within the user's explicit scope. Make the smallest correct, modular change in the owning area and do not opportunistically refactor adjacent code. Keep GUI concerns separate from application, scientific/domain, and infrastructure logic.

For every base-10 logarithmic plot, render decade tick labels as typographic powers such as `10⁻³`, never as `1E-03`, `E-03`, or equivalent E notation.

Never invent scientific values, FHI-aims or AITRANSS syntax, species defaults, electrode geometry, or remote configuration. Use approved configuration, templates, reference files, validated examples, and explicit user input. When required evidence is missing, stop that implementation step and report what is needed.

Do not add silent fallbacks, speculative features, generic plugin frameworks, god files, unnecessary abstractions, dependencies, or infrastructure. Prefer explicit failures and fix violated models or invariants instead of stacking special-case patches. Distinguish local/unit validation, remote submission, scheduler completion, technical program success, and scientific convergence.

## Validation and OpenSpec

- Small edits: run the affected modules' focused tests. Completed features: run the subsystem and its direct integration tests, using explicit `tools/run_tests.ps1 -Tests` paths.
- Run the full offline suite only for schema/persistence, scientific behavior, high-risk scheduler/security/cross-layer architecture changes, or a formal pre-commit snapshot. It is not a default task for every OpenSpec change.
- Independent Claude audit applies only to those high-risk changes and public-release blockers, not ordinary GUI, documentation, or local features.
- Run `openspec validate <change> --strict` once when the proposal is complete, once when apply is complete, and once before archive. Reuse existing validation evidence when the artifacts have not changed; do not rerun it merely because another command or test completed.
- Full privacy, provenance, secret, AI-attribution, and repository-hierarchy audits belong to pre-public validation, not the ordinary feature loop. This does not permit introducing secrets or unverified scientific resources during development.
- Report only validation actually performed; mark unavailable required validation as unverified with its reason. Offline tests do not establish acceptance on a real HPC environment. Keep changes and tests scoped; do not build a new testing framework or hide warnings to achieve a pass.
