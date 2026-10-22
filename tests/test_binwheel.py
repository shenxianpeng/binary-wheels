from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from packaging.tags import parse_tag
from packaging.utils import parse_wheel_filename

from binwheel import fetch, ops, wheel
from binwheel.config import ConfigError, Platform, discover, load_tool

from .conftest import LICENSE, make_upstream, write_config

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


@pytest.mark.parametrize("member", ["../etc/passwd", "/etc/passwd"])
def test_unsafe_archive_member_is_refused(tmp_path, member):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ok", b"x")
    with pytest.raises(ValueError):
        fetch.read_member(archive, member)


def test_read_member_handles_bare_files_zips_and_tars(tmp_path):
    up = make_upstream(tmp_path)
    bare = tmp_path / "osv-scanner_linux_amd64"
    bare.write_bytes(b"ELF fake")
    assert fetch.read_member(bare, "") == b"ELF fake"  # '' = the asset itself is the binary
    assert fetch.read_member(up / "faketool_1.0.0_windows.zip", "helper.dll") == b"MZ fake dll"
    assert fetch.read_member(up / "faketool_1.0.0_linux.tar.gz", "faketool-1.0.0/LICENSE") == LICENSE
    with tarfile.open(tmp_path / "dirs.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo("faketool-1.0.0")
        info.type = tarfile.DIRTYPE
        tf.addfile(info)
    with pytest.raises(ValueError, match="not a regular file"):
        fetch.read_member(tmp_path / "dirs.tar.gz", "faketool-1.0.0")
    (tmp_path / "faketool.7z").write_bytes(b"7z")
    with pytest.raises(ValueError, match="don't know how to read"):
        fetch.read_member(tmp_path / "faketool.7z", "faketool")


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


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda text: text.replace('name = "fake-tool"\n', ""), "missing required key 'name'"),
        (lambda text: text.replace('layout = "scripts"', 'layout = "flat"'), "layout must be one of"),
        (lambda text: "platforms = []\n" + text.split("[[platforms]]")[0], "at least one [[platforms]] entry"),
    ],
    ids=["missing-name", "bad-layout", "no-platforms"],
)
def test_invalid_config_is_rejected(tmp_path, edit, message):
    cfg = write_config(tmp_path, make_upstream(tmp_path))
    cfg.write_text(edit(cfg.read_text()))
    with pytest.raises(ConfigError, match=re.escape(message)):
        load_tool(cfg)


def test_platform_is_windows():
    assert Platform(tag="win_amd64", asset="x.zip", files={}).is_windows
    assert not Platform(tag="macosx_11_0_arm64", asset="x.tar.gz", files={}).is_windows


def test_post_release_changes_the_wheel_version_only(fake):
    data = json.loads(fake.lock_path.read_text())
    data["post"] = 1
    fake.lock_path.write_text(json.dumps(data))
    tool = load_tool(fake.path)
    assert (tool.lock.version, tool.lock.wheel_version) == ("1.0.0", "1.0.0.post1")
    assert wheel.wheel_filename(tool, tool.platforms[0]).startswith("fake_tool-1.0.0.post1-py3-none-")
    assert "Version: 1.0.0.post1" in wheel.metadata(tool, "")


def test_metadata_keywords_are_optional(fake):
    assert "Keywords: fake" in wheel.metadata(fake, "")
    assert "Keywords:" not in wheel.metadata(replace(fake, keywords=[]), "")


def test_license_from_a_url(tmp_path, monkeypatch):
    up = make_upstream(tmp_path)
    cfg = write_config(tmp_path, up, license_url=True)
    monkeypatch.setattr(fetch, "latest_release_tag", lambda repo: "v1.0.0")
    ops.bump(load_tool(cfg), cache=tmp_path / "cache", hooks_file=tmp_path / "none")
    tool = load_tool(cfg)
    assert tool.lock.sha256["LICENSE"] == hashlib.sha256(LICENSE).hexdigest()
    whl = ops.build(tool, tmp_path / "dist", cache=tmp_path / "cache", match="manylinux")[0]
    with zipfile.ZipFile(whl) as zf:
        assert zf.read("fake_tool-1.0.0.dist-info/licenses/LICENSE") == LICENSE


def test_extra_downloads_are_pinned_and_fetched(tmp_path, monkeypatch):
    up = make_upstream(tmp_path)
    cfg = write_config(tmp_path, up, extras=True)
    monkeypatch.setattr(fetch, "latest_release_tag", lambda repo: "v1.0.0")
    ops.bump(load_tool(cfg), cache=tmp_path / "cache", hooks_file=tmp_path / "none")
    tool = load_tool(cfg)
    source = "faketool-1.0.0.src.tar.gz"
    assert tool.lock.sha256[source] == hashlib.sha256((up / source).read_bytes()).hexdigest()
    got = ops.fetch_extras(tool, tmp_path / "extras", cache=tmp_path / "cache")
    assert got == [tmp_path / "extras" / source]
    assert got[0].read_bytes() == (up / source).read_bytes()


def test_bump_to_an_explicit_version_and_force(fake, tmp_path):
    make_upstream(tmp_path, version="1.1.0")
    assert ops.bump(fake, version="1.1.0", cache=tmp_path / "cache", hooks_file=tmp_path / "none") == "1.1.0"
    assert load_tool(fake.path).lock.version == "1.1.0"
    assert ops.bump(fake, version="1.0.0", force=True, cache=tmp_path / "cache", hooks_file=tmp_path / "none") == "1.0.0"
    assert load_tool(fake.path).lock.version == "1.0.0"


def test_build_needs_a_pinned_hash_for_every_download(fake, tmp_path):
    data = json.loads(fake.lock_path.read_text())
    del data["sha256"]["faketool_1.0.0_linux.tar.gz"]
    fake.lock_path.write_text(json.dumps(data))
    with pytest.raises(fetch.ChecksumError, match="run `binwheel bump fake-tool`"):
        ops.build(load_tool(fake.path), tmp_path / "dist", cache=tmp_path / "cache", match="manylinux")


def test_build_with_no_matching_platform(fake, tmp_path, capsys):
    assert ops.build(fake, tmp_path / "dist", cache=tmp_path / "cache", match="win") == []
    assert "fake-tool: no platform selected" in capsys.readouterr().err
    assert not (tmp_path / "dist").exists()


def test_compatible_wheel_prefers_the_most_specific_tag(fake, tmp_path):
    dist = tmp_path / "dist"
    host = fake.platforms[1]
    ops.build(fake, dist, cache=tmp_path / "cache", match=host.tag)
    (dist / "fake_tool-1.0.0-py3-none-any.whl").write_bytes(b"")  # installs anywhere, so it ranks last
    (dist / "fake_tool-1.0.0-py3-none-nonexistent_os_cpu.whl").write_bytes(b"")  # not for this machine
    (dist / "fake_tool-2.0.0-py3-none-any.whl").write_bytes(b"")  # not the pinned version
    assert ops.compatible_wheel(fake, dist) == dist / wheel.wheel_filename(fake, host)


def test_smoke_skips_a_platform_without_a_wheel(fake, tmp_path, capsys):
    (tmp_path / "dist").mkdir()
    assert ops.smoke(fake, tmp_path / "dist") is None
    assert "fake-tool: no wheel for this platform" in capsys.readouterr().out


def test_resolve_by_name_or_path(fake):
    assert ops.resolve("faketool", fake.path.parent).name == "fake-tool"
    assert ops.resolve(str(fake.path)).name == "fake-tool"
    with pytest.raises(FileNotFoundError):
        ops.resolve("missing", fake.path.parent)


def _serve(monkeypatch, responses: dict[str, bytes | Exception]) -> list[urllib.request.Request]:
    """Fake urlopen: a URL maps to the body to return or the error to raise. Returns the requests made."""
    seen = []

    def urlopen(req, timeout=None):
        seen.append(req)
        body = responses[req.full_url]
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_request_headers(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    req = fetch._request("https://api.github.com/repos/example/faketool/releases/latest", accept="application/json")
    assert req.get_header("User-agent") == fetch.USER_AGENT
    assert req.get_header("Accept") == "application/json"
    assert req.get_header("Authorization") == "Bearer gh-secret"
    assert not fetch._request("https://objects.githubusercontent.com/x").has_header("Authorization")  # only the API
    monkeypatch.delenv("GH_TOKEN")
    assert not fetch._request("https://api.github.com/x").has_header("Authorization")


def test_latest_release_tag_asks_the_github_api(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    seen = _serve(monkeypatch, {"https://api.github.com/repos/example/faketool/releases/latest": b'{"tag_name": "v1.2.3"}'})
    assert fetch.latest_release_tag("example/faketool") == "v1.2.3"
    assert seen[0].get_header("Authorization") == "Bearer gh-secret"


def test_on_pypi(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")

    def error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("https://pypi.org/", code, "boom", {}, None)

    seen = _serve(
        monkeypatch,
        {
            "https://pypi.org/pypi/fake-tool/1.0.0/json": b"{}",
            "https://pypi.org/pypi/fake-tool/1.0.1/json": error(404),
            "https://pypi.org/pypi/fake-tool/1.0.2/json": error(503),
        },
    )
    assert ops.on_pypi("fake-tool", "1.0.0") is True
    assert ops.on_pypi("fake-tool", "1.0.1") is False
    with pytest.raises(urllib.error.HTTPError):
        ops.on_pypi("fake-tool", "1.0.2")
    assert not any(req.has_header("Authorization") for req in seen)  # the GitHub token never goes to PyPI


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
