# binary-wheels

Upstream release binaries of widely used developer tools, repackaged as Python wheels
so they install with `pip`, `uv`, `pipx` and pre-commit, with no Go/C++ toolchain, no
Docker and no `curl | tar` in CI.

| PyPI package | Upstream | Platforms | pre-commit hook |
|---|---|---|---|
| [`gitleaks`](https://pypi.org/project/gitleaks/) | [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) | Linux (glibc + musl) x86_64/aarch64/i686/armv7l/armv6l, macOS 11+, Windows x64/x86/arm64 | `gitleaks` |
| [`osv-scanner`](https://pypi.org/project/osv-scanner/) | [google/osv-scanner](https://github.com/google/osv-scanner) | Linux (glibc + musl) x86_64/aarch64, macOS 13+, Windows x64/arm64 | `osv-scanner` |
| [`doxygen`](https://pypi.org/project/doxygen/) | [doxygen/doxygen](https://github.com/doxygen/doxygen) | Linux glibc 2.35+ x86_64, macOS 15+, Windows x64 | none |

These are **unofficial** redistributions of the unmodified upstream binaries. If an
upstream project wants to publish to PyPI itself, we are happy to hand the PyPI project
over.

## Using the pre-commit hooks

```yaml
repos:
  - repo: https://github.com/shenxianpeng/binary-wheels
    rev: gitleaks-v8.30.1 # any tag works; every tag carries the latest pin of every hook
    hooks:
      - id: gitleaks
      - id: osv-scanner
```

Upstream's hooks use `language: golang`, so every new machine compiles the tool with a Go
toolchain. These use `language: python` and install the wheel.

## How it works

```
tools/<tool>.toml        how to package it (hand-written): assets per platform tag, files, license
tools/<tool>.lock.json   what is packaged (written by `binwheel bump`): version + sha256 of every download
tools/<tool>.md          the PyPI project description
src/binwheel/            the builder (stdlib + packaging)
```

1. **Bump.** `check-upstream.yml` runs daily. For a new upstream release it pins the
   version and the sha256 of every asset (taken from upstream's checksum file when there
   is one, otherwise computed and left for review), builds and runs the Linux wheel, and
   opens a pull request with a before/after table and the hash diff.
2. **Review and merge.** CI builds every wheel, checks every download against the pinned
   hash, installs and runs the wheels on Linux x86_64/arm64, macOS arm64/x86_64 and
   Windows x64/arm64, in Alpine (musl) and Debian 11 containers, and exercises the
   pre-commit hooks against a repo with a planted secret and a vulnerable dependency.
3. **Publish.** Merging changes `tools/`, so `publish.yml` publishes every tool whose
   pinned version is not on PyPI yet (Trusted Publishing, PEP 740 attestations), then
   tags `<tool>-v<version>` and attaches the wheels (and, for GPL tools, the matching
   source tarball) to a GitHub release.

So the only routine work is reviewing and merging the bump pull requests.

Wheel layout: executables go to `<dist>.data/scripts/` and land directly in the
environment's `bin/`, like ruff and uv do, so running the tool starts no Python. Tools
whose binary needs neighbouring files (doxygen on Windows needs `libclang.dll`) use
`layout = "package"`: binaries in `<module>/bin/` behind a console-script entry point.
Every wheel also has a small module, so `python -m gitleaks` and
`gitleaks.find_binary()` work. Builds are reproducible: same inputs, byte-identical
wheels.

## Commands

```bash
uv run binwheel list                         # tools and pinned versions
uv run binwheel bump gitleaks                # pin the latest upstream release
uv run binwheel bump gitleaks --version 8.30.0
uv run binwheel build gitleaks -o dist       # all platforms
uv run binwheel build --host -o dist         # only what installs on this machine
uv run binwheel smoke --dist dist            # install into a fresh venv and run
uv run pytest
```

A packaging-only fix of the same upstream version: set `"post": 1` in the lock file;
the wheels become `X.Y.Z.post1`.

## Adding a tool

1. Write `tools/<name>.toml` (copy the closest existing one) and `tools/<name>.md`.
2. `uv run binwheel bump <name>`, then `uv run binwheel build <name> -o dist` and
   `uv run binwheel smoke <name> --dist dist`.
3. On PyPI, add a pending trusted publisher for the new project (below).
4. Open a pull request; merging publishes.

Check before you add one: the upstream license allows redistribution, the platform tags
match what the binaries actually need (`file`, `otool -l | grep minos`, glibc symbol
versions), and the PyPI name is free.

## One-time setup

- PyPI: for each project (`gitleaks`, `osv-scanner`, `doxygen`), add a (pending)
  trusted publisher: owner/repository of this repo, workflow `publish.yml`,
  environment `pypi`.
- GitHub: create the `pypi` environment (optionally with required reviewers), and enable
  *Settings > Actions > General > Allow GitHub Actions to create and approve pull
  requests* for the bump pull requests.
- PyPI's default per-project size limit is 10 GB. doxygen releases are about 190 MB of
  wheels, so that is roughly 50 releases.

## License

The packaging code in this repository is MIT. Each wheel bundles an upstream program
under its own license (MIT for gitleaks, Apache-2.0 for osv-scanner, GPL-2.0 for
doxygen), included in the wheel's `.dist-info/licenses/`.
