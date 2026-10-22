"""A fake upstream release served from file:// URLs, so tests run offline."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from packaging.tags import sys_tags

SCRIPT = b'#!/bin/sh\necho "faketool 1.0.0 $@"\n'
LICENSE = b"Fake License\n"


def _tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(data))


def _zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def make_upstream(root: Path, version: str = "1.0.0") -> Path:
    up = root / "upstream"
    up.mkdir(parents=True, exist_ok=True)
    _tar(up / f"faketool_{version}_linux.tar.gz", {f"faketool-{version}/faketool": SCRIPT, f"faketool-{version}/LICENSE": LICENSE})
    _tar(up / f"faketool_{version}_host.tar.gz", {f"faketool-{version}/faketool": SCRIPT, f"faketool-{version}/LICENSE": LICENSE})
    _zip(up / f"faketool_{version}_windows.zip", {"faketool.exe": b"MZ fake exe", "helper.dll": b"MZ fake dll"})
    sums = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(up.iterdir()) if p.suffix != ".txt"
    )
    (up / f"faketool_{version}_checksums.txt").write_text(sums)
    return up


def write_config(root: Path, up: Path, *, layout: str = "scripts", checksums: bool = True) -> Path:
    host_tag = next(iter(sys_tags())).platform
    tools = root / "tools"
    tools.mkdir(exist_ok=True)
    checksums_line = 'checksums = "faketool_{version}_checksums.txt"' if checksums else ""
    (tools / "faketool.md").write_text("# faketool {{version}}\n\nPinned at {{wheel_version}}.\n")
    path = tools / "faketool.toml"
    path.write_text(
        f'''
[package]
name = "fake-tool"
binary = "faketool"
layout = "{layout}"
readme = "faketool.md"
summary = "A fake tool"
license = "MIT"
keywords = ["fake"]

[package.urls]
Homepage = "https://example.invalid/faketool"
"Release notes" = "https://example.invalid/faketool/{{tag}}"

[upstream]
repo = "example/faketool"
url = "{up.as_uri()}/{{asset}}"
{checksums_line}

[license]
member = "faketool-{{version}}/LICENSE"

[smoke]
args = ["--version"]
expect = "faketool {{version}}"

[[platforms]]
tag = "manylinux_2_17_x86_64.manylinux2014_x86_64.musllinux_1_1_x86_64"
asset = "faketool_{{version}}_linux.tar.gz"
files = {{ "faketool" = "faketool-{{version}}/faketool" }}

[[platforms]]
tag = "{host_tag}"
asset = "faketool_{{version}}_host.tar.gz"
files = {{ "faketool" = "faketool-{{version}}/faketool" }}
'''
    )
    return path


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A tool config pointing at a fake upstream, bumped to 1.0.0."""
    from binwheel import fetch, ops
    from binwheel.config import load_tool

    up = make_upstream(tmp_path)
    cfg = write_config(tmp_path, up)
    monkeypatch.setattr(fetch, "latest_release_tag", lambda repo: "v1.0.0")
    hooks = tmp_path / ".pre-commit-hooks.yaml"
    hooks.write_text('additional_dependencies: ["fake-tool==0", "other-fake-tool==0"]\n')
    ops.bump(load_tool(cfg), cache=tmp_path / "cache", hooks_file=hooks)
    return load_tool(cfg)
