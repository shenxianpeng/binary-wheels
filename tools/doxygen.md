# doxygen on PyPI

[Doxygen](https://www.doxygen.nl/) generates documentation from annotated C, C++,
Python, Java and other sources. It is the usual first step of a Sphinx +
[Breathe](https://breathe.readthedocs.io/) or [Exhale](https://exhale.readthedocs.io/)
documentation build.

This package ships the **unmodified upstream doxygen {{version}} binary** as Python
wheels, so a docs build can pin doxygen in `requirements.txt` / `pyproject.toml` instead
of relying on whatever version `apt` or `brew` happens to have.

> Unofficial redistribution. Doxygen is developed by Dimitri van Heesch and contributors;
> this project only repackages the official release binaries. Report doxygen bugs
> [upstream](https://github.com/doxygen/doxygen/issues) and packaging bugs
> [here](https://github.com/shenxianpeng/binary-wheels/issues).

## Install

```bash
pip install doxygen
doxygen --version
```

Read the Docs (`.readthedocs.yaml` installs your docs requirements):

```text
# docs/requirements.txt
doxygen=={{version}}
breathe
sphinx
```

From Python:

```python
import subprocess, doxygen

subprocess.run([doxygen.find_binary(), "Doxyfile"], check=True)
```

## Platforms

| OS | Architectures | Notes |
|---|---|---|
| Linux, glibc 2.35+ | x86_64 | Ubuntu 22.04+, Debian 12+, Fedora 36+, RHEL 10+. Uses the system's `libz` and `libtinfo`. |
| macOS 15+ | x86_64, arm64 | |
| Windows | x64 | ships `libclang.dll` next to `doxygen.exe` |

Linux aarch64 and musl (Alpine) are not covered: upstream publishes no binary for them.
`dot` from [Graphviz](https://graphviz.org/) is not included; install it separately if you
use diagrams.

The wheel contains only `doxygen` (no `doxywizard`, `doxyindexer` or `doxysearch.cgi`).

## License and source

Doxygen is licensed under the GNU GPL v2. The corresponding source code is the upstream
release tarball
[`doxygen-{{version}}.src.tar.gz`](https://github.com/doxygen/doxygen/releases/download/{{tag}}/doxygen-{{version}}.src.tar.gz);
a copy is also attached to the matching
[packaging release](https://github.com/shenxianpeng/binary-wheels/releases/tag/doxygen-v{{wheel_version}}).

## Versions and verification

The package version is the doxygen version (`.postN` marks a packaging-only fix). Upstream
publishes no checksum file, so the sha256 of each release asset is recorded when the
version is bumped, reviewed in the bump pull request, committed to the packaging
repository and checked on every build. Wheels are published with PyPI Trusted Publishing
and carry PEP 740 attestations.
