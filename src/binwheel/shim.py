"""Source of the tiny Python module shipped inside every wheel.

It lets people run `python -m <module>` and lets Python code find the
binary with `<module>.find_binary()`. For the "package" layout it is also
the console-script entry point.
"""

from __future__ import annotations

INIT_TEMPLATE = '''\
"""{name} {version}, repackaged from the upstream release for PyPI.

Upstream project: {homepage}
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

__all__ = ["__version__", "find_binary", "main"]

__version__ = "{version}"

_LAYOUT = "{layout}"
_EXE = "{binary}" + (".exe" if sys.platform == "win32" else "")


def _script_dirs():
    yield sysconfig.get_path("scripts")
    try:
        user_scheme = sysconfig.get_preferred_scheme("user")
    except AttributeError:  # Python < 3.10
        user_scheme = os.name + "_user"
    try:
        yield sysconfig.get_path("scripts", scheme=user_scheme)
    except KeyError:
        pass
    # `pip install --target <dir>` puts scripts in <dir>/bin
    yield str(Path(__file__).resolve().parent.parent / "bin")


def find_binary() -> str:
    """Return the absolute path of the bundled {binary} executable."""
    if _LAYOUT == "package":
        candidates = [str(Path(__file__).resolve().parent / "bin")]
    else:
        candidates = list(_script_dirs())
    for directory in candidates:
        path = Path(directory) / _EXE
        if path.is_file():
            return str(path)
    raise FileNotFoundError(_EXE + " not found; looked in: " + ", ".join(candidates))


def main() -> None:
    binary = find_binary()
    argv = [binary, *sys.argv[1:]]
    if sys.platform == "win32":
        import subprocess

        try:
            sys.exit(subprocess.call(argv))
        except KeyboardInterrupt:
            sys.exit(130)
    os.execv(binary, argv)
'''

MAIN_TEMPLATE = '''\
from . import main

main()
'''


def render(*, name: str, version: str, homepage: str, layout: str, binary: str) -> dict[str, str]:
    values = dict(name=name, version=version, homepage=homepage, layout=layout, binary=binary)
    return {"__init__.py": INIT_TEMPLATE.format(**values), "__main__.py": MAIN_TEMPLATE}
