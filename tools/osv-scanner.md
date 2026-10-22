# osv-scanner on PyPI

[OSV-Scanner](https://github.com/google/osv-scanner) checks your dependencies against
the [OSV.dev](https://osv.dev) vulnerability database. It reads lockfiles from many
ecosystems, including `uv.lock`, `poetry.lock`, `requirements.txt`, `Pipfile.lock`,
`package-lock.json`, `Cargo.lock` and `go.mod`, as well as SBOMs and container images.

This package ships the **unmodified upstream osv-scanner {{version}} binary** as Python
wheels, so Python projects can pin and run it like any other dev dependency.

> Unofficial redistribution. OSV-Scanner is developed by Google and contributors; this
> project only repackages their release binaries. Report scanner bugs
> [upstream](https://github.com/google/osv-scanner/issues) and packaging bugs
> [here](https://github.com/shenxianpeng/binary-wheels/issues).

## Install

```bash
pip install osv-scanner       # or
uv tool install osv-scanner   # or run once with
uvx osv-scanner scan source -r .
```

Pin it next to your other dev tools:

```toml
[dependency-groups]
dev = ["osv-scanner=={{version}}"]
```

```bash
uv run osv-scanner scan source --lockfile uv.lock
```

## pre-commit

The upstream hook uses `language: golang`. This one installs the wheel instead:

```yaml
repos:
  - repo: https://github.com/shenxianpeng/binary-wheels
    rev: osv-scanner-v{{wheel_version}}
    hooks:
      - id: osv-scanner
```

## Platforms

| OS | Architectures |
|---|---|
| Linux (glibc and musl) | x86_64, aarch64 |
| macOS 13+ | x86_64, arm64 |
| Windows | x64, arm64 |

## Versions and verification

The package version is the osv-scanner version (`.postN` marks a packaging-only fix).
Wheels are built from the assets of upstream tag `v{{version}}`; their sha256 hashes come
from the upstream `osv-scanner_SHA256SUMS`, are committed to the packaging repository and
are checked on every build. Wheels are published with PyPI Trusted Publishing and carry
PEP 740 attestations.
