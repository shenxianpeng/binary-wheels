# gitleaks on PyPI

[Gitleaks](https://github.com/gitleaks/gitleaks) finds hardcoded secrets (passwords,
API keys, tokens) in git repositories, files and stdin.

This package ships the **unmodified upstream gitleaks {{version}} binary** as Python
wheels, so you can install it with the Python tooling you already have. No Go toolchain,
no Docker, no `curl | tar` in CI.

> Unofficial redistribution. Gitleaks is developed by the Gitleaks authors; this project
> only repackages their release binaries. Report gitleaks bugs
> [upstream](https://github.com/gitleaks/gitleaks/issues) and packaging bugs
> [here](https://github.com/shenxianpeng/binary-wheels/issues).

## Install

```bash
pip install gitleaks          # or
uv tool install gitleaks      # or run once with
uvx gitleaks version
```

The `gitleaks` command lands in your environment's `bin/` (`Scripts\` on Windows) and is
the native binary itself, so no Python interpreter starts when you run it.

## pre-commit

The upstream hook uses `language: golang` and builds gitleaks from source on every new
machine. This one installs the wheel instead:

```yaml
repos:
  - repo: https://github.com/shenxianpeng/binary-wheels
    rev: gitleaks-v{{wheel_version}}
    hooks:
      - id: gitleaks
```

## From Python

```python
import subprocess, gitleaks

subprocess.run([gitleaks.find_binary(), "dir", "."], check=False)
```

`python -m gitleaks ...` works too.

## Platforms

| OS | Architectures |
|---|---|
| Linux (glibc and musl) | x86_64, aarch64, i686, armv7l, armv6l |
| macOS 11+ | x86_64, arm64 |
| Windows | x64, x86, arm64 |

## Versions and verification

The package version is the gitleaks version. A `.postN` suffix marks a packaging-only
fix of the same gitleaks release.

Every wheel is built from the upstream release assets of tag `v{{version}}`. Their
sha256 hashes come from the upstream `gitleaks_{{version}}_checksums.txt`, are committed to
the packaging repository, and are checked on every build. Wheels are published from
GitHub Actions with PyPI Trusted Publishing and carry PEP 740 attestations.
