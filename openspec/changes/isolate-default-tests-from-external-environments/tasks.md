## 1. Declare the default-test contract

- [x] 1.1 Add a `test` optional dependency containing `pytest` to `pyproject.toml`, and verify the file parses and exposes the expected extra without changing runtime dependencies.
- [x] 1.2 Update `README.md` with the clean-environment test installation command, the existing `tools/run_tests.ps1 -Full` entry point, and the explicit synthetic/offline versus real-environment validation boundary; verify the documented commands and claims agree with project metadata and runner behavior.

## 2. Enforce default-suite isolation

- [x] 2.1 Add the shared `tests/unit/conftest.py` setup that redirects `APPDATA` to test-owned temporary storage before unit-test module use and restores process state afterward; verify a focused test resolves Moltage application paths inside the temporary root and leaves a sentinel user path untouched.
- [x] 2.2 Extend the shared guard to reject unmocked socket/SSH and native credential-store access through the production boundaries currently used by the repository, with explicit failure messages; verify focused tests prove each real boundary is denied before access while injected fake clients, scripted executors, and memory credential backends still operate.
- [x] 2.3 Add `tests/unit/test_default_test_isolation.py` coverage for temporary local state, network/Paramiko denial, credential denial, and fake-boundary compatibility; run this test module directly and confirm it performs no real external operation.

## 3. Remove machine-specific test assumptions

- [x] 3.1 Replace the fixed Git Bash absolute paths in `tests/unit/test_runtime_configuration.py` with portable `PATH` discovery, preserving syntax validation when `bash` exists and an explicit skip/unverified result when it does not; run the affected runtime-configuration tests in both mocked discovery outcomes.
- [x] 3.2 Add `tests/fixtures/README.md` documenting synthetic provenance, permitted minimal fixture content, prohibited real/distribution data, and the validation boundary; verify every current fixture subtree is covered by this statement or its existing more-specific README without changing fixture data.

## 4. Validate the isolated default suite

- [x] 4.1 Run focused tests for default isolation, application paths, Paramiko execution, credential safety, and runtime configuration; record the exact pass/fail/skip result and do not replace a missing validation with inference.
- [x] 4.2 Run `tools/run_tests.ps1 -Full` with the central guard active and record collected/passed/failed/skipped counts and exit code; do not connect to real SSH/HPC/FHI-aims/AITRANSS resources to investigate or compensate for failures.
- [x] 4.3 Compare `git status --short` with the pre-apply state and verify the test run added no non-ignored repository artifacts, implementation changes remain within the approved files, and unrelated existing changes—including any Qt timing failure—were neither modified nor hidden.
