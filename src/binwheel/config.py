"""Load a tool definition (tools/<name>.toml) and its lock file (tools/<name>.lock.json).

The TOML file is written by hand and describes *how* to package a tool.
The lock file is written by `binwheel bump` and pins *what* is packaged:
the upstream version and the sha256 of every file we download.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LAYOUTS = ("scripts", "package")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Platform:
    tag: str  # wheel platform tag, may be a compressed set ("a.b.c")
    asset: str  # release asset name template
    files: dict[str, str]  # file name in the wheel -> path inside the asset ("" = the asset itself)

    @property
    def is_windows(self) -> bool:
        return self.tag.startswith("win")


@dataclass(frozen=True)
class Lock:
    version: str
    post: int
    sha256: dict[str, str]

    @property
    def wheel_version(self) -> str:
        return self.version if self.post == 0 else f"{self.version}.post{self.post}"


@dataclass(frozen=True)
class Tool:
    path: Path
    name: str  # PyPI project name
    module: str  # import name of the small Python shim
    binary: str  # main executable name (without .exe)
    layout: str
    summary: str
    license: str  # SPDX expression for the whole wheel
    keywords: list[str]
    classifiers: list[str]
    requires_python: str
    urls: dict[str, str]
    readme: Path
    repo: str
    tag_template: str
    tag_regex: str
    url_template: str
    checksums: str | None
    license_member: str | None
    license_url: str | None
    platforms: list[Platform]
    smoke_args: list[str]
    smoke_expect: str
    lock: Lock
    extra_downloads: dict[str, str] = field(default_factory=dict)

    @property
    def lock_path(self) -> Path:
        return lock_path_for(self.path)

    def vars(self, version: str | None = None) -> dict[str, str]:
        version = version or self.lock.version
        values = {"version": version, "version_underscore": version.replace(".", "_")}
        values["tag"] = self.tag_template.format(**values)
        return values

    def render(self, template: str, version: str | None = None) -> str:
        return template.format(**self.vars(version))

    def asset_url(self, asset: str, version: str | None = None) -> str:
        return self.url_template.format(asset=asset, **self.vars(version))


def lock_path_for(toml_path: Path) -> Path:
    return toml_path.with_name(toml_path.stem + ".lock.json")


def _require(table: dict, key: str, where: str):
    if key not in table:
        raise ConfigError(f"{where}: missing required key {key!r}")
    return table[key]


def load_lock(path: Path) -> Lock:
    if not path.exists():  # a new tool that has never been bumped
        return Lock(version="0", post=0, sha256={})
    data = json.loads(path.read_text())
    return Lock(version=data["version"], post=int(data.get("post", 0)), sha256=dict(data["sha256"]))


def write_lock(path: Path, lock: Lock) -> None:
    data = {"version": lock.version, "post": lock.post, "sha256": dict(sorted(lock.sha256.items()))}
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_tool(path: Path | str) -> Tool:
    path = Path(path)
    raw = tomllib.loads(path.read_text())
    where = str(path)
    pkg = _require(raw, "package", where)
    up = _require(raw, "upstream", where)
    lic = raw.get("license", {})
    smoke = raw.get("smoke", {})

    name = _require(pkg, "name", where)
    layout = pkg.get("layout", "scripts")
    if layout not in LAYOUTS:
        raise ConfigError(f"{where}: layout must be one of {LAYOUTS}, got {layout!r}")
    if bool(lic.get("member")) == bool(lic.get("url")):
        raise ConfigError(f"{where}: [license] needs exactly one of 'member' or 'url'")

    platforms = []
    for i, p in enumerate(_require(raw, "platforms", where)):
        pwhere = f"{where}: platforms[{i}]"
        platforms.append(
            Platform(tag=_require(p, "tag", pwhere), asset=_require(p, "asset", pwhere), files=dict(_require(p, "files", pwhere)))
        )
    if not platforms:
        raise ConfigError(f"{where}: at least one [[platforms]] entry is required")

    module = pkg.get("module", re.sub(r"[^A-Za-z0-9]+", "_", name).lower())
    readme = path.parent / pkg.get("readme", f"{path.stem}.md")

    return Tool(
        path=path,
        name=name,
        module=module,
        binary=pkg.get("binary", name),
        layout=layout,
        summary=_require(pkg, "summary", where),
        license=_require(pkg, "license", where),
        keywords=list(pkg.get("keywords", [])),
        classifiers=list(pkg.get("classifiers", [])),
        requires_python=pkg.get("requires-python", ">=3.9"),
        urls=dict(pkg.get("urls", {})),
        readme=readme,
        repo=_require(up, "repo", where),
        tag_template=up.get("tag", "v{version}"),
        tag_regex=up.get("tag-regex", r"^v(\d+\.\d+\.\d+)$"),
        url_template=up.get("url", "https://github.com/" + up["repo"] + "/releases/download/{tag}/{asset}"),
        checksums=up.get("checksums"),
        license_member=lic.get("member"),
        license_url=lic.get("url"),
        platforms=platforms,
        smoke_args=list(smoke.get("args", ["--version"])),
        smoke_expect=smoke.get("expect", "{version}"),
        lock=load_lock(lock_path_for(path)),
        extra_downloads=dict(raw.get("extra-downloads", {})),
    )


def discover(tools_dir: Path | str = "tools") -> list[Path]:
    return sorted(Path(tools_dir).glob("*.toml"))
