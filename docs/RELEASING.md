# Release Procedure

This checklist defines the minimum evidence for a Moltage binary development
release. It does not replace a legal review or real-environment scientific
acceptance.

## 1. Freeze the release tree

- Confirm the version in `pyproject.toml`, Windows version metadata, installer,
  changelog, and bundled update log.
- Confirm `git status --short` is clean and the public branch contains no old
  private-history refs, real HPC data, credentials, licensed scientific
  distributions, or user runtime state.
- Run the focused packaging/license tests, then one full offline suite.

## 2. Build and inspect Windows artifacts

- Build with the exact versions in `packaging/requirements-build-lock.txt`.
- Run `packaging/build_windows.ps1` from repository root.
- Verify that `LICENSE.txt`, `THIRD_PARTY_NOTICES.md`, and `LICENSES/` are
  installed, and that unused Qt Virtual Keyboard/PDF/QML modules are absent.
- Launch the unpacked application with isolated application data and exercise
  the About and license-notices actions.
- Record the installer size and SHA-256. The current development installer is
  unsigned; do not describe a checksum as a publisher identity signature.

## 3. Prepare corresponding source

Run:

```powershell
.\.venv-packaging\Scripts\python.exe packaging\prepare_release_sources.py --bundle
```

The command downloads only the versioned entries in
`packaging/third_party_sources.toml`, verifies every SHA-256 value, and creates
the third-party source asset under `dist/release-sources/`. Create the Moltage
source archive from the exact release commit with `git archive`; never build a
release source archive from an uncommitted working tree.

The same GitHub Release as the installer must contain:

- the exact Moltage source archive;
- the verified third-party corresponding-source bundle;
- the installer and its SHA-256 file;
- release notes that identify the development status and external-program
  boundary.

Do not replace these assets with a written source offer or only upstream URLs.

## 4. Publish deliberately

- Review the release with an independent privacy, provenance, license, and
  quality audit.
- Verify the GitHub repository visibility and branch/tag targets before push.
- Create the release as a pre-release until real download/install smoke testing
  is complete.
- Keep FHI-aims, AITRANSS, ORCA, server profiles, credentials, and scientific
  calculation outputs outside the repository and release assets.
