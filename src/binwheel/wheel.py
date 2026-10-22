"""Write a platform wheel that carries upstream binaries.

Two layouts:

* "scripts": executables go to `<dist>.data/scripts/`, so pip/uv install
  them straight into the environment's bin/ (or Scripts\\) directory. No Python
  starts when you run the tool. This is how ruff and uv ship.
* "package": executables (and any DLLs they need) go to `<module>/bin/`, and a
  console-script entry point runs them through the Python shim. Use it when a
  binary needs neighbouring files that must not land in bin/.
"""

from __future__ import annotations

import base64
import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import PROJECT_URL, __version__, shim
from .config import Platform, Tool

ZIP_DATE = (1980, 1, 1, 0, 0, 0)  # fixed, so the same inputs give byte-identical wheels

PACKAGING_LICENSE = """\
MIT License

Copyright (c) binary-wheels contributors

This license covers only the packaging glue in this wheel (the small Python
module and the metadata). The bundled upstream program keeps its own license,
see the LICENSE file next to this one.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def dist_name(name: str) -> str:
    """Normalised distribution name as used in wheel file names."""
    return re.sub(r"[-_.]+", "_", name).lower()


def wheel_filename(tool: Tool, platform: Platform) -> str:
    return f"{dist_name(tool.name)}-{tool.lock.wheel_version}-py3-none-{platform.tag}.whl"


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def metadata(tool: Tool, long_description: str) -> str:
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {tool.name}",
        f"Version: {tool.lock.wheel_version}",
        f"Summary: {tool.summary}",
    ]
    if tool.keywords:
        lines.append("Keywords: " + ",".join(tool.keywords))
    lines.append(f"License-Expression: {tool.license}")
    lines += ["License-File: LICENSE", "License-File: LICENSE-PACKAGING"]
    for label, url in tool.urls.items():
        lines.append(f"Project-URL: {label}, {tool.render(url)}")
    lines.append(f"Project-URL: Packaging, {PROJECT_URL}")
    lines += [f"Classifier: {c}" for c in tool.classifiers]
    lines.append(f"Requires-Python: {tool.requires_python}")
    lines.append("Description-Content-Type: text/markdown")
    return "\n".join(lines) + "\n\n" + long_description


def render_readme(tool: Tool) -> str:
    text = tool.readme.read_text()
    for key, value in tool.vars().items():
        text = text.replace("{{" + key + "}}", value)
    return text.replace("{{wheel_version}}", tool.lock.wheel_version)


@dataclass
class _Writer:
    zf: zipfile.ZipFile
    records: list[str]

    def add(self, arcname: str, data: bytes, executable: bool = False) -> None:
        info = zipfile.ZipInfo(arcname, ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = ((0o100755 if executable else 0o100644) & 0xFFFF) << 16
        self.zf.writestr(info, data, compresslevel=9)
        self.records.append(f"{arcname},{_record_hash(data)},{len(data)}")


def build_wheel(tool: Tool, platform: Platform, files: dict[str, bytes], license_text: bytes, out_dir: Path) -> Path:
    """files maps the file name inside the wheel (e.g. 'gitleaks.exe') to its bytes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    version = tool.lock.wheel_version
    dist = f"{dist_name(tool.name)}-{version}"
    info_dir = f"{dist}.dist-info"
    path = out_dir / wheel_filename(tool, platform)

    homepage = tool.render(tool.urls.get("Homepage", f"https://github.com/{tool.repo}"))
    shim_files = shim.render(name=tool.name, version=version, homepage=homepage, layout=tool.layout, binary=tool.binary)

    with zipfile.ZipFile(path, "w") as zf:
        w = _Writer(zf, [])
        for fname in sorted(shim_files):
            w.add(f"{tool.module}/{fname}", shim_files[fname].encode())
        for fname in sorted(files):
            if tool.layout == "scripts":
                w.add(f"{dist}.data/scripts/{fname}", files[fname], executable=True)
            else:
                w.add(f"{tool.module}/bin/{fname}", files[fname], executable=True)

        w.add(f"{info_dir}/METADATA", metadata(tool, render_readme(tool)).encode())
        wheel_meta = [
            "Wheel-Version: 1.0",
            f"Generator: binwheel {__version__}",
            "Root-Is-Purelib: false",
            *[f"Tag: py3-none-{p}" for p in platform.tag.split(".")],
        ]
        w.add(f"{info_dir}/WHEEL", ("\n".join(wheel_meta) + "\n").encode())
        if tool.layout == "package":
            w.add(f"{info_dir}/entry_points.txt", f"[console_scripts]\n{tool.binary} = {tool.module}:main\n".encode())
        w.add(f"{info_dir}/licenses/LICENSE", license_text)
        w.add(f"{info_dir}/licenses/LICENSE-PACKAGING", PACKAGING_LICENSE.encode())

        record_name = f"{info_dir}/RECORD"
        record = "\n".join([*w.records, f"{record_name},,"]) + "\n"
        info = zipfile.ZipInfo(record_name, ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100644 & 0xFFFF) << 16
        zf.writestr(info, record)
    return path
