"""The three jobs: build wheels, bump to a new upstream release, smoke-test wheels."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path, PurePosixPath

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

from . import fetch
from .config import Lock, Platform, Tool, load_tool, write_lock
from .wheel import build_wheel, dist_name

DEFAULT_CACHE = Path(".cache/downloads")
HOOKS_FILE = Path(".pre-commit-hooks.yaml")


def downloads(tool: Tool, version: str | None = None) -> dict[str, str]:
    """Every upstream file a build needs, keyed the same way as the lock file."""
    result = {}
    for p in tool.platforms:
        asset = tool.render(p.asset, version)
        result[asset] = tool.asset_url(asset, version)
    if tool.license_url:
        result["LICENSE"] = tool.render(tool.license_url, version)
    for url in tool.extra_downloads.values():
        url = tool.render(url, version)
        result[PurePosixPath(url).name] = url
    return result


def fetch_key(tool: Tool, key: str, cache: Path) -> Path:
    url = downloads(tool)[key]
    try:
        expected = tool.lock.sha256[key]
    except KeyError:
        raise fetch.ChecksumError(f"{tool.lock_path} has no sha256 for {key!r}; run `binwheel bump {tool.name}`") from None
    return fetch.fetch_verified(url, cache / tool.name / tool.lock.version / key, expected)


def host_platform_tags() -> set[str]:
    return {t.platform for t in sys_tags()}


def select_platforms(tool: Tool, host: bool = False, match: str | None = None) -> list[Platform]:
    chosen = tool.platforms
    if host:
        tags = host_platform_tags()
        chosen = [p for p in chosen if tags.intersection(p.tag.split("."))]
    if match:
        chosen = [p for p in chosen if match in p.tag]
    return chosen


def build(tool: Tool, out_dir: Path, cache: Path = DEFAULT_CACHE, host: bool = False, match: str | None = None) -> list[Path]:
    built = []
    platforms = select_platforms(tool, host=host, match=match)
    if not platforms:
        print(f"{tool.name}: no platform selected", file=sys.stderr)
    for p in platforms:
        asset_key = tool.render(p.asset)
        archive = fetch_key(tool, asset_key, cache)
        files = {name: fetch.read_member(archive, tool.render(member)) for name, member in p.files.items()}
        if tool.license_member:
            license_text = fetch.read_member(archive, tool.render(tool.license_member))
        else:
            license_text = fetch_key(tool, "LICENSE", cache).read_bytes()
        path = build_wheel(tool, p, files, license_text, out_dir)
        print(f"built {path} ({path.stat().st_size / 1e6:.1f} MB)")
        built.append(path)
    return built


def fetch_extras(tool: Tool, out_dir: Path, cache: Path = DEFAULT_CACHE) -> list[Path]:
    """Download the pinned extra files (e.g. a GPL source tarball) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for url in tool.extra_downloads.values():
        key = PurePosixPath(tool.render(url)).name
        cached = fetch_key(tool, key, cache)
        dest = out_dir / key
        dest.write_bytes(cached.read_bytes())
        result.append(dest)
    return result


def version_from_tag(tool: Tool, tag: str) -> str:
    m = re.match(tool.tag_regex, tag)
    if not m:
        raise ValueError(f"{tool.name}: tag {tag!r} does not match {tool.tag_regex!r}")
    return ".".join(m.groups())


def bump(tool: Tool, version: str | None = None, force: bool = False, cache: Path = DEFAULT_CACHE, hooks_file: Path = HOOKS_FILE) -> str | None:
    """Pin a new upstream version. Returns the new version, or None if already current."""
    if version is None:
        version = version_from_tag(tool, fetch.latest_release_tag(tool.repo))
    if version == tool.lock.version and not force:
        print(f"{tool.name}: already at {version}")
        return None

    published: dict[str, str] = {}
    if tool.checksums:
        name = tool.render(tool.checksums, version)
        published = fetch.parse_checksums(fetch.read_url(tool.asset_url(name, version)).decode())

    hashes = {}
    for key, url in downloads(tool, version).items():
        if key in published:
            hashes[key] = published[key]
        else:
            # Upstream publishes no checksum for this file: hash it ourselves.
            # The hash then shows up in the bump PR for review (trust on first use).
            path = fetch.download(url, cache / tool.name / version / key)
            hashes[key] = fetch.sha256_file(path)

    write_lock(tool.lock_path, Lock(version=version, post=0, sha256=hashes))
    if hooks_file.exists():
        pin = re.compile(rf"(?<![\w-]){re.escape(tool.name)}==[\w.]+")
        text = hooks_file.read_text()
        hooks_file.write_text(pin.sub(f"{tool.name}=={version}", text))
    print(f"{tool.name}: {tool.lock.version} -> {version}")
    return version


def compatible_wheel(tool: Tool, dist: Path) -> Path | None:
    tags = list(sys_tags())
    rank = {t: i for i, t in enumerate(tags)}
    best = None
    for whl in dist.glob(f"{dist_name(tool.name)}-{tool.lock.wheel_version}-*.whl"):
        _, _, _, wtags = parse_wheel_filename(whl.name)
        score = min((rank[t] for t in wtags if t in rank), default=None)
        if score is not None and (best is None or score < best[0]):
            best = (score, whl)
    return best[1] if best else None


def smoke(tool: Tool, dist: Path) -> bool | None:
    """Install the host's wheel into a fresh venv and run it. None = no wheel for this host."""
    whl = compatible_wheel(tool, dist)
    if whl is None:
        print(f"{tool.name}: no wheel for this platform in {dist}, skipping")
        return None
    expect = tool.render(tool.smoke_expect)
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        bindir = venv / ("Scripts" if sys.platform == "win32" else "bin")
        python = bindir / ("python.exe" if sys.platform == "win32" else "python")
        subprocess.run([str(python), "-m", "pip", "install", "-q", "--no-index", str(whl)], check=True)
        exe = bindir / (tool.binary + (".exe" if sys.platform == "win32" else ""))
        commands = {
            "console": [str(exe), *tool.smoke_args],
            "python -m": [str(python), "-m", tool.module, *tool.smoke_args],
            "find_binary": [str(python), "-c", f"import {tool.module}; print({tool.module}.find_binary())"],
        }
        ok = True
        for label, cmd in commands.items():
            proc = subprocess.run(cmd, capture_output=True, text=True)
            out = proc.stdout + proc.stderr
            passed = proc.returncode == 0 and (label == "find_binary" or expect in out)
            ok &= passed
            print(f"{tool.name} [{whl.name}] {label}: {'ok' if passed else 'FAILED'} -> {out.strip().splitlines()[:1]}")
    return ok


def on_pypi(name: str, version: str) -> bool:
    try:
        fetch.read_url(f"https://pypi.org/pypi/{name}/{version}/json")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def resolve(spec: str, tools_dir: Path = Path("tools")) -> Tool:
    path = Path(spec)
    if not path.suffix:
        path = tools_dir / f"{spec}.toml"
    return load_tool(path)
