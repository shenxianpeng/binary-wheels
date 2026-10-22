from __future__ import annotations

import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from packaging.tags import parse_tag
from packaging.utils import parse_wheel_filename

from binwheel import fetch, ops
from binwheel.config import ConfigError, discover, load_tool

from .conftest import make_upstream, write_config

REPO = Path(__file__).resolve().parent.parent


def _record_ok(zf: zipfile.ZipFile) -> None:
    record_name = next(n for n in zf.namelist() if n.endswith(".dist-info/RECORD"))
    rows = [line.split(",") for line in zf.read(record_name).decode().splitlines()]
    listed = {row[0] for row in rows}
    assert listed == set(zf.namelist())
    for path, digest, size in rows:
        if path == record_name:
            assert digest == size == ""
            continue
        data = zf.read(path)
        expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        assert digest == expected and int(size) == len(data), path


def test_bump_pins_hashes_and_hook_versions(fake, tmp_path):
    lock = json.loads(fake.lock_path.read_text())
    assert lock["version"] == "1.0.0" and lock["post"] == 0
    up = tmp_path / "upstream"
    for asset in ("faketool_1.0.0_linux.tar.gz", "faketool_1.0.0_host.tar.gz"):
        assert lock["sha256"][asset] == hashlib.sha256((up / asset).read_bytes()).hexdigest()
    hooks = (tmp_path / ".pre-commit-hooks.yaml").read_text()
    assert '"fake-tool==1.0.0"' in hooks
    assert '"other-fake-tool==0"' in hooks  # a tool whose name merely ends the same is left alone


def test_bump_is_a_no_op_when_current(fake, tmp_path):
    assert ops.bump(fake, cache=tmp_path / "cache", hooks_file=tmp_path / "none") is None


def test_bump_without_upstream_checksums_hashes_downloads(tmp_path, monkeypatch):
    up = make_upstream(tmp_path)
    cfg = write_config(tmp_path, up, checksums=False)
    monkeypatch.setattr(fetch, "latest_release_tag", lambda repo: "v1.0.0")
    ops.bump(load_tool(cfg), cache=tmp_path / "cache", hooks_file=tmp_path / "none")
    tool = load_tool(cfg)
    asset = "faketool_1.0.0_linux.tar.gz"
    assert tool.lock.sha256[asset] == hashlib.sha256((up / asset).read_bytes()).hexdigest()


def test_scripts_layout_wheel(fake, tmp_path):
    built = ops.build(fake, tmp_path / "dist", cache=tmp_path / "cache")
    assert len(built) == 2
    whl = next(p for p in built if "manylinux" in p.name)
    name, version, _, tags = parse_wheel_filename(whl.name)
    assert (name, str(version)) == ("fake-tool", "1.0.0")
    assert {t.platform for t in tags} == {"manylinux_2_17_x86_64", "manylinux2014_x86_64", "musllinux_1_1_x86_64"}
    with zipfile.ZipFile(whl) as zf:
        _record_ok(zf)
        script = zf.getinfo("fake_tool-1.0.0.data/scripts/faketool")
        assert (script.external_attr >> 16) & 0o111  # executable
        assert zf.read("fake_tool-1.0.0.data/scripts/faketool").startswith(b"#!/bin/sh")
        meta = zf.read("fake_tool-1.0.0.dist-info/METADATA").decode()
        assert "License-Expression: MIT" in meta
        assert "Project-URL: Release notes, https://example.invalid/faketool/v1.0.0" in meta
        assert meta.rstrip().endswith("Pinned at 1.0.0.")
        wheel = zf.read("fake_tool-1.0.0.dist-info/WHEEL").decode()
        assert "Root-Is-Purelib: false" in wheel and wheel.count("Tag: py3-none-") == 3
        assert zf.read("fake_tool-1.0.0.dist-info/licenses/LICENSE") == b"Fake License\n"
        assert "fake_tool/__init__.py" in zf.namelist()
        assert not any(n.endswith("entry_points.txt") for n in zf.namelist())


def test_package_layout_wheel(tmp_path, monkeypatch):
    up = make_upstream(tmp_path)
    cfg = write_config(tmp_path, up, layout="package")
    monkeypatch.setattr(fetch, "latest_release_tag", lambda repo: "v1.0.0")
    ops.bump(load_tool(cfg), cache=tmp_path / "cache", hooks_file=tmp_path / "none")
    whl = ops.build(load_tool(cfg), tmp_path / "dist", cache=tmp_path / "cache", match="manylinux")[0]
    with zipfile.ZipFile(whl) as zf:
        _record_ok(zf)
        assert "fake_tool/bin/faketool" in zf.namelist()
        assert not any(".data/" in n for n in zf.namelist())
        assert zf.read("fake_tool-1.0.0.dist-info/entry_points.txt") == b"[console_scripts]\nfaketool = fake_tool:main\n"


def test_builds_are_reproducible(fake, tmp_path):
    a = ops.build(fake, tmp_path / "a", cache=tmp_path / "cache", match="manylinux")[0]
    b = ops.build(fake, tmp_path / "b", cache=tmp_path / "cache", match="manylinux")[0]
    assert a.read_bytes() == b.read_bytes()


def test_tampered_download_is_rejected(fake, tmp_path):
    asset = tmp_path / "upstream" / "faketool_1.0.0_linux.tar.gz"
    asset.write_bytes(asset.read_bytes() + b"tampered")
    with pytest.raises(fetch.ChecksumError):
        ops.build(fake, tmp_path / "dist", cache=tmp_path / "fresh-cache", match="manylinux")
    assert not list((tmp_path / "fresh-cache").rglob("*.tar.gz"))  # the bad file is not kept


def test_unsafe_archive_member_is_refused(tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ok", b"x")
    with pytest.raises(ValueError):
        fetch.read_member(archive, "../etc/passwd")


def test_parse_checksums():
    text = "a" * 64 + "  one.tar.gz\n" + "B" * 64 + " *two.zip\nnot a line\n"
    assert fetch.parse_checksums(text) == {"one.tar.gz": "a" * 64, "two.zip": "b" * 64}


def test_version_from_tag_with_underscores():
    tool = load_tool(REPO / "tools" / "doxygen.toml")
    assert ops.version_from_tag(tool, "Release_1_18_0") == "1.18.0"
    assert tool.vars("1.18.0")["tag"] == "Release_1_18_0"
    with pytest.raises(ValueError):
        ops.version_from_tag(tool, "v1.18.0")


def test_license_needs_exactly_one_source(tmp_path):
    up = make_upstream(tmp_path)
    cfg = write_config(tmp_path, up)
    cfg.write_text(cfg.read_text().replace("[license]\n", '[license]\nurl = "https://example.invalid/LICENSE"\n'))
    with pytest.raises(ConfigError):
        load_tool(cfg)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake binary is a shell script")
def test_installed_wheel_runs(fake, tmp_path):
    ops.build(fake, tmp_path / "dist", cache=tmp_path / "cache", host=True)
    assert ops.smoke(fake, tmp_path / "dist") is True


@pytest.mark.parametrize("path", discover(REPO / "tools"), ids=lambda p: p.stem)
def test_shipped_tool_configs_are_complete(path):
    tool = load_tool(path)
    assert tool.readme.is_file()
    assert tool.lock.version != "0", "run `binwheel bump`"
    pinned = set(tool.lock.sha256)
    for key in ops.downloads(tool):
        assert key in pinned, f"{key} has no pinned sha256"
    tags = [p.tag for p in tool.platforms]
    assert len(tags) == len(set(tags))
    for tag in tags:
        assert parse_tag(f"py3-none-{tag}")
        for platform in tag.split("."):
            assert platform.startswith(("manylinux", "musllinux", "linux_arm", "macosx_", "win")), platform
