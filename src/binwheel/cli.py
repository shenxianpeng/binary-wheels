"""Command line: `binwheel list | build | bump | smoke | plan`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import ops
from .config import discover, load_tool


def _github_output(**values: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def _tools(specs: list[str], tools_dir: Path):
    if not specs or specs == ["all"]:
        return [load_tool(p) for p in discover(tools_dir)]
    return [ops.resolve(s, tools_dir) for s in specs]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="binwheel", description=__doc__)
    parser.add_argument("--tools-dir", type=Path, default=Path("tools"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show the packaged tools and their pinned versions")
    p_list.add_argument("--json", action="store_true", help="print a JSON array of tool names (for CI matrices)")

    p_build = sub.add_parser("build", help="build wheels")
    p_build.add_argument("tools", nargs="*", help="tool names or paths (default: all)")
    p_build.add_argument("-o", "--out", type=Path, default=Path("dist"))
    p_build.add_argument("--cache", type=Path, default=ops.DEFAULT_CACHE)
    p_build.add_argument("--host", action="store_true", help="only wheels that install on this machine")
    p_build.add_argument("--platform", help="only platform tags containing this text")

    p_bump = sub.add_parser("bump", help="pin the latest (or a given) upstream release")
    p_bump.add_argument("tool")
    p_bump.add_argument("--version")
    p_bump.add_argument("--force", action="store_true", help="re-pin even if the version is unchanged")
    p_bump.add_argument("--cache", type=Path, default=ops.DEFAULT_CACHE)

    p_smoke = sub.add_parser("smoke", help="install this machine's wheel in a fresh venv and run it")
    p_smoke.add_argument("tools", nargs="*")
    p_smoke.add_argument("--dist", type=Path, default=Path("dist"))
    p_smoke.add_argument("--require", action="store_true", help="fail if no wheel matches this machine")

    sub.add_parser("plan", help="JSON list of publishable tools whose pinned version is not on PyPI yet")

    p_version = sub.add_parser("version", help="print the pinned wheel version of a tool")
    p_version.add_argument("tool")

    p_extras = sub.add_parser("extras", help="download a tool's pinned extra files (e.g. GPL source tarball)")
    p_extras.add_argument("tool")
    p_extras.add_argument("-o", "--out", type=Path, default=Path("extras"))
    p_extras.add_argument("--cache", type=Path, default=ops.DEFAULT_CACHE)

    args = parser.parse_args(argv)

    if args.cmd == "version":
        print(ops.resolve(args.tool, args.tools_dir).lock.wheel_version)
        return 0

    if args.cmd == "extras":
        for path in ops.fetch_extras(ops.resolve(args.tool, args.tools_dir), args.out, args.cache):
            print(path)
        return 0

    if args.cmd == "list":
        tools = _tools([], args.tools_dir)
        if args.json:
            print(json.dumps([t.name for t in tools]))
        else:
            for t in tools:
                print(f"{t.name:16} {t.lock.wheel_version:12} {len(t.platforms)} platforms  ({t.repo})")
        return 0

    if args.cmd == "build":
        built = []
        for tool in _tools(args.tools, args.tools_dir):
            built += ops.build(tool, args.out, args.cache, host=args.host, match=args.platform)
        return 0 if built else 1

    if args.cmd == "bump":
        tool = ops.resolve(args.tool, args.tools_dir)
        new = ops.bump(tool, version=args.version, force=args.force, cache=args.cache)
        _github_output(changed="true" if new else "false", version=new or tool.lock.version)
        return 0

    if args.cmd == "smoke":
        failed = False
        for tool in _tools(args.tools, args.tools_dir):
            result = ops.smoke(tool, args.dist)
            failed |= result is False or (result is None and args.require)
        return 1 if failed else 0

    if args.cmd == "plan":
        pending = [t.name for t in _tools([], args.tools_dir) if t.publish and not ops.on_pypi(t.name, t.lock.wheel_version)]
        print(json.dumps(pending))
        _github_output(tools=json.dumps(pending), any="true" if pending else "false")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
