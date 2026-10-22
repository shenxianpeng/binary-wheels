"""The `binwheel` command line, driven through main() against the fake upstream."""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

import pytest

from binwheel import ops
from binwheel.cli import main
from binwheel.config import load_tool
from binwheel.wheel import wheel_filename

from .conftest import make_upstream, write_config


@pytest.fixture
def cli(fake, capsys, monkeypatch):
    """Run `binwheel --tools-dir <the fake tool's dir> ...`; returns (exit code, stdout)."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)  # set by GitHub Actions; tests that want it point it at a file

    def run(*argv: str) -> tuple[int, str]:
        capsys.readouterr()
        code = main(["--tools-dir", str(fake.path.parent), *argv])
        return code, capsys.readouterr().out

    return run


def test_version(cli):
    assert cli("version", "faketool") == (0, "1.0.0\n")


def test_list(cli):
    code, out = cli("list")
    assert code == 0
    assert out.split() == ["fake-tool", "1.0.0", "2", "platforms", "(example/faketool)"]
    assert cli("list", "--json") == (0, '["fake-tool"]\n')


def test_build(cli, fake, tmp_path):
    out_dir, cache = tmp_path / "out", tmp_path / "cache"
    linux, host = fake.platforms
    code, out = cli("build", "faketool", "-o", str(out_dir), "--cache", str(cache), "--platform", "manylinux")
    assert code == 0
    assert out.startswith(f"built {out_dir / wheel_filename(fake, linux)} (")
    assert [p.name for p in out_dir.glob("*.whl")] == [wheel_filename(fake, linux)]
    code, _ = cli("build", "--host", "-o", str(out_dir), "--cache", str(cache))  # no tool given: all of them
    assert code == 0
    assert (out_dir / wheel_filename(fake, host)).is_file()


def test_build_fails_when_nothing_is_built(cli, tmp_path):
    code, out = cli("build", "all", "-o", str(tmp_path / "out"), "--cache", str(tmp_path / "cache"), "--platform", "win")
    assert (code, out) == (1, "")


def test_bump_writes_github_outputs(cli, tmp_path, monkeypatch):
    make_upstream(tmp_path, version="1.1.0")
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.chdir(tmp_path)  # the hooks file is looked up in the working directory
    code, out = cli("bump", "faketool", "--version", "1.1.0", "--cache", str(tmp_path / "cache"))
    assert code == 0
    assert "fake-tool: 1.0.0 -> 1.1.0" in out
    assert output.read_text() == "changed=true\nversion=1.1.0\n"
    assert '"fake-tool==1.1.0"' in (tmp_path / ".pre-commit-hooks.yaml").read_text()

    output.write_text("")
    code, out = cli("bump", "faketool", "--version", "1.1.0", "--cache", str(tmp_path / "cache"))
    assert code == 0
    assert "fake-tool: already at 1.1.0" in out
    assert output.read_text() == "changed=false\nversion=1.1.0\n"


def test_bump_force_outside_github_actions(cli, fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = cli("bump", "faketool", "--force", "--cache", str(tmp_path / "cache"))
    assert code == 0
    assert "fake-tool: 1.0.0 -> 1.0.0" in out
    assert load_tool(fake.path).lock == fake.lock


def test_smoke(cli, tmp_path, monkeypatch):
    calls = []
    results = {}
    monkeypatch.setattr(ops, "smoke", lambda tool, dist: calls.append((tool.name, dist)) or results[tool.name])
    results["fake-tool"] = True
    assert cli("smoke", "faketool", "--dist", str(tmp_path / "wheels"))[0] == 0
    results["fake-tool"] = False
    assert cli("smoke")[0] == 1
    results["fake-tool"] = None  # no wheel for this machine: fine, unless --require
    assert cli("smoke")[0] == 0
    assert cli("smoke", "--require")[0] == 1
    assert calls == [("fake-tool", tmp_path / "wheels")] + [("fake-tool", Path("dist"))] * 3


def test_plan_lists_publishable_tools_missing_from_pypi(cli, fake, tmp_path, monkeypatch):
    unpublished = fake.path.with_name("unpublished.toml")
    unpublished.write_text(fake.path.read_text().replace('name = "fake-tool"', 'name = "unpublished-tool"\npublish = false'))
    asked = []
    monkeypatch.setattr(ops, "on_pypi", lambda name, version: asked.append((name, version)) or False)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert cli("plan") == (0, '["fake-tool"]\n')
    assert asked == [("fake-tool", "1.0.0")]  # publish = false tools are never looked up
    assert output.read_text() == 'tools=["fake-tool"]\nany=true\n'

    monkeypatch.setattr(ops, "on_pypi", lambda name, version: True)
    output.write_text("")
    assert cli("plan") == (0, "[]\n")
    assert output.read_text() == "tools=[]\nany=false\n"


def test_extras(cli, tmp_path):
    cfg = write_config(tmp_path, tmp_path / "upstream", extras=True)
    ops.bump(load_tool(cfg), force=True, cache=tmp_path / "cache", hooks_file=tmp_path / "none")  # pins the tarball too
    out_dir, source = tmp_path / "extras", "faketool-1.0.0.src.tar.gz"
    assert cli("extras", "faketool", "-o", str(out_dir), "--cache", str(tmp_path / "cache")) == (0, f"{out_dir / source}\n")
    assert (out_dir / source).read_bytes() == (tmp_path / "upstream" / source).read_bytes()


def test_usage_errors_exit_2():
    with pytest.raises(SystemExit) as exc:
        main([])  # a subcommand is required
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2


def test_unhandled_command_returns_2(monkeypatch):
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self, *a, **kw: argparse.Namespace(cmd="frobnicate"))
    assert main([]) == 2


@pytest.mark.filterwarnings("ignore:'binwheel.cli' found in sys.modules:RuntimeWarning")  # running it twice is the point
def test_module_runs_as_a_script(fake, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["binwheel", "--tools-dir", str(fake.path.parent), "version", "faketool"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("binwheel.cli", run_name="__main__")
    assert exc.value.code == 0
    assert capsys.readouterr().out == "1.0.0\n"
