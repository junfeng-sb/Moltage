# Third-Party Notices

Moltage 0.2.1 is Copyright (C) 2026 Junfeng Lin and is distributed under
GNU GPL version 3 only. This file identifies third-party software present in
the Windows distribution; it does not replace the complete license texts under
`LICENSES/` or the notices shipped in the corresponding-source archives.

The release source manifest is `packaging/third_party_sources.toml`. The exact
archives named there, their SHA-256 values, and the Moltage source archive must
be published with the installer at the same GitHub Release. The source bundle
also preserves upstream copyright, license, and bundled-component notice files.

## Qt and Qt for Python

- **Qt 6.11.2**, including Qt Core, GUI, Widgets, Network, OpenGL, SVG, and
  **Qt Charts**
- **PySide6 / Shiboken6 6.11.2**
- Copyright The Qt Company Ltd. and other contributors
- License option selected for this Moltage binary distribution:
  **GPL-3.0-only**
- Project information: <https://www.qt.io/> and
  <https://doc.qt.io/qtforpython-6/>

Qt's essential modules are offered under LGPLv3 or GPLv3; Moltage exercises
the permitted GPLv3 option for this distribution. Qt Charts is a
GPLv3-or-commercial module. Moltage itself and the distributed combined work
are therefore provided under GPLv3 only. The installer does not include Qt
Virtual Keyboard, Qt PDF, Qt QML, or Qt Quick, which Moltage does not use. Qt
and Qt for Python corresponding source, including their component-specific
third-party notices, is included in the release source bundle.

### Qt software OpenGL fallback

The distributed `PySide6/opengl32sw.dll` is Qt's software-OpenGL fallback for
systems without a usable hardware OpenGL driver. It contains:

- **Mesa 11.2.2 llvmpipe** — MIT and component-specific terms; see
  `LICENSES/Mesa-11.2.2.txt`.
- **LLVM 3.6.2** — University of Illinois/NCSA Open Source License and
  bundled-component terms; see `LICENSES/LLVM-3.6.2.txt`.

The matching Mesa and LLVM upstream source archives are separate release
source assets because the Qt source archive contains only provisioning scripts
for this prebuilt fallback, not its source code.

## Python runtime and packaging

- **CPython 3.13.2** — Python Software Foundation License; see
  `LICENSES/Python-3.13.2.txt`.
- **PyInstaller 6.22.2** — GPL-2.0-or-later with the PyInstaller bootloader
  exception; see `LICENSES/PyInstaller-6.22.2.txt`.
- **setuptools 84.0.0**, **packaging 26.3**, **altgraph 0.17.5**,
  **pefile 2024.8.26**, **pyinstaller-hooks-contrib 2026.7**,
  **jaraco.classes 3.4.0**, **jaraco.context 6.1.2**,
  **jaraco.functools 4.6.0**, **more-itertools 11.1.0**,
  **pycparser 3.0**, and **pywin32-ctypes 0.2.3** retain the permissive or
  exception-bearing terms stated in their source distributions and metadata.

## Scientific and visualization libraries

- **VTK 9.7.0** — BSD 3-Clause; see `LICENSES/VTK-9.7.0.txt`.
- **NumPy 2.5.3** — BSD 3-Clause plus bundled-component terms; the complete
  wheel notice set, including OpenBLAS, LAPACK, and the GCC Runtime Library
  Exception, is reproduced under `LICENSES/NumPy-2.5.3/`.
- **OpenBLAS 0.3.34** (the NumPy wheel reports the SciPy build identifier
  `0.3.34.106.0`) — BSD 3-Clause with bundled LAPACK and GCC runtime terms.

VTK and Qt include additional third-party code under compatible permissive and
open-source licenses. Their complete versioned source archives are release
assets and contain the authoritative per-component notices.

## SSH, cryptography, and credential handling

- **Paramiko 5.0.0** — LGPL-2.1-only; see
  `LICENSES/Paramiko-5.0.0.txt`.
- **cryptography 50.0.1** — Apache-2.0 OR BSD-3-Clause; see
  `LICENSES/cryptography-50.0.1/`.
- **OpenSSL 4.0.2** — Apache-2.0, statically linked into the `cryptography`
  binding.
- **OpenSSL 3.0.15** — Apache-2.0, used by CPython's `_ssl` and `_hashlib`
  modules through the distributed `libssl-3.dll` and `libcrypto-3.dll`.

The component mapping in `LICENSES/OpenSSL-3.0.15-and-4.0.2.txt` points to the
complete Apache-2.0 text shipped under `LICENSES/cryptography-50.0.1/`, and
both exact upstream source archives are included in the release source bundle.
- **bcrypt 5.0.0** — Apache-2.0; see `LICENSES/bcrypt-5.0.0.txt`.
- **PyNaCl 1.6.2** and its bundled **libsodium** — Apache-2.0 and ISC terms;
  see `LICENSES/PyNaCl-1.6.2/`.
- **cffi 2.1.1** — MIT-0; see `LICENSES/cffi-2.1.1.txt`.
- **keyring 25.7.0** — MIT; see `LICENSES/keyring-25.7.0.txt`.
- **Invoke 3.0.3** — BSD 2-Clause; see `LICENSES/Invoke-3.0.3.txt`.

## Microsoft runtime libraries

The Windows build contains unmodified Microsoft Visual C++ runtime DLLs
supplied with the official CPython, PySide6, and NumPy Windows distributions.
They remain subject to the Microsoft software license terms and distributable
code rules. They are not relicensed under GPLv3. Microsoft documents the
current redistribution rules at:
<https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files>.

## External scientific programs

FHI-aims, AITRANSS, and ORCA are **not** included. Moltage stores only user
configuration and invokes user-provided installations after explicit setup.
Their licenses are independent of Moltage and remain the user's responsibility.

## No endorsement

Names of upstream projects and contributors are used only for attribution. No
upstream project or contributor endorses Moltage.
